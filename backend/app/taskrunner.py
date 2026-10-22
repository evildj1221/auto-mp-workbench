#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/app/taskrunner.py · 异步任务运行器（自 workbench/taskrunner.py 原样迁入）。

- 子进程任务：python cat_shop.py <cmd> ...（分钟级命令后台跑，前端轮询 /api/tasks/{tid}）
- 函数任务：进程内函数（如导出器），后台线程执行
"""
import codecs
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).resolve().parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent
PYTHON = sys.executable

_tasks = {}
_lock = threading.Lock()

MAX_OUTPUT_CHARS = 20000  # 输出截断，防内存膨胀
MAX_TASKS_KEPT = 200      # 内存任务表上限（超过丢最旧的已完结任务）


def _find_running_by_key(key: str) -> str | None:
    with _lock:
        for t in _tasks.values():
            if t.get("key") == key and t["status"] == "running":
                return t["id"]
    return None


def _new_task(kind, label, key=None):
    tid = uuid.uuid4().hex[:8]
    with _lock:
        if len(_tasks) >= MAX_TASKS_KEPT:  # 只清已完结的，运行中永不丢
            finished = sorted((t for t in _tasks.values() if t["status"] != "running"),
                              key=lambda t: t["started_epoch"])
            for t in finished[:len(_tasks) - MAX_TASKS_KEPT + 1]:
                _tasks.pop(t["id"], None)
        _tasks[tid] = {
            "id": tid, "kind": kind, "label": label, "key": key,
            "status": "running",  # running / done / error
            "output": "", "result": None,
            "started_epoch": time.time(),   # epoch：跨午夜排序正确（%H:%M:%S 曾致错序）
            "started_at": time.strftime("%H:%M:%S"),
            "finished_at": None,
        }
    return tid


def _append(tid, text):
    with _lock:
        t = _tasks.get(tid)
        if t is None:
            return
        t["output"] = (t["output"] + text)[-MAX_OUTPUT_CHARS:]


def _finish(tid, status, result=None):
    with _lock:
        t = _tasks.get(tid)
        if t is None:
            return
        t["status"] = status
        t["result"] = result
        t["finished_at"] = time.strftime("%H:%M:%S")


def run_command(args, label=None, key=None):
    """后台子进程跑 cat_shop 子命令。args 例: ['score', '--date', '2026-08-16']。
    frozen 模式下没有独立 python 解释器，spawn 同一个 exe（带子命令参数），
    run_workbench.py 检测到子命令后走 CLI 模式执行。
    key：单飞键——同 key 任务在跑时直接返回现有 tid（防双入口并发双跑）。"""
    label = label or " ".join(args)
    if key and (rid := _find_running_by_key(key)):
        return rid
    tid = _new_task("cmd", label, key=key)

    def worker():
        try:
            if getattr(sys, "frozen", False):
                cmd = [sys.executable] + list(args)
            else:
                cmd = [PYTHON, str(SCRIPT_DIR / "cat_shop.py")] + list(args)
            # windowed exe 子进程走 PIPE 不稳定（PyInstaller 6.x bootloader 虚拟句柄），
            # 改用任务专属临时日志文件 + 等待结束两阶段：
            # 1) 阶段1：tail 日志文件实时回灌 task output
            # 2) 阶段2：proc.wait() 后再读一次末尾确保完整
            tmp_dir = SCRIPT_DIR / "data" / "tasks"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp_log = tmp_dir / f"{tid}.log"
            tmp_log.write_text("", encoding="utf-8")
            with open(tmp_log, "w", encoding="utf-8", errors="replace") as f:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(SCRIPT_DIR),
                    stdout=f, stderr=subprocess.STDOUT,
                    env={"PYTHONIOENCODING": "utf-8", **os.environ},
                )
            # 增量 tail：只读新写入的字节（曾经的整文件每 0.8s 重读是后台线程
            # 的隐性 IO 放大器，LLM 步骤输出多时达 MB 级）
            rf = open(tmp_log, "rb")
            dec = codecs.getincrementaldecoder("utf-8")("replace")
            try:
                while proc.poll() is None:
                    try:
                        chunk = rf.read()
                        if chunk:
                            _append(tid, dec.decode(chunk))
                    except Exception:
                        pass
                    time.sleep(0.8)
                try:
                    tail = dec.decode(rf.read(), True)
                    if tail:
                        _append(tid, tail)
                except Exception:
                    pass
            finally:
                rf.close()
            _finish(tid, "done" if proc.returncode == 0 else "error",
                    {"returncode": proc.returncode})
            try:
                tmp_log.unlink()
            except OSError:
                pass
        except Exception:
            _append(tid, traceback.format_exc())
            _finish(tid, "error")

    threading.Thread(target=worker, daemon=True).start()
    return tid


def run_function(fn, kwargs=None, label=None, key=None):
    """后台线程跑进程内函数（如导出器）。fn 的返回/异常会记录到 result/output。
    key：单飞键——同 key 任务在跑时直接返回现有 tid（防双入口并发双跑）。"""
    label = label or getattr(fn, "__name__", "function")
    if key and (rid := _find_running_by_key(key)):
        return rid
    tid = _new_task("func", label, key=key)

    def worker():
        try:
            result = fn(**(kwargs or {}))
            _append(tid, f"✅ 完成\n{result}\n" if result is not None else "✅ 完成\n")
            _finish(tid, "done", {"ok": True})
        except Exception:
            _append(tid, traceback.format_exc())
            _finish(tid, "error")

    threading.Thread(target=worker, daemon=True).start()
    return tid


def get_task(tid):
    with _lock:
        t = _tasks.get(tid)
        return dict(t) if t else None


def list_tasks(limit=20):
    with _lock:
        items = sorted(_tasks.values(), key=lambda t: t.get("started_epoch", 0), reverse=True)
        return [dict(t) for t in items[:limit]]
