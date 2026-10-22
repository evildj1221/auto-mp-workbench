#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/quality_tools.py · 质量门禁工具（redline / wordcount / similarity）。

纯规则检查（红线为短语匹配，不调 LLM），是 Phase 3 gates.py 的底层能力面。
规范口径与 step_write 自检一致：900-1200 汉字（超 1300 报警）。
"""
from langchain_core.tools import tool

from core.paths import slot_paths
from core.quality import _find_similar_sentences, _load_past_full, count_chinese_chars
from core.redlines import redline_check
from tools._base import envelope, tool_error

WORD_MIN, WORD_MAX, WORD_HARD_MAX = 900, 1200, 1300


@tool
def check_redline(text: str, date: str = "unknown", step_name: str = "tool") -> dict:
    """红线合规检查（纯规则短语匹配，零 LLM 成本）。critical/high 级命中即 should_block=True。

    Args:
        text: 待检文本（或文稿全文）
        date: 关联日期（日志归档用）
        step_name: 调用环节名（日志归档用）
    """
    try:
        rl = redline_check(text, step=step_name, date=date, file="tool_check", auto_log=False)
        return envelope("ok" if not rl["should_block"] else "error",
                        f"{len(rl['hits'])} 条命中" + ("（含阻断级）" if rl["should_block"] else ""),
                        hits=[{"level": h.get("level"), "matched_text": h.get("matched_text"),
                               "message": h.get("message")} for h in rl["hits"]],
                        should_block=rl["should_block"],
                        block_reason=rl.get("block_reason", ""))
    except Exception as e:
        return tool_error(e)


@tool
def check_wordcount(date: str, slot: str | None = None, version: str = "v1") -> dict:
    """字数检查（汉字口径）：合格区间 900-1200，<900 不足，>1300 超限。

    Args:
        date: 目标日期
        slot: 文稿槽位（默认当日活动槽位）
        version: 文稿版本（v1/v2）
    """
    try:
        md_path = slot_paths(date, slot)[version]
        if not md_path.exists():
            return envelope("error", f"文稿不存在: {md_path.name}，先写稿")
        content = md_path.read_text(encoding="utf-8")
        wc = count_chinese_chars(content)
        if wc < WORD_MIN:
            verdict = "字数不足"
        elif wc > WORD_HARD_MAX:
            verdict = "字数超限"
        else:
            verdict = "合格"
        return envelope("ok" if verdict == "合格" else "error",
                        f"{verdict}（{wc} 汉字，标准 {WORD_MIN}-{WORD_MAX}）",
                        word_count=wc, verdict=verdict, draft_path=str(md_path))
    except Exception as e:
        return tool_error(e)


@tool
def check_similarity(date: str, text: str | None = None, slot: str | None = None,
                     version: str = "v1") -> dict:
    """反重复检查：当前稿 vs 历史稿（同系列优先，4 篇内），3-gram Jaccard + 共现短语双信号。
    相似度 >= 0.45 判雷同。text 传入时检查该文本，否则读当日文稿。

    Args:
        date: 目标日期
        text: 待检文本（空则读 date 当日文稿）
        slot: 文稿槽位
        version: 文稿版本
    """
    try:
        if text is None:
            md_path = slot_paths(date, slot)[version]
            if not md_path.exists():
                return envelope("error", f"文稿不存在: {md_path.name}，先写稿")
            text = md_path.read_text(encoding="utf-8")
        past = _load_past_full(date)
        if not past:
            return envelope("ok", "无历史稿可比（首次写作）", similar_sentences=[])
        hits = _find_similar_sentences(text, past)
        return envelope("ok" if not hits else "error",
                        f"{len(hits)} 句与历史稿雷同" if hits else "无雷同句",
                        similar_sentences=[{"current": c[:60], "past": p[:60],
                                            "similarity": round(s, 2)} for c, p, s in hits])
    except Exception as e:
        return tool_error(e)
