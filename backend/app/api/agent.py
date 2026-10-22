#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/app/api/agent.py · Agent 中枢（原 workbench/agent_api.py 的 JSON 化 + SSE）。

路由：
  GET  /api/agent/{date}         状态快照 JSON（状态 + trace 数据，SPA 自行渲染）
  GET  /api/agent/{date}/stream  SSE 实时流（状态/trace 变化推送，断线可重连续轮询兜底）
  POST /api/agent/start          启动或基于当日 checkpoint 续跑
  POST /api/agent/approve        人工关卡审批（选题点选 / 发布批准或驳回）
  POST /api/agent/budget         预算提额续跑（paused_budget 后）
  POST /api/agent/cancel         标记取消

设计约定（承旧版）：
- 同一日期同一时刻只有一个 agent 线程（checkpoint 写者唯一），重入返回 busy；
  工作台重启后内存标记清零。
- runner 导入失败（langgraph 缺失）不拖垮整个后端，状态返回 error 提示。
"""
import asyncio
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

try:
    from pipeline import runner
except Exception:  # langgraph 依赖缺失时不阻断工作台其余功能
    runner = None

from core import errors as errcls
from core import logging as clog
from core.paths import DATA_DIR

from .. import taskrunner
from ..common import norm_date, wb_log
from ..schemas import AgentApproveReq, AgentBudgetReq, AgentStartReq
from . import envelope, fail

router = APIRouter(prefix="/api/agent", tags=["agent"])

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 状态元数据：label（中文标签）/ tone（前端配色语义）/ hint（下一步提示）
STATUS_META = {
    "none":            {"label": "未运行",      "tone": "idle", "hint": "今天还没让 Agent 干活，点右侧按钮开始"},
    "planning":        {"label": "规划中",      "tone": "run",  "hint": "Agent 正在思考下一步做什么"},
    "acting":          {"label": "执行中",      "tone": "run",  "hint": "Agent 正在调用工具干活"},
    "waiting_topic":   {"label": "等你选题",    "tone": "wait", "hint": "Top5 已就绪，在下方审批卡点选"},
    "waiting_publish": {"label": "等你批准发布", "tone": "wait", "hint": "终稿已导出，在下方审批卡确认"},
    "done":            {"label": "已完成",      "tone": "ok",   "hint": "今日目标完成，产物见时间线与档案库"},
    "blocked":         {"label": "已升级人工",  "tone": "err",  "hint": "Agent 卡住了，看时间线定位后介入处理"},
    "paused_budget":   {"label": "预算暂停",    "tone": "err",  "hint": "步数/成本到上限，可提额后继续"},
    "cancelled":       {"label": "已取消",      "tone": "idle", "hint": "已标记取消，可随时重新启动"},
    "interrupted":     {"label": "已中断",      "tone": "err",  "hint": "上次运行未完成（服务重启/异常退出），点「一键运行」从断点续跑"},
    "error":           {"label": "出错",        "tone": "err",  "hint": "运行出错，按分类建议处理，详情见 data/workbench_error.log"},
}
_ERROR_META = STATUS_META["error"]

# trace 节点 → 中文名（时间线渲染用）
NODE_META = {
    "init": "初始化",
    "plan": "规划",
    "act": "执行",
    "topic_confirm": "选题关卡",
    "publish_confirm": "发布关卡",
    "finish": "收尾",
}

DEFAULT_GOAL = "完成今日公众号内容生产：选题评分 → 写稿 → 标题 → 导出 → 发布准备"

_active_lock = threading.Lock()
_active = {}  # date → tid（运行中的 agent 线程）

# 快照/状态进程内缓存：侧边栏轮询 + SSE 循环 + 页面首载全部打在
# 「SQLite checkpoint 反序列化 + trace/error 全量文件读」上，是卡顿主因。
# TTL 内的重复请求直接复用结果；动作路由（start/approve/cancel）会主动失效。
_SNAP_TTL = 1.5
_STATE_TTL = 1.5
_snap_cache: dict = {}   # date → (monotonic, snapshot)
_state_cache: dict = {}  # date → (monotonic, state)


def _cache_get(cache: dict, key, ttl):
    hit = cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    return None


def bust_cache(date: str):
    """动作后立即失效缓存，让下一次快照反映最新状态。"""
    _snap_cache.pop(date, None)
    _state_cache.pop(date, None)


def _date_of(raw: str | None) -> str:
    d = (raw or "").strip()
    return d if _DATE_RE.match(d) else datetime.now().strftime("%Y-%m-%d")


def _llm_ready() -> bool:
    return bool(os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY"))


def _cancel_marker(date):
    return DATA_DIR / "agent_runs" / date / "cancelled.json"


def is_running(date: str) -> bool:
    with _active_lock:
        return date in _active


def query_state(date: str, fresh: bool = False) -> dict:
    """安全查询运行状态（驾驶舱/选题页/中枢页共用；异常不炸页面）。

    结果带短 TTL 缓存：get_state 每次都要从 SQLite 反序列化整个 checkpoint，
    是全站轮询最重的一跳；1.5s 内的重复查询直接复用（动作路由会主动失效）。
    fresh=True 跳过缓存——审批等决策点必须读当下状态。
    """
    if not fresh:
        cached = _cache_get(_state_cache, date, _STATE_TTL)
        if cached is not None:
            return dict(cached)
    st = _query_state_uncached(date)
    _state_cache[date] = (time.monotonic(), dict(st))
    return st


def _query_state_uncached(date: str) -> dict:
    if runner is None:
        return {"date": date, "status": "error",
                "message": "Agent 依赖未安装（langgraph），请用源码模式启动",
                "steps_used": 0, "cost_cny": 0.0, "goal": "",
                "pending_approval": None, "running": False}
    try:
        st = runner.get_run_state(date)
    except Exception as e:
        return {"date": date, "status": "error", "message": f"读取运行状态失败：{e}",
                "steps_used": 0, "cost_cny": 0.0, "goal": "",
                "pending_approval": None, "running": False}
    # 取消标记优先于一切活跃/等待态
    if (st.get("status") in ("planning", "acting", "waiting_topic",
                             "waiting_publish", "paused_budget")
            and _cancel_marker(date).exists()):
        st["status"] = "cancelled"
    # 重启后假运行态修正：checkpoint 停在 planning/acting 但本进程没有对应
    # 线程 → 服务重启/崩溃留下的残影，改标 interrupted，避免 UI 永远「运行中」
    if st.get("status") in ("planning", "acting") and not is_running(date):
        st["status"] = "interrupted"
    st["running"] = is_running(date)
    return st


def read_trace(date, limit=60):
    if runner is None:
        return []
    try:
        return runner.read_trace(date, limit=limit)
    except Exception:
        return []


def _run_guarded(date, fn, kwargs, label):
    """同日重入保护：已有 agent 线程在跑 → None；否则后台线程执行并登记。"""
    def _wrapped(**kw):
        try:
            return fn(**kw)
        finally:
            with _active_lock:
                _active.pop(date, None)

    with _active_lock:
        if date in _active:
            return None
        tid = taskrunner.run_function(_wrapped, kwargs=kwargs, label=label)
        _active[date] = tid
    bust_cache(date)
    return tid


def _latest_error(date):
    """当日最近一条结构化错误（错误态时在状态卡展示七分类建议）。"""
    try:
        return next(iter(clog.read_errors(limit=20, date=date)), None)
    except Exception:
        return None


def agent_snapshot(date: str) -> dict:
    """状态 + trace 的纯数据快照（SSE 与轮询共用）。带 TTL 缓存。"""
    cached = _cache_get(_snap_cache, date, _SNAP_TTL)
    if cached is not None:
        return cached
    st = query_state(date)
    trace = read_trace(date)
    from .settings import load_settings
    snap = {
        "page": "agent", "date": date, "state": st,
        "meta": STATUS_META.get(st.get("status"), _ERROR_META),
        "node_meta": NODE_META, "trace": trace,
        "log_text": _log_text(trace),
        "last_error": _latest_error(date),
        "err_meta": errcls.CATEGORIES,
        "llm_ready": _llm_ready(),
        "agent_ready": runner is not None,
        "default_goal": DEFAULT_GOAL,
        "autonomy": load_settings().get("autonomy", "assisted"),
    }
    _snap_cache[date] = (time.monotonic(), snap)
    return snap


def _log_text(trace) -> str:
    lines = []
    for e in trace[-40:]:
        parts = [str(e.get("ts", ""))[11:19], e.get("node", "?")]
        if e.get("tool"):
            parts.append(e["tool"])
        if e.get("status"):
            parts.append(str(e["status"]))
        if e.get("message"):
            parts.append(str(e["message"])[:80])
        lines.append(" · ".join(p for p in parts if p))
    return "\n".join(lines)


@router.get("/{date}")
def agent_state(date: str):
    return envelope(agent_snapshot(_date_of(date)))


@router.get("/{date}/status")
def agent_state_light(date: str):
    """轻量状态（侧边栏状态灯轮询专用）：只查状态，不读 trace/error 文件。"""
    date = _date_of(date)
    st = query_state(date)
    meta = STATUS_META.get(st.get("status"), _ERROR_META)
    return envelope({"date": date, "status": st.get("status"),
                     "running": bool(st.get("running")),
                     "label": meta["label"], "tone": meta["tone"],
                     "steps_used": st.get("steps_used", 0),
                     "cost_cny": st.get("cost_cny", 0.0)})


@router.get("/{date}/stream")
async def agent_stream(date: str, request: Request):
    """SSE：每 2s 轮询快照，内容变化才推送（事件 changed）；15s 心跳。"""
    date = _date_of(date)
    loop = asyncio.get_running_loop()

    async def snapshot():
        return await loop.run_in_executor(None, agent_snapshot, date)

    async def gen():
        last_hash = ""
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            snap = await snapshot()
            raw = json.dumps({"state": snap["state"], "trace": snap["trace"]},
                             ensure_ascii=False, sort_keys=True, default=str)
            h = hashlib.md5(raw.encode("utf-8")).hexdigest()
            if h != last_hash:
                last_hash = h
                idle = 0
                yield f"event: changed\ndata: {json.dumps(snap, ensure_ascii=False, default=str)}\n\n"
            else:
                idle += 1
                if idle % 8 == 0:  # ~16s 心跳
                    yield ": keepalive\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _runner_guard():
    """四个动作路由共用的前置守卫：runner 不可用即返回失败信封。"""
    if runner is None:
        return fail("unavailable", "Agent 依赖未安装（langgraph）")
    return None


@router.post("/start")
def agent_start(req: AgentStartReq):
    date = _date_of(req.date)
    if (g := _runner_guard()):
        return g
    goal = (req.goal or "").strip() or DEFAULT_GOAL
    marker = _cancel_marker(date)
    if marker.exists():  # 重新启动清除取消标记
        try:
            marker.unlink()
        except OSError:
            pass
    # 档位与预算来自设置页（data/settings.json），随 run config 注入状态机
    from .settings import load_settings
    s = load_settings()
    run_config = {"autonomy": s.get("autonomy", "assisted"),
                  "max_steps": s.get("max_steps", 40),
                  "max_cost_cny": s.get("max_cost_cny", 2.0)}
    tid = _run_guarded(date, runner.start_run,
                       {"date": date, "goal": goal, "config": run_config},
                       label=f"Agent 运行 {date}")
    wb_log("agent_start", date,
           f"启动/续跑 Agent：{goal[:60]}（autonomy={run_config['autonomy']}）")
    if tid is None:
        # busy 时不冒泡（旧版先写「开工」再报忙，对话里留真假两条开工记录）
        return fail("busy", "当日 Agent 已在运行")
    from .chat import notify
    notify(date, "主编开始跑今天的全套活儿（Agent 主图）", persona="chief")
    return envelope({"task_id": tid, "date": date, "goal": goal,
                     "autonomy": run_config["autonomy"]})


@router.post("/approve")
def agent_approve(req: AgentApproveReq):
    date = _date_of(req.date)
    if (g := _runner_guard()):
        return g
    st = query_state(date, fresh=True)
    status = st.get("status")
    note = (req.note or "").strip()
    if status == "waiting_topic":
        payload = {"choice": int(req.choice or 0), "note": note}
        label = f"Agent 选题审批 #{req.choice} ({date})"
        wb_log("agent_approve", date,
               f"选题审批：#{req.choice}" + (f"（备注：{note}）" if note else ""))
    elif status == "waiting_publish":
        approved = bool(req.approved)
        payload = {"approved": approved, "note": note}
        label = f"Agent 发布审批·{'批准' if approved else '驳回'} ({date})"
        wb_log("agent_approve", date,
               f"发布审批：{'批准' if approved else '驳回'}" + (f"（意见：{note}）" if note else ""))
    else:
        return fail("nowait", "当前状态无需审批")
    tid = _run_guarded(date, runner.resume_run,
                       {"date": date, "payload": payload}, label=label)
    if tid is None:
        return fail("busy", "当日 Agent 已在运行")
    return envelope({"task_id": tid, "date": date})


@router.post("/budget")
def agent_budget(req: AgentBudgetReq):
    date = _date_of(req.date)
    if (g := _runner_guard()):
        return g
    extra_steps = max(0, int(req.extra_steps or 10))
    extra_cost = max(0.0, float(req.extra_cost or 1.0))
    note = (req.note or "").strip()
    wb_log("agent_budget", date, f"提额续跑：+{extra_steps} 步 / +{extra_cost} CNY")
    tid = _run_guarded(date, runner.raise_budget,
                       {"date": date, "extra_steps": extra_steps,
                        "extra_cost_cny": extra_cost, "note": note},
                       label=f"Agent 提额续跑 ({date})")
    if tid is None:
        return fail("busy", "当日 Agent 已在运行")
    return envelope({"task_id": tid, "date": date})


@router.post("/cancel")
def agent_cancel(req: AgentStartReq):
    date = _date_of(req.date)
    if (g := _runner_guard()):
        return g
    runner.cancel_run(date)
    bust_cache(date)
    wb_log("agent_cancel", date, "标记取消 Agent 运行")
    return envelope({"cancelled": True, "date": date})
