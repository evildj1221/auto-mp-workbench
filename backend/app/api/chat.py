#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/app/api/chat.py · 中控台对话（猫咪编辑部）。

路由：
  GET  /api/chat/personas    同事栏（花名 + 忙碌状态灯）
  GET  /api/chat/brief       右侧面板今日概要（时节/流水线/成本/节奏）
  GET  /api/chat/{date}      当日对话历史 + 动作状态（前端仅在动作活跃时轮询）
  POST /api/chat             发消息：路由到子 agent → 人设回复（act 意图附带确认卡）
  POST /api/chat/confirm     确认卡：批准执行 / 取消
  POST /api/chat/choose      Top5 卡点选选题（对话里拍板 Step 3.7）

设计约定（docs/2026-09-26_中控台设计方案.md §二）：
- 轻聊天 1 次 LLM 调用；动手模式复用现有 CLI 命令（taskrunner 子进程，
  与手动面板同一条执行通道），产物以卡片落进对话流；
- 每日同时只有一个动作（data/chat/{date}_action.json 状态机：offer→running→done）；
  完成检测在读取历史时推进（_advance_action，带锁防并发双写）；
- 不新增常驻轮询：前端只在动作活跃期间轮询历史接口。
"""
import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request

from core.chat_router import classify, persona_reply
from core.personas import PERSONAS, roster

from .. import taskrunner
from ..common import (CHECKLIST_ITEMS, cost_summary, load_json, pipeline_status,
                      published_dates, publish_streak, slot_arg, weekday_label,
                      wb_log)
from ..schemas import ChatChooseReq, ChatConfirmReq, ChatSendReq
from . import body_date, envelope, fail, q_date
from .agent import query_state

router = APIRouter(prefix="/api/chat", tags=["chat"])

HISTORY_LIMIT = 200

# 后台任务 label → 负责的猫咪（同事栏忙碌灯；按子串匹配，花名最优先）
_TASK_LABEL_MAP = [
    ("主编", "chief"), ("选题官", "topic"), ("撰稿人", "writer"), ("审校", "reviewer"),
    ("美编", "artist"), ("发行", "publisher"), ("档案员", "archivist"),
    ("选题", "topic"), ("候选", "topic"), ("new-batch", "topic"),
    ("choose", "topic"), ("评分", "topic"),
    ("写稿", "writer"), ("write", "writer"), ("大纲", "writer"),
    ("outline", "writer"), ("标题", "writer"), ("titles", "writer"),
    ("红线", "reviewer"), ("redline", "reviewer"),
    ("封面", "artist"), ("cover", "artist"),
    ("导出", "publisher"), ("排版", "publisher"), ("推送", "publisher"),
    ("一键出稿", "writer"), ("重新生成", "topic"), ("公众号导出", "publisher"),
    ("format", "publisher"), ("sync", "publisher"),
    ("复盘", "archivist"), ("学习", "archivist"), ("learn", "archivist"),
]

# ===== 动作注册表：chat 动作 → 既有 CLI 命令（与手动面板同一执行通道） =====
_ACTION_DEFS = {
    "new_batch": {
        "label": "生成一批候选", "persona": "topic",
        "args": lambda date, slot, choice: ["new-batch", "--date", date],
        "done_text": "候选出炉！Top5 在下面，点一条就定它。",
        "card": "top5",
    },
    "write": {
        "label": "起草 v1", "persona": "writer",
        "args": lambda date, slot, choice: (["write", "--date", date]
                                            + (["--slot", slot] if slot else [])),
        "done_text": "v1 写好了，工作间的「写稿」里可以细看和改。",
        "card": "draft",
    },
    "outline": {
        "label": "先出大纲", "persona": "writer",
        "args": lambda date, slot, choice: (["outline", "--date", date]
                                            + (["--slot", slot] if slot else [])),
        "done_text": "大纲好了，写稿页里能看到；要按大纲写就说一声。",
        "card": "info",
    },
    "titles": {
        "label": "出几个标题方案", "persona": "writer",
        "args": lambda date, slot, choice: (["titles", "--date", date]
                                            + (["--slot", slot] if slot else [])),
        "done_text": "标题方案出炉，挑一个顺眼的：",
        "card": "titles",
    },
    "format": {
        "label": "排版导出", "persona": "publisher",
        "args": lambda date, slot, choice: (["format", "--date", date]
                                            + (["--slot", slot] if slot else [])),
        "done_text": "排版稿出来了，规范校验在工作间「导出」里。",
        "card": "exported",
    },
    # P4：主编的全套开工——对接 Agent 主图（args 走特殊分支 _start_agent_run）
    "auto_run": {
        "label": "安排今天的全套活儿", "persona": "chief",
        "args": None,
        "done_text": "今天的活儿告一段落，产物都在档案里。",
        "card": "info",
    },
    # P5 关联性：流程级写稿（写稿→标题→排版一条龙，与写稿页「一键出稿」同源）
    "oneclick": {
        "label": "把这篇写完（写稿→标题→排版）", "persona": "writer",
        "args": None,
        "done_text": "全套齐了：文稿、标题方案、排版稿都在，标题挑一个顺眼的，预览在右侧。",
        "card": "exported",
    },
    # P5：美编的头图（生成/重画；导出时自动插入篇首）
    "cover": {
        "label": "画头图", "persona": "artist",
        "args": None,
        "done_text": "头图好了——下次排版导出会自动插到篇首，手机预览里直接看效果。",
        "card": None,
    },
}

# 人设默认动作（classify 判定 act 意图时随回复附确认卡）
_PERSONA_ACTION = {"topic": "new_batch", "writer": "write", "chief": "auto_run",
                   "publisher": "format", "artist": "cover",
                   "reviewer": None, "archivist": None}


def _refine_action(persona_id: str, text: str, default: str | None) -> str | None:
    """按消息关键词细分动作：同一个人设下，「标题/大纲」不该给成默认的写 v1。"""
    if default is None:
        return None
    if persona_id == "writer":
        if any(w in text for w in ("写完", "全套", "交稿", "出稿", "完整", "一键", "两篇")):
            return "oneclick"
        if "标题" in text:
            return "titles"
        if "大纲" in text:
            return "outline"
    return default


def _start_agent_run(date: str) -> str | None:
    """auto_run：经主编确认后启动 Agent 主图（复用 agent 路由的同日单线程守卫）。"""
    from .agent import DEFAULT_GOAL, _cancel_marker, _run_guarded
    from .settings import load_settings

    try:
        from pipeline import runner as agent_runner
    except Exception:
        return None
    marker = _cancel_marker(date)
    if marker.exists():
        try:
            marker.unlink()
        except OSError:
            pass
    s = load_settings()
    run_config = {"autonomy": s.get("autonomy", "assisted"),
                  "max_steps": s.get("max_steps", 40),
                  "max_cost_cny": s.get("max_cost_cny", 2.0)}
    return _run_guarded(date, agent_runner.start_run,
                        {"date": date, "goal": DEFAULT_GOAL, "config": run_config},
                        label=f"Agent 运行 {date}（对话发起）")

_action_lock = threading.Lock()


# ===== 持久化（data/chat/{date}.jsonl + {date}_action.json） =====
def _chat_path(date: str) -> Path:
    from core.paths import DATA_DIR
    return DATA_DIR / "chat" / f"{date}.jsonl"


def _action_path(date: str) -> Path:
    from core.paths import DATA_DIR
    return DATA_DIR / "chat" / f"{date}_action.json"


def _append(date: str, entry: dict) -> dict:
    entry.setdefault("id", uuid.uuid4().hex[:8])
    entry.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
    p = _chat_path(date)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _read(date: str, limit: int = HISTORY_LIMIT) -> list:
    p = _chat_path(date)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _read_action(date: str) -> dict | None:
    p = _action_path(date)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_action(date: str, st: dict) -> None:
    p = _action_path(date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def _clear_action(date: str) -> None:
    try:
        _action_path(date).unlink()
    except OSError:
        pass


# ===== 同事栏忙碌灯 =====
def _busy_personas(date: str) -> set:
    busy = set()
    st = query_state(date)  # 带缓存，轻
    if st.get("running"):
        busy.add("chief")
    for t in taskrunner.list_tasks():
        if t.get("status") != "running":
            continue
        label = t.get("label") or ""
        for kw, pid in _TASK_LABEL_MAP:
            if kw in label:
                busy.add(pid)
    act = _read_action(date)
    if act and act.get("status") == "running" and act.get("persona"):
        busy.add(act["persona"])
    return busy


@router.get("/personas")
def chat_personas(request: Request):
    date = q_date(request)
    busy = _busy_personas(date)
    return envelope({"date": date,
                     "personas": [{**p, "busy": p["id"] in busy} for p in roster()]})


@router.get("/brief")
def chat_brief(request: Request):
    """右侧面板今日概要：时节语境 + 流水线 + 成本 + 发布节奏（一次轻聚合）。"""
    date = q_date(request)
    steps = pipeline_status(date)
    st = query_state(date)
    from core.calendar_ctx import build_time_context
    tc = build_time_context(date)
    published = published_dates()
    cost = cost_summary(date)
    return envelope({
        "date": date, "weekday": weekday_label(date),
        "time_ctx": {"festivals": tc["festivals"], "solar_terms": tc["solar_terms"],
                     "weekday_note": tc["weekday_note"], "season_scene": tc["season_scene"],
                     "month_position": tc["month_position"]},
        "steps": [{"name": n, "done": ok} for n, ok in steps],
        "done_count": sum(1 for _, ok in steps if ok),
        "agent": {"status": st.get("status"), "running": bool(st.get("running")),
                  "steps_used": st.get("steps_used", 0),
                  "cost_cny": st.get("cost_cny", 0.0)},
        "cost_today": cost.get("today", 0.0),
        "streak": publish_streak(published),
        "total_published": len(published),
    })


# ===== 动作推进：任务完成检测 + 产物卡（读取历史时顺带推进，带锁防双写） =====
def _latest_top5_card(date: str) -> dict:
    import cat_shop
    batches = cat_shop.load_batches(date).get("batches", [])
    rounds = [b["round"] for b in batches] or [1]
    r = max(rounds)
    _, tf = cat_shop.batch_file_names(date, r)
    results = (load_json(tf, None) or {}).get("results") or []
    top5 = []
    for i, t in enumerate(results):
        top5.append({"rank": t.get("rank", i + 1),
                     "topic": (t.get("topic") or "")[:60],
                     "total": t.get("total", 0)})
    return {"type": "top5", "round": r, "top5": top5}


def _build_done_cards(date: str, action: str, st: dict) -> list:
    """动作完成 → 产物卡 + 下一步操作卡（方案 A：对话里直接拍板，不跳页）。"""
    import cat_shop
    kind = _ACTION_DEFS[action]["card"]
    slot = st.get("slot") or ""
    sp = cat_shop.slot_paths(date, slot or None)
    if action == "oneclick":
        # 一条龙完成：标题点选卡（写完后挑标题是唯一悬而未决的决策）+ 检查单
        data = load_json(sp["titles"], None) or {}
        titles = [t.get("title", "") for t in (data.get("titles") or [])][:5]
        return [{"type": "titles", "slot": slot, "titles": titles,
                 "selected": data.get("selected")},
                {"type": "exported", "slot": slot,
                 "checklist": [list(c) for c in CHECKLIST_ITEMS]}]
    if kind == "top5":
        return [_latest_top5_card(date)]
    if kind == "draft":
        words = 0
        if sp["v1"].exists():
            words = cat_shop.count_chinese_chars(sp["v1"].read_text(encoding="utf-8"))
        return [{"type": "draft", "slot": slot, "words": words,
                 "next": {"label": "接着排版导出", "send": "@发行 排版导出"}}]
    if kind == "titles":
        data = load_json(sp["titles"], None) or {}
        titles = [t.get("title", "") for t in (data.get("titles") or [])][:5]
        return [{"type": "titles", "slot": slot, "titles": titles,
                 "selected": data.get("selected")}]
    if kind == "exported":
        # 方案 A：发布检查单直接嵌进对话——勾满 7 项即可登记（与写稿页同源 API）
        return [{"type": "exported", "slot": slot,
                 "checklist": [list(c) for c in CHECKLIST_ITEMS]}]
    return [{"type": "info", "text": _ACTION_DEFS[action]["label"] + "完成"}]


def _agent_stop_message(date: str) -> tuple[str, dict | None]:
    """Agent 停靠状态 → (对话文案, 卡片)。选题/发布关卡/预算/完工各给一句话。"""
    ag = query_state(date)
    status = ag.get("status")
    if status == "waiting_topic":
        top5 = ((ag.get("pending_approval") or {}).get("top5")) or []
        return ("跑到选题关卡了——点一条就开写：",
                {"type": "top5", "round": 0, "channel": "agent",
                 "top5": [{"rank": t.get("rank", i + 1),
                           "topic": (t.get("topic") or "")[:60],
                           "total": t.get("total", 0)}
                          for i, t in enumerate(top5)]})
    if status == "waiting_publish":
        return ("终稿出炉，就差你一句「准发」——审批在右侧「运行日志」里，也可以直接说「批准推送」。",
                {"type": "exported", "slot": ""})
    if status == "paused_budget":
        return ("预算到了上限先停了手，提额后随时继续（右侧「运行日志」可提额）。", None)
    if status == "done":
        return ("今日完工 ✅ 产物都在档案里。", None)
    if status == "interrupted":
        return ("上次运行没跑完（服务重启过），点「一键运行」可从断点续跑，已花的钱不浪费。", None)
    if status in ("error", "blocked"):
        return ("这一步卡住了，详情在右侧「运行日志」的时间线里。", None)
    return ("今天的活儿告一段落，产物都在档案里。", None)


def _advance_action(date: str) -> dict | None:
    """动作运行中 → 查任务状态；完成后落产物卡并清状态。返回最新动作状态。

    自愈（P0 修复）：任务表在内存、动作状态在文件——服务重启后 get_task
    查无此 tid 时旧逻辑当成「还在跑」永久卡死。现在查不到即判中断，
    落一条说明消息并清状态。tid='agent' 是「跟随模式」：不查 taskrunner，
    改看 Agent 主图是否停靠。"""
    st = _read_action(date)
    if not st or st.get("status") != "running":
        return st
    if st.get("tid") == "agent":
        ag = query_state(date)
        if ag.get("running") or ag.get("status") in ("planning", "acting"):
            return st
        with _action_lock:
            st = _read_action(date)
            if not st or st.get("status") != "running" or st.get("tid") != "agent":
                return st
            ag = query_state(date)
            if ag.get("running") or ag.get("status") in ("planning", "acting"):
                return st
            text, card = _agent_stop_message(date)
            _append(date, {"role": "agent", "persona": "chief", "mode": "act",
                           "text": text, "cards": [card] if card else []})
            _clear_action(date)
        return _read_action(date)
    task = taskrunner.get_task(st.get("tid", ""))
    if task is None:
        # tid 查无：服务重启过（任务表纯内存）。判中断，不再永久卡 running。
        with _action_lock:
            st = _read_action(date)
            if not st or st.get("status") != "running" or st.get("tid") == "agent":
                return st
            if taskrunner.get_task(st.get("tid", "")) is not None:
                return st
            defn = _ACTION_DEFS.get(st.get("action", ""))
            label = defn["label"] if defn else "任务"
            _append(date, {"role": "system", "event": "action_interrupted",
                           "text": f"「{label}」跑到一半服务重启了，状态丢失；"
                                   "产物若已生成会在对应工作间里，没生成可再下一单。"})
            _clear_action(date)
        return _read_action(date)
    if task.get("status") == "running":
        return st
    with _action_lock:
        st = _read_action(date)  # 锁内复核（并发 GET 防双写）
        if not st or st.get("status") != "running":
            return st
        if st.get("tid") == "agent":
            return st
        task = taskrunner.get_task(st.get("tid", ""))
        if not task or task.get("status") == "running":
            return st
        defn = _ACTION_DEFS.get(st.get("action", ""))
        if defn is None:
            _clear_action(date)
            return None
        name = PERSONAS[defn["persona"]]["name"]
        if task.get("status") == "error":
            out_lines = (task.get("output") or "").strip().splitlines()
            tail = out_lines[-1] if out_lines else ""
            _append(date, {"role": "system", "event": "action_error",
                           "text": f"{name}{defn['label']}没成功{('：' + tail[:140]) if tail else ''}，可在工作间手动重试。"})
        elif st.get("action") == "auto_run":
            text, card = _agent_stop_message(date)
            _append(date, {"role": "agent", "persona": "chief", "mode": "act",
                           "text": text, "cards": [card] if card else []})
        else:
            cards = _build_done_cards(date, st["action"], st)
            _append(date, {"role": "agent", "persona": defn["persona"], "mode": "act",
                           "text": defn["done_text"], "cards": cards})
        _clear_action(date)
    return _read_action(date)


_DATE_RE = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}$")


@router.get("/{date}")
def chat_history(date: str):
    if not _DATE_RE.match(date or ""):
        date = datetime.now().strftime("%Y-%m-%d")
    act = _advance_action(date)
    return envelope({"date": date, "messages": _read(date), "action": act})


def _one_click_checked(date: str, slot: str | None) -> dict:
    """one_click 包装：业务错误（如「尚未决策选题」）转异常——run_function 只看
    异常不看返回值，不转的话对话里会误报「全套齐了」。"""
    from .drafts import one_click
    res = one_click(date, slot)
    if isinstance(res, dict) and res.get("error"):
        raise RuntimeError(res["error"])
    return res


def _execute_offer(date: str, st: dict):
    """执行一张已确认的动作卡（confirm 路由与「肯定跟进直达」共用）。
    返回 envelope 数据或 fail 信封。"""
    action = st["action"]
    defn = _ACTION_DEFS[action]
    name = PERSONAS[defn["persona"]]["name"]
    if action == "auto_run":
        tid = _start_agent_run(date)
        if tid is None:
            return fail("busy", "主编已经在跑了，或 Agent 依赖未安装")
    elif action == "oneclick":
        slot = slot_arg(date, "")
        tid = taskrunner.run_function(
            lambda: _one_click_checked(date, slot or None),
            label=f"对话·{name}·{defn['label']} ({date})",
            key=f"oneclick:{date}:{slot or '-'}")  # 单飞：写稿页与对话并发只跑一份
        st["slot"] = slot
    elif action == "cover":
        from pipeline.images import generate_cover
        slot = slot_arg(date, "") or None
        force = bool(st.get("force"))
        tid = taskrunner.run_function(
            lambda: generate_cover(date, slot=slot, force=force),
            label=f"对话·{name}·{'重画' if force else '生成'}头图 ({date})")
        st["slot"] = slot or ""
    else:
        slot = slot_arg(date, "") if action in ("write", "outline", "titles", "format") else ""
        tid = taskrunner.run_command(
            defn["args"](date, slot, 0),
            label=f"对话·{name}·{defn['label']} ({date})")
        st["slot"] = slot
    st.update({"status": "running", "tid": tid})
    _write_action(date, st)
    _append(date, {"role": "system", "event": "action_start",
                   "text": f"{name}去{defn['label']}了，好了会在对话里冒泡。"})
    wb_log("chat_act", date, f"{action}（{defn['label']}）经对话确认执行")
    return envelope({"task_id": tid, "action": action, "slot": st.get("slot", "")})


@router.post("")
@router.post("/")
def chat_send(req: ChatSendReq, request: Request):
    date = body_date(request, req.date)
    text = (req.text or "").strip()
    if not text:
        return fail("empty", "消息不能为空")
    if len(text) > 2000:
        return fail("too_long", "消息太长了，拆成两条说？")
    entries = _read(date)

    # ===== 待确认卡在挂 → 肯定跟进直接执行 / 拒绝跟进直接取消（修绕圈子的关键：
    # 台长回「现在马上写」「好，画吧」不该再被问一遍"要不要"）。
    # 锁内读-验-写：两标签页同时确认/连点只执行一次，后到者走正常聊天 =====
    act_state = _read_action(date)
    if act_state and act_state.get("status") == "offer":
        from core.chat_router import is_affirmative
        if is_affirmative(text):
            with _action_lock:
                st = _read_action(date)
                if st and st.get("status") == "offer":
                    user_entry = _append(date, {"role": "user", "text": text})
                    result = _execute_offer(date, st)
                    if result.get("ok"):
                        return envelope({"user": user_entry, "executed": True, **result["data"]})
                    return envelope({"user": user_entry, "executed": False,
                                     "error": result.get("error")})
                # 卡刚被另一标签页消费 → 落回正常聊天
            entries = _read(date)
            persona_id, act = classify(text, entries)
            user_entry = _append(date, {"role": "user", "text": text})
            reply_entry = _append(date, {"role": "agent", "persona": persona_id,
                                         "mode": "chat",
                                         "text": persona_reply(persona_id, text, date, entries)})
            wb_log("chat", date, f"→{PERSONAS[persona_id]['name']}：{text[:60]}")
            return envelope({"user": user_entry, "reply": reply_entry, "persona": persona_id})
        if len(text) <= 24 and re.search(r"不|别|算了|先等|等会|再说|取消", text):
            with _action_lock:
                st = _read_action(date)
                if st and st.get("status") == "offer":
                    user_entry = _append(date, {"role": "user", "text": text})
                    _clear_action(date)
                    ack = _append(date, {"role": "system", "event": "action_cancel",
                                         "text": "好，先不动手，随叫随到。"})
                    return envelope({"user": user_entry, "declined": True, "ack": ack})

    persona_id, act = classify(text, entries)
    user_entry = _append(date, {"role": "user", "text": text})
    # 动作状态入语境：正在跑/刚提议过时，猫咪绝不再重复提议（绕圈子的另一半来源）
    extra = ""
    act_now = _read_action(date)
    if act_now and act_now.get("status") == "running":
        label = _ACTION_DEFS.get(act_now.get("action", ""), {}).get("label", "任务")
        extra = f"编辑部正在执行「{label}」，还没完成。台长再催就告诉他正在做，绝不要重复提议或另排计划。"
    elif act_now and act_now.get("status") == "offer":
        label = _ACTION_DEFS.get(act_now.get("action", ""), {}).get("label", "任务")
        extra = f"你刚提议过「{label}」，确认卡已经给出。台长还没答复前绝不要问第二遍。"
    # 确认卡必须在 _append 之前组装进 entry——曾因写盘后才加 cards 导致
    # 磁盘无卡、轮询刷新后卡片凭空消失（P6 冒泡慢轮询会放大此 bug）
    running = (act_now or {}).get("status") == "running"
    action = (_refine_action(persona_id, text, _PERSONA_ACTION.get(persona_id))
              if (act and not running) else None)
    entry = {"role": "agent", "persona": persona_id, "mode": "chat",
             "text": persona_reply(persona_id, text, date, entries,
                                   extra_context=extra)}
    if action:
        defn = _ACTION_DEFS[action]
        entry["cards"] = [{"type": "confirm", "action": action,
                           "label": defn["label"], "persona": defn["persona"]}]
        entry["status"] = "offer"
    reply_entry = _append(date, entry)
    if action:
        _write_action(date, {"status": "offer", "action": action,
                             "persona": _ACTION_DEFS[action]["persona"],
                             "msg_id": reply_entry["id"],
                             "force": bool(re.search(r"换|重画", text))})
    name = PERSONAS[persona_id]["name"]
    wb_log("chat", date, f"→{name}：{text[:60]}")
    return envelope({"user": user_entry, "reply": reply_entry, "persona": persona_id})


@router.post("/confirm")
def chat_confirm(req: ChatConfirmReq, request: Request):
    date = body_date(request, req.date)
    # 锁内读-验-执行：确认卡的 check-then-act 曾无锁——双标签页/连点会
    # 双重执行同一动作（双份 LLM 花费，一份产物卡永久丢失）
    with _action_lock:
        st = _read_action(date)
        if not st or st.get("status") != "offer" or st.get("msg_id") != req.msg_id:
            return fail("no_offer", "这张确认卡已经处理过或不存在了")
        if not req.approved:
            _clear_action(date)
            _append(date, {"role": "system", "event": "action_cancel",
                           "text": "好，先不动手，随叫随到。"})
            return envelope({"cancelled": True})
        return _execute_offer(date, st)


# 撰稿人接单台词（选题→撰稿交接的可视化；随机一条，编辑部的人味）
_WRITER_HANDOFFS = [
    "收到，#{c} 这篇我接了——先把主人公从稿子里翻出来。",
    "#{c} 交给我。这个题眼得慢慢磨，你们先忙。",
    "好嘞，#{c} 我抱走了。今晚的灯我来亮。",
    "#{c} 归我啦。开头我想好了要从一个具体的物件写起。",
]


@router.post("/follow-agent")
def chat_follow_agent(req: ChatSendReq, request: Request):
    """跟随模式：对话里点选选题关卡后挂上，Agent 到下一个关卡再向对话冒泡。"""
    date = body_date(request, req.date)
    cur = _read_action(date)
    if cur and cur.get("status") == "running" and cur.get("tid") != "agent":
        return fail("busy", "编辑部正忙着一件事，等它冒泡了再跟随 Agent")
    _write_action(date, {"status": "running", "action": "auto_run",
                         "persona": "chief", "tid": "agent", "msg_id": "", "slot": ""})
    return envelope({"following": True, "date": date})


def _mark_message(date: str, msg_id: str, patch: dict) -> None:
    """回写某条消息的标记（choose 幂等用）。文件小，整读整写。"""
    p = _chat_path(date)
    if not p.exists():
        return
    with _action_lock:
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            out = []
            for line in lines:
                try:
                    m = json.loads(line)
                    if m.get("id") == msg_id:
                        m.update(patch)
                    out.append(json.dumps(m, ensure_ascii=False))
                except Exception:
                    out.append(line)
            p.write_text("\n".join(out) + "\n", encoding="utf-8")
        except OSError:
            pass


@router.post("/choose")
def chat_choose(req: ChatChooseReq, request: Request):
    """Top5 卡点选：与选题中心 /api/topics/choose 同一命令通道。
    msg_id 幂等：同一张卡刷新/复活后重复点选不重复执行（旧版会重复建槽位）。"""
    date = body_date(request, req.date)
    st = _read_action(date)
    if st and st.get("status") == "running":
        return fail("busy", "编辑部正忙着一件事，稍等一下")
    if not (1 <= int(req.choice) <= 5):
        return fail("bad_choice", "点选序号需在 Top5 内")
    if req.msg_id:
        for m in _read(date):
            if m.get("id") == req.msg_id and m.get("chosen_done"):
                return envelope({"duplicate": True, "choice": req.choice,
                                 "round": req.round})
    tid = taskrunner.run_command(
        ["choose", "--date", date, "--choice", str(req.choice), "--round", str(req.round)],
        label=f"对话·选题官·选定 #{req.choice} ({date})")
    _append(date, {"role": "system", "event": "chosen",
                   "text": f"已拍板 #{req.choice}，文稿槽位建好后撰稿人随时可以开工。"})
    import random
    _append(date, {"role": "agent", "persona": "writer", "mode": "chat",
                   "text": random.choice(_WRITER_HANDOFFS).format(c=req.choice)})
    if req.msg_id:
        _mark_message(date, req.msg_id, {"chosen_done": True, "chosen": req.choice})
    wb_log("chat_choose", date, f"对话点选 r{req.round} #{req.choice}")
    return envelope({"task_id": tid, "choice": req.choice, "round": req.round})


# ===== 方案 B：工作台事件冒泡（手动面板动作 → 对话留痕，零 LLM 成本） =====
def notify(date: str, text: str, persona: str | None = None) -> None:
    """选题/写稿/导出等页面上的手动操作冒泡进当日对话——对话流成为整个
    工作台的群聊留痕。冒泡失败绝不影响主操作。"""
    try:
        if persona:
            _append(date, {"role": "agent", "persona": persona, "mode": "chat",
                           "text": text})
        else:
            _append(date, {"role": "system", "event": "wb", "text": text})
    except Exception:
        pass


def notify_choose(date: str, choice: int, round: int) -> None:
    """选题中心手动点选 → 冒泡 + 撰稿人接单（与对话内点选同款体验）。"""
    import random
    try:
        _append(date, {"role": "system", "event": "wb",
                       "text": f"台长在选题中心拍板了 r{round} #{choice}"})
        _append(date, {"role": "agent", "persona": "writer", "mode": "chat",
                       "text": random.choice(_WRITER_HANDOFFS).format(c=choice)})
    except Exception:
        pass
