#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/topic_families.py · 选题句式家族（治“句式雷同疲劳”的结构化基础）。

为什么需要：生成端“同批句式不重复”曾是模糊指令——模型只换词不换结构，
「我把X…，反而Y」「时间词，我…」两副骨架长期霸屏（2026-10 复盘实锤）。
家族化之后：生成端可下配额、评分端可跨家族选 Top5、疲劳可注入。

家族判据为启发式（正则），用于：LLM 未打标时的兜底、历史数据回填、
疲劳统计。主标注由生成端 LLM 按 prompt 里的家族菜单输出 family 字段。
"""
import re
from pathlib import Path

# 家族菜单：id → (名称, 骨架描述, 启发式正则，顺序即优先级——具体优先于宽泛)
FAMILIES = {
    "F1": ("动作转折", "我/和某人 + 把/给/在 + 具体动作，后接反而/却/先的微小反转"),
    "F2": ("意象起兴", "自然/天气意象开场（桂花开了/起风了/降温那晚），再落到人"),
    "F3": ("时间切片", "时间状语开头（周X晚上/月初/寒露前/秋招第N周），逗号后进场景"),
    "F4": ("物件特写", "一件小物领题（半瓶温水/一张便签/那盏灯），物件即主角"),
    "F5": ("留白不语", "以「没」收束的动作（没等回复/没发出去/谁都没提），沉默承载情绪"),
    "F6": ("场景蒙太奇", "两个画面并置切换，中间不解释"),
    "F7": ("数字细节", "数量词撑起具体性（删到只剩一行/第三周/一刻钟）"),
    "F8": ("自省问句", "开放式自我询问（无标准答案，每轮限 1 条）"),
    "F9": ("定义命名", "「X，是Y」式隐喻定义（秋天的悲伤，是心在换一件厚衣服）"),
    "F10": ("回信召唤", "「给X…，我…」书信体向某个对象说话"),
}

_RULES = [
    ("F5", re.compile(r"谁都没|没等|没往下|没发出去|没先开口|没提|没有回复|没说完")),
    ("F9", re.compile(r"^[^，？]{2,12}，是")),
    ("F10", re.compile(r"^给.{2,14}，我")),
    ("F3", re.compile(r"^(周[一二三四五六日]|月初|月末|降温|寒露|霜降|秋招|开学|年底|"
                      r"国庆|中秋|清晨|傍晚|今晚|凌晨|\d{1,2}月|第[一二三四]周)[^，]{0,10}，")),
    ("F1", re.compile(r"^(我|和|跟)[^，]{0,8}(把|给|在)[^，]{0,24}，")),
    ("F4", re.compile(r"^(翻到|半瓶|一张|一句|一盏|一封|那盏|那棵|那碗|那张|那件)")),
    ("F2", re.compile(r"^(桂花|落叶|下雨|起风|入秋|秋天|秋意|降温|太阳|月亮|月亮出来|"
                      r"风|雨|雪|花期)[^，]{0,6}[，开刮来停落]")),
    ("F8", re.compile(r"[？?]$|有没有")),
    ("F7", re.compile(r"只剩|\d+(行|条|天|周|分钟|岁)|第[一二三四五六七八九十]+|一刻钟|半瓶|一遍")),
]


def classify_family(topic: str) -> str:
    """启发式判家族（LLM 打标缺失时的兜底；判不出 F0）。"""
    t = str(topic or "").strip()
    if not t:
        return "F0"
    for fam, rule in _RULES:
        if rule.search(t):
            return fam
    return "F0"


# 题材主题词（家族管"怎么写"，主题管"写什么"——两层正交，各查各的疲劳）
_THEME_WORDS = {
    "职场求职": ("求职", "简历", "面试", "秋招", "校招", "offer", "试用", "加班",
                 "裁员", "跳槽", "职场", "升职", "绩效", "考公", "考研", "工资",
                 "薪资", "老板", "同事", "工位"),
    "校园青春": ("宿舍", "同桌", "考试", "论文", "开学", "暑假", "校园"),
    "原生家庭": ("妈妈", "爸爸", "父母", "奶奶", "爷爷", "外婆", "外公", "老家"),
    "亲密关系": ("对象", "男友", "女友", "老公", "老婆", "前任", "分手", "异地",
                 "暗恋", "婚礼", "相亲"),
    "节令物候": ("桂花", "落叶", "初雪", "梅雨", "蝉", "烟花", "麦", "落叶"),
    "城市角落": ("便利店", "地铁", "公交", "高铁", "楼道", "电梯", "小区", "公园",
                 "天桥", "夜市", "菜场", "菜市"),
}


def classify_theme(topic: str) -> str:
    """启发式判题材主题（未命中归"日常其他"）。"""
    t = str(topic or "")
    for name, words in _THEME_WORDS.items():
        if any(w in t for w in words):
            return name
    return "日常其他"


def theme_fatigue_line(topics: list, threshold: int = 2) -> str:
    """题材疲劳行：近几天高频题材点名限用（治"天天求职秋招"）。"""
    counts: dict[str, int] = {}
    for t in topics:
        th = classify_theme(t)
        if th != "日常其他":
            counts[th] = counts.get(th, 0) + 1
    hot = sorted(((k, n) for k, n in counts.items() if n >= threshold),
                 key=lambda x: -x[1])
    if not hot:
        return ""
    segs = "、".join(f"{k}×{n}" for k, n in hot[:3])
    return (f"- 题材疲劳清单（近几天已高频的题材，本批合计 ≤ 2 条，优先日常其他象限）：{segs}")


def family_label(fam: str) -> str:
    f = FAMILIES.get(fam)
    return f"{fam}·{f[0]}" if f else (fam or "F0·未识别")


def recent_fatigue(days: int = 3, today: str | None = None,
                   drafts_dir: Path | None = None) -> dict:
    """近 N 天 Top5 的家族疲劳统计 + 样例（供生成端注入与 UI 展示）。

    返回 {"topics": [...], "counts": {fam: n}, "line": "注入 prompt 的疲劳行"}。
    """
    from datetime import datetime, timedelta
    from core.storage import load_json
    base = drafts_dir or _drafts_dir()
    try:
        d0 = datetime.fromisoformat(str(today)) if today else datetime.now()
    except ValueError:
        d0 = datetime.now()
    dates = [(d0 - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    topics = []
    for d in dates:
        for p in sorted(base.glob(f"{d}*_top5.json")):
            data = load_json(p, {}) or {}
            for r in (data.get("results") or [])[:5]:
                t = str(r.get("topic") or "").strip()
                if t:
                    topics.append(t)
    counts: dict[str, int] = {}
    for t in topics:
        fam = classify_family(t)
        counts[fam] = counts.get(fam, 0) + 1
    hot = sorted(((f, n) for f, n in counts.items() if n >= 2 and f != "F0"),
                 key=lambda x: -x[1])
    if hot:
        segs = [f"{family_label(f)}×{n}" for f, n in hot[:4]]
        line = ("- 结构疲劳清单（近几天 Top5 已高频的骨架，本批这些家族合计 ≤ 3 条，"
                "优先用菜单里的其他家族）："
                + "、".join(segs)
                + (f"；例：{topics[0][:24]}…" if topics else ""))
    else:
        line = "- 结构疲劳清单：近几天无高频骨架（正常轮换即可）"
    return {"topics": topics, "counts": counts, "line": line}


def _drafts_dir() -> Path:
    from core.paths import DRAFTS_DIR
    return DRAFTS_DIR


def diverse_top_k(results: list, k: int = 5, per_family: int = 2) -> list:
    """总分排序 + 家族配额选取：每家族 ≤ per_family 条；不够再放宽补齐。

    治「Top5 五条同构」——旧版纯按 total 截取，「秋招第四周，我把…」
    三条同骨架霸榜的来源。
    """
    ranked = sorted(results, key=lambda r: r.get("total", 0), reverse=True)
    picked: list = []
    fam_count: dict[str, int] = {}
    for cap in (per_family, per_family + 1, 99):
        for r in ranked:
            if len(picked) >= k:
                return picked
            if r in picked:
                continue
            fam = str(r.get("family") or classify_family(r.get("topic", "")) or "F0")
            if fam_count.get(fam, 0) >= cap:
                continue
            picked.append(r)
            fam_count[fam] = fam_count.get(fam, 0) + 1
    return picked[:k]
