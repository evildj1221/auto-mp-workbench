#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/logging.py · 结构化日志统一入口（v4.0）。

三条通道、各有分工：
  1. log_event  → data/logs/app.jsonl        技术事件流（info/warn/error，全模块共用）
  2. wb_log     → data/workbench_log.jsonl   业务操作日志（UI「最近操作」展示，格式兼容 v3）
  3. log_error  → data/workbench_error.log   结构化错误（JSON lines + 七分类，替代旧纯文本）

CLI 双写约定：warn/error 级事件同时 print 到 stdout——taskrunner 靠读子进程
stdout 临时文件轮询进度，这条通道不能断（exe windowed 模式下尤其重要）。
info 级不 print（Flask 进程内会刷屏）；pipeline 步骤的原有 print 全部保留不动。

写入失败一律静默吞掉：日志永远不能反过来弄挂业务主流程。
"""
import json
import sys
import traceback
from datetime import datetime

from core import errors as _errors
from core.paths import DATA_DIR

LOGS_DIR = DATA_DIR / "logs"
APP_LOG = LOGS_DIR / "app.jsonl"
WB_LOG = DATA_DIR / "workbench_log.jsonl"
ERROR_LOG = DATA_DIR / "workbench_error.log"

_MAX_LINE = 4000  # detail 超长截断，防单条日志膨胀


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _trim(text):
    text = str(text or "")
    return text if len(text) <= _MAX_LINE else text[:_MAX_LINE] + "…（截断）"


def log_event(level, event, date="-", detail="", echo=None, **extra):
    """技术事件 → data/logs/app.jsonl。

    echo=None 时按级别决定是否 print 双写（warn/error 必写 stdout）。
    extra 里的键并入日志行（如 tool=write_draft、steps_used=3）。
    """
    line = {"ts": datetime.now().isoformat(timespec="seconds"),
            "level": level, "event": event, "date": date,
            "detail": _trim(detail)}
    if extra:
        line.update(extra)
    try:
        _write(APP_LOG, line)
    except Exception:
        pass
    if echo is None:
        echo = level in ("warn", "error")
    if echo:
        try:
            print(f"[{level.upper()}] {event}" + (f"：{_trim(detail)[:200]}" if detail else ""),
                  file=sys.stdout, flush=True)
        except Exception:
            pass


def wb_log(action, date="-", detail=""):
    """业务操作日志 → data/workbench_log.jsonl（决策审计，UI 直接展示）。

    格式与 v3 完全一致（ts/action/date/detail），同时转发一条 info 事件到 app.jsonl。
    app.py 与 agent_api.py 的本地实现统一收敛到这里，单一写入点。
    """
    try:
        _write(WB_LOG, {"ts": datetime.now().isoformat(timespec="seconds"),
                        "action": action, "date": date, "detail": str(detail or "")})
    except Exception:
        pass
    log_event("info", action, date=date, detail=detail, echo=False)


def log_error(category, message, date="-", context="", exc=None):
    """结构化错误 → data/workbench_error.log（JSON lines + 七分类 + traceback）。

    category 传 None/未知值时自动重新分类。返回该错误的类别（调用方可用于 UI 文案）。
    """
    cat = category if category in _errors.CATEGORIES else _errors.classify(exc, message, context)
    m = _errors.meta(cat)
    line = {"ts": datetime.now().isoformat(timespec="seconds"),
            "category": cat, "label": m["label"], "disposal": m["disposal"],
            "date": date, "context": context, "message": _trim(message),
            "hint": m["hint"]}
    if exc is not None:
        line["traceback"] = _trim(traceback.format_exc())
    try:
        _write(ERROR_LOG, line)
    except Exception:
        pass
    log_event("error", "error", date=date,
              detail=f"[{cat}] {message}", context=context)
    return cat


def read_wb_log(limit=100):
    """读业务操作日志尾部（倒序，最新在前；坏行跳过）。"""
    if not WB_LOG.exists():
        return []
    try:
        lines = WB_LOG.read_text(encoding="utf-8", errors="replace").strip().split("\n")
    except Exception:
        return []
    out = []
    for line in reversed(lines[-limit:]):
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def read_errors(limit=50, date=None):
    """读最近错误（跳过旧版纯文本行）。倒序返回，最新在前。"""
    if not ERROR_LOG.exists():
        return []
    out = []
    try:
        lines = ERROR_LOG.read_text(encoding="utf-8", errors="replace").strip().split("\n")
    except Exception:
        return []
    for line in reversed(lines[-limit * 3:]):  # 旧纯文本行会占名额，多读再筛
        if len(out) >= limit:
            break
        try:
            rec = json.loads(line)
        except Exception:
            continue  # v3 纯文本 traceback 段落，跳过
        if isinstance(rec, dict) and date and rec.get("date") not in (date, "-"):
            continue
        out.append(rec)
    return out


def read_app_log(limit=100):
    """读技术事件流尾部（调试用）。"""
    if not APP_LOG.exists():
        return []
    try:
        lines = APP_LOG.read_text(encoding="utf-8", errors="replace").strip().split("\n")
    except Exception:
        return []
    out = []
    for line in reversed(lines[-limit:]):
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
