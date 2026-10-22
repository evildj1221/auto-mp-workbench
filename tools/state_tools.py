#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/state_tools.py · 只读环境感知工具（status / read_draft / list_candidates）。

planner 每轮决策前感知环境用：不产生任何写入、不调 LLM、零成本。
10 步检查的权威实现在 core/pipeline_status.py（UI/CLI/Agent 三方共用），
本层只做工具信封包装。
"""
from langchain_core.tools import tool

from core.pipeline_status import STEP_ORDER, status_map
from core.paths import SELECTIONS_DIR, slot_paths
from core.quality import count_chinese_chars
from core.storage import load_json
from tools._base import envelope, tool_error

# agent/graph 与 agent/planner 的既有调用点沿用旧名；实现已收敛到 core
_status_map = status_map


@tool
def get_pipeline_status(date: str) -> dict:
    """查询当日流水线 10 步完成状态与关键产物信息（选题/写稿/归档/排版/学习）。
    只读零成本，返回 next_step 提示下一步该做什么。

    Args:
        date: 目标日期，YYYY-MM-DD
    """
    try:
        steps, pub, choice = status_map(date)
        done = [k for k in STEP_ORDER if steps[k]]
        next_step = next((k for k in STEP_ORDER if not steps[k]), "")
        user_choice = choice.get("user_choice") if choice else None
        extra = {}
        if pub:
            extra["published"] = {"title": pub.get("title", "?"),
                                  "word_count": pub.get("word_count", 0),
                                  "feishu_doc_url": pub.get("feishu_doc_url", "")}
        if user_choice:
            extra["user_choice"] = user_choice
        return envelope("ok", f"{len(done)}/10 步完成" + (f"，下一步: {next_step}" if next_step else "，全流程完成"),
                        steps=steps, completed=done, next_step=next_step, **extra)
    except Exception as e:
        return tool_error(e)


@tool
def read_draft(date: str, slot: str | None = None, version: str = "v1") -> dict:
    """读取当日文稿全文（planner 修订前先读原文，避免凭记忆改稿）。只读。

    Args:
        date: 目标日期，YYYY-MM-DD
        slot: 文稿槽位（默认当日活动槽位）
        version: 文稿版本（v1/v2）
    """
    try:
        md_path = slot_paths(date, slot)[version]
        if not md_path.exists():
            return envelope("error", f"文稿不存在: {md_path.name}，先 write_draft")
        content = md_path.read_text(encoding="utf-8")
        title = content.split("\n", 1)[0].lstrip("# ").strip()
        return envelope("ok", f"{md_path.name}（{count_chinese_chars(content)} 汉字）",
                        title=title, word_count=count_chinese_chars(content),
                        content=content, draft_path=str(md_path))
    except Exception as e:
        return tool_error(e)


@tool
def list_candidates(date: str, round: int | None = None) -> dict:
    """列出当日候选与 Top5 评分结果（含用户已选项标记）。只读，供选题决策参考。

    Args:
        date: 目标日期，YYYY-MM-DD
        round: 批次号（默认当前批次）
    """
    try:
        from core.storage import load_batches
        from core.paths import batch_file_names
        r = round or load_batches(date).get("active_round") or 1
        cands_path, top5_path = batch_file_names(date, r)
        cands = load_json(cands_path, {}).get("candidates", [])
        top5 = load_json(top5_path, {}).get("results", [])
        choice = load_json(SELECTIONS_DIR / f"{date}.json", {}) or {}
        if not cands and not top5:
            return envelope("error", f"当日无候选（r{r}），先 collect_candidates")
        return envelope("ok", f"r{r}: {len(cands)} 候选 / Top{len(top5)}",
                        round=r,
                        candidates=[{"topic": c.get("topic", "?"),
                                     "motif_series": c.get("motif_series", "")}
                                    for c in cands],
                        top5=[{"rank": i + 1, "topic": t.get("topic", "?"),
                               "total": t.get("total", 0),
                               "compliance_blocked": t.get("compliance_blocked", False)}
                              for i, t in enumerate(top5)],
                        user_choice=(choice.get("user_choice") or None))
    except Exception as e:
        return tool_error(e)
