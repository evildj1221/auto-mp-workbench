#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/topic_bank.py · 选题灵感库（蒸馏沉淀 + 生成端注入）。

定位：把"大V题材坐标系 + 日常感样例"沉淀成可复用资产（data/topic_bank.json），
生成选题时按日期稳定抽样注入 prompt——LLM 取骨架与坐标，禁止照抄文字。

蒸馏来源（合规）：LLM 风格访谈（头部情感号的题材偏好/角度模式，零采集成本）
+ 自家历史文稿坐标。产出是"题材坐标与切法"，不是任何人的具体句子。
"""
import json
import random
from datetime import datetime
from pathlib import Path

_BANK_ENTRIES_KINDS = ("daily", "fresh", "angle")


def bank_path() -> Path:
    from core.paths import DATA_DIR
    return DATA_DIR / "topic_bank.json"


def load_bank() -> dict:
    p = bank_path()
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def save_bank(bank: dict) -> None:
    p = bank_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")


def sample_bank_line(date: str, n: int = 10) -> str:
    """按日期稳定抽样（同一天重跑得到同一批样本，便于对比调试）。"""
    bank = load_bank()
    entries = bank.get("entries") or []
    if not entries:
        return ""
    rng = random.Random(date)  # 种子=日期 → 稳定抽样
    picks = rng.sample(entries, min(n, len(entries)))
    lines = []
    for e in picks:
        fam = e.get("family", "")
        axis = e.get("axis", "")
        lines.append(f"- [{fam or '?'}|{axis or '?'}] {e.get('topic', '')}")
    return ("- 灵感库样本（来自头部情感号题材蒸馏；取【骨架与坐标】创作新选题，"
            "禁止照抄或同义改写样本文字）：\n" + "\n".join(lines))


DISTILL_PROMPT = """你是情感类公众号的内容研究者。请"访谈"头部情感治愈类账号
（如十点读书、夜听、视觉志类大号，以及高阅读情感博主的普遍写法），提炼它们
【日常感选题】的题材坐标与切入角度，输出 JSON 样例库。

背景：给一个"治愈×晚安陪伴"账号（调性：温柔/慢/陪伴/真实）补选题灵感。
它的问题：选题长期困在求职/秋招/简历等职场题材和"时间切片"句式里，读者疲劳。
需要【日常生活流】的选题——买菜、做饭、阳台、通勤、便利店、收纳、散步、
洗澡、浇花、晾衣服、夜宵、宠物、公交、楼道、邻居、药店、理发店、公园长椅……

要求：
1. 产出 {count} 条日常感选题样例，每条 8-22 字，是"具体小事承载情绪"的写法
   （参照这类大号的常见手法：物件领题/动作反转/意象起兴/留白不语）；
2. 每条标注：family（F1 动作转折/F2 意象起兴/F3 时间切片/F4 物件特写/F5 留白不语/
   F6 场景蒙太奇/F7 数字细节/F9 定义命名/F10 回信召唤，任选）、axis（题材坐标：
   厨房/阳台/通勤/便利店/家/身体/邻里/季节物候/独处仪式/关系日常/城市角落/夜晚/清晨 等）、
   kind（daily=纯日常 / fresh=跟时令节气 / angle=新切法）；
3. 题材分布：日常 70% + 时令 20% + 切法 10%；12 个以上不同 axis；
4. **零职场/求职/简历/秋招内容**；不出现"晚安"致意、"为什么"设问等 6 类
   高频模板；不鸡汤不说教；
5. 是"样例库"不是"今日选题"——写成可复用的原型，不要绑定具体今天。

输出 JSON：
{{"entries": [{{"topic": "...", "family": "F4", "axis": "厨房", "kind": "daily"}}]}}"""


def distill_bank(count: int = 48) -> dict:
    """LLM 蒸馏一次题材坐标库并落盘（旧样例保留，去重合并）。"""
    from llm.client import call_llm
    result = call_llm(DISTILL_PROMPT.replace("{count}", str(count)),
                      temperature=0.8, max_tokens=6000, module="topic_bank_distill")
    entries = result.get("entries") or []
    bank = load_bank()
    existing = bank.get("entries") or []
    seen = {e.get("topic") for e in existing}
    added = 0
    for e in entries:
        t = str(e.get("topic") or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        existing.append({"topic": t,
                         "family": str(e.get("family") or "").strip(),
                         "axis": str(e.get("axis") or "").strip(),
                         "kind": str(e.get("kind") or "daily").strip(),
                         "distilled_at": datetime.now().isoformat(timespec="seconds")})
        added += 1
    bank.update({"updated_at": datetime.now().isoformat(timespec="seconds"),
                 "entries": existing})
    save_bank(bank)
    return {"added": added, "total": len(existing)}
