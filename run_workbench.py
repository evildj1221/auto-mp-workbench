#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
创作工作台 · 桌面客户端启动器
================================
   双击 exe  = 窗口模式（桌面窗口 + 工作台界面）
   命令行模式：exe collect --date 2026-08-17   （直接调用 cat_shop 子命令，输出到 stdout/日志后退出）

关键设计：frozen 模式下 taskrunner 需要调用 cat_shop 子命令时，spawn 同一个 exe + 子命令参数，
exe 检测到 CLI 模式后 exec cat_shop.py 源文件复用其 argparse+handlers，调用 DeepSeek 等 LLM。

为什么不用 spawn "python cat_shop.py"：frozen 模式下没有独立 python 解释器。
为什么不用 in-process 直接调 cat_shop 函数：taskrunner 在工作台主进程里跑，调函数会
阻塞服务响应线程；子进程隔离更稳。
"""
import os
import socket
import sys
import threading
import time
from pathlib import Path


# P0 修复：PyInstaller 打包后 certifi.where() 指向 _MEIPASS 内的虚拟路径，requests
# 调用 DeepSeek/微信 API 时抛 OSError: Could not find a suitable TLS CA certificate bundle。
# spec 已把 cacert.pem 打包到 _MEIPASS/certifi/，这里强制环境变量指向真实位置。
if getattr(sys, "frozen", False):
    _meipass = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    _cert_path = os.path.join(_meipass, "certifi", "cacert.pem")
    if os.path.exists(_cert_path):
        os.environ["REQUESTS_CA_BUNDLE"] = _cert_path
        os.environ["SSL_CERT_FILE"] = _cert_path


# ===== 模式判断 =====
CAT_SHOP_SUBCOMMANDS = {
    "setup", "collect", "score", "choose",
    "feedback", "feedback-dedup", "analyze", "write", "format",
    "learn", "status", "sync", "all", "full", "redline", "redline-semantic",
    "titles", "new-batch", "regenerate-batch", "outline",
    "ccp",  # 商业化阶段一：客户内容档案 CCP（引导/投喂/闭环）
    "agent",  # v4.0 项目级 Agent（taskrunner 后台也会 spawn 此子命令）
}


def _app_root():
    """项目根目录（cat_shop.py 所在）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _is_cli_mode():
    """exe 双模式判断：首参为子命令即 CLI 模式。
    防御：首参是 .py 脚本路径、次参为子命令时也视为 CLI（防止误把 cat_shop.py
    路径传给 exe 导致其进入窗口模式、弹出新的工作台窗口）。"""
    if len(sys.argv) > 1 and sys.argv[1] in CAT_SHOP_SUBCOMMANDS:
        return True
    if (len(sys.argv) > 2 and sys.argv[1].endswith(".py")
            and sys.argv[2] in CAT_SHOP_SUBCOMMANDS):
        return True
    return False


def _find_port(start=8765, end=8785):
    for p in range(start, end):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", p))
                return p
        except OSError:
            continue
    return start


# ===== CLI 模式：执行 cat_shop 子命令 =====
def _run_cli():
    """复用 cat_shop.py 的 argparse + handlers（exec 源文件，隔离 __name__='__main__'）"""
    """
    cat_shop.py 顶层会执行 load_dotenv、mkdir 等，是幂等的；handlers 在 main() 内部定义，
    所以这里通过 exec 源文件触发 main() 让它自己构造。
    """
    root = _app_root()
    cat_shop_path = root / "cat_shop.py"
    if not cat_shop_path.exists():
        print(f"ERROR: 未找到 {cat_shop_path}（exe 需与 cat_shop.py 同目录）", file=sys.stderr)
        sys.exit(1)

    # cat_shop.main() 会自己 argparse sys.argv，我们只需把它设好
    # 防御：若首参是 .py 脚本路径（误传），剥掉，确保 argv[1] 是子命令
    rest = sys.argv[1:]
    if rest and rest[0].endswith(".py") and len(rest) > 1:
        rest = rest[1:]
    sys.argv = [str(cat_shop_path)] + rest

    # 用独立 globals（__name__='__main__'），避免与已 import 的 cat_shop 冲突
    with open(cat_shop_path, "r", encoding="utf-8") as f:
        code = compile(f.read(), str(cat_shop_path), "exec")
    main_globals = {"__name__": "__main__", "__file__": str(cat_shop_path)}
    # 共享常用 stdlib；cat_shop 顶层 import 会自行加载其余依赖
    main_globals.update({k: v for k, v in globals().items()
                         if k in ("sys", "os", "json", "re", "time", "threading",
                                  "logging", "traceback", "subprocess", "statistics",
                                  "datetime")})
    exec(code, main_globals)


# ===== 窗口模式：FastAPI(uvicorn) + pywebview =====
def _run_window():
    root = _app_root()
    os.chdir(str(root))
    sys.path.insert(0, str(root))

    # 商业化阶段一：CCP 冷启动建档 + 吸收既有资产（幂等，不覆盖人工数据）
    try:
        from core.ccp import ensure_startup
        ensure_startup()
    except Exception as e:  # noqa: BLE001 —— 启动钩子失败仅告警，绝不阻塞工作台
        print(f"[ccp] ensure_startup 跳过：{e}")

    port = _find_port()

    def run_uvicorn():
        import uvicorn
        from backend.app.main import app
        logging = __import__("logging")
        logging.getLogger("uvicorn.access").setLevel(logging.ERROR)
        logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

    threading.Thread(target=run_uvicorn, daemon=True).start()

    import requests as req
    url = f"http://127.0.0.1:{port}/"
    for _ in range(50):
        try:
            req.get(url, timeout=0.5)
            break
        except Exception:
            time.sleep(0.1)

    import webview
    # 所有 target=_blank 外链（如飞书云文档）在系统默认浏览器打开，
    # 绝不弹出新的 pywebview 工作台窗口（工作台功能全部留在当前窗口）
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True

    class _WindowApi:
        """无边框窗口控制按钮的后端（前端标题栏经 window.pywebview.api 调用）。"""

        def __init__(self):
            self._win = None

        def bind(self, w):
            self._win = w

        def minimize(self):
            if self._win:
                self._win.minimize()

        def toggle_max(self):
            if self._win:
                self._win.toggle_fullscreen()

        def close(self):
            if self._win:
                self._win.destroy()

    win_api = _WindowApi()
    win = webview.create_window(
        title="创作工作台",
        url=url, width=1280, height=860, min_size=(1000, 700), text_select=True,
        frameless=True, easy_drag=False,   # 无系统边框：拖拽/窗口控制由前端标题栏接管
        background_color="#8ea8e8",        # 启动底色跟天光同色，消灭白底闪烁
        js_api=win_api,                    # pywebview 6.x：js_api 挂在 create_window 上
    )
    win_api.bind(win)
    webview.start(icon="assets/cat.ico")


def _fix_std_streams():
    """关键修复：PyInstaller windowed 模式（console=False）下 sys.stdout/stderr 为 None，
    cat_shop 等模块里的 print() 会抛 AttributeError 导致 Flask 500。
    重定向到 data/workbench_console.log（CLI 模式子进程有管道不受影响）。

    P0 修复：errors='replace' 避免写入非 UTF-8 字节触发 OSError [Errno 22] Invalid argument
    （Windows 在某些 _MEIPASS 路径下对编码严格）。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    root = _app_root()
    log_dir = root / "data"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        _log = open(log_dir / "workbench_console.log", "a", encoding="utf-8",
                    errors="replace", buffering=1)
    except Exception:
        import io
        _log = io.StringIO()
    if sys.stdout is None:
        sys.stdout = _log
    if sys.stderr is None:
        sys.stderr = _log


def main():
    _fix_std_streams()
    if _is_cli_mode():
        _run_cli()
        return
    _run_window()


if __name__ == "__main__":
    main()