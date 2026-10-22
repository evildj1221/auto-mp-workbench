#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/app/common.py · 协议层公共工具与常量（自 workbench/app.py 通用段迁入，逻辑不变）。

分层约定：本模块只做参数解析 / 常量 / 数据汇聚，不含 Web 框架对象；
路由层（api/*）负责 HTTP ↔ dict 的转换。
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).resolve().parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import cat_shop  # noqa: E402
from core import errors as errcls  # noqa: E402
from core import logging as clog  # noqa: E402
from core.logging import wb_log  # noqa: E402,F401  (re-export 供路由层统一入口)
from core.storage import load_json  # noqa: E402,F401  (re-export)
from llm.provider import COST_LOG  # noqa: E402


DATA_DIR = SCRIPT_DIR / "data"
STATS_PATH = cat_shop.FEEDBACK_DIR / "article_stats.jsonl"

# P0-5 发布检查单（全勾选才能登记已发布）
CHECKLIST_ITEMS = [
    ("title_ok", "标题已确认"),
    ("cover_ok", "封面图已就绪"),
    ("summary_ok", "摘要 / 导语已填"),
    ("images_ok", "正文配图已就位"),
    ("series_ok", "系列 / 标签已核对"),
    ("schedule_ok", "定时 22:00 已设置（或已安排发布）"),
    ("final_ok", "已在公众号后台完成发布"),
]

TONE_WORDS = ["温柔", "慢", "陪伴", "真实"]
V2_ANGLES = ["时间切片", "反例切入", "物件线索", "回信体", "他人视角"]
DIM_LABELS = {"resonance": "共鸣", "timeliness": "时效", "controversy": "争议",
              "uniqueness": "独特", "writability": "写作可行"}
SERIES_LABELS = {"A": "独居治愈", "B": "晚安陌生人", "C": "家庭关系",
                 "D": "中年危机", "E": "身体小秘密", "F": "亲密关系"}
LEVELS = ("critical", "high", "medium", "low")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def valid_date(d: str) -> bool:
    """宽松校验日期串（datetime.fromisoformat 可解析即通过）。"""
    try:
        datetime.fromisoformat(d)
        return True
    except (ValueError, TypeError):
        return False


def norm_date(d: str | None) -> str:
    """查询参数 date → 规范日期；空/非法回退今天（与原 get_date 语义一致）。"""
    d = (d or "").strip()
    return d if _DATE_RE.match(d) and valid_date(d) else today()


# wb_log / load_json 已在文件头部自 core.logging / core.storage re-export


def slot_arg(date: str, raw: str | None) -> str:
    """槽位参数缺省回退当日活动槽位（原 _slot_arg 语义）。"""
    slot = (raw or "").strip()
    if not slot:
        sel = load_json(cat_shop.SELECTIONS_DIR / f"{date}.json", {}) or {}
        slot = sel.get("active_slot") or ""
    return slot


def published_path(date: str, slot: str | None) -> Path:
    return cat_shop.DRAFTS_DIR / f"{date}{'_' + slot if slot else ''}_published.json"


def pipeline_status(date):
    """10 步检查（委托 core.pipeline_status 权威实现，UI/CLI/Agent 三方共用）。"""
    from core.pipeline_status import STEP_LABELS, STEP_ORDER, status_map
    steps, _pub, _choice = status_map(date)
    return [(STEP_LABELS[k], bool(steps[k])) for k in STEP_ORDER]


def next_action_hint(date):
    """根据流水线状态给出下一步引导（前端自行路由；按钮名与选题/写稿页实际一致）。"""
    steps = pipeline_status(date)
    done = {name: ok for name, ok in steps}
    if not done["Step 1 · 选题生成"]:
        return "今天还没生成选题 → 去「选题中心」点【生成新批次】，或在 Agent 中枢一键运行", "/topics"
    if not done["Step 2 · 评分 Top5"]:
        return "候选已生成 → 去「选题中心」查看评分 Top5（【重新生成本批】可刷新）", "/topics"
    if not done["Step 3.7 · 用户决策"]:
        return "Top5 已就绪 → 去「选题中心」点选今天的选题", "/topics"
    if not done["Step 4 · 写稿 v1"]:
        return "选题已定 → 去「写稿与发布」点【写稿 v1】", "/write"
    if not (cat_shop.DRAFTS_DIR / f"{date}_mp.html").exists():
        return "文稿已就绪 → 去「公众号导出」生成规范版排版", "/export"
    return "今日流程基本完成 ✅ 发布后去「复盘」回填数据", "/review"


def weekday_label(date):
    try:
        return "周" + "一二三四五六日"[datetime.fromisoformat(date).weekday()]
    except (ValueError, TypeError):
        return ""


_TTL_CACHE: dict = {}


def _ttl(key: str, ttl_s: float, fn):
    hit = _TTL_CACHE.get(key)
    if hit and time.time() - hit[0] < ttl_s:
        return hit[1]
    val = fn()
    _TTL_CACHE[key] = (time.time(), val)
    return val


def published_dates():
    """所有已登记发布的日期集合（含槽位独立发布的 *_published.json）。
    60s TTL 缓存：/brief /dashboard 轮询热路径曾每次 glob 全 drafts 并逐个读 JSON。"""
    return _ttl("published_dates", 60, _published_dates_impl)


def _published_dates_impl():
    out = set()
    for p in cat_shop.DRAFTS_DIR.glob("*_published.json"):
        data = load_json(p, {}) or {}
        if data.get("user_published"):
            m = re.match(r"(\d{4}-\d{2}-\d{2})", p.name)
            if m:
                out.add(m.group(1))
    return out


def publish_streak(dates):
    if not dates:
        return 0
    d = datetime.fromisoformat(max(dates))
    n = 0
    cur = d.strftime("%Y-%m-%d")
    while cur in dates:
        n += 1
        d -= timedelta(days=1)
        cur = d.strftime("%Y-%m-%d")
    return n


def candidates_count(date):
    """当日全部批次的候选数与批次数。"""
    batches = cat_shop.load_batches(date).get("batches", [])
    total = 0
    for b in batches:
        cf, _ = cat_shop.batch_file_names(date, b["round"])
        data = load_json(cf, {}) or {}
        total += len(data.get("candidates", []) or [])
    return total, len(batches)


def load_stats():
    if not STATS_PATH.exists():
        return []
    out = []
    for line in STATS_PATH.read_text(encoding="utf-8").strip().split("\n"):
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def save_stats(entry):
    """按 date 幂等：已存在同日期记录则覆盖，保持原位置。"""
    entries = load_stats()
    replaced = False
    for i, e in enumerate(entries):
        if e.get("date") == entry.get("date"):
            entries[i] = entry
            replaced = True
            break
    if not replaced:
        entries.append(entry)
    text = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries)
    STATS_PATH.write_text(text + ("\n" if entries else ""), encoding="utf-8")
    return len(entries)


def attach_meta(date):
    """从 published/selections/top5 带出标题/系列/5 维评分，供校准使用。"""
    pub = load_json(published_path(date, None), {}) or {}
    sel = load_json(cat_shop.SELECTIONS_DIR / f"{date}.json", {}) or {}
    scores = {}
    top5 = cat_shop.load_top5(date) or []
    for r in top5:
        if r.get("cand_id") == sel.get("cand_id"):
            scores = r.get("avg_scores") or {}
            break
    return {"title": pub.get("title") or sel.get("topic") or "",
            "series": sel.get("motif_series") or "",
            "ai_scores_detail": scores}


def stats_ctx(date):
    """复盘页上下文：统计汇总 + 微信接入状态。"""
    from core import wechat
    stats = load_stats()
    total_reads = sum(int(s.get("reads") or 0) for s in stats)
    total_shares = sum(int(s.get("shares") or 0) for s in stats)
    total_likes = sum(int(s.get("likes") or 0) for s in stats)
    avg_reads = round(total_reads / len(stats), 1) if stats else 0
    by_series = {}
    for s in stats:
        k = s.get("series") or "?"
        by_series.setdefault(k, {"n": 0, "reads": 0, "shares": 0})
        by_series[k]["n"] += 1
        by_series[k]["reads"] += int(s.get("reads") or 0)
        by_series[k]["shares"] += int(s.get("shares") or 0)
    for k, v in by_series.items():
        v["avg_reads"] = round(v["reads"] / v["n"], 1) if v["n"] else 0
    health_ok, health_msg = wechat.api_health()
    return {"stats": sorted(stats, key=lambda s: s.get("date", ""), reverse=True),
            "total_reads": total_reads, "total_shares": total_shares,
            "total_likes": total_likes, "avg_reads": avg_reads,
            "by_series": by_series, "health_ok": health_ok,
            "health_msg": health_msg}


def feedback_ctx(date):
    """复盘页底部的评论与画像只读上下文。"""
    entries = cat_shop.load_user_feedback(date)
    analysis = load_json(cat_shop.FEEDBACK_DIR / f"feedback_analysis_{date}.json", None)
    fb_stats = load_json(cat_shop.FEEDBACK_DIR / "daily_stats.json", []) or []
    fb_stats = [s for s in fb_stats if isinstance(s, dict)][-14:]
    return {"entries": entries, "analysis": analysis, "fb_stats": fb_stats}


def redline_log_entries(limit=50):
    """设置页红线折叠区的命中日志。"""
    entries = []
    if cat_shop.REDLINE_LOG.exists():
        for line in cat_shop.REDLINE_LOG.read_text(encoding="utf-8").strip().split("\n")[-limit:]:
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
        entries.reverse()
    return entries


def cost_summary(date):
    """成本摘要（今日/累计）。从 llm_cost.jsonl 聚合。"""
    if not COST_LOG.exists():
        return {"n": 0, "today": 0.0, "total": 0.0, "tokens": 0,
                "ok": 0, "fail": 0, "by_module": []}
    entries = []
    for line in COST_LOG.read_text(encoding="utf-8").strip().split("\n"):
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    today_sum = sum(e.get("cost_total_cny", 0) for e in entries if e.get("date") == date)
    total = sum(e.get("cost_total_cny", 0) for e in entries)
    tokens = sum(e.get("total_tokens", 0) for e in entries)
    ok = sum(1 for e in entries if e.get("ok"))
    by_module = {}
    for e in entries:
        m = e.get("module", "?")
        by_module.setdefault(m, {"n": 0, "cost": 0.0})
        by_module[m]["n"] += 1
        by_module[m]["cost"] += e.get("cost_total_cny", 0)
    rows = sorted(by_module.items(), key=lambda x: -x[1]["cost"])
    return {"n": len(entries), "today": round(today_sum, 4), "total": round(total, 4),
            "tokens": tokens, "ok": ok, "fail": len(entries) - ok,
            "by_module": rows}


def calibrate_weights_from_stats(stats, min_n=10):
    """P0-1：真实发布数据反向校准评分权重（需 ≥ min_n 篇含 5 维分的记录）。"""
    rows = [s for s in stats if s.get("ai_scores_detail")]
    if len(rows) < min_n:
        return None, f"有效样本 {len(rows)} < {min_n}，暂不校准"
    reads = sorted(int(r.get("reads") or 0) for r in rows)
    hi_th = reads[max(0, len(reads) * 2 // 3 - 1)]
    lo_th = reads[len(reads) // 3]
    highs = [r for r in rows if int(r.get("reads") or 0) >= hi_th]
    lows = [r for r in rows if int(r.get("reads") or 0) <= lo_th]
    if not highs or not lows:
        return None, "分组后无样本"

    def _mean_dim(es, dim):
        vals = [float(e.get("ai_scores_detail", {}).get(dim) or 0) for e in es]
        return sum(vals) / len(vals) if vals else 0.0

    disc = {k: max(0.0, _mean_dim(highs, k) - _mean_dim(lows, k))
            for k in cat_shop.DIMENSIONS}
    if sum(disc.values()) <= 0:
        return None, "维度无区分度，保持默认权重"
    raw = {k: cat_shop.DEFAULT_W[k] + 0.35 *
           (disc[k] / sum(disc.values()) - cat_shop.DEFAULT_W[k])
           for k in cat_shop.DIMENSIONS}
    s = sum(raw.values())
    cal = {k: round(v / s, 3) for k, v in raw.items()}
    profile = {"updated_at": datetime.now().isoformat(timespec="seconds"),
               "data_window": "真实发布数据校准",
               "total_choices": len(rows),
               "total_user_comments": 0,
               "data_quality": "high",
               "source": "real_stats",
               "scoring_calibration": cal,
               "ai_notes": f"基于 {len(rows)} 篇真实发布数据（阅读量）校准，替代自评学习。"}
    if cat_shop.PROFILE_PATH.exists():
        import shutil
        shutil.copy2(cat_shop.PROFILE_PATH,
                     cat_shop.PROFILE_PATH.with_suffix(".json.bak"))
    cat_shop.write_json(cat_shop.PROFILE_PATH, profile)
    return cal, f"已按 {len(rows)} 篇真实数据校准：{cal}"


def format_hit_stats(dh) -> str:
    """风向命中统计 → 展示文本。兼容三种形态：对象 {hits,total,rate}/数字/空。"""
    if isinstance(dh, dict):
        rate = dh.get("rate")
        text = f"{dh.get('hits', 0)}/{dh.get('total', 0)}"
        if isinstance(rate, (int, float)):
            text += f" · {round(rate * 100)}%"
        return text
    if isinstance(dh, (int, float)):
        return str(dh)
    return ""


def directive_cards():
    """M3：生效风向 → 展示卡片（剩余天数 / 上批命中）。"""
    from core.directives import list_directives
    now = datetime.now()
    cards = []
    for d in list_directives():
        days_left = None
        if d.get("expires_at"):
            try:
                days_left = (datetime.fromisoformat(d["expires_at"]).date() - now.date()).days
            except ValueError:
                pass
        cards.append({"id": d["id"], "text": d["text"], "scope": d.get("scope", "week"),
                      "negative": bool(d.get("negative")), "days_left": days_left,
                      "hits": d.get("hits_last_batch"),
                      "hits_text": format_hit_stats(d.get("hits_last_batch"))})
    return cards
