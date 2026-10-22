#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/app/api/review.py · 数据复盘（原 /review 系列的 JSON 化）。"""
from datetime import datetime

from fastapi import APIRouter, Request

from core import wechat

from ..common import (attach_meta, calibrate_weights_from_stats, feedback_ctx,
                      load_stats, save_stats, stats_ctx, wb_log)
from ..schemas import ReviewSaveReq
from . import envelope, fail, path_date, q_date, body_date

router = APIRouter(prefix="/api/review", tags=["review"])


@router.get("/{date}")
def get_review(request: Request, date: str = ""):
    d = path_date(request, date)
    return envelope({"page": "review", "date": d,
                     **stats_ctx(d), **feedback_ctx(d)})


@router.post("/save")
def review_save(req: ReviewSaveReq, request: Request):
    """录入发布数据（人工）。"""
    date = body_date(request, req.date)
    entry = {"date": date,
             "reads": int(req.reads or 0), "likes": int(req.likes or 0),
             "shares": int(req.shares or 0),
             "fans_delta": int(req.fans_delta or 0),
             "fans_total": int(req.fans_total or 0),
             "note": req.note.strip(), "source": "manual",
             "ts": datetime.now().isoformat(timespec="seconds")}
    entry.update(attach_meta(date))
    save_stats(entry)
    wb_log("review_save", date,
           f"录入发布数据：阅读 {entry['reads']} 在看 {entry['likes']} 分享 {entry['shares']}")
    return envelope({"saved": True})


@router.post("/fetch")
def review_fetch(request: Request):
    """尝试从微信公众平台 API 拉取指定日期数据（需 IP 白名单 + 接口权限）。"""
    date = q_date(request)
    fans_total, err1 = wechat.fetch_fans_total()
    fans_sum, err2 = wechat.fetch_fans_summary(date)
    arts, err3 = wechat.fetch_article_summary(date)
    errors = [e for e in (err1, err2, err3) if e]
    if errors:
        return fail("api_err", "；".join(errors)[:300])
    reads = shares = likes = 0
    for item in (arts or {}).get("list", []):
        reads += int(item.get("int_page_read_user") or 0)
        shares += int(item.get("share_user") or 0)
        likes += int(item.get("add_to_fav_user") or 0)
    new_fans = 0
    for item in (fans_sum or {}).get("list", []):
        new_fans += int(item.get("new_user") or 0) - int(item.get("cancel_user") or 0)
    entry = {"date": date, "reads": reads, "likes": likes, "shares": shares,
             "fans_delta": new_fans, "fans_total": fans_total or 0,
             "note": "微信API自动拉取", "source": "api",
             "ts": datetime.now().isoformat(timespec="seconds")}
    entry.update(attach_meta(date))
    save_stats(entry)
    wb_log("review_fetch", date, f"API 拉取数据：阅读 {reads} 在看 {likes} 分享 {shares}")
    return envelope({"fetched": True, "reads": reads, "likes": likes, "shares": shares})


@router.post("/calibrate")
def review_calibrate(request: Request):
    """真实发布数据反向校准评分权重。"""
    stats = load_stats()
    cal, msg = calibrate_weights_from_stats(stats)
    wb_log("review_calibrate", q_date(request), msg)
    return envelope({"calibrated": bool(cal), "weights": cal, "message": msg[:200]})
