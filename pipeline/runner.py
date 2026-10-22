#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline/runner.py · Agent 运行器（CLI 与 UI 的唯一入口，保证两通道行为一致）。

start_run / resume_run / raise_budget / cancel_run / get_run_state。
checkpoint 落 data/agent_runs.db（thread_id = run-{date}）：
进程崩溃或工作台重启后，agent 从最后一个 checkpoint 恢复，已花的钱和产物不浪费。
"""
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from agent.config import AgentRunConfig
from agent.graph import build_graph, initial_state

_Savers = {}   # db_path → SqliteSaver（多 db 并存：生产库 + 测试临时库）
_Graphs = {}   # (db_path, id(model)) → compiled graph


def _default_db() -> Path:
    from core.paths import DATA_DIR
    p = DATA_DIR / "agent_runs.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _saver(db_path: Path) -> SqliteSaver:
    key = str(db_path)
    if key not in _Savers:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        _Savers[key] = SqliteSaver(conn)
    return _Savers[key]


def _graph(db_path: Path, model=None):
    key = (str(db_path), id(model))
    if key not in _Graphs:
        _Graphs[key] = build_graph(model=model, checkpointer=_saver(db_path))
    return _Graphs[key]


def _thread_id(date: str) -> str:
    return f"run-{date}"


def _summarize(result: dict, date: str) -> dict:
    """invoke 结果 → 运行摘要（interrupt 优先于状态字段，paused 单列）。"""
    out = {"date": date,
           "status": result.get("status", "unknown"),
           "steps_used": result.get("steps_used", 0),
           "cost_cny": result.get("cost_cny", 0.0),
           "pending_approval": None}
    interrupts = result.get("__interrupt__") or []
    if interrupts:
        payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        ptype = payload.get("type", "")
        out["status"] = "waiting_topic" if ptype == "topic_confirm" else "waiting_publish"
        out["pending_approval"] = payload
    return out


def start_run(date: str, goal: str = "", config: dict | None = None,
              model=None, db_path=None) -> dict:
    """启动（或基于当日 checkpoint 续跑）一次 agent 运行，跑到 interrupt 或结束。
    续跑保留 checkpoint 里的 steps_used/cost——预算暂停后再点运行不会清零计数
    （旧版直接喂 initial_state，等于免费刷新预算，护栏形同虚设）。"""
    graph = _graph(db_path or _default_db(), model)
    cfg = AgentRunConfig.from_dict(config)
    cfg_key = {"configurable": {"thread_id": _thread_id(date)}}
    st = initial_state(date, goal, cfg.to_dict())
    try:
        snap = graph.get_state(cfg_key)
        if snap.values:
            st["steps_used"] = snap.values.get("steps_used", 0)
            st["cost_cny"] = snap.values.get("cost_cny", 0.0)
    except Exception:
        pass  # 查不到状态按全新跑（不阻塞启动）
    result = graph.invoke(st, cfg_key)
    return _summarize(result, date)


def resume_run(date: str, payload: dict, model=None, db_path=None) -> dict:
    """审批恢复：payload 见 agent/interrupts.py 顶部约定。"""
    graph = _graph(db_path or _default_db(), model)
    result = graph.invoke(Command(resume=payload),
                          {"configurable": {"thread_id": _thread_id(date)}})
    return _summarize(result, date)


def raise_budget(date: str, extra_steps: int = 0, extra_cost_cny: float = 0.0,
                 note: str = "", model=None, db_path=None) -> dict:
    """paused_budget 后提额续跑：更新 config 并注入提示消息。"""
    graph = _graph(db_path or _default_db(), model)
    cfg_key = {"configurable": {"thread_id": _thread_id(date)}}
    snap = graph.get_state(cfg_key)
    if not snap.values:
        return {"status": "error", "message": f"{date} 无运行记录，先 start_run"}
    cfg = AgentRunConfig.from_dict(snap.values.get("config"))
    cfg = cfg.with_overrides(
        max_steps=cfg.max_steps + max(0, extra_steps),
        max_cost_cny=round(cfg.max_cost_cny + max(0.0, extra_cost_cny), 4))
    result = graph.invoke(
        {"config": cfg.to_dict(),
         "messages": [HumanMessage(
             f"【预算已提升】步数上限 {cfg.max_steps}，成本上限 {cfg.max_cost_cny} CNY。"
             + (f"用户备注：{note}" if note else "") + " 请继续未完成的任务。")]},
        cfg_key)
    return _summarize(result, date)


def get_run_state(date: str, model=None, db_path=None) -> dict:
    """查询运行状态（含等待审批时的载荷）。"""
    graph = _graph(db_path or _default_db(), model)
    cfg_key = {"configurable": {"thread_id": _thread_id(date)}}
    snap = graph.get_state(cfg_key)
    if not snap.values:
        return {"date": date, "status": "none", "message": "当日无 agent 运行记录"}
    values = snap.values
    out = {"date": date, "status": values.get("status", "unknown"),
           "steps_used": values.get("steps_used", 0),
           "cost_cny": values.get("cost_cny", 0.0),
           "goal": values.get("goal", ""), "pending_approval": None,
           "is_active": bool(snap.next)}
    for t in snap.tasks:
        for intr in (t.interrupts or []):
            payload = intr.value if hasattr(intr, "value") else intr
            ptype = payload.get("type", "")
            out["status"] = "waiting_topic" if ptype == "topic_confirm" else "waiting_publish"
            out["pending_approval"] = payload
    return out


def cancel_run(date: str, db_path=None) -> dict:
    """取消运行：写取消标记（下次 start_run 前可见；运行中无法强停，同 taskrunner 约定）。"""
    from core.paths import DATA_DIR
    marker = DATA_DIR / "agent_runs" / date / "cancelled.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(
        {"date": date, "cancelled_at": datetime.now().isoformat(timespec="seconds")},
        ensure_ascii=False), encoding="utf-8")
    return {"date": date, "status": "cancelled",
            "message": "已标记取消。注意：正在执行中的工具调用无法中断，"
                       "重新 start_run 会基于 checkpoint 覆盖续跑。"}


_TRACE_TAIL_BYTES = 512 * 1024  # 只读文件尾部 512KB，trace 再大快照开销也恒定


def read_trace(date: str, limit: int = 100) -> list:
    """读当日 agent trace 尾部（UI 时间线数据源）。

    每 2s 的 SSE 快照都会调本函数——seek 到尾部只读最后一个窗口，
    避免随 trace 增长全量读文件（曾经的卡顿热点之一）。
    """
    from core.paths import DATA_DIR
    p = DATA_DIR / "agent_runs" / date / "trace.jsonl"
    if not p.exists():
        return []
    try:
        with p.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - _TRACE_TAIL_BYTES))
            blob = f.read()
    except OSError:
        return []
    out = []
    for line in blob.decode("utf-8", errors="replace").splitlines()[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
