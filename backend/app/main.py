#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/app/main.py · 创作工作台 FastAPI 应用装配。

- 挂载全部 /api 路由（JSON + SSE），托管 frontend/dist（SPA，history 路由回退 index.html）
- 全局异常兜底：七分类错误 + 结构化日志（core.errors / core.logging）
- 桌面窗口模式：run_workbench.py 用 uvicorn 启动本应用后以 pywebview 加载
"""
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).resolve().parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent

from .api import uncaught_handler  # noqa: E402
from .api import (agent, archive, chat, dashboard, drafts,  # noqa: E402
                  export, review, settings, tasks, topics, typography, uploads)

app = FastAPI(title="创作工作台 API", version="5.0",
              docs_url="/api/docs", openapi_url="/api/openapi.json")

app.add_exception_handler(Exception, uncaught_handler)


@app.middleware("http")
async def local_origin_guard(request: Request, call_next):
    """本地服务加固（P4 对抗性审查）：
    - Host 必须是 127.0.0.1/localhost（阻断 DNS rebinding：恶意域名解析到
      127.0.0.1 后借浏览器携带的 Cookie/内网权限访问工作台）
    - 写操作（POST/PUT/DELETE）若携带 Origin 头，必须同源（阻断外部网页
      对 127.0.0.1 的跨站 CSRF——简单表单请求不需要 CORS 预检即可到达）
    - 非 /api 路径（SPA 静态资源）同样校验 Host，Origin 规则仅写操作需要
    """
    host = (request.headers.get("host") or "").lower()
    host_ok = any(host.startswith(h) for h in ("127.0.0.1:", "localhost:"))
    if not host_ok:
        return JSONResponse(status_code=403,
                            content={"ok": False, "error": {
                                "category": "forbidden",
                                "message": f"非法访问来源 Host: {host or '缺失'}"}})
    if request.method in ("POST", "PUT", "DELETE"):
        origin = request.headers.get("origin")
        if origin and not origin.lower().startswith(("http://127.0.0.1:", "http://localhost:")):
            return JSONResponse(status_code=403,
                                content={"ok": False, "error": {
                                    "category": "forbidden",
                                    "message": "跨站写操作已被拒绝"}})
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_handler(request, exc):
    return JSONResponse(status_code=422,
                        content={"ok": False,
                                 "error": {"category": "invalid_input",
                                           "label": "参数错误",
                                           "message": str(exc.errors()[:3])}})


@app.get("/api/health", tags=["system"])
def health():
    return {"ok": True, "data": {"status": "up"}}


for r in (agent.router, chat.router, topics.router, drafts.router, export.router,
          review.router, archive.router, settings.router, typography.router,
          dashboard.router, tasks.router, uploads.router):
    app.include_router(r)


@app.api_route("/api/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE"],
               include_in_schema=False)
def api_fallback(full_path: str):
    """未匹配的 /api/* 一律 404 JSON——SPA 回退绝不能吞掉 API 错误。"""
    return JSONResponse(status_code=404,
                        content={"ok": False,
                                 "error": {"category": "notfound",
                                           "message": f"未知 API 路由: /api/{full_path}"}})

# ===== SPA 静态托管（frontend/dist；开发期由 vite dev server 代理 /api） =====
SPA_DIST = SCRIPT_DIR / "frontend" / "dist"

if SPA_DIST.exists():
    app.mount("/assets", StaticFiles(directory=SPA_DIST / "assets"), name="spa-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        """history 路由回退：有文件给文件，其余回 index.html（/api 由路由优先匹配）。"""
        candidate = (SPA_DIST / full_path).resolve()
        try:
            candidate.relative_to(SPA_DIST.resolve())  # 边界校验：../%2e%2e 曾可读 dist 外文件
        except ValueError:
            return FileResponse(SPA_DIST / "index.html")
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(SPA_DIST / "index.html")
