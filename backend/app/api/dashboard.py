#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/app/api/dashboard.py · 总览驾驶舱（原 / 的 JSON 化）。"""
from datetime import datetime

from fastapi import APIRouter, Request

import cat_shop
from core import charts

from ..common import (candidates_count, feedback_ctx, load_json, next_action_hint,
                      published_dates, publish_streak, pipeline_status,
                      redline_log_entries, today, weekday_label)
from . import envelope, q_date
from .agent import STATUS_META, query_state

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
@router.get("/")
@router.get("/{date}")
def dashboard(request: Request, date: str | None = None):
    date = date or q_date(request)
    steps = pipeline_status(date)
    done_count = sum(1 for _, ok in steps if ok)
    hint, hint_url = next_action_hint(date)
    pub = load_json(cat_shop.DRAFTS_DIR / f"{date}_published.json", {}) or {}
    sel = load_json(cat_shop.SELECTIONS_DIR / f"{date}.json", {}) or {}
    pending = load_json(cat_shop.SELECTIONS_DIR / f"{date}_pending.json", {}) or {}
    # P-07：发布登记后 3 天仍未录入复盘数据 → 提醒
    review_due = None
    if pub.get("user_published") and pub.get("due_review_date"):
        due = pub.get("due_review_date")
        stats = [s for s in load_stats_safe() if isinstance(s, dict)]
        if due <= today() and not any(s.get("date") == date for s in stats):
            review_due = f"《{pub.get('title', '')}》发布已满 3 天，去「数据复盘」录入阅读/在看/分享数据"
    slots_count = len(cat_shop.load_works(date).get("slots", []))
    candidate_count, batch_count = candidates_count(date)
    published = published_dates()
    trend = charts.dashboard_charts()  # M4 趋势三图（服务端 SVG 字符串，SPA 直接注入）
    from core.calendar_ctx import needs_year_data
    return envelope({
        "page": "dashboard", "today": today(), "date": date,
        "weekday": weekday_label(date),
        "steps": [{"name": n, "done": ok} for n, ok in steps],
        "done_count": done_count, "hint": hint, "hint_url": hint_url,
        "pub": pub, "sel": sel, "pending": pending, "review_due": review_due,
        "slots_count": slots_count,
        "cal_update_due": needs_year_data(),
        "next_year": datetime.now().year + 1,
        "candidate_count": candidate_count, "batch_count": batch_count,
        "streak": publish_streak(published), "total_published": len(published),
        "trend": trend,
        "agent_state": query_state(date), "agent_meta": STATUS_META,
        "redline_log": redline_log_entries(8),
        "fb": feedback_ctx(date),
    })


def load_stats_safe():
    from ..common import load_stats
    try:
        return load_stats()
    except Exception:
        return []
