#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/interrupts.py · 两个人工关卡（混合自主模式的核心）。

选题确认：score_candidates 产出 Top5 后图暂停，用户点选 → Command(resume) 恢复，
决策经 cmd_choose 落盘（与工作台 UI 完全同一份数据语义）。
发布前确认：export_mp_html 成功后暂停，用户批准 → finish；拒绝 → 意见回灌 planner。

恢复载荷约定（CLI 与 UI 共用）：
  topic:   {"choice": 1-5, "note": "可选备注"}   choice=0 表示拒绝全部候选
  publish: {"approved": true} 或 {"approved": false, "note": "修改意见"}
"""
import json
import shutil

from langchain_core.messages import HumanMessage
from langgraph.types import interrupt

from agent.config import AgentRunConfig
from core.paths import DRAFTS_DIR, SELECTIONS_DIR, slot_paths
from core.storage import load_json, load_works, save_works
from tools._base import run_step
from tools.publish_tools import push_wechat_draft, sync_feishu_doc


def _top5_payload(state):
    art = state.get("artifacts") or {}
    top5 = (art.get("top5") or {}).get("top5") or []
    if not top5:  # 兜底：直接读当日 top5 文件（planner 可能没走 score 工具）
        from core.storage import load_batches
        from core.paths import batch_file_names
        r = load_batches(state["date"]).get("active_round") or 1
        _, p = batch_file_names(state["date"], r)
        top5 = load_json(p, {}).get("results", [])
    return [{"rank": t.get("rank", i + 1), "topic": t.get("topic", "?"),
             "total": t.get("total", 0),
             "compliance_blocked": t.get("compliance_blocked", False)}
            for i, t in enumerate(top5)]


def topic_confirm_node(state):
    """选题确认关卡。semi/auto 档自动代选 Top1（跳过合规拦截候选），
    无可代选时回退人工 interrupt；assisted 档行为不变。"""
    from agent.executor import append_trace
    top5 = _top5_payload(state)
    cfg = AgentRunConfig.from_dict(state.get("config") or {})

    note = ""
    choice = 0
    auto_pick = None
    if cfg.auto_topic:
        pick = next((t for t in top5
                     if not t.get("compliance_blocked") and 1 <= t.get("rank", 0) <= len(top5)),
                    None)
        if pick is not None:
            auto_pick = pick["rank"]
        # 全被合规拦截 / Top5 为空 → 不代选，回退人工

    if auto_pick is not None:
        choice = auto_pick
        note = f"（{cfg.autonomy} 档自动代选）"
        append_trace(state["date"], {"node": "topic_confirm", "event": "auto_approved",
                                     "choice": choice, "autonomy": cfg.autonomy})
    else:
        payload = {"type": "topic_confirm", "date": state["date"], "top5": top5,
                   "question": "请选择今日要写的选题（回复 choice=1..5，choice=0 拒绝全部）"}
        append_trace(state["date"], {"node": "topic_confirm",
                                     "event": "waiting_approval", "top5": len(top5)})
        answer = interrupt(payload)
        choice = int(answer.get("choice", 0) or 0)
        note = str(answer.get("note", "") or "")

    if 1 <= choice <= len(top5):
        ok, log = run_step(_cmd_choose, date=state["date"], choice=choice,
                           note=note, round=None)
        sel = load_json(SELECTIONS_DIR / f"{state['date']}.json", {}) or {}
        topic = sel.get("topic") or top5[choice - 1]["topic"]
        art = dict(state.get("artifacts") or {})
        art["selection"] = {"choice": choice, "topic": topic,
                            "slot": sel.get("active_slot", "")}
        return {"messages": [HumanMessage(
                    f"【用户已选定】#{choice}：{topic}" + (f"（备注：{note}）" if note else "")
                    + "。请继续写稿流程。")],
                "status": "planning", "artifacts": art, "pending_approval": None}
    # 拒绝全部 → 理由自动转存为当日风向（v4.3 M3 拒绝即风向，plan §5.3），
    # 下一批候选生成时优先遵循；未填理由则直接让 planner 换角度重生成
    directive_note = ""
    if note:
        from core.directives import add_directive
        r = add_directive(note, scope="today", source="topic_confirm")
        directive_note = ("你的理由已自动存为今日风向，生成新一批时将优先遵循。"
                          if r.get("ok")
                          else f"（理由未能转为风向：{r.get('reason', '')}）")
    return {"messages": [HumanMessage(
                "【用户拒绝了全部候选】" + (f"理由：{note}。" if note else "")
                + directive_note
                + "请生成新一批选题（可换角度/时段），或调用 escalate_to_human 求助。")],
            "status": "planning", "pending_approval": None}


def _cmd_choose(args):
    from pipeline.topics import cmd_choose
    return cmd_choose(args)


def _fmt_tool_result(r):
    return r.get("status") if isinstance(r, dict) else "error"


def _execute_publish(state):
    """批准发布后真正落地：推送草稿箱 + 归档飞书。

    修复 v4.8：此前 approved 只是把状态置 done（空动作），导致"已收尾但草稿箱为空"。
    现在批准即触发两个工具，并把结果回写 artifacts.publish / works（幂等：各工具自带去重）。
    """
    art = state.get("artifacts") or {}
    date = state["date"]
    sel = art.get("selection") or {}
    slot = sel.get("slot") or None
    titles = (art.get("titles") or {}).get("titles") or []
    res = {}
    try:
        if slot is None:  # 快进场景：续跑 checkpoint 后 artifacts 为空，从 works 对齐活动槽位，
            ws0 = load_works(date)  # 否则无槽位推送幂等失效（重复草稿）且标题兜底成日期
            s0 = next((x for x in ws0.get("slots", [])
                       if x.get("selected_title") or x.get("topic")), None)
            if s0 is not None:
                slot = s0.get("id")
        sp = slot_paths(date, slot)
        if not sp["mp"].exists():  # agent 以 slot=None 产出 {date}_mp.html，推送按槽位找 →
            plain = slot_paths(date, None)  # 无槽位产物缺失时回退复制，保证能推
            if plain["mp"].exists():
                sp["mp"].parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(plain["mp"], sp["mp"])
        if titles:  # 把已生成标题落进 works，草稿标题用正式标题而非兜底日期
            ws = load_works(date)
            s = next((x for x in ws.get("slots", []) if x.get("id") == (slot or "")), None)
            if s is not None and not s.get("selected_title"):
                s["selected_title"] = titles[0]
                save_works(date, ws)
        res["push"] = push_wechat_draft.invoke({"date": date, "slot": slot})
        res["sync"] = sync_feishu_doc.invoke({"date": date, "slot": slot})
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
    return res


def publish_confirm_node(state):
    """发布前确认关卡。auto 档且质检全过（自检 all_ok + 门禁通过 + 导出无错）
    自动批准推送；任一不满足回退人工 interrupt；assisted/semi 档行为不变。"""
    from core.paths import cover_image_path
    art = state.get("artifacts") or {}
    export = art.get("export") or {}
    draft = art.get("draft") or {}
    titles = (art.get("titles") or {}).get("titles") or []
    cfg = AgentRunConfig.from_dict(state.get("config") or {})

    # 头图缩略图（v4.2 M1：审批卡顺带审图——合规与风格把关前移）
    # 优先用导出信封带回的头图（与导出槽位一致），回退按选题槽位推算
    ex_cover = export.get("cover")
    if isinstance(ex_cover, dict) and ex_cover.get("path"):
        cover = {"present": bool(ex_cover.get("present")), "path": ex_cover["path"]}
    else:
        slot = ((art.get("selection") or {}).get("slot")) or None
        cover_p = cover_image_path(state["date"], slot)
        cover = {"present": cover_p.exists(), "path": f"images/{cover_p.name}"}

    # auto 档放行条件（代码侧硬校验，LLM 不可绕过）
    checks = export.get("checks_summary") or {}
    gate_ok = (state.get("gate_status") or {}).get("passed") is True
    auto_ok = (cfg.auto_publish and bool(checks.get("all_ok"))
               and gate_ok and not export.get("error"))

    payload = {"type": "publish_confirm", "date": state["date"],
               "title": draft.get("title") or (titles[0] if titles else "?"),
               "word_count": draft.get("word_count", 0),
               "titles": titles[:3],
               "mp_path": export.get("mp_path", ""),
               "cover": cover,
               "checks_summary": export.get("checks_summary", {}),
               "gate_passed": (state.get("gate_status") or {}).get("passed", None),
               "question": "是否批准发布到公众号草稿箱？（approved=true/false + note）"}
    from agent.executor import append_trace
    if auto_ok:
        append_trace(state["date"], {"node": "publish_confirm", "event": "auto_approved",
                                     "autonomy": cfg.autonomy,
                                     "checks": export.get("checks_summary", {})})
        answer = {"approved": True,
                  "note": f"（{cfg.autonomy} 档自动批准：自检 {checks.get('passed')}/{checks.get('total')} 全过 + 门禁通过）"}
    else:
        if cfg.auto_publish:
            append_trace(state["date"], {"node": "publish_confirm", "event": "auto_blocked",
                                         "reason": "质检未全过，回退人工确认",
                                         "checks": export.get("checks_summary", {}),
                                         "gate_passed": gate_ok})
        append_trace(state["date"], {"node": "publish_confirm", "event": "waiting_approval",
                                     "title": payload["title"]})
        answer = interrupt(payload)

    if answer.get("approved"):
        res = _execute_publish(state)
        art = dict(state.get("artifacts") or {})
        art["publish"] = res
        parts = [f"{k}={_fmt_tool_result(v)}" for k, v in res.items() if k in ("push", "sync")]
        extra = f"；发布异常：{res['error']}" if res.get("error") else ""
        from agent.executor import append_trace
        append_trace(state["date"], {"node": "publish_act", "event": "auto_publish",
                                     "result": {k: _fmt_tool_result(v)
                                                for k, v in res.items()}})
        return {"messages": [HumanMessage(
                    f"【用户已批准发布】已触发推送草稿箱与飞书归档（{"、".join(parts)}{extra}）。")],
                "status": "done", "pending_approval": None, "artifacts": art}
    note = str(answer.get("note", "") or "")
    return {"messages": [HumanMessage(
                f"【用户暂不批准发布】{note}。请按意见修改文稿（edit_draft / 重写）"
                "并重新导出后再请求确认。")],
            "status": "planning", "pending_approval": None}
