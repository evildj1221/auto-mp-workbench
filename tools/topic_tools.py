#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/topic_tools.py · 选题链路工具（collect / score）。

包装 pipeline.topics 的 step 函数为纯参数签名的 LangChain @tool。
幂等语义沿用 step 内建机制（产物存在即跳过，返回 skipped 而非异常）。
"""
from langchain_core.tools import tool

from core.paths import batch_file_names
from core.storage import load_batches, load_json
from pipeline.topics import step_collect, step_score
from tools._base import envelope, run_step, tool_error


def _round_of(date, round_no):
    if round_no:
        return int(round_no)
    return load_batches(date).get("active_round") or 1


@tool
def collect_candidates(date: str, timeframe: str = "", trending: list[str] | None = None,
                       force: bool = False, round: int | None = None) -> dict:
    """生成一批选题候选（15-20 个，含历史去重与偏好注入）。幂等：当日该批候选已存在时跳过（force 覆盖重跑）。

    Args:
        date: 目标日期，YYYY-MM-DD
        timeframe: 时段描述（如 '2026 夏季 / 8 月'，空则自动生成）
        trending: 热搜关键词列表（可选，注入选题上下文）
        force: 覆盖已有候选重新生成（消耗 LLM 额度）
        round: 批次号（默认当前批次；多批共存场景指定）
    """
    try:
        ok, log = run_step(step_collect, date=date, timeframe=timeframe,
                           trending=trending or [], force=force, round=round)
        cands_path, _ = batch_file_names(date, _round_of(date, round))
        cands = load_json(cands_path, {}).get("candidates", [])
        topics = [c.get("topic", "?") for c in cands]
        if not ok and not cands:
            return envelope("error", "候选生成失败，查看 log", log=log)
        if "已存在" in log and "跳过" in log:
            return envelope("skipped", f"候选已存在（{len(cands)} 个），force=True 可覆盖",
                            log=log, candidates=topics)
        return envelope("ok", f"生成 {len(cands)} 个候选", log=log, candidates=topics,
                        candidates_path=str(cands_path))
    except Exception as e:
        return tool_error(e)


@tool
def score_candidates(date: str, top_k: int = 5, runs: int = 1, force: bool = False,
                     round: int | None = None) -> dict:
    """对候选做多维度评分（共鸣/时效/争议/独特/写作可行，五维加权），产出 Top5。
    返回 needs_user_judgment 标记：总分标准差大、有合规风险或仅一个有效候选时为 True，
    表示应交人工决策而非自动选择。幂等：top5 已存在时跳过（force 覆盖）。

    Args:
        date: 目标日期，YYYY-MM-DD
        top_k: 保留前几名（默认 5）
        runs: 每候选评分轮数（>=3 才有稳定的 std/置信度；多轮更贵）
        force: 覆盖已有 top5（用户已决策时慎用）
        round: 批次号（默认当前批次）
    """
    try:
        ok, log = run_step(step_score, date=date, top_k=top_k, runs=runs,
                           batch_size=5, workers=5, force=force, round=round)
        _, top5_path = batch_file_names(date, _round_of(date, round))
        results = load_json(top5_path, {}).get("results", [])
        if not ok and not results:
            return envelope("error", "评分失败，查看 log", log=log)
        summary = [{"rank": i + 1, "topic": r.get("topic", "?"), "total": r.get("total", 0),
                    "compliance_blocked": r.get("compliance_blocked", False),
                    "needs_user_judgment": r.get("needs_user_judgment", False)}
                   for i, r in enumerate(results)]
        nj = sum(1 for s in summary if s["needs_user_judgment"])
        status = "skipped" if ("已存在" in log and "跳过" in log) else "ok"
        return envelope(status, f"Top{len(results)} 就绪；{nj} 个候选建议人工决策",
                        log=log, top5=summary, needs_user_judgment_count=nj,
                        needs_user_judgment=nj > 0, top5_path=str(top5_path))
    except Exception as e:
        return tool_error(e)
