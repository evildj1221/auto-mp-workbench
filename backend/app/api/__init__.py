#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/app/api · 路由层公共约定。

响应信封：成功 {"ok": true, "data": ...}；业务失败 {"ok": false, "error": {...}}；
未捕获异常 → 七分类错误（core.errors）+ 结构化日志（core.logging），与旧版语义一致。
"""
import re as _re

from fastapi import Request
from fastapi.responses import JSONResponse

from core import errors as errcls
from core import logging as clog

from ..common import norm_date, wb_log

_DATE_SHAPE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLOT_SHAPE = _re.compile(r"^w\d{2,3}$")


def envelope(data, ok=True):
    """成功信封。失败请用 fail()——失败响应统一走 error 结构。"""
    assert ok, "失败响应请使用 fail()"
    return {"ok": True, "data": data}


def fail(reason: str, message: str | None = None, **extra):
    """失败信封：{"ok": false, "error": {...}}，与全局异常兜底结构一致。"""
    return {"ok": False,
            "error": {"category": "biz", "reason": reason,
                      "message": message or reason, **extra}}


def envelope_error(category: str, message: str, status_code: int = 400):
    m = errcls.meta(category)
    return JSONResponse(
        status_code=status_code,
        content={"ok": False,
                 "error": {"category": category, "label": m["label"], "hint": m["hint"],
                           "message": message}})


async def uncaught_handler(request: Request, e: Exception) -> JSONResponse:
    """全局异常兜底：七分类 + 结构化写入 workbench_error.log（与旧版 app.py 语义一致）。"""
    from starlette.exceptions import HTTPException as StarletteHTTPException
    if isinstance(e, StarletteHTTPException):
        return JSONResponse(status_code=e.status_code,
                            content={"ok": False, "error": {"category": "http",
                                                            "message": str(e.detail)}})
    try:
        date = norm_date(request.query_params.get("date"))
    except Exception:
        date = "-"
    category = clog.log_error(None, f"{type(e).__name__}: {e}", date=date,
                              context=f"{request.method} {request.url.path}", exc=e)
    m = errcls.meta(category)
    return JSONResponse(
        status_code=500,
        content={"ok": False,
                 "error": {"category": category, "label": m["label"], "hint": m["hint"],
                           "message": f"操作失败（{m['label']}）。详情已记录到 data/workbench_error.log"}})


def q_date(request: Request) -> str:
    """查询参数 date → 规范日期。"""
    return norm_date(request.query_params.get("date"))


def body_date(request: Request, req_date: str | None) -> str:
    """动作路由的日期解析：body 优先，query 兜底（承旧 Flask request.values 语义）。
    安全约定：任何写操作路由都必须经由此函数取日期，测试必须显式传沙箱日期。"""
    d = (req_date or "").strip()
    if d:
        if not _DATE_SHAPE.match(d):
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"非法日期格式: {d}")
        return d
    return q_date(request)


def norm_slot(raw: str | None) -> str:
    """槽位参数规范化：只接受 w01/w02… 形态（阻断拼接进文件路径的穿越输入）。
    空值合法（回退活动槽位由调用方处理）。"""
    s = (raw or "").strip()
    if s and not _SLOT_SHAPE.match(s):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"非法槽位标识: {s}")
    return s


def q_slot(request: Request, date: str, form_slot: str = "") -> str:
    """槽位参数缺省回退当日活动槽位。"""
    from ..common import load_json, slot_arg
    import cat_shop
    raw = form_slot or request.query_params.get("slot") or ""
    if not raw:
        sel = load_json(cat_shop.SELECTIONS_DIR / f"{date}.json", {}) or {}
        raw = sel.get("active_slot") or ""
    return slot_arg(date, raw)


def path_date(request: Request, raw: str | None) -> str:
    """GET /{date} 路径参数归一：形状合法用路径值，否则回退 query/今天——
    曾各路由 raw 使用，/write/2026-9-7 静默渲染空白页、chat 可拼出越界路径。"""
    d = (raw or "").strip()
    if d and _DATE_SHAPE.match(d):
        return d
    return q_date(request)
