#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/scoring.py · 评分维度常量 + 权重解析（偏好画像/rubric 校准）+ 反馈学习校准。

自 cat_shop.py 483-486 / 1294-1329 / 2285-2309 行原样迁出，行为不变。
"""
import json
import statistics
from datetime import datetime, timedelta

from core.paths import PROFILE_PATH, RUBRIC_REVISIONS_PATH
from core.memory import load_preference

# ===== 评分维度（统一常量，避免硬编码散落） =====
DIMENSIONS = ("resonance", "timeliness", "controversy", "uniqueness", "writability")
DEFAULT_W = {"resonance": 0.35, "timeliness": 0.20, "controversy": 0.15,
             "uniqueness": 0.20, "writability": 0.10}

def load_effective_weights(today):
    """评分权重：默认 → 偏好画像（7 天内） → rubric 校准（14 天内，逐维钳制 ±0.15）。"""
    w = dict(DEFAULT_W)

    profile = load_preference()
    if profile:
        cal = profile.get("scoring_calibration") or {}
        if isinstance(cal, dict) and any(k in cal for k in DIMENSIONS):
            try:
                updated = datetime.fromisoformat(str(profile.get("updated_at", "")))
                if (today - updated).days <= 7:
                    w = {k: float(cal.get(k, DEFAULT_W[k])) for k in DIMENSIONS}
            except (ValueError, TypeError):
                pass

    deltas = {k: 0.0 for k in DIMENSIONS}
    if RUBRIC_REVISIONS_PATH.exists():
        cutoff = (today - timedelta(days=14)).strftime("%Y-%m-%d")
        for line in RUBRIC_REVISIONS_PATH.read_text(encoding="utf-8").strip().split("\n"):
            if not line:
                continue
            try:
                e = json.loads(line)
                d = e.get("dimension")
                if d in deltas and e.get("date", "") >= cutoff:
                    try:
                        deltas[d] += float(e.get("to", 0)) - float(e.get("from", 0))
                    except (ValueError, TypeError):
                        pass
            except Exception:
                continue

    for k in DIMENSIONS:
        w[k] = max(0.05, min(0.60, w[k] + max(-0.15, min(0.15, deltas[k]))))
    s = sum(w.values())
    return {k: round(v / s, 3) for k, v in w.items()}

# ===== Step 7: Learn（v2：weekly 真实学习校准权重） =====
def learn_calibration_from_feedback(entries, min_each=2):
    """从用户评论学习维度权重：
    用户给高分（≥4）的选题在哪个维度上 AI 分显著更高 → 该维度更贴合用户决策 → 提高权重。
    返回 None 表示数据不足以校准（沿用默认权重）。
    """
    highs = [e for e in entries if (e.get("user_score") or 0) >= 4]
    lows = [e for e in entries if (e.get("user_score") or 0) <= 2]
    if len(highs) < min_each or len(lows) < min_each:
        return None

    def _mean_dim(es, dim):
        vals = [float(e.get("ai_scores_detail", {}).get(dim) or 0) for e in es]
        return statistics.mean(vals) if vals else 0.0

    disc = {}
    for k in DIMENSIONS:
        disc[k] = max(0.0, _mean_dim(highs, k) - _mean_dim(lows, k))
    if sum(disc.values()) <= 0:
        return None

    # 向区分度方向平滑移动（0.35 步长），再归一化
    raw = {k: DEFAULT_W[k] + 0.35 * (disc[k] / sum(disc.values()) - DEFAULT_W[k]) for k in DIMENSIONS}
    s = sum(raw.values())
    return {k: round(v / s, 3) for k, v in raw.items()}
