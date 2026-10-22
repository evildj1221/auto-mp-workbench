#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/planner.py · 规划节点（LLM 决策）+ 元工具 + 路由。

planner 拥有核心自主权：调哪个工具、什么顺序、是否重写。
两个元工具显式化终止语义（便于调试与路由）：finish_run / escalate_to_human。
系统提示词注入：角色 + 当日状态 + 策略约束 + 剩余预算（init 节点组装）。
"""
import json

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END

from agent.config import AgentRunConfig
from agent.executor import append_trace
from llm.provider import DeepSeekProvider

META_TOOLS = []


@tool
def finish_run(report: str) -> dict:
    """宣布本次运行完成并汇报。所有目标已达成（或确认无可继续事项）时调用。

    Args:
        report: 总结汇报（做了什么、产物在哪、门禁结论、遗留事项）
    """
    return {"status": "ok", "message": report}


@tool
def escalate_to_human(reason: str, question: str = "") -> dict:
    """升级人工处理。门禁反复不过 / 需要用户判断 / 工具连续失败时调用。

    Args:
        reason: 升级原因（具体描述卡在哪里）
        question: 需要用户回答的问题（可选）
    """
    return {"status": "ok", "message": f"已升级人工: {reason}"}


META_TOOLS = [finish_run, escalate_to_human]


def estimate_cost_cny(msg) -> float:
    """从 AIMessage.usage_metadata 估算成本（DeepSeek 费率）。

    只统计 planner 侧 token；工具内部的 step 级 LLM 成本由 provider 记账
    （llm_cost.jsonl），finish 报告会汇总两本账。
    """
    um = getattr(msg, "usage_metadata", None) or {}
    pt = (um.get("input_tokens") or 0) / 1000.0
    ct = (um.get("output_tokens") or 0) / 1000.0
    return round(pt * DeepSeekProvider.INPUT_RATE + ct * DeepSeekProvider.OUTPUT_RATE, 6)


def _reasoning_text(msg) -> str:
    """从 AIMessage 提取工具调用前的自然语言推理（content 可能是 str 或分块 list）。"""
    c = getattr(msg, "content", "")
    if isinstance(c, str):
        return c.strip()
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict)).strip()
    return ""


def make_plan_node(bound_model):
    """plan 节点工厂：bound_model 已 bind_tools(23 工具 + 2 元工具)。"""

    def plan_node(state):
        response = bound_model.invoke(state["messages"])
        updates = {"messages": [response], "status": "planning"}
        if isinstance(response, AIMessage):
            updates["cost_cny"] = round(state["cost_cny"] + estimate_cost_cny(response), 6)
        # 决策理由入 trace：UI 时间线「它为什么这么做」的数据源
        append_trace(state["date"], {
            "node": "plan",
            "reasoning": _reasoning_text(response)[:300],
            "tools": [c.get("name", "?") for c in (getattr(response, "tool_calls", None) or [])],
            "steps_used": state["steps_used"],
            "cost_cny": updates.get("cost_cny", state["cost_cny"]),
        })
        return updates

    return plan_node


def _last_ai(state) -> AIMessage | None:
    for m in reversed(state["messages"]):
        if isinstance(m, AIMessage):
            return m
    return None


def _tool_calls_of(ai_msg):
    return getattr(ai_msg, "tool_calls", None) or []


def route_after_plan(state) -> str:
    """plan 之后：元工具走 finish，普通工具走 act，纯文本视为汇报收尾。"""
    last = state["messages"][-1] if state["messages"] else None
    calls = _tool_calls_of(last) if isinstance(last, AIMessage) else []
    if not calls:
        return "finish"  # 无工具调用的自然语言回复 = 汇报，收尾
    names = {c["name"] for c in calls}
    if "escalate_to_human" in names or "finish_run" in names:
        return "finish"
    return "act"


def route_after_act(state) -> str:
    """act 之后：预算暂停即止；评分完且未选题 → 选题确认；导出成功 → 发布确认。"""
    if state["status"] == "paused_budget":
        return END
    cfg = AgentRunConfig.from_dict(state["config"])
    tool_msgs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    if not tool_msgs:
        return "plan"
    tm = tool_msgs[-1]
    try:
        env = json.loads(tm.content)
    except (ValueError, TypeError):
        env = {}
    ok = env.get("status") in ("ok", "skipped")

    if tm.name == "score_candidates" and ok and cfg.topic_confirm:
        from tools.state_tools import _status_map
        steps, _pub, _choice = _status_map(state["date"])
        if not steps["choose"]:  # 用户尚未决策 → 必须人工关卡
            return "topic_confirm"
    if tm.name == "export_mp_html" and ok and cfg.publish_confirm:
        return "publish_confirm"
    return "plan"


def route_after_publish_confirm(state) -> str:
    return "finish" if state["status"] == "done" else "plan"
