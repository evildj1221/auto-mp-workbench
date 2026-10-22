#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/graph.py · LangGraph 图组装。

init → plan ⇄ act →（gate 注入回灌）→ plan …
plan ── finish_run/escalate ──→ reflect → finish → END
act ── score 后未选题 ──→ topic_confirm（interrupt）→ plan
act ── export 成功 ──→ publish_confirm（interrupt）→ plan / reflect → finish
act ── 预算超限 ──→ END（paused_budget，提额后可续跑）

reflect（v4.1 A1）：finish 前 LLM 复盘本次运行，经验落 data/memory/episodes.jsonl，
planner 下次运行的系统提示词自动注入近 N 条经验。

checkpoint：SqliteSaver 落 data/agent_runs.db，thread_id = run-{date}。
进程崩溃/重启后从最后一个 checkpoint 恢复，已花的钱和产物不浪费。
"""
import json
from datetime import datetime
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from agent.config import AgentRunConfig
from agent.executor import append_trace, make_act_node
from agent.interrupts import publish_confirm_node, topic_confirm_node
from agent.planner import (META_TOOLS, _last_ai, _tool_calls_of,
                           make_plan_node, route_after_act, route_after_plan,
                           route_after_publish_confirm)
from agent.reflection import make_reflect_node, recent_lessons
from agent.state import AgentState, initial_state
from tools.registry import get_tools

_STEP_LABELS = {
    "collect": "选题生成", "score": "评分", "feedback": "用户评论",
    "analyze": "反馈分析", "choose": "用户决策", "write_v1": "写稿v1", "write_v2": "写稿v2",
    "sync": "云文档归档", "format": "HTML排版", "learn": "反馈学习",
}


def _directives_block():
    """M3 选题风向：生效中的用户显式指令注入 planner 系统提示词（优先级最高）。"""
    from core.directives import banned_keywords, list_directives
    ds = list_directives()
    if not ds:
        return ""
    scope_label = {"today": "今日", "week": "本周", "long": "长期"}
    lines = ["## 选题风向（用户显式指令，优先级最高，生成选题必须遵循）"]
    for d in ds:
        kind = "禁止" if d.get("negative") else "正向"
        hits = d.get("hits_last_batch")
        hit_txt = (f"（上批命中 {hits['hits']}/{hits['total']}）" if hits else "")
        lines.append(f"- [{scope_label.get(d.get('scope'), '?')}·{kind}] {d['text']}{hit_txt}")
    banned = banned_keywords()
    if banned:
        lines.append(f"- 禁止关键词（候选中不得出现）：{'、'.join(banned)}")
    lines.append("- 生成新一批选题前先调用 list_topic_directives 复核；"
                 "用户表达新方向意图时可用 set_topic_directive 固化为风向")
    return "\n".join(lines) + "\n\n"


def _system_prompt(state, steps, pub, choice) -> str:
    cfg = AgentRunConfig.from_dict(state["config"])
    done = [k for k in _STEP_LABELS if steps.get(k)]
    todo = [k for k in _STEP_LABELS if not steps.get(k)]
    chosen = choice.get("topic") if (choice or {}).get("user_choice") else ""
    pub_line = (f"- 已发布：{pub.get('title', '?')}（{pub.get('word_count', '?')} 字）\n"
                if pub else "")
    lessons = recent_lessons(6)
    lessons_block = ("## 历史经验（近几日运行复盘，供决策参考）\n"
                     + "\n".join(f"- {l}" for l in lessons) + "\n"
                     if lessons else "")
    return f"""你是「创作工作台」工作台的内容主编 Agent，负责今日公众号内容的自主生产。

## 当日状态（{state['date']}）
- 流水线：{len(done)}/10 步完成，未完成：{"、".join(_STEP_LABELS[k] for k in todo) or "无"}
- 已选选题：{chosen or "未选（需先评分并等用户确认）"}
{pub_line}
## 今日目标
{state['goal']}

{_directives_block()}{lessons_block}## 策略约束（必须遵守）
1. 每轮先看状态缺什么补什么；工具幂等，status=skipped 是正常跳过，不要重试
2. 标准顺序：collect_candidates → score_candidates →【等用户选题确认】→
   write_draft → generate_titles → generate_cover_image（头图，未配置时 skipped 属正常）
   → export_mp_html →【等用户发布确认】→ finish_run
3. 写稿后系统自动过门禁（红线/字数/相似度），gate.passed=false 时必须处置：
   - 红线阻断 → edit_draft 重写违规内容或换角度；最多 2 轮，仍不过 → escalate_to_human
   - 字数不在 900-1300 → edit_draft 调整篇幅；与历史雷同 → 换切入角度重写
4. 软门禁（soft_gate）：硬门禁通过但 soft_gate.action=rewrite_once 时，按
   soft_gate.suggestions 用 edit_draft 重写一轮；重写后 action=pass 即接受现状
   继续（软门禁只建议，不阻断发布）
5. critical/high 级红线命中永远不能放行
6. 工具 status=error：读 message/log 判断，重试一次或换路径；连续失败 → escalate_to_human
7. 全部完成后调用 finish_run 汇报（做了什么、产物在哪、门禁结论、遗留事项）

## 剩余预算
- 步数 {cfg.max_steps - state['steps_used']}/{cfg.max_steps} ·
  planner 成本 {state['cost_cny']:.4f}/{cfg.max_cost_cny} CNY
- 接近上限时优先保核心链路（写稿 + 导出），舍弃非必要步骤"""


def init_node(state):
    """注入系统提示词与当日状态（重入时幂等：已有 SystemMessage 则跳过）。"""
    from tools.state_tools import _status_map
    has_system = any(isinstance(m, SystemMessage) for m in state["messages"])
    if has_system:  # 提额续跑等重入场景，不重复注入
        return {"status": "planning"}
    steps, pub, choice = _status_map(state["date"])
    append_trace(state["date"], {"node": "init", "goal": state["goal"],
                                 "steps_done": sum(1 for v in steps.values() if v)})
    return {"messages": [
        SystemMessage(content=_system_prompt(state, steps, pub, choice)),
        HumanMessage(content=f"开始执行今日目标：{state['goal']}"),
    ], "status": "planning"}


def _finish_report_path(date):
    from core.paths import DATA_DIR
    p = DATA_DIR / "agent_runs" / date / "report.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class _LazyChatModel:
    """延迟解析真实模型：图构建期零配置依赖。

    get_state / 审批恢复等纯读操作不需要 LLM_API_KEY；首次 plan 调用时才
    解析（生产 get_run_state 轮询、UI 状态卡都走这条零依赖路径）。
    """

    def __init__(self, temperature=0.2):
        self.temperature = temperature
        self._bound = None

    def bind_tools(self, tools):
        return _LazyBound(self, tools)


class _LazyBound:
    def __init__(self, lazy, tools):
        self._lazy = lazy
        self._tools = tools

    def invoke(self, messages, **kwargs):
        if self._lazy._bound is None:
            from llm.langchain_model import get_chat_model
            self._lazy._bound = get_chat_model(
                temperature=self._lazy.temperature).bind_tools(self._tools)
        return self._lazy._bound.invoke(messages, **kwargs)


def finish_node(state):
    """收尾：判定终态（done/blocked），写 report.json。"""
    last_ai = _last_ai(state)
    calls = _tool_calls_of(last_ai) if last_ai else []
    args = {c["name"]: (c.get("args") or {}) for c in calls}
    status, error, text = "done", None, ""
    if "escalate_to_human" in args:
        status = "blocked"
        e = args["escalate_to_human"]
        error = {"category": "ESCALATED", "reason": e.get("reason", ""),
                 "question": e.get("question", "")}
        text = f"已升级人工：{e.get('reason', '')}"
    elif "finish_run" in args:
        text = args["finish_run"].get("report", "")
    elif last_ai is not None:
        text = str(last_ai.content or "")
    if not text:
        text = state["status"]

    report = {
        "date": state["date"], "goal": state["goal"], "status": status,
        "steps_used": state["steps_used"],
        "cost_cny_planner": state["cost_cny"],
        "artifacts": state.get("artifacts") or {},
        "gate_status": state.get("gate_status") or {},
        "soft_gate": state.get("soft_gate") or {},
        "report": text, "error": error,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        _finish_report_path(state["date"]).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    append_trace(state["date"], {"node": "finish", "status": status,
                                 "steps_used": state["steps_used"]})
    return {"status": status, "error": error, "pending_approval": None}


def build_graph(model=None, checkpointer=None, config=None):
    """构建 agent 图。model 缺省延迟解析真实模型（测试注入 ScriptedModel）。"""
    cfg = AgentRunConfig.from_dict(config)
    if model is None:
        model = _LazyChatModel(temperature=cfg.temperature)
    bound = model.bind_tools(get_tools() + META_TOOLS)

    g = StateGraph(AgentState)
    g.add_node("init", init_node)
    g.add_node("plan", make_plan_node(bound))
    g.add_node("act", make_act_node())
    g.add_node("topic_confirm", topic_confirm_node)
    g.add_node("publish_confirm", publish_confirm_node)
    g.add_node("reflect", make_reflect_node())
    g.add_node("finish", finish_node)

    g.add_edge(START, "init")
    g.add_edge("init", "plan")
    # finish 路径统一先过 reflect（经验沉淀），再到 finish 终结
    g.add_conditional_edges("plan", route_after_plan,
                            {"act": "act", "finish": "reflect"})
    g.add_conditional_edges("act", route_after_act,
                            {"plan": "plan", "topic_confirm": "topic_confirm",
                             "publish_confirm": "publish_confirm", END: END})
    g.add_edge("topic_confirm", "plan")
    g.add_conditional_edges("publish_confirm", route_after_publish_confirm,
                            {"plan": "plan", "finish": "reflect"})
    g.add_edge("reflect", "finish")
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer)


__all__ = ["build_graph", "initial_state"]
