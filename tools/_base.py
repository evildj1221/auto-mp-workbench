#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/_base.py · 工具层共享基础设施（内部模块，不对外导出）。

三个职责：
  1. run_step：以纯参数调 pipeline/core 的 step 函数——构建 SimpleNamespace、
     捕获 print 输出（step 函数的人读日志变成 agent 可读的工具返回）。
  2. 结果信封：全部工具统一返回 {status, message, log, ...}，
     status ∈ ok / skipped / error；skipped 表示幂等跳过（产物已存在），非异常。
  3. log 截断：print 输出截尾保留（LLM 上下文友好），全文已在工具层留痕。
"""
import contextlib
import io
import sys
import traceback
from types import SimpleNamespace

LOG_TAIL_LIMIT = 4000


def _tail(text, limit=LOG_TAIL_LIMIT):
    if len(text) <= limit:
        return text
    return "…（前文截断）…" + text[-limit:]


def run_step(step_fn, **kwargs):
    """用纯参数调用 step 函数，返回 (ok, log)。

    step 函数沿用 v3 的 args namespace 约定（Phase 1 冻结），此处做适配；
    kwargs 里 None 值的属性仍会设置（step 内部自行 getattr 默认）。
    """
    buf_out, buf_err = io.StringIO(), io.StringIO()
    args = SimpleNamespace(**kwargs)
    ok = False
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        try:
            ok = bool(step_fn(args))
        except SystemExit as e:  # cmd_* 类函数失败时会 sys.exit(1)
            ok = False
            buf_err.write(f"[SystemExit {e.code}]")
    log = (buf_out.getvalue() + (("\n[stderr]\n" + buf_err.getvalue()) if buf_err.getvalue() else "")).strip()
    return ok, log


def envelope(status, message, log="", **data):
    """统一结果信封。data 里的键并入顶层（调用方直接取，不用嵌套 data）。"""
    out = {"status": status, "message": message, "log": _tail(log)}
    out.update(data)
    return out


def tool_error(e, log=""):
    """异常 → error 信封（traceback 进 log，报错可维护性：七分类在 Phase 6 接管）。"""
    tb = traceback.format_exc()
    return envelope("error", f"{type(e).__name__}: {e}", log=f"{log}\n{tb}".strip())
