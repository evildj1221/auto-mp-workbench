#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/publish_tools.py · 发布链路工具（sync / export_mp_html / push_wechat_draft）。

外部系统全部可失败降级：FEISHU_SYNC=0 / lark-cli 未授权 / 微信未配置时
返回 skipped + 明确说明，不抛异常、不阻断主流程。
"""
import json
from datetime import datetime

from langchain_core.tools import tool

from core.paths import DRAFTS_DIR, slot_paths
from pipeline.publish import step_sync
from tools._base import envelope, run_step, tool_error


@tool
def sync_feishu_doc(date: str, slot: str | None = None, force: bool = False) -> dict:
    """把文稿同步到飞书云文档（按月归档，命名 日期_标题）。
    降级：FEISHU_SYNC=0 或 lark-cli 未授权时返回 skipped 说明，不算失败。
    幂等：已同步过自动跳过（force 覆盖重传）。

    Args:
        date: 目标日期，YYYY-MM-DD
        slot: 文稿槽位（默认当日活动槽位）
        force: 覆盖已同步的文档重传
    """
    try:
        ok, log = run_step(step_sync, date=date, slot=slot, force_sync=force)
        if "已关闭" in log:
            return envelope("skipped", "飞书同步已关闭（FEISHU_SYNC=0），本地档案库可查产物", log=log)
        if not ok and ("未授权" in log or "lark" in log.lower()):
            return envelope("skipped", "lark-cli 未授权或不可用，可稍后手动同步", log=log)
        status = "ok" if ok else ("skipped" if "跳过" in log else "error")
        return envelope(status, "飞书云文档同步完成" if ok else "同步未完成，查看 log", log=log)
    except Exception as e:
        return tool_error(e)


@tool
def export_mp_html(date: str, slot: str | None = None, version: str = "v1") -> dict:
    """导出公众号排版 HTML（渲染 → 17 项校验 → 红线 → 落盘 mp.html + 校验报告）。
    导出是发布前最终关卡，含语义红线检查（LLM，失败降级不阻断）。

    Args:
        date: 目标日期，YYYY-MM-DD
        slot: 文稿槽位（默认当日活动槽位）
        version: 文稿版本（v1/v2）
    """
    try:
        from core import exporter
        report = exporter.export_article(date, version=version, slot=slot)
        summary = report.get("summary", {})
        all_ok = summary.get("all_ok", False)
        blocked = report.get("redline", {}).get("should_block", False)
        status = "ok" if (all_ok and not blocked) else "error"
        msg = (f"校验 {summary.get('passed', '?')}/{summary.get('total', '?')} 通过"
               + ("；红线阻断！" if blocked else ""))
        cover = report.get("cover") or {}
        return envelope(status, f"导出完成（{msg}）",
                        checks_summary=summary, word_count=report.get("word_count", 0),
                        redline_blocked=blocked, mp_path=report.get("output", ""),
                        cover=cover)
    except FileNotFoundError as e:
        return envelope("error", f"导出前置缺失: {e}")
    except Exception as e:
        return tool_error(e)


@tool
def push_wechat_draft(date: str, slot: str | None = None) -> dict:
    """把已排版的 mp.html 推送到公众号草稿箱（头图自动上传：正文换微信 URL + 封面素材）。
    降级：微信 API 未配置 / 无可用封面时返回 skipped 说明。幂等：已推送过跳过。

    Args:
        date: 目标日期，YYYY-MM-DD
        slot: 文稿槽位（默认当日活动槽位）
    """
    try:
        from core import wechat
        from core.paths import cover_image_path
        from core.storage import load_json, load_works, save_works
        sp = slot_paths(date, slot)
        if not sp["mp"].exists():
            return envelope("error", f"未排版（无 {sp['mp'].name}），先 export_mp_html")
        health_ok, health_msg = wechat.api_health()
        if not health_ok:
            return envelope("skipped", f"微信 API 不可用（{health_msg}），导出产物已就绪可手动推送")
        ws = load_works(date)
        slots = ws.get("slots", [])
        s = next((x for x in slots if x.get("id") == (slot or "")), None)
        if s is None and not slot:  # 无槽位调用（agent act 直调）：回退当日活动槽位，
            cand = next((x for x in slots if x.get("selected_title") or x.get("topic")), None)  # 幂等登记依赖槽位记录
            if cand is not None and slot_paths(date, cand.get("id"))["mp"].exists():
                slot = cand.get("id")
                s = cand
                sp = slot_paths(date, slot)
        if s is not None and s.get("draft_media_id"):
            return envelope("skipped", f"已推送过（media_id {s['draft_media_id']}）")

        # 头图（v4.2 每篇唯一配图）：本地头图优先 → 两步换装；失败降级素材库首图
        content_html = sp["mp"].read_text(encoding="utf-8")
        # P3 文中配图：本地 file:// 图片先换微信素材 URL（单张失败跳过，不阻塞推送）
        content_html, _n_rep, _rep_err = wechat.replace_local_images(content_html)
        cover_p = cover_image_path(date, slot)
        cover_media_id, cover_note, cerr = None, "", ""
        if cover_p.exists():
            content_html, cover_media_id, cover_note, cerr = wechat.swap_cover_url(
                content_html, cover_p.read_bytes(), cover_p.name)
            if cerr:
                content_html = wechat.strip_cover_img(content_html)
                cover_note = ""
        if not cover_media_id:
            items, cover_err = wechat.list_image_materials(0, 10)
            if cover_err or not items:
                reason = f"素材库无封面图（{cover_err or '空'}）"
                if cerr:
                    reason += f"；头图上传微信失败（{cerr}）"
                return envelope("skipped", f"{reason}，可手动上传后重推")
            cover_media_id = items[0]["media_id"]
            cover_note = f"素材库首图（{items[0].get('name', '?')}）"

        title = ((s or {}).get("selected_title") or (s or {}).get("topic") or "").strip()
        if not title:  # 仍无标题时读当日选题记录，避免草稿箱出现「2026-09-16 」日期兜底标题
            from core.paths import SELECTIONS_DIR
            selj = load_json(SELECTIONS_DIR / f"{date}.json", default={}) or {}
            title = (selj.get("topic") or "").strip() or date
        title = title[:64]
        media_id, err = wechat.draft_add(title, content_html,
                                         cover_media_id)
        if err:
            return envelope("error", f"草稿推送失败: {err}")
        if s is not None:
            s["draft_media_id"] = media_id
            s["pushed_at"] = datetime.now().isoformat(timespec="seconds")
            save_works(date, ws)
        log_path = DRAFTS_DIR / f"{date}_push_log.jsonl"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"date": date, "slot": slot or "", "title": title,
                                "media_id": media_id, "cover": cover_note,
                                "pushed_at": datetime.now().isoformat(timespec="seconds")},
                               ensure_ascii=False) + "\n")
        return envelope("ok", f"已推送公众号草稿箱（{title} · 封面：{cover_note or '未标注'}）",
                        media_id=media_id, title=title)
    except Exception as e:
        return tool_error(e)
