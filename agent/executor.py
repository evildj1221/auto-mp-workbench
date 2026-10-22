#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/executor.py · 执行节点（工具调用 + 预算护栏 + 门禁注入 + trace）。

act 节点执行 planner 选定的工具：
  1. 执行前查预算：steps_used >= max_steps 或 cost >= max_cost → paused_budget
     （回灌 ToolMessage 说明未执行原因，图终止；用户提额后可续跑）
  2. 经 tools.registry.run_tool 统一入口（超时包装 + 调用留痕）
  3. write_draft / edit_draft 之后自动追加三项门禁（gates.py），结论注入
     ToolMessage——planner 不能跳过质检
  4. 关键产物路径写入 state.artifacts（interrupt 载荷与 finish 报告用）
  5. 每步追加 data/agent_runs/{date}/trace.jsonl
"""
import json
from datetime import datetime

from langchain_core.messages import AIMessage, ToolMessage

from agent.config import AgentRunConfig
from agent.gates import run_gates
from agent.judge import run_judge
from tools.registry import run_tool

# 这些工具执行后自动跑门禁（写稿通道）
_GATE_TOOLS = ("write_draft", "edit_draft")

# 产物路径映射：工具名 → artifacts 键（供 interrupt 载荷 / finish 报告）
_ARTIFACT_KEYS = {
    "collect_candidates": "candidates",
    "score_candidates": "top5",
    "write_draft": "draft",
    "edit_draft": "draft",
    "generate_titles": "titles",
    "export_mp_html": "export",
    "sync_feishu_doc": "sync",
}


def _trace_path(date):
    from core.paths import DATA_DIR
    p = DATA_DIR / "agent_runs" / date / "trace.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def append_trace(date, entry):
    try:
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), **entry}
        with _trace_path(date).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # trace 失败不影响主流程


def _today_llm_cost(date: str) -> float:
    """当日 llm_cost.jsonl 总花费（含工具侧写稿/导出/审校——它们走 provider
    独立记账不回灌 state）。护栏口径用真实流水，而不是只看 planner 计数。"""
    from llm.provider import COST_LOG
    if not COST_LOG.exists():
        return 0.0
    total = 0.0
    try:
        for line in COST_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            if date not in line:
                continue  # 快速预筛，避免逐行 json.loads
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("date") == date:
                total += float(e.get("cost_total_cny") or 0)
    except OSError:
        return 0.0
    return total


def make_act_node():
    def act_node(state):
        cfg = AgentRunConfig.from_dict(state["config"])
        date = state["date"]
        last_ai = next((m for m in reversed(state["messages"])
                        if isinstance(m, AIMessage)), None)
        calls = getattr(last_ai, "tool_calls", None) or []

        # 预算护栏（代码侧边界，LLM 不可突破）。
        # 成本取 max(planner 计数, 当日真实流水)——工具侧花费曾完全不计入，
        # 真实花费可超上限数倍才（或永远不）触发暂停
        real_cost = max(state["cost_cny"], _today_llm_cost(date))
        budget_left = cfg.max_steps - state["steps_used"]
        if budget_left <= 0 or real_cost >= cfg.max_cost_cny:
            reason = (f"步数用尽（{state['steps_used']}/{cfg.max_steps}）"
                      if budget_left <= 0 else
                      f"成本超限（{real_cost:.4f}/{cfg.max_cost_cny} CNY，含工具侧）")
            msgs = [ToolMessage(content=json.dumps({
                "status": "paused_budget", "message": f"预算护栏触发：{reason}",
                "suggestion": "用户可通过 --raise-budget 提额后继续运行"}, ensure_ascii=False),
                tool_call_id=c["id"], name=c["name"]) for c in calls]
            append_trace(date, {"node": "act", "event": "paused_budget", "reason": reason})
            return {"messages": msgs, "status": "paused_budget"}

        updates = {"messages": [], "steps_used": state["steps_used"],
                   "status": "acting", "artifacts": dict(state.get("artifacts") or {}),
                   "gate_status": state.get("gate_status") or {},
                   "soft_gate": state.get("soft_gate") or {}}

        for call in calls:
            name, cargs = call["name"], call.get("args") or {}
            env = run_tool(name, timeout=cfg.tool_timeout_s, **cargs)
            env.pop("duration_s", None)

            # 写稿通道自动门禁（混合自主的安全边界）
            if name in _GATE_TOOLS:
                version = "v2" if cargs.get("v2_angle") else cargs.get("version", "v1")
                gates = run_gates(date, slot=cargs.get("slot"), version=version)
                env["gate"] = gates
                updates["gate_status"] = gates
                append_trace(date, {"node": "act", "tool": name,
                                    "gate_passed": gates["passed"],
                                    "gate_verdict": gates["verdict"]})
                # 软门禁（v4.1 A4）：硬门禁通过后评审一次，低分建议重写一轮（不硬拦截）
                if cfg.soft_gate and gates["passed"]:
                    prev_soft = dict(updates.get("soft_gate") or {})
                    soft = run_judge(date, slot=cargs.get("slot"), version=version,
                                     threshold=cfg.soft_gate_threshold)
                    # 跟随 rewrite_once 指令的 edit_draft 才算消耗软门禁重写机会
                    soft["rewrite_used"] = bool(prev_soft.get("rewrite_used") or (
                        name == "edit_draft" and prev_soft.get("action") == "rewrite_once"))
                    if not soft["passed"] and not soft.get("degraded"):
                        soft["action"] = "pass" if soft["rewrite_used"] else "rewrite_once"
                    env["soft_gate"] = soft
                    updates["soft_gate"] = soft
                    append_trace(date, {"node": "act", "tool": name,
                                        "soft_gate_passed": soft["passed"],
                                        "soft_gate_avg": soft.get("avg_score"),
                                        "soft_gate_action": soft.get("action", "")})

            key = _ARTIFACT_KEYS.get(name)
            if key and env.get("status") in ("ok", "skipped"):
                updates["artifacts"][key] = _brief_artifact(env)

            updates["messages"].append(ToolMessage(
                content=json.dumps(env, ensure_ascii=False),
                tool_call_id=call["id"], name=name))
            updates["steps_used"] += 1
            entry = {"node": "act", "tool": name,
                     "status": env.get("status", "?"),
                     "message": (env.get("message") or "")[:200]}
            if env.get("error_category"):
                entry["category"] = env["error_category"]
            append_trace(date, entry)
        return updates

    return act_node


def _brief_artifact(env):
    """从工具信封提取 interrupt 载荷 / 报告需要的产物摘要。"""
    out = {"status": env.get("status")}
    for k in ("candidates", "top5", "titles", "draft_path", "version", "word_count",
              "title", "mp_path", "checks_summary", "redline_blocked", "media_id",
              "cover"):
        if k in env:
            out[k] = env[k]
    return out
