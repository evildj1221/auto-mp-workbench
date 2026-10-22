#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/app/api/drafts.py · 写稿与发布（原 /write 及其动作路由的 JSON 化）。"""
import subprocess
import sys
from datetime import datetime

from fastapi import APIRouter, Request

import cat_shop
from core import exporter

from .. import taskrunner
from ..common import (CHECKLIST_ITEMS, SCRIPT_DIR, V2_ANGLES, load_json,
                      published_path, slot_arg, wb_log)
from ..schemas import (EditDraftReq, OneClickReq, OutlineReq, PublishRegisterReq,
                       QuoteAddReq, ReviewDoneReq, RunCmdReq, TitlesChooseReq)
from . import envelope, fail, path_date, q_date, body_date

router = APIRouter(prefix="/api/drafts", tags=["drafts"])


def drafts_ctx(date: str, slot: str) -> dict:
    """写稿页数据（原 write_page() 视图上下文的 JSON 版）。"""
    sp = cat_shop.slot_paths(date, slot or None)
    pub = load_json(published_path(date, slot), {}) or {}
    sel = load_json(cat_shop.SELECTIONS_DIR / f"{date}.json", {}) or {}
    slots_meta = cat_shop.load_works(date).get("slots", [])
    drafts = {}
    for v in ("v1", "v2"):
        p = sp[v]
        drafts[v] = {"exists": p.exists(),
                     "words": cat_shop.count_chinese_chars(p.read_text(encoding="utf-8"))
                     if p.exists() else 0}
    html_v1 = sp["html"].exists()
    html_v2 = sp["v2"].exists() and (cat_shop.DRAFTS_DIR / f"{date}_v2.html").exists()
    titles_data = load_json(sp["titles"], None)
    draft_text = sp["v1"].read_text(encoding="utf-8") if sp["v1"].exists() else ""
    outline_path = sp["v1"].with_name(sp["v1"].name.replace("_v1.md", "_outline.md"))
    outline_text = outline_path.read_text(encoding="utf-8") if outline_path.exists() else ""
    quotes = cat_shop.load_quotes(10)
    similar_sents = []
    if sp["v1"].exists():
        v1t = sp["v1"].read_text(encoding="utf-8")
        slot_series = None
        if slot:
            sm = cat_shop.get_slot(date, slot) or {}
            slot_series = sm.get("motif_series")
        past_full = cat_shop._load_past_full(date, series=slot_series)
        similar_sents = cat_shop._find_similar_sentences(v1t, past_full)
    return {"page": "write", "date": date, "pub": pub, "sel": sel, "drafts": drafts,
            "slot": slot, "slots_meta": slots_meta,
            "slot_meta": (cat_shop.get_slot(date, slot) if slot else None),
            "outline_text": outline_text, "quotes": quotes,
            "similar_sents": similar_sents, "html_v1": html_v1, "html_v2": html_v2,
            "v2_angles": V2_ANGLES, "mp_exists": sp["mp"].exists(),
            "titles_data": titles_data, "draft_text": draft_text,
            "checklist": CHECKLIST_ITEMS}


@router.get("/{date}")
def get_drafts(request: Request, date: str, slot: str = ""):
    d = path_date(request, date)
    s = slot_arg(d, slot)
    return envelope(drafts_ctx(d, s))


@router.get("/{date}/md")
def get_md(request: Request, date: str, slot: str = "", version: str = "v1"):
    """文稿原文（编辑器加载）。"""
    d = path_date(request, date)
    if version not in ("v1", "v2"):
        return fail("invalid_version", "version 仅支持 v1/v2")
    sp = cat_shop.slot_paths(d, slot_arg(d, slot) or None)
    p = sp[version]
    if not p.exists():
        return fail("notfound", f"{version} 文稿不存在")
    return envelope({"version": version, "slot": slot_arg(d, slot),
                     "content": p.read_text(encoding="utf-8")})


@router.post("/edit")
def edit_draft(req: EditDraftReq, request: Request):
    """保存人工编辑的文稿，保存时重跑红线/字数自检。"""
    date = body_date(request, req.date)
    slot = slot_arg(date, req.slot)
    content = req.content
    if not content.strip():
        return fail("empty", "内容为空，未保存")
    path = cat_shop.slot_paths(date, slot or None)[req.version]
    path.write_text(content, encoding="utf-8")
    wc = cat_shop.count_chinese_chars(content)
    rl = cat_shop.redline_check(content, step="workbench_edit", date=date,
                                file=str(path.relative_to(SCRIPT_DIR)))
    wb_log("edit_draft", date, f"人工编辑 {req.version}（{wc} 字，红线={rl['risk_level']}）")
    msg = f"已保存 {req.version}：{wc} 字"
    if rl["should_block"]:
        msg += f"；红线 {rl['risk_level']}：{rl['block_reason']}"
    elif rl["hits"]:
        msg += f"；红线提示 {len(rl['hits'])} 条（非阻断）"
    return envelope({"message": msg, "words": wc, "redline": rl})


@router.post("/titles/choose")
def titles_choose(req: TitlesChooseReq, request: Request):
    """P0-4 标题方案选择：同步 published / v1 首行 / 槽位 titles / works meta。"""
    date = body_date(request, req.date)
    slot = slot_arg(date, req.slot)
    title = req.title.strip()
    if not title:
        return fail("empty", "标题为空")
    sp = cat_shop.slot_paths(date, slot or None)
    pub_path = published_path(date, slot)
    pub = load_json(pub_path, {}) or {}
    pub.update({"title": title, "title_selected": True,
                "title_selected_at": datetime.now().isoformat(timespec="seconds")})
    cat_shop.write_json(pub_path, pub)
    # 同步 v1 文稿第一行标题，使导出/排版全链路生效
    md_path = sp["v1"]
    if md_path.exists():
        lines = md_path.read_text(encoding="utf-8").split("\n")
        if lines and lines[0].startswith("# "):
            lines[0] = f"# {title}"
            md_path.write_text("\n".join(lines), encoding="utf-8")
    tp = sp["titles"]
    if tp.exists():
        data = cat_shop.load_json(tp, {}) or {}
        data["selected"] = title
        cat_shop.write_json(tp, data)
    # 同步槽位 meta 的 selected_title
    if slot:
        ws = cat_shop.load_works(date)
        for s in ws["slots"]:
            if s["id"] == slot:
                s["selected_title"] = title
                s["updated_at"] = datetime.now().isoformat(timespec="seconds")
        cat_shop.save_works(date, ws)
    wb_log("titles_choose", date, f"[{slot or '默认'}] 选定标题：{title}")
    from .chat import notify
    notify(date, f"台长定了标题：《{title}》", persona="writer")
    return envelope({"selected": title, "slot": slot})


@router.post("/review-done")
def review_done(req: ReviewDoneReq, request: Request):
    """标记审校完成。"""
    date = body_date(request, req.date)
    slot = slot_arg(date, req.slot)
    pub_path = published_path(date, slot)
    pub = load_json(pub_path, {}) or {}
    pub.update({"review_done": True,
                "reviewed_at": datetime.now().isoformat(timespec="seconds")})
    cat_shop.write_json(pub_path, pub)
    wb_log("review_done", date, "审校完成标记")
    return envelope({"marked": True})


@router.post("/publish-register")
def publish_register(req: PublishRegisterReq, request: Request):
    """P0-5 发布登记 + 检查单（全勾选才能登记）。"""
    date = body_date(request, req.date)
    slot = slot_arg(date, req.slot)
    pub_path = published_path(date, slot)
    pub = load_json(pub_path, {}) or {}
    checked = [k for k, _ in CHECKLIST_ITEMS if (req.checklist or {}).get(k)]
    checklist = {k: (k in checked) for k, _ in CHECKLIST_ITEMS}
    if len(checked) < len(CHECKLIST_ITEMS):
        pub.update({"publish_checklist": checklist})
        cat_shop.write_json(pub_path, pub)
        wb_log("publish_register", date,
               f"检查单未完成（{len(checked)}/{len(CHECKLIST_ITEMS)}）")
        return fail("incomplete",
                    f"检查单未完成（{len(checked)}/{len(CHECKLIST_ITEMS)}）")
    from datetime import timedelta
    pub.update({"user_published": True,
                "published_at": datetime.now().isoformat(timespec="seconds"),
                "due_review_date": (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d"),
                "publish_checklist": checklist})
    cat_shop.write_json(pub_path, pub)
    if slot:
        ws = cat_shop.load_works(date)
        for s in ws["slots"]:
            if s["id"] == slot:
                s["status"] = "published"
                s["updated_at"] = datetime.now().isoformat(timespec="seconds")
        cat_shop.save_works(date, ws)
    wb_log("publish_register", date, f"[{slot or '默认'}] 登记已发布")
    from .chat import notify
    notify(date, f"台长登记发布完成（{slot or '默认'}）——三天后记得回来录复盘数据", persona="archivist")
    return envelope({"registered": True})


@router.post("/outline")
def outline_gen(req: OutlineReq, request: Request):
    """大纲搭建：为当前槽位生成信件体大纲。"""
    date = body_date(request, req.date)
    slot = slot_arg(date, req.slot)
    tid = taskrunner.run_command(
        ["outline", "--date", date] + (["--slot", slot] if slot else []),
        label=f"生成大纲 {date} [{slot or '默认'}]")
    wb_log("outline", date, f"[{slot or '默认'}] 生成大纲")
    return envelope({"task_id": tid})


@router.post("/quotes")
def quote_add(req: QuoteAddReq, request: Request):
    """金句入库（手动添加）。"""
    date = body_date(request, req.date)
    slot = slot_arg(date, req.slot)
    text = req.text.strip()
    ok = cat_shop.add_quote(text, source="manual", date=date)
    wb_log("quote_add", date, f"金句入库（{'新增' if ok else '重复跳过'}）")
    return envelope({"added": bool(ok)})


def one_click(date: str, slot: str | None) -> dict:
    """一键出稿流水线：写稿（若未写）→ 标题方案（缺失则补）→ 公众号导出。
    每步失败即返回明确错误 + 产物二次校验（原 _one_click 原样迁入）。"""
    sel = load_json(cat_shop.SELECTIONS_DIR / f"{date}.json", {}) or {}
    if not slot and not sel.get("user_choice"):
        return {"error": "尚未决策选题，请先到「选题中心」点选选题"}
    sp = cat_shop.slot_paths(date, slot or None)
    v1 = sp["v1"]
    if not v1.exists():
        if getattr(sys, "frozen", False):
            # exe 双模式：argv[1] 必须是子命令（如 "write"）才会走 CLI 模式。
            _cmd = [sys.executable, "write", "--date", date]
        else:
            _cmd = [sys.executable, str(cat_shop.SCRIPT_DIR / "cat_shop.py"),
                    "write", "--date", date]
        if slot:  # 槽位模式必须传 --slot，否则写到旧命名文件导致产物校验误报失败
            _cmd += ["--slot", slot]
        try:
            r = subprocess.run(_cmd,
                capture_output=True, text=True, encoding="utf-8", timeout=600,
                cwd=str(cat_shop.SCRIPT_DIR))
        except subprocess.TimeoutExpired:
            return {"error": "写稿超时（>10 分钟）。请稍后重试，或先到「写稿与发布」手动点「写稿 v1」"}
        except Exception as e:
            return {"error": f"写稿进程启动失败：{e}"}
        # 二次校验：进程返回 0 但产物缺失（如 LLM 瞬时失败）也要拦下来
        if r.returncode != 0 or not v1.exists():
            detail = (r.stderr or r.stdout or "").strip()[-300:]
            return {"error": f"写稿失败：{detail or 'v1 文稿未生成（可能 LLM 调用失败），请重试'}"}
    if not sp["titles"].exists():
        try:
            sm = cat_shop.get_slot(date, slot) if slot else {}
            cat_shop._gen_titles(date, (sm or {}).get("topic") or sel.get("topic") or "",
                                 (sm or {}).get("motif_series") or sel.get("motif_series") or "B",
                                 v1.read_text(encoding="utf-8"), slot=slot or None)
        except Exception as e:
            return {"error": f"标题方案生成失败（不影响已写好的文稿，可稍后在写稿页重试）：{e}"}
    try:
        return exporter.export_article(date, "v1", slot=slot or None)
    except Exception as e:
        return {"error": f"导出失败：{e}"}


@router.post("/oneclick")
def oneclick(req: OneClickReq, request: Request):
    """一键出稿（后台任务，前端轮询 /api/tasks/{tid}）。"""
    date = body_date(request, req.date)
    slot = slot_arg(date, req.slot)
    tid = taskrunner.run_function(one_click, kwargs={"date": date, "slot": slot or None},
                                  label=f"一键出稿 {date} [{slot or '默认'}]",
                                  key=f"oneclick:{date}:{slot or '-'}")  # 单飞：与对话入口互斥
    wb_log("oneclick", date, f"一键出稿 [{slot or '默认'}]")
    from .chat import notify
    notify(date, "台长在写稿页点了「一键出稿」，撰稿人开工了", persona="writer")
    return envelope({"task_id": tid, "slot": slot})


@router.post("/run")
def run_cmd(req: RunCmdReq, request: Request):
    """通用 cat_shop 子命令执行（原 /run 语义，前端按需调用）。"""
    date = body_date(request, req.date)
    cmd = req.cmd
    args = [cmd, "--date", date]
    if cmd in ("collect", "all", "full", "score") and req.force:
        args.append("--force")
    if cmd == "sync" and req.force_sync:
        args.append("--force-sync")
    if cmd == "write":
        if req.v2_angle:
            args += ["--v2-angle", req.v2_angle]
        if req.force_v2:
            args.append("--force-v2")
        slot = req.slot.strip()
        if slot:
            args += ["--slot", slot]
        if req.use_outline:
            args.append("--use-outline")
    if cmd == "analyze" and req.apply_rubric:
        args.append("--apply-rubric")
    if cmd == "learn":
        args += ["--mode", req.mode or "daily"]
    # 其他命令也支持 slot 透传
    if cmd in ("outline", "format", "titles", "sync") and not any(a.startswith("--slot") for a in args):
        slot = req.slot.strip()
        if slot:
            args += ["--slot", slot]
    if cmd == "choose" and req.round.strip():
        args += ["--round", req.round.strip()]
    wb_log("run", date, f"执行 {cmd}")
    from .chat import notify
    notify(date, f"台长在写稿页执行了「{cmd}」")
    tid = taskrunner.run_command(args, label=f"{cmd} ({date})")
    return envelope({"task_id": tid, "cmd": cmd})
