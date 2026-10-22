#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/image_tools.py · 头图工具（v4.2 M1 · 每篇一张，文中图已取消）。

外部生图 API 可失败降级：未配置 IMAGE_API_KEY 时返回 skipped + 说明，
不抛异常、不阻断写稿与发布链路（导出无头图照常，可 force 补生成）。
"""
from langchain_core.tools import tool

from pipeline.images import generate_cover
from tools._base import envelope, tool_error


@tool
def generate_cover_image(date: str, slot: str | None = None, force: bool = False) -> dict:
    """为文稿生成公众号头图（每篇唯一配图 · 21:9 · 治愈系插画风治愈插画 · 无文字）。
    流程：读选题与正文 → LLM 提炼场景 → 生图 API → 落盘 data/images/。
    建议在 export_mp_html 之前调用（导出自动把头图插在篇首分隔符后）。
    降级：未配置 IMAGE_API_KEY 返回 skipped；失败不阻断，可 force 重试。

    Args:
        date: 目标日期，YYYY-MM-DD
        slot: 文稿槽位（默认当日活动槽位）
        force: 覆盖已有头图重新生成
    """
    try:
        from llm.image_provider import image_configured
        if not image_configured():
            return envelope("skipped",
                            "未配置 IMAGE_API_KEY（.env 写入火山方舟 key 后重启），"
                            "头图暂缺不阻断发布，可稍后补生成")
        res = generate_cover(date, slot=slot, force=force)
        if res.get("skipped"):
            return envelope("skipped", res["skipped"], cover_path=res.get("path", ""))
        return envelope("ok", f"头图已生成（{res['path']}）",
                        cover_path=res["path"])
    except Exception as e:
        return tool_error(e)
