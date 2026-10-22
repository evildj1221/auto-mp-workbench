#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/app/api/archive.py · 档案库（原 /archive + /file 的 JSON 化）。"""
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

import cat_shop

from ..common import DATA_DIR, load_json, published_path, today, wb_log
from . import envelope, fail, q_date

router = APIRouter(prefix="/api/archive", tags=["archive"])


@router.get("")
@router.get("/")
def archive_dates(request: Request):
    dates = set()
    for d, pat in ((cat_shop.CANDIDATES_DIR, "*.json"),
                   (cat_shop.DRAFTS_DIR, "*_top5.json"),
                   (cat_shop.SELECTIONS_DIR, "2*.json")):
        for p in d.glob(pat):
            name = p.name.split("_")[0]
            try:
                datetime.fromisoformat(name)
                dates.add(name)
            except ValueError:
                continue
    dates = sorted(dates, reverse=True)
    return envelope({"page": "archive", "today": today(),
                     "dates": dates, "total_published": len(dates)})


@router.get("/file")
def file_view(request: Request, path: str = "", download: bool = False):
    """安全读取 data/ 内文件（限制在 DATA_DIR 内）；图片以二进制送出。
    注意：必须声明在 /{date} 之前，否则会被路径参数吞掉。
    加固（P4）：用 relative_to 而非 startswith 判边界——后者可被
    "data_x" 这类同前缀兄弟目录或符号链接绕过。"""
    rel = path
    try:
        p = (DATA_DIR / rel).resolve()
        p.relative_to(DATA_DIR.resolve())
    except (ValueError, OSError):
        return fail("notfound", "文件不存在或越权路径")
    if not p.exists():
        return fail("notfound", "文件不存在或越权路径")
    if download:
        return FileResponse(p, filename=p.name)
    if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return FileResponse(p)
    content = p.read_text(encoding="utf-8", errors="replace")
    return envelope({"rel": rel, "content": content, "is_html": p.suffix == ".html"})


@router.get("/{date}")
def archive_detail(request: Request, date: str):
    """某日的产物清单 + 发布登记。"""
    artifacts = []
    for folder, patterns in (
        ("candidates", [f"{date}.json"]),
        ("selections", [f"{date}.json", f"{date}_pending.json", "history.jsonl"]),
        ("drafts", [f"{date}_top5.json", f"{date}_v1.md", f"{date}_v2.md",
                    f"{date}.html", f"{date}_v2.html", f"{date}_mp.html",
                    f"{date}_mp_report.json", f"{date}_published.json",
                    f"{date}_titles.json", f"{date}_push_log.jsonl",
                    f"{date}_w01_v1.md", f"{date}_w01_mp.html", f"{date}_w01_mp_report.json",
                    f"{date}_w02_v1.md", f"{date}_w02_mp.html", f"{date}_w02_mp_report.json"]),
        ("feedback", [f"feedback_analysis_{date}.json"]),
    ):
        for pat in patterns:
            p = DATA_DIR / folder / pat
            if p.exists():
                artifacts.append({"folder": folder, "name": pat,
                                  "size": p.stat().st_size, "rel": f"{folder}/{pat}"})
    # 槽位补充扫描：works 索引里登记的槽位产物（w03+ 等动态槽位）
    ws = cat_shop.load_works(date).get("slots", [])
    for s in ws:
        sid = s.get("id", "")
        if not sid:
            continue
        for suf in ("v1.md", "v2.md", "mp.html", "mp_report.json", "titles.json"):
            p = cat_shop.DRAFTS_DIR / f"{date}_{sid}_{suf}"
            rel = f"drafts/{p.name}"
            if p.exists() and not any(a["rel"] == rel for a in artifacts):
                artifacts.append({"folder": "drafts", "name": p.name,
                                  "size": p.stat().st_size, "rel": rel})
    pub = load_json(published_path(date, None), {}) or {}
    return envelope({"page": "archive", "date": date, "artifacts": artifacts, "pub": pub})
