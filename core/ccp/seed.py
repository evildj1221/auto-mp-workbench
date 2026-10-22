#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/ccp/seed.py · CCP 初始化投喂：启动引导 + 问卷投喂 + 历史文稿风格提取。

对齐商业化方案 §6.3「初始化：问卷 + 历史文稿投喂（GetDraft 模式）」：
  - 冷启动：无档案时建『场景默认画像』（seed.py 的 ensure_seeded 在系统启动调用）
  - 问卷投喂：`ingest_questionnaire` 把客户问卷的显式偏好并入风格/选题维（幂等）
  - 历史文稿投喂：`ingest_articles` 对 3-5 篇代表文稿做**确定性**风格提取（零 LLM，
    可测试、低成本），写回风格维并记录内容哈希保证幂等

完整性校验：validate_profile 结构校验 + ingest 输入边界校验（空稿/过短跳过、
超出 5 篇截断）。所有写操作经 ClientProfile.save（原子 + 版本快照）。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, List, Optional

from core.ccp.model import ProfileError
from core.ccp.profile import ClientProfile, _ts

# 单次投喂上限（§ "3-5 篇代表文本" 的硬保护，防海量投喂稀释风格）
ARTICLE_SEED_MAX = 5
_ARTICLE_MIN_CHARS = 80        # 过短（标题/碎片）无风格信息，跳过
_SENT_TERM = re.compile(r"[。！？!?…]")
_Q_PUNCT = re.compile(r"[“”‘’《》——……、（）]")
_CN = re.compile(r"[\u4e00-\u9fff]")
_HASH_PAT = re.compile(r"[^\w\u4e00-\u9fff]")


def _content_hash(text: str) -> str:
    """内容归一化哈希：去空白/标点后 sha1 前 16。空内容回退原文哈希。"""
    norm = _HASH_PAT.sub("", text)
    if not norm:
        norm = text
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def _split_sentences(text: str) -> List[str]:
    parts = _SENT_TERM.split(text)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) >= 4]


def _extract_style(text: str) -> Dict:
    """确定性风格提取：句长分布、标点节奏、用词习惯、候选金句（零 LLM）。
    返回可并入 style 维的增量（label + candidates）。"""
    sents = _split_sentences(text)
    if not sents:
        return {"language_tone": "", "sentence_habits": [], "quote_preferences": []}
    total_cn = sum(len(_CN.findall(s)) for s in sents)
    lens = [len(_CN.findall(s)) for s in sents]
    avg = round(sum(lens) / len(lens), 1) if lens else 0.0
    short = sum(1 for x in lens if x <= 8); mid = sum(1 for x in lens if 9 <= x <= 20)
    # 句子习惯
    habits = []
    if short / len(lens) >= 0.5:
        habits.append("短句为主，节奏轻快")
    elif mid / len(lens) >= 0.5:
        habits.append("中长句为主，铺陈细腻")
    else:
        habits.append("长短句交错，节奏有起伏")
    q = sum(1 for s in sents if any(p in s for p in _Q_PUNCT.findall(s)))
    if q / len(sents) >= 0.3:
        habits.append("善用引号/破折号/省略号营造停顿感")
    else:
        habits.append("标点克制，句号收束为主")
    # 候选金句：较短（6-40 中文），去重过短
    quotes = []
    for s in sents:
        c = len(_CN.findall(s))
        if 6 <= c <= 40 and s not in quotes:
            quotes.append(s)
    quotes = quotes[:5]
    return {
        "language_tone": f"平均句长约 {avg} 字（{'短句' if avg <= 10 else '中句' if avg <= 18 else '长句'}），"
                          f"共 {len(sents)} 句、约 {total_cn} 字。",
        "sentence_habits": habits,
        "quote_preferences": quotes,
    }


# ============================================================ 引导
def ensure_seeded(profile: ClientProfile, scene: Optional[str] = None) -> Dict:
    """启动引导：无档案则建场景默认画像并落盘；已有则原样返回。
    幂等：重复调用不重复建档（靠 exists 判断），不覆盖任何已有人工数据。"""
    p = profile._p
    if p is not None:
        return p
    if profile.path.exists():
        profile.load()
        return profile.profile
    profile.load()  # 内部 create default + save
    return profile.profile


# ============================================================ 问卷投喂
QUESTIONNAIRE_KEYS = {
    # 问卷输入键 → (dim, style_key)
    "tone": ("style", "language_tone"),
    "sentence_habits": ("style", "sentence_habits"),
    "frameworks": ("style", "frameworks"),
    "avoid_styles": ("style", "avoid_styles"),
    "topic_weights": ("topic", "preference_weights"),
}


def ingest_questionnaire(profile: ClientProfile, answers: Dict, *,
                         merge: bool = False) -> Dict:
    """问卷投喂：把显式偏好并入对应维。缺省 merge=False 表示『覆盖』（问卷是客户
    当前明确意图，优先于历史推断）；merge=True 用于二次问卷做增量合并。
    空 answers 视为未投喂，返回空 report。"""
    if not answers or not isinstance(answers, dict):
        return {}
    p = profile.load()
    report = {}
    for key, (dim, style_key) in QUESTIONNAIRE_KEYS.items():
        if key not in answers or answers[key] is None:
            continue
        val = answers[key]
        target = p[dim]
        if key == "topic_weights":
            if isinstance(val, dict):
                if merge:
                    target["preference_weights"].update({k: float(v)
                                                         for k, v in val.items()})
                    report.setdefault("topic", []).append("merge 主题权重")
                else:
                    p["topic"]["preference_weights"] = {k: float(v) for k, v in val.items()}
                    report.setdefault("topic", []).append("覆盖主题权重")
        elif isinstance(val, list):
            if merge:
                seen = set(target.get(style_key, []) or [])
                merged = list(seen)
                for x in val:
                    if x not in seen:
                        merged.append(x)
                        seen.add(x)
                target[style_key] = merged
            else:
                target[style_key] = list(val)
            report.setdefault(dim, []).append(style_key)
        elif isinstance(val, str) and val.strip():
            target[style_key] = val.strip()
            report.setdefault(dim, []).append(style_key)
    p["style"]["seed_source"] = "questionnaire"
    profile.save(reason="问卷投喂")
    return report


# ============================================================ 历史文稿投喂
def ingest_article(profile: ClientProfile, text: str, *, title: str = "",
                   date: str = "") -> Dict:
    """投喂单篇文稿，做确定性风格提取并入 style 维。
    幂等：按内容哈希去重，重复投喂不叠加。返回本{"added": bool, "hash", "chars"}。"""
    text = (text or "").strip()
    if not text:
        return {"added": False, "hash": "", "chars": 0, "reason": "empty"}
    if len(text) < _ARTICLE_MIN_CHARS:
        return {"added": False, "hash": _content_hash(text), "chars": len(text),
                "reason": "too_short"}
    p = profile.load()
    h = _content_hash(text)
    ing = p["style"]["ingested_articles"]
    if any(a.get("hash") == h for a in ing):
        return {"added": False, "hash": h, "chars": len(text), "reason": "duplicate"}
    if len(ing) >= ARTICLE_SEED_MAX:
        return {"added": False, "hash": h, "chars": len(text), "reason": "limit"}

    ext = _extract_style(text)
    st = p["style"]
    for v in ext.get("sentence_habits", []):
        if v not in st["sentence_habits"]:
            st["sentence_habits"].append(v)
    for q in ext.get("quote_preferences", []):
        if q not in st["quote_preferences"]:
            st["quote_preferences"].append(q)
    if ext.get("language_tone"):
        # 多稿共存时 tone 取汇总口径（用最后投喂的一条描述即可，粒度不叠加）
        st["language_tone"] = ext["language_tone"]
    st["ingested_articles"].append({
        "hash": h, "title": title or "", "date": date or "",
        "chars": len(text), "ts": _ts()})
    st["seed_source"] = "articles"
    profile.save(reason=f"历史文稿投喂{'' if not title else '：' + title[:20]}")
    return {"added": True, "hash": h, "chars": len(text), "reason": "ok"}


def ingest_articles(profile: ClientProfile, texts: List[str],
                    *, titles: Optional[List[str]] = None) -> Dict:
    """批量投喂多篇文稿（GetDraft 模式，3-5 篇）。逐篇幂等，超出上限截断。
    titles 与 texts 按位对应。返回汇总 report。"""
    if not texts:
        return {"added": 0, "skipped": 0, "rejected": [], "reason": "empty"}
    titles = titles or []
    added = skipped = 0
    rejected = []
    for i, text in enumerate(texts):
        r = ingest_article(profile, text, title=(titles[i] if i < len(titles) else ""))
        if r["added"]:
            added += 1
        elif r.get("reason") in ("duplicate", "limit"):
            skipped += 1
        else:
            rejected.append((i + 1, r.get("reason")))
    return {"added": added, "skipped": skipped, "rejected": rejected,
            "count": len(texts)}


def verify_seed(profile: ClientProfile) -> Dict:
    """投喂后完整性校验：结构合法 + style 维含有效条目数。返回诊断摘要；抛错说明坏。"""
    p = profile.load()
    return {
        "ok": True,
        "schema_version": p["schema_version"],
        "version": p["meta"]["version"],
        "seed_source": p["style"]["seed_source"],
        "ingested_count": len(p["style"]["ingested_articles"]),
        "sentence_habits": len(p["style"]["sentence_habits"]),
        "frameworks": len(p["style"]["frameworks"]),
        "quote_preferences": len(p["style"]["quote_preferences"]),
        "topic_weights": len(p["topic"]["preference_weights"]),
        "active_theme": p["layout"]["active_theme"],
    }


# ============================================================ 顶层便捷
def seed_from_sources(profile: ClientProfile, *, ml_path: Optional[Path] = None,
                      questionnaire: Optional[Dict] = None) -> Dict:
    """一键投喂：可选从历史文稿目录（ml_path 下的 .md/.txt，取 N 篇）与问卷。
    返回四件套报告。ml_path 为空或不存在 → 不投喂文稿。"""
    report = {"questionnaire": {}, "articles": {}, "verify": {}}
    if questionnaire:
        report["questionnaire"] = ingest_questionnaire(profile, questionnaire)
    if ml_path and Path(ml_path).exists():
        files = sorted(p for p in Path(ml_path).rglob("*.md")
                       if "mp.html" not in p.name and not p.name.startswith("_"))
        files = files[:ARTICLE_SEED_MAX]
        texts = [p.read_text(encoding="utf-8", errors="replace") for p in files]
        report["articles"] = ingest_articles(
            profile, texts, titles=[p.stem for p in files])
    report["verify"] = verify_seed(profile)
    return report