#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/memory.py · 金句库 + 用户评论/偏好 + 历史与画像加载。

自 cat_shop.py 253-295 / 1080-1224 / 1238-1291 行原样迁出，行为不变。
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from core.paths import (DRAFTS_DIR, FEEDBACK_DIR, HISTORY_PATH, PROFILE_PATH,
                        QUOTES_PATH, USER_FB_PATH, USER_PREF_PATH)
from core.storage import load_json, write_json

def add_quote(text, source="manual", date=None, score=None):
    """金句入库（按文本幂等）。返回是否新增。"""
    if not text or len(text) < 4:
        return False
    lines = []
    exists = False
    if QUOTES_PATH.exists():
        for line in QUOTES_PATH.read_text(encoding="utf-8").strip().split("\n"):
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("text") == text:
                exists = True
                continue
            lines.append(line)
    if not exists:
        entry = {"text": text.strip(),
                 "source": source or "manual",
                 "date": date or datetime.now().strftime("%Y-%m-%d"),
                 "score": score,
                 "ts": datetime.now().isoformat(timespec="seconds")}
        lines.append(json.dumps(entry, ensure_ascii=False))
        QUOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
        QUOTES_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return not exists


def load_quotes(n=6):
    """取金句库最近 n 条（作为写稿风格参考）。"""
    if not QUOTES_PATH.exists():
        return []
    out = []
    for line in QUOTES_PATH.read_text(encoding="utf-8").strip().split("\n"):
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out[-n:]

# ===== 用户评论收集 =====
def collect_feedback(date, feedback_str=None, from_file=None):
    top5 = load_top5(date)
    if not top5:
        print(f"❌ 未找到 top 5: data/drafts/{date}_top5.json")
        return False

    if from_file:
        data = json.loads(Path(from_file).read_text(encoding="utf-8"))
        parsed = {}
        for k, v in data.items():
            try:
                rank = int(k)
            except (ValueError, TypeError):
                continue
            if 1 <= rank <= len(top5) and isinstance(v, dict) and v.get("score") is not None:
                try:
                    parsed[rank] = {"score": int(v["score"]), "note": str(v.get("note", "")).strip()}
                except (ValueError, TypeError):
                    continue
    elif feedback_str:
        parsed = parse_feedback_string(feedback_str, top5)
    elif not sys.stdin.isatty():
        # 管道输入
        s = sys.stdin.read().strip()
        parsed = parse_feedback_string(s, top5) if s else {}
    else:
        # 交互
        s = input("你的评论（格式: 1:5 喜欢; 2:3 太老套; ... 直接回车跳过）: ").strip()
        parsed = parse_feedback_string(s, top5) if s else {}

    if not parsed:
        print("⏭️  无评论")
        return False

    entries = []
    for rank, fb in parsed.items():
        r = top5[rank - 1]
        entries.append({
            "date": date, "rank": rank, "cand_id": r["cand_id"], "topic": r["topic"],
            "ai_score": r["total"], "ai_scores_detail": r["avg_scores"], "ai_reasoning": r["ai_reasoning"],
            "user_score": fb["score"], "user_note": fb["note"],
            "delta": round(fb["score"] - r["total"], 2),
            "ts": datetime.now().isoformat(timespec="seconds"),
        })

    with USER_FB_PATH.open("a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    # P0-6：幂等去重（同 date+rank 保留最新一条），避免重复评论污染 analyze/learn 统计
    dedup_feedback()

    print(f"\n✅ 已收集 {len(entries)} 条评论：")
    for e in entries:
        print(f"  {e['rank']}. {e['topic'][:30]} (AI={e['ai_score']} 你={e['user_score']} 差={e['delta']:+.1f})")
        if e["user_note"]:
            print(f"     💬 {e['user_note']}")
    return True


def parse_feedback_string(s, top5):
    items = re.split(r"[;,，；\n]", s.strip())
    parsed = {}
    for item in items:
        item = item.strip()
        if not item:
            continue
        m = re.match(r"^(\d+)\s*[:=：]\s*(\d+)\s*[:]?\s*(.*)$", item)
        if not m:
            continue
        rank = int(m.group(1))
        score = int(m.group(2))
        if rank < 1 or rank > len(top5) or score < 1 or score > 5:
            continue
        parsed[rank] = {"score": score, "note": m.group(3).strip()}
    return parsed


def record_user_pref(date, rank, action, top5):
    """P-02：记录用户对候选的收藏(like)/排除(exclude)偏好。
    按 (date, rank, cand_id, action) 幂等：重复操作覆盖更新。"""
    if not top5 or not (1 <= rank <= len(top5)):
        return False
    r = top5[rank - 1]
    entry = {"date": date, "rank": rank, "cand_id": r.get("cand_id"),
             "topic": r.get("topic"), "motif_series": r.get("motif_series"),
             "action": action, "ts": datetime.now().isoformat(timespec="seconds")}
    lines = []
    if USER_PREF_PATH.exists():
        for line in USER_PREF_PATH.read_text(encoding="utf-8").strip().split("\n"):
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if (e.get("date"), e.get("rank"), e.get("cand_id"), e.get("action")) ==                (date, rank, r.get("cand_id"), action):
                continue  # 同键覆盖：移除旧记录
            lines.append(line)
    lines.append(json.dumps(entry, ensure_ascii=False))
    USER_PREF_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def load_excluded_series(n=10):
    """P-02：最近 n 条「排除」记录涉及的系列集合（用于 score 硬降权）。"""
    if not USER_PREF_PATH.exists():
        return set()
    out, rows = set(), []
    for line in USER_PREF_PATH.read_text(encoding="utf-8").strip().split("\n"):
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    for e in rows[-n:]:
        if e.get("action") == "exclude" and e.get("motif_series"):
            out.add(e["motif_series"])
    return out


def dedup_feedback():
    """P0-6：按 (date, rank) 幂等去重 user_feedback.jsonl。
    同键保留最新一条（保持首次出现的顺序），返回去重后条数。"""
    if not USER_FB_PATH.exists():
        return 0
    lines = USER_FB_PATH.read_text(encoding="utf-8").strip().split("\n")
    entries, seen = [], {}
    for line in lines:
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        key = (e.get("date"), e.get("rank"))
        if key in seen:
            entries[seen[key]] = e  # 同键覆盖为最新
        else:
            seen[key] = len(entries)
            entries.append(e)
    text = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries)
    USER_FB_PATH.write_text(text + ("\n" if entries else ""), encoding="utf-8")
    return len(entries)

def load_top5(date):
    p = DRAFTS_DIR / f"{date}_top5.json"
    if not p.exists():
        return None
    data = load_json(p)
    return data.get("results", []) if data else None


def _analysis_path(date):
    """反馈分析报告路径（按日期分文件，避免多日分析互相覆盖）。"""
    return FEEDBACK_DIR / f"feedback_analysis_{date}.json"


def load_user_feedback(date):
    if not USER_FB_PATH.exists():
        return []
    entries = []
    for line in USER_FB_PATH.read_text(encoding="utf-8").strip().split("\n"):
        if not line:
            continue
        try:
            e = json.loads(line)
            if e.get("date") == date:
                entries.append(e)
        except Exception:
            continue
    return entries


def load_all_feedback():
    if not USER_FB_PATH.exists():
        return []
    out = []
    for line in USER_FB_PATH.read_text(encoding="utf-8").strip().split("\n"):
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def load_preference():
    if not PROFILE_PATH.exists():
        return None
    return load_json(PROFILE_PATH)


def load_history(n=20):
    if not HISTORY_PATH.exists():
        return []
    lines = HISTORY_PATH.read_text(encoding="utf-8").strip().split("\n")
    return [json.loads(l) for l in lines[-n:] if l]
