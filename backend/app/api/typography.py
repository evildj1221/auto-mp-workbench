#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/app/api/typography.py · 排版中心（原 /typography 系列的 JSON 化）。

主题画廊 + 微调面板 + 组件画廊 + 真实渲染管线预览（渲染逻辑在 core.typography/core.exporter）。
"""
from fastapi import APIRouter, Request

import cat_shop
from core.typography import REGISTRY, ThemeRuntime
from core.typography.manager import get_manager
from core.typography.theme import ThemeError

from ..common import SERIES_LABELS, load_json, wb_log
from ..schemas import (TypographyActivateReq, TypographyDeleteReq,
                       TypographyOverrideReq, TypographyResetReq, TypographySaveReq)
from . import envelope, fail, q_date

router = APIRouter(prefix="/api/typography", tags=["typography"])

TYPO_SAMPLE_MD = (
    "# 排版中心 · 示例信笺\n\n"
    "深夜的窗台有一盏灯，她把今天没说出口的情绪叠好收进抽屉，明天还要早起赶地铁。"
    "楼下的便利店还亮着，玻璃上蒙着一层雾气。\n\n"
    ":::quote\n金句示例：慢一点，也是往前走。\n:::\n\n"
    ":::letter\n亲爱的创作工作台：\n"
    "**今晚特别想写点什么。**\n"
    "白天开了一整天的会，说的话比一年还多。\n"
    "> 落款：一个不敢请假的中年人\n:::\n\n"
    ":::card\n卡片示例\n把要点收进一张卡片里，适合清单与提醒。\n:::\n\n"
    ":::action\n给今晚的自己写一句话，明早再读一遍。\n:::"
)

# 组件画廊演示载荷（scaffold 与 divider 走配置，None 即可）
_TYPO_DEMO_PAYLOADS = {
    "p": "这是一段正文示例，句子在夜里慢慢展开。",
    "h2": "章节标题 · 示例",
    "h3": "小节标题 · 示例",
    "quote": ["金句示例：慢一点，也是往前走。"],
    "letter": ["亲爱的创作工作台：", "**今晚特别想写点什么。**", "> 落款：测试者"],
    "action": ["给今晚的自己写一句话。"],
    "card": ["卡片标题", "卡片正文：把要点收进一张卡片里。"],
    "timeline": ["- 黄昏：写下第一行", "- 深夜：把信叠好", "- 清晨：寄出"],
    "emphasis": ["重点强调示例：这一句值得多看一眼。"],
    "sign": ["创作工作台"],
    "footnote": ["往期回顾：《城市夜灯下的第 7 封信》"],
    "svgslot": ["互动素材位示例", "21:9"],
    "imgtext": ["https://example.com/cover.png", "图注示例"],
    "title": "大标题 · 示例",
}


def _typo_latest_draft():
    files = sorted(cat_shop.DRAFTS_DIR.glob("*_v1.md"))
    return files[-1] if files else None


def _typo_coerce_value(key, value):
    """JSON 字符串 → set_override 期望类型（组件配置按 schema；token 恒为字符串）。"""
    if not key.startswith("components."):
        return value
    parts = key.split(".")
    spec = REGISTRY.get(parts[1]) if len(parts) == 3 else None
    schema = (spec.config_schema or {}).get(parts[2]) if spec else None
    vtype = (schema or {}).get("type")
    if vtype == "bool":
        return value in ("1", "true", "on", "yes")
    if vtype == "list":
        return [ln.strip() for ln in (value or "").splitlines() if ln.strip()]
    return value


def typography_ctx(src: str = "sample", theme_id: str = "") -> dict:
    """排版中心主页数据（原 typography_page() 视图上下文的 JSON 版）。"""
    mgr = get_manager()
    themes = mgr.list_themes()
    ids = {t["id"] for t in themes}
    theme_id = (theme_id or "").strip() or mgr.active_theme_id()
    if theme_id not in ids:
        theme_id = mgr.active_theme_id()
    th = mgr.effective_theme(theme_id)
    rt = ThemeRuntime(th, REGISTRY)

    # 预览文稿：最新存量稿（沿用其当日系列词）或内置示例信笺
    md_text, series, draft_date = TYPO_SAMPLE_MD, "B", None
    if src == "draft":
        p = _typo_latest_draft()
        if p:
            draft_date = p.stem[:-3] if p.stem.endswith("_v1") else p.stem
            sel = load_json(cat_shop.SELECTIONS_DIR / f"{draft_date.split('_')[0]}.json", {}) or {}
            md_text = p.read_text(encoding="utf-8")
            series = sel.get("motif_series") or "B"

    from core import exporter
    html = exporter.render_from_markers(md_text, series=series, theme=th)
    checks = exporter.validate_html(html, th)

    # 组件画廊：按 kind 分组，用预览主题渲染演示载荷
    series_label = SERIES_LABELS.get(series, "晚安陌生人")
    groups = {"block": [], "scaffold": [], "meta": []}
    for spec in REGISTRY.all():
        demo_html = ""
        if spec.render is not None and spec.name != "cover":
            try:
                demo_html = spec.render(_TYPO_DEMO_PAYLOADS.get(spec.name),
                                        rt.context(spec.name, series_label))
            except Exception:
                demo_html = ""
        groups[spec.kind].append({
            "name": spec.name, "label": spec.label, "marker": spec.marker,
            "description": spec.description, "html": demo_html,
            "enabled": rt.config(spec.name).get("enabled", True)})

    # 微调面板：全部颜色 token + 带配置的组件（enabled 布尔开关不进面板，保持轻量）
    config_fields = []
    for spec in REGISTRY.all():
        if spec.kind == "meta":
            continue
        fields = [(k, s) for k, s in (spec.config_schema or {}).items()
                  if s.get("type") != "bool"]
        if fields:
            config_fields.append({
                "label": spec.label, "name": spec.name, "fields": fields,
                "values": {k: rt.config(spec.name).get(k) for k, _ in fields}})

    return {"page": "typography", "themes": themes, "theme_id": theme_id,
            "active_id": mgr.active_theme_id(),
            "has_overrides": bool(mgr.get_overrides(theme_id)),
            "src": src, "draft_date": draft_date, "series": series,
            "series_label": series_label, "preview_html": html,
            "checks_passed": sum(1 for c in checks if c["ok"]),
            "checks_total": len(checks),
            "check_fails": [c["name"] for c in checks if not c["ok"]],
            "component_groups": groups,
            "color_tokens": sorted(th.tokens.get("colors", {}).items()),
            "config_fields": config_fields, "load_errors": mgr.load_errors}


@router.get("")
@router.get("/")
def typography_page(request: Request, src: str = "sample", theme: str = ""):
    return envelope(typography_ctx(src, theme))


@router.post("/activate")
def typography_activate(req: TypographyActivateReq):
    try:
        get_manager().activate_theme(req.theme_id.strip())
        wb_log("typo_activate", "-", f"激活排版主题: {req.theme_id}")
    except ThemeError as e:
        return fail("theme_error", str(e))
    return envelope({"activated": req.theme_id.strip()})


@router.post("/override")
def typography_override(req: TypographyOverrideReq):
    value = _typo_coerce_value(req.key.strip(), req.value)
    if isinstance(value, list) and not value:
        return fail("empty_list", "列表值不能为空（如收尾文案文案）")
    try:
        get_manager().set_override(req.theme_id.strip(), req.key.strip(), value)
        wb_log("typo_override", "-", f"{req.theme_id} · {req.key}")
    except ThemeError as e:
        return fail("theme_error", str(e))
    return envelope({"overridden": req.key.strip()})


@router.post("/reset")
def typography_reset(req: TypographyResetReq):
    get_manager().reset_overrides(req.theme_id.strip())
    wb_log("typo_reset", "-", f"重置排版微调: {req.theme_id}")
    return envelope({"reset": req.theme_id.strip()})


@router.post("/save")
def typography_save(req: TypographySaveReq):
    """把当前预览主题的微调固化为用户主题（base + diff），并自动激活。"""
    try:
        th = get_manager().save_user_theme(req.name.strip(), base_id=req.base_id.strip() or None)
        wb_log("typo_save", "-", f"另存排版主题: {req.name}（base={req.base_id or 'active'}）")
        return envelope({"theme_id": th.id})
    except ThemeError as e:
        return fail("theme_error", str(e))


@router.post("/delete")
def typography_delete(req: TypographyDeleteReq):
    try:
        get_manager().delete_user_theme(req.theme_id.strip())
        wb_log("typo_delete", "-", f"删除排版主题: {req.theme_id}")
    except ThemeError as e:
        return fail("theme_error", str(e))
    return envelope({"deleted": req.theme_id.strip()})
