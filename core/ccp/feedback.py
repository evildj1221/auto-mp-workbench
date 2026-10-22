#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/ccp/feedback.py · CCP 反馈闭环：使用结果与用户反馈回流到数据模型。

写入侧四路（§6.3 图 6-2）：
  ① 用户打分   user_feedback.jsonl（选题维，AI≠用户 → 归因偏好）
  ② 收藏/排除  user_pref.jsonl     （选题维 + 排除系列）
  ③ 排版采纳   typography settings （排版维：激活主题/停用组件）
  ④ 发布回流   article_stats.jsonl （读者维：阅读/在看/分享）
  ⑤ 金句采纳   quotes.jsonl       （风格维：金句偏好参考）

harvest()：扫描以上既有数据源 → 归因聚合 → 更新四维 → 生成 learned_leads 留痕
（"本周学习到的 N 条偏好"，可撤销）。**幂等可持续**：meta.harvest_cursors 记录
每个源的已消费游标，重复 harvest 不重复学习；满足阈值才触发更新（避免一次抖动
即写回导致画像来回跳）。所有落盘经 ClientProfile.save（原子 + 版本快照 + 审计）。
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from core import paths as _paths
from core.storage import load_json
from core.ccp.profile import ClientProfile


def _SRC(name: str) -> Path:
    """动态读取数据源路径（录入 core.paths；测试用 monkeypatch 可隔离）。"""
    return getattr(_paths, name)

# ---- 触发阈值（防抖动 & 成本护栏：样本够才写回）----
MIN_TOPIC_FEEDBACK = 3        # 选题归因：同主题至少 3 条有效打分才写回
MIN_READER_ROWS = 2           # 读者维：至少 2 天数据才算趋势
ENGAGEMENT_DELTA = 0.03       # 互动率相对变化阈值（0 即全量记录，这里防微噪音）
HARVEST_WINDOW_DAYS = 30      # 学习窗口

Cur = Dict[str, object]  # cursor 形如 {offset: N}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _add_lead(profile: ClientProfile, dim: str, lead: str, evidence: str):
    """追加一条 learned_lead 留痕（去重：同 lead 已有未撤销条目则跳过）。"""
    p = profile.profile
    for x in p["learned_leads"]:
        if x.get("dim") == dim and x.get("lead") == lead and not x.get("reverted"):
            return
    p["learned_leads"].append({
        "id": _now().replace(":", "") + f"_{len(p['learned_leads'])}",
        "dim": dim, "lead": lead, "evidence": evidence,
        "ts": _now(), "reverted": False,
    })
    p["learned_leads"] = p["learned_leads"][-40:]


def revert_lead(profile: ClientProfile, lead_id: str) -> bool:
    """撤销某条学习偏好（留痕置 reverted，不删除）。返回是否找到。"""
    for x in profile.profile["learned_leads"]:
        if x.get("id") == lead_id:
            x["reverted"] = True
            profile.save(reason=f"撤销学习偏好：{lead_id}")
            return True
    return False


# ---- 每源 cursor 读写 ----
def _cursor(profile: ClientProfile, source: str) -> int:
    return int((profile.profile["meta"].get("harvest_cursors") or {}).get(source, 0))


def _advance(profile: ClientProfile, source: str, offset: int):
    profile.profile["meta"].setdefault("harvest_cursors", {})[source] = offset


def _read_new(profile: ClientProfile, path: Path, source: str) -> List[Dict]:
    """读 jsonl 自上次 cursor 起的增量行。返回 (rows, new_offset)。"""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    start = _cursor(profile, source)
    rows = []
    for i in range(start, len(lines)):
        if not lines[i]:
            continue
        try:
            rows.append(json.loads(lines[i]))
        except Exception:
            continue
    _advance(profile, source, len(lines))
    return rows


# ============================================================ 四大维归因
def _harvest_topic(profile: ClientProfile) -> int:
    """① 打分（AI vs 用户偏差归因）+ ② 收藏/排除 → 选题偏好权重。
    阈值按主题"累积样本数"判定（meta.topic_feedback_tally），跨批次持续学习：
    同主题首达阈值才写回；此后新增信号继续按批次偏差累加/回调。采集游标幂等，
    同一条信号只累计一次。"""
    counts, deltas = defaultdict(int), defaultdict(float)
    rows = _read_new(profile, _SRC("USER_FB_PATH"), "user_feedback")
    for e in rows:
        topic = (e.get("topic") or "").strip()
        us, ai = e.get("user_score"), e.get("ai_score")
        if not topic or us is None or ai is None:
            continue
        d = us - ai if isinstance(us, (int, float)) and isinstance(ai, (int, float)) else 0
        counts[topic] += 1
        deltas[topic] += d
    # 收藏(+1) / 排除(-1) 追溯主题
    pref_rows = _read_new(profile, _SRC("USER_PREF_PATH"), "user_pref")
    for e in pref_rows:
        topic = (e.get("topic") or "").strip()
        if not topic:
            continue
        counts[topic] += 1
        deltas[topic] += 1.0 if e.get("action") == "like" else -1.0 if \
            e.get("action") == "exclude" else 0.0

    if not counts:
        return 0
    # 累积样本计数（跨批次幂等累计；仅处理本次采集到的行，游标保证每行只加一次）
    meta = profile.profile["meta"]
    tally = meta.setdefault("topic_feedback_tally", {})
    for topic, c in counts.items():
        tally[topic] = tally.get(topic, 0) + c
    # 高表现模式：出现过的高偏好主题沉淀为模式（达阈值后，仅上调时沉淀）
    hp = profile.profile["topic"]["high_performance_patterns"]
    for topic, c in counts.items():
        if tally.get(topic, 0) >= MIN_TOPIC_FEEDBACK and deltas[topic] / max(c, 1) > 0.3:
            if topic not in hp:
                hp.append(topic)
                hp[:] = hp[:10]

    w = profile.profile["topic"]["preference_weights"]
    changed = 0
    got = {}
    for topic, c in counts.items():
        if tally.get(topic, 0) >= MIN_TOPIC_FEEDBACK:
            base = float(w.get(topic, 0.0))
            new = round(min(max(base + deltas[topic] / max(c, 1), 0.0), 3.0), 2)
            got[topic] = new - base
    if got:
        for topic, delta in got.items():
            if abs(delta) >= 0.1:
                w[topic] = round(w.get(topic, 0.0) + delta, 2)
                changed += 1
                _add_lead(profile, "topic",
                          f"主题「{topic}」偏好权重 {'上调' if delta > 0 else '下调'}",
                          f"{delta:+.2f}（累积 {tally.get(topic, 0)} 条反馈）")
    return changed


def _harvest_excluded(profile: ClientProfile) -> int:
    """② 排除系列 → topic.excluded_series（追加，幂等）。"""
    rows = _read_new(profile, _SRC("USER_PREF_PATH"), "user_pref_excluded")
    added = 0
    for e in rows:
        if e.get("action") == "exclude" and e.get("motif_series"):
            ex = profile.profile["topic"]["excluded_series"]
            if e["motif_series"] not in ex:
                ex.append(e["motif_series"])
                added += 1
    return added


def _harvest_layout(profile: ClientProfile) -> int:
    """③ 排版采纳 → 排版维。直接从 settings.json 读取当前激活态（最新为准，幂等）。"""
    s = load_json(_SRC("TYPOGRAPHY_SETTINGS_PATH")) or {}
    l = profile.profile["layout"]
    changed = 0
    theme = s.get("active_theme")
    if theme and theme != (l.get("active_theme") or ""):
        l["active_theme"] = theme
        _add_lead(profile, "layout", f"采用排版主题「{theme}」", "来自排版中心激活")
        changed += 1
    ov = s.get("overrides") or {}
    if isinstance(ov, dict) and isinstance(ov.get(theme), dict):
        branches = [b for b in ov[theme] if isinstance(ov[theme][b], dict)]
        if branches:
            note = f"{theme} 微调维度：" + "、".join(branches)
            if note not in (l.get("palette_notes") or []):
                l["palette_notes"].append(note)
                changed += 1
    return changed


def _harvest_reader(profile: ClientProfile) -> int:
    """④ 发布回流 → 读者维。每日推进 trend，达阈值算 avg_engagement。
    用 date 作为游标（文件非增量 append，用日期去重幂等）。"""
    p = _SRC("ARTICLE_STATS_PATH")
    if not p.exists():
        return 0
    rows = []
    for line in p.read_text(encoding="utf-8").strip().split("\n"):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    trend = profile.profile["reader"]["trend"]
    existing = {r.get("date") for r in trend}
    added = 0
    for r in rows:
        d = r.get("date")
        if not d or d in existing:
            continue
        trend.append({
            "date": d,
            "reads": r.get("reads", 0),
            "likes": r.get("likes", 0),
            "shares": r.get("shares", 0),
        })
        existing.add(d)
        added += 1
    trend.sort(key=lambda x: x.get("date", ""))
    if len(trend) >= MIN_READER_ROWS:
        recent = trend[-HARVEST_WINDOW_DAYS:]
        tot_reads = max(sum(x.get("reads", 0) for x in recent), 1)
        avg = {
            "reads": round(sum(x.get("reads", 0) for x in recent) / len(recent), 1),
            "likes": round(sum(x.get("likes", 0) for x in recent) / len(recent), 1),
            "shares": round(sum(x.get("shares", 0) for x in recent) / len(recent), 1),
            "engagement": round(sum(x.get("likes", 0) + x.get("shares", 0) for x in recent)
                                / tot_reads, 4),
            "sample_days": len(recent),
        }
        old = profile.profile["reader"]["avg_engagement"] or {}
        if avg["engagement"] - (old.get("engagement") or 0) > ENGAGEMENT_DELTA and added:
            _add_lead(profile, "reader",
                      f"读者互动率升至 {avg['engagement']:.1%}",
                      f"最近 {len(recent)} 天样本")
        profile.profile["reader"]["avg_engagement"] = avg
    return added


def _harvest_style_quotes(profile: ClientProfile) -> int:
    """⑤ 金句采纳 → 风格维 quote_preferences。取金句库总量作为 cursor 即可（非增量去重）。"""
    p = _SRC("QUOTES_PATH")
    if not p.exists():
        return 0
    qs = []
    for line in p.read_text(encoding="utf-8").strip().split("\n"):
        if not line.strip():
            continue
        try:
            qs.append(json.loads(line))
        except Exception:
            continue
    if not qs:
        return 0
    pool = profile.profile["style"]["quote_preferences"]
    before = len(pool)
    for q in qs:
        t = (q.get("text") or "").strip()
        if 6 <= len(t) <= 40 and t not in pool:
            pool.append(t)
            if len(pool) <= 12:  # 上限保护，防风格维被金句库撑爆
                pass
    # 截断到风格维上限
    profile.profile["style"]["quote_preferences"] = pool[:12]
    return len(profile.profile["style"]["quote_preferences"]) - before


HARVESTERS = ("topic", "excluded", "layout", "reader", "quote")


def harvest(profile: ClientProfile, *, dry_run: bool = False) -> Dict:
    """反馈闭环主入口：采集现有信号 → 归因 → 写回四维 + 留痕。
    dry_run=True 只在档案的深拷贝上计算（含 cursor 推进），不落盘也不污染原档案。
    返回每维触发摘要。"""
    if dry_run:
        import copy
        clone = ClientProfile.__new__(ClientProfile)
        clone._p = copy.deepcopy(profile.load())
        profile = clone
    else:
        profile.load()
    r = {
        "topic_changed": _harvest_topic(profile),
        "excluded_added": _harvest_excluded(profile),
        "layout_changed": _harvest_layout(profile),
        "reader_added": _harvest_reader(profile),
        "quote_added": _harvest_style_quotes(profile),
    }
    if not dry_run and any(r.values()):
        profile.save(reason="反馈闭环 harvest 归因写回")
    return r


def collect_feedback_into_ccp(profile: ClientProfile, notes: List[str]) -> int:
    """写入侧便捷：把一段自由文本反馈（复盘录入）并入 reader.notes/风格，返回条数。
    【一期手动接入】新客户无自动发布回流时以此兜底（§"复盘录入"）。"""
    p = profile.profile
    notes = [n.strip() for n in notes if n and n.strip()]
    reader_notes = p["reader"]["notes"]
    added = 0
    for n in notes:
        if n not in reader_notes:
            reader_notes.append(n)
            _add_lead(profile, "reader", f"复盘录入：{n[:20]}", "手动录入")
            added += 1
    if added:
        profile.save(reason=f"复盘录入 {added} 条")
    return added