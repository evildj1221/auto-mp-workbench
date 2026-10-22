#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/gates.py · 确定性质量门禁（写稿后强制三检）。

门禁不是 planner 可选项：红线短语 / 字数区间 / 相似句三检由 executor 在
write_draft 之后自动追加，结论注入 ToolMessage——这是混合自主的安全边界。
口径与 quality_tools 完全一致（同一套 core 检查函数）。
"""
from core.paths import slot_paths
from core.quality import _find_similar_sentences, _load_past_full, count_chinese_chars
from core.redlines import redline_check

WORD_MIN, WORD_MAX, WORD_HARD_MAX = 900, 1200, 1300
SIM_THRESHOLD = 0.45


def run_gates(date: str, slot=None, version: str = "v1", text: str | None = None) -> dict:
    """跑三检并汇总结论。text 缺省读当日文稿（写稿门禁的标准用法）。

    返回 {passed, verdict, redline, wordcount, similarity}——
    passed=False 时 executor 会把结论注入 planner，由其决定重写或升级人工。
    """
    md_path = slot_paths(date, slot)[version]
    if text is None:
        if not md_path.exists():
            return {"passed": False, "verdict": "文稿不存在（写稿未产出文件）",
                    "redline": {}, "wordcount": {}, "similarity": {}}
        text = md_path.read_text(encoding="utf-8")

    rl = redline_check(text, step="agent_gate", date=date, file=md_path.name, auto_log=False)
    redline = {"should_block": rl["should_block"],
               "hits": [{"level": h.get("level"), "matched_text": h.get("matched_text")}
                        for h in rl["hits"]]}

    wc = count_chinese_chars(text)
    wordcount = {"count": wc, "ok": WORD_MIN <= wc <= WORD_HARD_MAX,
                 "standard": f"{WORD_MIN}-{WORD_MAX}"}

    past = _load_past_full(date)
    hits = _find_similar_sentences(text, past) if past else []
    similarity = {"similar_count": len(hits), "ok": not hits,
                  "samples": [{"current": c[:50], "past": p[:50], "similarity": round(s, 2)}
                              for c, p, s in hits[:5]]}

    problems = []
    if redline["should_block"]:
        problems.append(f"红线阻断（{len(redline['hits'])} 条命中，含 critical/high）")
    if not wordcount["ok"]:
        problems.append(f"字数 {wc} 不在 {WORD_MIN}-{WORD_HARD_MAX} 区间")
    if not similarity["ok"]:
        problems.append(f"与历史稿存在 {len(hits)} 处雷同句（阈值 {SIM_THRESHOLD}）")

    return {"passed": not problems,
            "verdict": "门禁通过" if not problems else "；".join(problems),
            "redline": redline, "wordcount": wordcount, "similarity": similarity}
