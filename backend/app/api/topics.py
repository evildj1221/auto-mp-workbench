#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/app/api/topics.py · 选题中心（原 /topics 及其动作路由的 JSON 化）。"""
from fastapi import APIRouter, Request

import cat_shop
from core.directives import add_directive, low_hit_streak, remove_directive

from .. import taskrunner
from ..common import directive_cards, format_hit_stats, load_json, wb_log
from ..schemas import ChooseReq, DirectiveDeleteReq, DirectiveReq, FeedbackQuickReq, PrefReq, SlotDeleteReq
from . import envelope, fail, path_date, q_date, body_date
from .agent import query_state

router = APIRouter(prefix="/api/topics", tags=["topics"])


def topics_ctx(date: str) -> dict:
    """选题页数据（原 topics() 视图上下文的 JSON 版）。"""
    cat_shop.ensure_batch_index(date)
    batches_data = cat_shop.load_batches(date)
    batch_rounds = [b["round"] for b in batches_data.get("batches", [])]
    batch_top5s = []
    for r in batch_rounds:
        cf, tf = cat_shop.batch_file_names(date, r)
        data = load_json(tf, None)
        cdata = load_json(cf, None) or {}
        top5 = (data or {}).get("results") if data else None
        if top5:  # 兜底注入名次（存量数据可能缺 rank 字段）
            for i, t in enumerate(top5):
                t.setdefault("rank", i + 1)
        batch_top5s.append({"round": r, "top5": top5, "file": tf.name,
                            "directive_hits": cdata.get("directive_hits"),
                            "directive_hits_text": format_hit_stats(cdata.get("directive_hits"))})
    slots = cat_shop.load_works(date).get("slots", [])
    for s in slots:
        sp = cat_shop.slot_paths(date, s["id"])
        s["has_draft"] = sp["v1"].exists()
        s["words"] = cat_shop.count_chinese_chars(
            sp["v1"].read_text(encoding="utf-8")) if sp["v1"].exists() else 0
    sel = load_json(cat_shop.SELECTIONS_DIR / f"{date}.json", {}) or {}
    pending = load_json(cat_shop.SELECTIONS_DIR / f"{date}_pending.json", {}) or {}
    fb_entries = {e["rank"]: e for e in cat_shop.load_user_feedback(date)}
    return {"page": "topics", "date": date, "batch_top5s": batch_top5s,
            "batch_rounds": batch_rounds, "slots": slots, "sel": sel,
            "pending": pending, "fb_entries": fb_entries,
            "agent_state": query_state(date),
            "directives": directive_cards(), "low_hit_streak": low_hit_streak()}


@router.get("/{date}")
@router.get("")
def get_topics(request: Request, date: str | None = None):
    d = path_date(request, date)
    return envelope(topics_ctx(d))


@router.post("/directives")
def add_directive_route(req: DirectiveReq, request: Request):
    """M3：保存选题风向（今日/本周/长期）。"""
    date = body_date(request, req.date)
    text = req.text.strip()
    scope = req.scope.strip() or "week"
    if not text:
        return fail("风向内容为空")
    r = add_directive(text, scope=scope, source="workbench")
    if not r["ok"]:
        wb_log("directive_reject", date, f"风向被拒收：{r['reason']}")
        return fail(r["reason"][:90])
    dup = "（已存在）" if r.get("duplicated") else ""
    wb_log("directive_add", date, f"[{scope}] {text}{dup}")
    return envelope({"added": True, "duplicated": bool(r.get("duplicated"))})


@router.post("/directives/delete")
def delete_directive_route(req: DirectiveDeleteReq, request: Request):
    date = body_date(request, req.date)
    ok = remove_directive(req.id.strip())
    wb_log("directive_delete", date, f"{req.id}{' 已移除' if ok else ' 不存在'}")
    return envelope({"removed": bool(ok)})


@router.post("/choose")
def choose_route(req: ChooseReq, request: Request):
    """选定选题 → 后台创建文稿槽位（不锁定，可继续选其他选题）。"""
    date = body_date(request, req.date)
    args = ["choose", "--date", date, "--choice", str(req.choice),
            "--round", str(req.round)]
    if req.note.strip():
        args += ["--note", req.note.strip()]
    tid = taskrunner.run_command(args, label=f"choose r{req.round} #{req.choice} ({date})")
    wb_log("choose", date, f"r{req.round} 选定 #{req.choice}" +
           (f"，备注：{req.note.strip()}" if req.note.strip() else ""))
    from .chat import notify_choose
    notify_choose(date, req.choice, req.round)
    return envelope({"task_id": tid, "round": req.round, "choice": req.choice})


@router.post("/slots/delete")
def slot_delete_route(req: SlotDeleteReq, request: Request):
    """删除文稿槽位（选错重来）。已发布的槽位拒绝删除。"""
    date = body_date(request, req.date)
    slot_id = req.slot.strip()
    if not slot_id:
        return fail("missing_slot", "缺少槽位 id")
    ok, msg = cat_shop.delete_slot(date, slot_id)
    wb_log("slot_delete", date, msg)
    if not ok:
        return fail("published", "已发布的槽位不能删除")
    remaining = [s for s in cat_shop.load_works(date).get("slots", [])
                 if s.get("status") != "published"]
    return envelope({"deleted": True,
                     "remaining_slot": remaining[0]["id"] if remaining else None})


@router.post("/new-batch")
def new_batch_route(request: Request):
    """生成新一批选题（不限次数，多批共存）。"""
    date = q_date(request)
    tid = taskrunner.run_command(["new-batch", "--date", date], label=f"新批次 ({date})")
    wb_log("new_batch", date, "生成新一批选题")
    from .chat import notify
    notify(date, "台长在选题中心点了「生成新批次」，选题官去翻候选池了", persona="topic")
    return envelope({"task_id": tid})


@router.post("/regenerate-batch")
def regenerate_batch_route(request: Request, round: int = 1):
    """强制重新生成某批（--round 必填，不限次数）。"""
    date = q_date(request)
    tid = taskrunner.run_command(
        ["regenerate-batch", "--date", date, "--round", str(round)],
        label=f"重新生成 r{round} ({date})")
    wb_log("regenerate_batch", date, f"重新生成批次 r{round}")
    from .chat import notify
    notify(date, f"台长在选题中心重新生成 r{round}，选题官重新翻一遍", persona="topic")
    return envelope({"task_id": tid, "round": round})


@router.post("/pref")
def pref_route(req: PrefReq, request: Request):
    """P-02：收藏/排除偏好信号。"""
    date = body_date(request, req.date)
    _, tf = cat_shop.batch_file_names(date, req.round)
    top5 = (load_json(tf, None) or {}).get("results") or []
    ok = cat_shop.record_user_pref(date, req.rank, req.action, top5)
    wb_log("pref", date, f"r{req.round} #{req.rank} {'收藏' if req.action == 'like' else '排除'}"
           + (f"（{top5[req.rank - 1].get('topic', '')[:20]}）"
              if ok and req.rank <= len(top5) else ""))
    return envelope({"recorded": bool(ok)})


@router.post("/feedback")
def feedback_quick_route(req: FeedbackQuickReq, request: Request):
    """选题卡片上的快捷打分（星级+评语），数据写入 user_feedback.jsonl。"""
    date = body_date(request, req.date)
    fb_str = f"{req.rank}:{req.score}" + (f" {req.note.strip()}" if req.note.strip() else "")
    ok = cat_shop.collect_feedback(date, feedback_str=fb_str)
    wb_log("feedback", date,
           f"#{req.rank} 打 {req.score} 分" + (f"：{req.note.strip()}" if req.note.strip() else ""))
    if not ok:
        return fail("record_failed", "打分写入失败（check collect_feedback 日志）")
    return envelope({"recorded": True})
