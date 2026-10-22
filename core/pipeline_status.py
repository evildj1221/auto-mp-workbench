#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/pipeline_status.py · 流水线 10 步状态的唯一权威实现。

收敛自 v5 前的三份重复实现（UI common.pipeline_status / Agent 工具
tools.state_tools._status_map / CLI pipeline.publish.cmd_status）——
三者判据原本逐条相同但各自维护，已出现文案漂移。判据 = data/ 产物
文件存在性/字段，只读、零 LLM 成本。
"""
from core.paths import CANDIDATES_DIR, DRAFTS_DIR, FEEDBACK_DIR, SELECTIONS_DIR
from core.storage import load_json

# 10 步检查顺序（key）
STEP_ORDER = ["collect", "score", "feedback", "analyze", "choose",
              "write_v1", "write_v2", "sync", "format", "learn"]

# 各步骤中文名（UI 与 CLI 共用一套文案）
STEP_LABELS = {
    "collect": "Step 1 · 选题生成",
    "score": "Step 2 · 评分 Top5",
    "feedback": "Step 3.5 · 用户评论",
    "analyze": "Step 3.6 · 反馈分析",
    "choose": "Step 3.7 · 用户决策",
    "write_v1": "Step 4 · 写稿 v1",
    "write_v2": "Step 4+ · 写稿 v2",
    "sync": "Step 4.5 · 云文档归档",
    "format": "Step 5 · HTML 排版",
    "learn": "Step 7 · 反馈学习",
}


def status_map(date):
    """当日 10 步完成状态。返回 (steps_dict, published, selection)。"""
    from core import memory as mem  # 函数内导入：memory 反向依赖本模块层级，避免环
    pub = load_json(DRAFTS_DIR / f"{date}_published.json", {}) or {}
    stats = load_json(FEEDBACK_DIR / "daily_stats.json", []) or []
    if not isinstance(stats, list):
        stats = []
    choice = load_json(SELECTIONS_DIR / f"{date}.json", {}) or {}
    return {
        "collect": (CANDIDATES_DIR / f"{date}.json").exists(),
        "score": (DRAFTS_DIR / f"{date}_top5.json").exists(),
        "feedback": bool(mem.load_user_feedback(date)),
        "analyze": mem._analysis_path(date).exists(),
        "choose": bool(choice.get("user_choice")),
        # 槽位对齐：检查无槽位旧命名 ∨ 任一 w 槽位产物（Agent 写槽位、
        # 旧数据写无槽位，两侧都算完成）
        "write_v1": (DRAFTS_DIR / f"{date}_v1.md").exists()
                    or any(DRAFTS_DIR.glob(f"{date}_w*_v1.md")),
        "write_v2": (DRAFTS_DIR / f"{date}_v2.md").exists()
                    or any(DRAFTS_DIR.glob(f"{date}_w*_v2.md")),
        "sync": bool(pub.get("feishu_doc_url")),
        # 排版判据：step_format 的旧命名 + 现行导出链的 {date}[_slot]_mp.html
        # （只跑过 oneclick/导出的日期曾恒显示 9/10）
        "format": (DRAFTS_DIR / f"{date}.html").exists()
                  or (DRAFTS_DIR / f"{date}_v2.html").exists()
                  or any(DRAFTS_DIR.glob(f"{date}*_mp.html")),
        "learn": any(isinstance(s, dict) and s.get("date") == date for s in stats),
    }, pub, choice
