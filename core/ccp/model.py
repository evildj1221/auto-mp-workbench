#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/ccp/model.py · 客户内容档案（Client Content Profile）数据结构定义。

商业化产品方案 §6.3 落地：CCP 是客户资产，四维结构（风格/选题/排版/读者），
写入侧来自初始化投喂 + 采纳反馈 + 发布回流 + 复盘，读取侧注入选题/写稿/排版/头图
四个节点。本模块只定义「结构与默认值」，读写/校验在 profile.py，投喂在 seed.py，
反馈闭环在 feedback.py —— 保持单一路径、确定性（新闭环不依赖 LLM）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

# ============================================================ 常量

CCP_SCHEMA_VERSION = 1

# 场景 = 评分维度 + 红线规则 + 排版主题 + 选题信号源的参数化标识（§6.2）
SCENE_DEFAULT = "companion_letter_writing"  # 陪伴式个人 IP 日更（创作工作台为旗舰示例账号）
SCENE_LABELS = {
    "companion_letter_writing": "陪伴式日更写作",
    "emotion": "情感感悟",
    "reading": "读书成长",
    "growth": "个人成长",
    "pet": "萌宠生活",
}

# 四维：互斥、全覆盖
DIMS = ("style", "topic", "layout", "reader")
DIM_LABELS = {
    "style": "风格维", "topic": "选题维",
    "layout": "排版维", "reader": "读者维",
}

# 风格维：确定性启发式提取的维度键（seed.py 用）
STYLE_KEYS = ("language_tone", "sentence_habits", "frameworks",
              "quote_preferences", "avoid_styles")

# 注入上下文上限（防 prompt 膨胀 → 成本护栏；软约束注入 §6.3）
INJECTION_DEFAULTS = {
    "enabled_dims": ["style", "topic", "layout", "reader"],
    "style_block_maxlen": 300,   # 字符上限
    "topic_block_maxlen": 200,
    "layout_block_maxlen": 200,
    "reader_block_maxlen": 150,
}

# 打分范围（与现有 collect_feedback 一致 1-5）
SCORE_MIN, SCORE_MAX = 1, 5


def empty_profile(client_id: str = "slowpost", scene: str = SCENE_DEFAULT) -> Dict:
    """返回一份全新（空）CCP 结构。写入侧渐进填充，读取侧按缺省降级。"""
    from datetime import datetime
    return {
        "schema_version": CCP_SCHEMA_VERSION,
        "meta": {
            "client_id": client_id,
            "owner": "",
            "scene": scene,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "version": 1,
            "revision_notes": [],
            "harvest_cursors": {},   # 反馈闭环去重光标（{source: cursor}）
        },
        "style": {
            "language_tone": "",
            "sentence_habits": [],
            "frameworks": [],
            "quote_preferences": [],
            "avoid_styles": [],
            "ingested_articles": [],  # 投喂历史（内容哈希幂等）
            "seed_source": "cold_start",  # cold_start | questionnaire | articles
        },
        "topic": {
            "preference_weights": {},   # {主题/系列: 归因偏好分}
            "high_performance_patterns": [],
            "excluded_series": [],
        },
        "layout": {
            "active_theme": "",
            "density": "medium",
            "component_toggles": {},
            "palette_notes": [],
        },
        "reader": {
            "trend": [],               # [{date, reads, likes, shares}]
            "avg_engagement": {},
            "notes": [],
        },
        "learned_leads": [],           # 留痕："本周学习到的 N 条偏好"，可撤销
        "injection": dict(INJECTION_DEFAULTS),  # 注入控制（enabled_dims / 各维上限）
        "injected_subject": [],        # 最后注入目标摘要（trace 用）
    }


def scene_default_profile(client_id: str = "slowpost",
                          scene: str = SCENE_DEFAULT) -> Dict:
    """冷启动场景默认画像（§6.3：新客户无数据时用场景默认，随使用渐进收敛）。
    所有值均为"该场景的通用写作基线"，不含任何客户端私有偏好；guarded 由
    validate_profile() 判定为合法，但 style.seed_source 标 cold_start 以区隔。
    """
    base = empty_profile(client_id, scene)
    base["meta"]["owner"] = SCENE_LABELS.get(scene, scene)
    base["style"].update({
        "language_tone": "真诚、克制的第二人称陪伴语调；多具体细节，少空泛说教。",
        "sentence_habits": ["短句为主，句号收束，节奏舒缓", "适度用第二人称『你』拉近距离",
                            "首段用生活场景切入，结尾留余韵"],
        "frameworks": ["场景切入 → 情绪铺陈 → 回信点题 → 留白收束"],
        "quote_preferences": ["轻柔、有余韵的短金句"],
        "avoid_styles": ["说教堆砌", "网络热词堆叠", "空泛鸡汤"],
    })
    return base


# ============================================================ 校验

# 顶层必须存在的键（含继承默认后）
_REQUIRED_TOP = ("schema_version", "meta", "style", "topic", "layout", "reader",
                 "learned_leads")
_STYLE_REQUIRED = ("language_tone", "sentence_habits", "frameworks",
                   "quote_preferences", "avoid_styles", "ingested_articles",
                   "seed_source")


class ProfileError(ValueError):
    """CCP 数据结构非法（坏文件/坏投喂/坏反馈），携带可读信息。"""


def validate_profile(p: Dict) -> Dict:
    """结构性完整性校验：只校验『结构与类型』，不做语义（语义由读写方 bound）。
    返回原样并抛出 ProfileError 描述首个缺失项；对损坏数据可据此优雅降级/重建。
    """
    if not isinstance(p, dict):
        raise ProfileError("CCP 根节点不是对象")
    if p.get("schema_version") != CCP_SCHEMA_VERSION:
        raise ProfileError(f"schema_version 不符: got {p.get('schema_version')}, "
                           f"期望 {CCP_SCHEMA_VERSION}")
    for k in _REQUIRED_TOP:
        if k not in p:
            raise ProfileError(f"缺顶层字段: {k}")
    meta = p.get("meta") or {}
    if not isinstance(meta, dict) or not meta.get("client_id"):
        raise ProfileError("meta.client_id 缺失")
    st = p.get("style") or {}
    for k in _STYLE_REQUIRED:
        if k not in st:
            raise ProfileError(f"缺 style.{k}")
    for name, lst in (("style.sentence_habits", st.get("sentence_habits")),
                      ("style.frameworks", st.get("frameworks")),
                      ("style.quote_preferences", st.get("quote_preferences")),
                      ("topic.preference_weights", (p.get("topic") or {}).get("preference_weights"))):
        if lst is not None and not isinstance(lst, (list, dict)):
            raise ProfileError(f"{name} 必须是列表或对象")
    if st.get("seed_source") not in ("cold_start", "questionnaire", "articles"):
        raise ProfileError(f"style.seed_source 非法: {st.get('seed_source')}")
    return p