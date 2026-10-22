#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/trending.py · 公开热榜聚合（选题"紧跟时事"的信号源）。

设计：只用公开无鉴权接口（今日头条热榜 JSON / 百度热搜页），本地缓存 6 小时，
任何失败静默降级为空（绝不阻塞选题生成）。产出"情绪/生活相关"的热点标题
清单，注入选题 prompt 的 {trending_line}——LLM 负责把热点转译成情感角度，
本模块只做采集与粗过滤。

合规：公开接口自用聚合，量小（每 6h 一次 2 个 GET），无登录、无反爬绕过。
"""
import json
import re
from datetime import datetime
from pathlib import Path

_CACHE_TTL = 6 * 3600
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 与情感/生活强相关的粗过滤词（命中即保留；都不命中则全留再靠 LLM 挑）
_EMOTION_HINTS = (
    "妈妈", "爸爸", "孩子", "老人", "奶奶", "爷爷", "女生", "男生", "女子", "男子",
    "独居", "租房", "外卖", "夜", "晚", "天气", "降温", "降温", "秋", "冬", "春", "夏",
    "假期", "回家", "高铁", "高速", "出发", "开学", "毕业", "婚礼", "结婚", "分手",
    "宠物", "猫", "狗", "阳台", "厨房", "城市", "小区", "邻居", "深夜", "加班",
    "医院", "药", "体检", "情绪", "焦虑", "治愈", "温暖", "陌生", "善意", "打工",
    "春运", "国庆", "中秋", "春节", "元宵", "清明", "端午", "除夕", "节气",
)
_DROP_HINTS = ("比分", "涨停", "指数", "股", "评测", "跑分", "版本", "发布会", "联赛")


def _cache_path() -> Path:
    from core.paths import DATA_DIR
    p = DATA_DIR / "trending_cache.json"
    return p


def _fetch_toutiao() -> list[str]:
    import requests
    r = requests.get("https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
                     headers=_UA, timeout=8)
    r.raise_for_status()
    return [str(i.get("Title") or "").strip()
            for i in (r.json().get("data") or []) if i.get("Title")]


def _fetch_baidu() -> list[str]:
    """百度热搜页（HTML）粗提取 top 标题；失败即跳过。"""
    import requests
    r = requests.get("https://top.baidu.com/board?tab=realtime", headers=_UA, timeout=8)
    r.raise_for_status()
    return [m.strip() for m in re.findall(r'"word":"([^"]+)"', r.text)][:30]


def _filter(titles: list[str]) -> list[str]:
    out, seen = [], set()
    for t in titles:
        t = (t or "").strip()
        if not t or len(t) > 40 or t in seen:
            continue
        seen.add(t)
        if any(d in t for d in _DROP_HINTS):
            continue
        if any(h in t for h in _EMOTION_HINTS):
            out.append(t)
    return out[:10]


def fetch_trending(force: bool = False) -> list[str]:
    """情绪/生活相关热点标题（≤10 条）。缓存 6h；任何失败返回 []（静默降级）。"""
    cp = _cache_path()
    if not force and cp.exists():
        try:
            c = json.loads(cp.read_text(encoding="utf-8"))
            if datetime.now().timestamp() - c.get("ts", 0) < _CACHE_TTL:
                return c.get("titles") or []
        except Exception:
            pass
    raw: list[str] = []
    for fn in (_fetch_toutiao, _fetch_baidu):
        try:
            raw.extend(fn())
        except Exception:
            continue
    titles = _filter(raw)
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps({"ts": datetime.now().timestamp(),
                                  "titles": titles}, ensure_ascii=False),
                      encoding="utf-8")
    except Exception:
        pass
    return titles
