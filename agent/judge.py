#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/judge.py · LLM-as-Judge 软门禁（v4.1 A4）。

在确定性硬门禁（红线/字数/相似度，gates.py）之上做五维软质量评审：
共鸣感/结构/调性/独特性/整体。软结论不硬拦截——发布权仍在硬门禁 + 人工关卡；
低于阈值时 executor 注入 rewrite_once 动作，planner 按建议自动重写一轮。

成本控制：只在 write_draft/edit_draft 且硬门禁通过后触发一次（约 ¥0.01/篇）。
阈值与开关在 agent/config.py（soft_gate / soft_gate_threshold）。
"""
from agent.config import AgentRunConfig
from core.paths import slot_paths
from llm.client import call_llm
from llm.prompts import JUDGE_PROMPT

DIMENSIONS = ("resonance", "structure", "tone", "uniqueness", "overall")


def run_judge(date: str, slot=None, version: str = "v1", text: str | None = None,
              threshold: float | None = None) -> dict:
    """软门禁评审。返回 {passed, avg_score, scores, suggestions, verdict}。

    threshold 缺省取 AgentRunConfig().soft_gate_threshold。
    任何失败（LLM 异常/解析失败/文稿缺失）都返回 passed=True 的降级结论——
    软门禁永远不因自身故障拖累主流程。
    """
    if text is None:
        md_path = slot_paths(date, slot)[version]
        if not md_path.exists():
            return {"passed": True, "degraded": True, "verdict": "skip",
                    "reason": "文稿不存在，软门禁跳过"}
        text = md_path.read_text(encoding="utf-8")
    if threshold is None:
        threshold = AgentRunConfig().soft_gate_threshold
    try:
        raw = call_llm(JUDGE_PROMPT.replace("{draft}", text[:6000]),
                       temperature=0, max_tokens=800, module="judge")
    except Exception as e:
        return {"passed": True, "degraded": True, "verdict": "skip",
                "reason": f"评审调用失败: {type(e).__name__}"}

    res = raw if isinstance(raw, dict) else {}
    scores = {}
    for d in DIMENSIONS:
        try:
            v = float((res.get("scores") or {}).get(d, 0) or 0)
        except (ValueError, TypeError):
            v = 0.0
        scores[d] = round(v, 1)
    if not any(scores.values()):
        return {"passed": True, "degraded": True, "verdict": "skip",
                "reason": "评审输出无可解析分数"}
    avg = round(sum(scores.values()) / len(DIMENSIONS), 2)
    suggestions = [str(s)[:80] for s in (res.get("suggestions") or [])[:3]]
    verdict = str(res.get("verdict", "")).strip()
    passed = avg >= threshold and verdict != "needs_rewrite"
    return {"passed": passed, "avg_score": avg, "scores": scores,
            "reasons": {k: str(v)[:30] for k, v in (res.get("reasons") or {}).items()
                        if k in DIMENSIONS},
            "suggestions": suggestions, "verdict": verdict or ("pass" if passed else "needs_rewrite")}
