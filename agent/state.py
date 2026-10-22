#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/state.py · AgentState（LangGraph 状态定义）。

状态经 SqliteSaver checkpoint 持久化（data/agent_runs.db），崩溃后可恢复。
config 以 dict 形式入 state（checkpoint 序列化友好），节点内转 AgentRunConfig。
"""
from typing import Annotated, Optional, TypedDict

from langgraph.graph.message import add_messages

# 状态机：planning → acting → waiting_topic → waiting_publish → done / blocked / paused_budget
STATUSES = ("planning", "acting", "waiting_topic", "waiting_publish",
            "done", "blocked", "paused_budget")


class AgentState(TypedDict):
    date: str
    goal: str
    messages: Annotated[list, add_messages]          # planner 对话史（含 tool_calls/结果）
    config: dict                                     # AgentRunConfig.to_dict()
    artifacts: dict                                  # candidates/top5/selection/draft/published 路径
    gate_status: dict                                # 最近一次写稿门禁结论
    soft_gate: dict                                  # 最近一次软门禁结论（含 rewrite_used 标记）
    pending_approval: Optional[dict]                 # interrupt 暂停时的审批载荷
    steps_used: int                                  # 已执行工具调用数
    cost_cny: float                                  # planner LLM 成本累计（估算口径）
    status: str
    error: Optional[dict]                            # {category, reason, suggestion}


def initial_state(date: str, goal: str, config: dict) -> AgentState:
    return {
        "date": date,
        "goal": goal or "完成今日公众号内容生产（选题 → 写稿 → 门禁 → 排版导出）",
        "messages": [],
        "config": config,
        "artifacts": {},
        "gate_status": {},
        "soft_gate": {},
        "pending_approval": None,
        "steps_used": 0,
        "cost_cny": 0.0,
        "status": "planning",
        "error": None,
    }
