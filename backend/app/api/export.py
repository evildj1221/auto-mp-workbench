#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/app/api/export.py · 公众号导出与草稿箱推送（原 /export 系列的 JSON 化）。"""
import json
import os
import re
import sys
from datetime import datetime

from fastapi import APIRouter, Request, UploadFile

import cat_shop
from core import exporter
from core import wechat

from .. import taskrunner
from ..common import load_json, published_path, slot_arg, wb_log
from ..schemas import ExportRunReq
from . import body_date, envelope, fail, norm_slot, path_date, q_date

router = APIRouter(prefix="/api/export", tags=["export"])


def _httpize_local_images(html: str) -> str:
    """预览 HTML 里的 file:// 本地图（头图/文中图）改写为 /api/archive/file 路由。
    http(s) 页面直接引用 file:// 会被浏览器拦截，SPA 预览必须走后端读文件。
    file:/// 里的中文/空格是百分号编码的，先 unquote 再比 data_root
    （否则 data/images 下永远匹配不上，预览图全裂）。"""
    from urllib.parse import unquote
    data_root = cat_shop.DATA_DIR.resolve().as_posix().rstrip("/")
    def _repl(m):
        p = unquote(m.group(1)).lstrip("/")  # 捕获组自带前导 /（file:// + /D:/...），不去掉永远匹配不上
        if p.startswith(data_root):
            return f"/api/archive/file?path={p[len(data_root):].lstrip('/')}"
        return m.group(0)
    return re.sub(r'file://(/[A-Za-z]:/[^"\')\s]+)', _repl, html)


def export_ctx(date: str, slot: str, version: str = "v1") -> dict:
    """导出页数据（原 export_page() 视图上下文的 JSON 版）。"""
    slots_meta = cat_shop.load_works(date).get("slots", [])
    if not slot and slots_meta:
        # 活动槽位缺省时回退首个槽位，保证预览与高亮始终有明确指向
        slot = slots_meta[0]["id"]
    sp = cat_shop.slot_paths(date, slot or None)
    report = load_json(sp["mp_report"], None)
    html = sp["mp"].read_text(encoding="utf-8") if sp["mp"].exists() else ""
    html = _httpize_local_images(html)
    md_exists = {v: sp[v].exists() for v in ("v1", "v2")}
    pub = load_json(published_path(date, slot), {}) or {}
    push_log = None
    plp = cat_shop.DRAFTS_DIR / f"{date}_push_log.jsonl"
    if plp.exists():
        lines = [l for l in plp.read_text(encoding="utf-8").strip().split("\n") if l.strip()]
        if lines:
            try:
                push_log = json.loads(lines[-1])
            except Exception:
                pass
    return {"page": "export", "date": date, "version": version, "html": html,
            "report": report, "md_exists": md_exists, "pub": pub, "slot": slot,
            "push_log": push_log, "slots_meta": slots_meta,
            "feishu_sync": os.environ.get("FEISHU_SYNC", "1") == "1"}


@router.get("/{date}")
def get_export(request: Request, date: str, slot: str = "", version: str = "v1"):
    d = path_date(request, date)
    s = slot_arg(d, slot)
    return envelope(export_ctx(d, s, version))


@router.get("/{date}/raw")
def export_raw(request: Request, date: str, slot: str = ""):
    """供复制/预览的原始 HTML（text/html；旧数据无槽位文件时回退默认路径）。"""
    from fastapi.responses import HTMLResponse
    d = path_date(request, date)
    s = slot_arg(d, slot)
    p = cat_shop.slot_paths(d, s or None)["mp"]
    if not p.exists() and s:
        p = cat_shop.slot_paths(d, None)["mp"]
    if not p.exists():
        return fail("notfound", "导出文件不存在")
    return HTMLResponse(_httpize_local_images(p.read_text(encoding="utf-8")))


@router.post("/run")
def export_run(req: ExportRunReq, request: Request):
    """生成公众号导出版（后台任务）。"""
    date = body_date(request, req.date)
    slot = slot_arg(date, req.slot)
    tid = taskrunner.run_function(exporter.export_article,
                                  kwargs={"date": date, "version": req.version,
                                          "slot": slot or None,
                                          "with_cover": bool(req.with_cover)},
                                  label=f"公众号导出 {date} ({req.version}"
                                        f"{'' if req.with_cover else ' · 无配图'})")
    wb_log("export", date, f"生成公众号导出版（{req.version}）")
    from .chat import notify
    notify(date, f"台长在导出页渲染排版（{slot or '默认'}），发行装稿中", persona="publisher")
    return envelope({"task_id": tid, "slot": slot, "version": req.version})


@router.post("/push")
async def draft_push(request: Request, cover: UploadFile | None = None):
    """P0-2 延伸：把最终 mp.html 直写进公众号草稿箱。
    封面：用户本地上传 → 生成头图（v4.2 每篇唯一配图，自动换正文 URL）→ 素材库第一张。"""
    form = await request.form()
    date = q_date(request) if not form.get("date") else str(form.get("date"))
    slot = slot_arg(date, str(form.get("slot") or ""))
    cover_file = form.get("cover")
    sp = cat_shop.slot_paths(date, slot or None)
    mp_path = sp["mp"]
    if not mp_path.exists():
        return fail("nofile", "导出文件不存在，请先生成导出")
    content_html = mp_path.read_text(encoding="utf-8")
    # P3 文中配图：本地 file:// 图片先换微信素材 URL（单张失败跳过，不阻塞推送）
    content_html, _n_rep, _rep_err = wechat.replace_local_images(content_html)

    # 标题（优先已选定标题）
    pub_path = published_path(date, slot)
    pub = load_json(pub_path, {}) or {}
    title = pub.get("title") or ""
    if not title:
        m = re.search(r'font-size:20px[^>]*>([^<]+)<', content_html)
        title = m.group(1).strip() if m else f"{date} 你的公众号"

    # 封面：本地上传 → 生成头图（两步换装）→ 素材库第一张兜底
    cover_media_id = None
    cover_note = ""
    cover_p = cat_shop.cover_image_path(date, slot or None)
    if cover_file is not None and getattr(cover_file, "filename", ""):
        data = await cover_file.read()
        content_html, cover_media_id, _note, cerr = wechat.swap_cover_url(
            content_html, data, cover_file.filename)
        if cerr:
            return fail("covererr", cerr[:150])
        cover_note = f"本地上传封面（{cover_file.filename}）"
    elif cover_p.exists():
        content_html, cover_media_id, _note, cerr = wechat.swap_cover_url(
            content_html, cover_p.read_bytes(), cover_p.name)
        if cerr:
            return fail("covererr", cerr[:150])
        cover_note = f"生成头图（{cover_p.name}）"
    else:
        items, err = wechat.list_image_materials(0, 10)
        if err or not items:
            return fail("nocover", err or "素材库无图片")
        cover_media_id = items[0]["media_id"]
        cover_note = f"素材库首图（{items[0].get('name', '?')}）"
        # 无本地头图：正文里可能残留 file:// 头图，微信内无法显示
        content_html = wechat.strip_cover_img(content_html)

    # P-04：已有草稿 → 删旧建新（draft/update 对未认证号实测 47001 不可用）
    existing = pub.get("draft_media_id")
    action = "新建草稿"
    if existing:
        del_data, del_err = wechat._api_post("/cgi-bin/draft/delete", {"media_id": existing})
        if del_data and del_data.get("errcode") == 0:
            action = "替换草稿（删旧建新）"
        else:
            print(f"  [WARN] 旧草稿删除失败（{del_err}），继续新建", file=sys.stderr)
    draft_media_id, err = wechat.draft_add(title, content_html, cover_media_id,
                                           digest=title)
    if err:
        return fail("fail", err[:200])
    pub.update({"draft_media_id": draft_media_id, "draft_title": title,
                "draft_cover_note": cover_note,
                "draft_pushed_at": datetime.now().isoformat(timespec="seconds")})
    cat_shop.write_json(pub_path, pub)
    # 同步槽位 draft_media_id
    if slot:
        ws = cat_shop.load_works(date)
        for s in ws["slots"]:
            if s["id"] == slot:
                s["draft_media_id"] = draft_media_id
                s["updated_at"] = datetime.now().isoformat(timespec="seconds")
        cat_shop.save_works(date, ws)
    wb_log("draft_push", date, f"[{slot or '默认'}] {action}：{title}（封面：{cover_note}）")
    # 自动归档：推送成功后若 FEISHU_SYNC 开（默认 1），后台归档飞书云文档（step_sync 幂等）
    task_id = ""
    if os.environ.get("FEISHU_SYNC", "1") == "1":
        task_id = taskrunner.run_command(
            ["sync", "--date", date] + (["--slot", slot] if slot else []),
            label=f"自动归档飞书 {date}" + (f" [{slot}]" if slot else ""))
        wb_log("draft_push", date,
               f"[{slot or '默认'}] 推送草稿箱后自动触发飞书云文档归档 tid={task_id}")
    return envelope({"action": action, "title": title, "draft_media_id": draft_media_id,
                     "cover_note": cover_note, "feishu_task_id": task_id, "slot": slot})


@router.post("/plan-reset")
def inline_plan_reset(request: Request, date: str = "", slot: str = ""):
    """换一批配图：删除 image_plan 与已生成图片，下次导出重新规划生成。"""
    from core.inline_images import delete_plan, load_plan
    d = q_date(request)
    s = norm_slot(slot_arg(d, slot))  # 加固（P4）：slot 进入 unlink 路径前先规范化
    old = load_plan(d, s or None)
    delete_plan(d, s or None)
    wb_log("inline_plan_reset", d, f"[{s or '默认'}] 清除配图计划（{len(old or [])} 张），下次导出重新生成")
    return envelope({"reset": True, "removed": len(old or []),
                     "note": "下次生成导出时将重新规划并生成配图"})


@router.post("/push-all")
async def draft_push_all(request: Request):
    """批量把当日所有已排版文稿写入公众号草稿箱。
    每篇独立推送（互不覆盖），记录 media_id + 时间戳到 push_log.jsonl 可追溯。"""
    date = q_date(request)
    ws = cat_shop.load_works(date)
    slots = ws.get("slots", [])
    results, ok_count = [], 0
    items, cover_err = wechat.list_image_materials(0, 10)
    for s in slots:
        sp = cat_shop.slot_paths(date, s["id"])
        if not sp["mp"].exists():
            results.append({"slot": s["id"], "status": "skip", "reason": "未排版（无 mp.html）"})
            continue
        title = (s.get("selected_title") or s.get("topic") or f"{date} {s['id']}")[:64]
        content_html = sp["mp"].read_text(encoding="utf-8")
        # P3 文中配图：本地 file:// 图片先换微信素材 URL
        content_html, _n_rep, _rep_err = wechat.replace_local_images(content_html)
        # 头图（v4.2）：每槽位各自的生成头图优先换装；无则素材库首图 + 去残留头图
        cover_p = cat_shop.cover_image_path(date, s["id"])
        cover_media_id = None
        if cover_p.exists():
            content_html, cover_media_id, _note, cerr = wechat.swap_cover_url(
                content_html, cover_p.read_bytes(), cover_p.name)
            if cerr:
                content_html = wechat.strip_cover_img(content_html)
        if not cover_media_id:
            if cover_err or not items:
                results.append({"slot": s["id"], "status": "fail",
                                "reason": cover_err or "素材库无封面图"})
                continue
            cover_media_id = items[0]["media_id"]
            content_html = wechat.strip_cover_img(content_html)
        media_id, err = wechat.draft_add(title, content_html, cover_media_id)
        if err:
            results.append({"slot": s["id"], "status": "fail", "reason": err})
            continue
        ts = datetime.now().isoformat(timespec="seconds")
        s["draft_media_id"] = media_id
        s["pushed_at"] = ts
        results.append({"slot": s["id"], "title": title, "media_id": media_id,
                        "pushed_at": ts, "status": "ok"})
        ok_count += 1
    cat_shop.save_works(date, ws)
    log = {"date": date, "pushed_at": datetime.now().isoformat(timespec="seconds"),
           "ok_count": ok_count, "total": len(slots), "results": results}
    log_path = cat_shop.DRAFTS_DIR / f"{date}_push_log.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(log, ensure_ascii=False) + "\n")
    wb_log("draft_push_all", date, f"批量推送草稿箱 {ok_count}/{len(slots)} 篇")
    return envelope({"ok_count": ok_count, "total": len(slots), "results": results})
