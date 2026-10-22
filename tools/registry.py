#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/registry.py · 工具注册表（agent 能力面的唯一入口）。

职责：
  1. 汇总全部 @tool（planner bind_tools 与 UI 工具目录页共用一份清单）。
  2. run_tool：统一执行入口——超时包装 + 耗时留痕（data/agent_runs/tool_calls.jsonl）。
     executor 只经 run_tool 调工具，trace 与超时策略单点维护。
  3. get_tool_schemas：schema 导出（`python -m tools.registry` 可打印，供调试与 UI）。

约定：工具本体永远返回 {status: ok|skipped|error, message, log, ...} 信封、不抛异常；
超时是 registry 的兜底（线程无法强杀，超时后后台线程自然结束，结果被丢弃）。
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from core import errors as errcls
from core import logging as clog
from tools._base import envelope
from tools.directive_tools import list_topic_directives, set_topic_directive
from tools.image_tools import generate_cover_image
from tools.memory_tools import (add_quote, learn_preferences, recall_lessons,
                                record_feedback, save_lesson)
from tools.publish_tools import export_mp_html, push_wechat_draft, sync_feishu_doc
from tools.quality_tools import check_redline, check_similarity, check_wordcount
from tools.state_tools import get_pipeline_status, list_candidates, read_draft
from tools.topic_tools import collect_candidates, score_candidates
from tools.write_tools import edit_draft, generate_outline, generate_titles, write_draft

TOOLS = [
    # 选题链路
    collect_candidates, score_candidates,
    # 选题风向标（v4.3 M3 · 用户显式掌舵）
    set_topic_directive, list_topic_directives,
    # 写稿链路
    generate_outline, write_draft, generate_titles, edit_draft,
    # 质量门禁
    check_redline, check_wordcount, check_similarity,
    # 发布链路
    sync_feishu_doc, export_mp_html, push_wechat_draft,
    # 头图（v4.2 M1 · 每篇唯一配图）
    generate_cover_image,
    # 记忆与反馈（含 v4.1 运行经验）
    record_feedback, learn_preferences, add_quote, recall_lessons, save_lesson,
    # 环境感知（只读）
    get_pipeline_status, read_draft, list_candidates,
]

_BY_NAME = {t.name: t for t in TOOLS}

DEFAULT_TIMEOUT = 300  # 秒；score 多轮等重步骤由 executor 显式传更大值

TOOL_CALL_LOG = None  # 延迟初始化（避免 import 期写盘）


def _log_path():
    global TOOL_CALL_LOG
    if TOOL_CALL_LOG is None:
        from core.paths import DATA_DIR
        TOOL_CALL_LOG = DATA_DIR / "agent_runs" / "tool_calls.jsonl"
        TOOL_CALL_LOG.parent.mkdir(parents=True, exist_ok=True)
    return TOOL_CALL_LOG


def get_tools():
    """全部工具对象列表（planner bind_tools 直接用）。"""
    return list(TOOLS)


def get_tool(name):
    """按名取工具；不存在返回 None（executor 转告 planner「无此工具」）。"""
    return _BY_NAME.get(name)


def tool_names():
    return [t.name for t in TOOLS]


def get_tool_schemas():
    """结构化 schema（UI 工具目录页 / 调试打印共用）。"""
    return [{"name": t.name,
             "description": (t.description or "").split("\n")[0],
             "args": t.args}
            for t in TOOLS]


def run_tool(name, timeout=DEFAULT_TIMEOUT, **kwargs):
    """统一执行入口：超时包装 + 耗时/状态留痕。永远返回信封 dict，不抛异常。"""
    t0 = time.monotonic()
    tool = _BY_NAME.get(name)
    if tool is None:
        return envelope("error", f"未知工具: {name}（可用: {', '.join(tool_names())}）")
    try:
        # 不用 with 块管理 executor：with 退出时 shutdown(wait=True) 会等卡死的
        # 工具线程跑完，超时形同虚设（agent 线程假死、SSE 无更新的元凶）。
        # 超时后放弃结果、不等待孤儿线程（线程无法强杀，自然结束）。
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            future = ex.submit(tool.invoke, kwargs)
            result = future.result(timeout=timeout)
        finally:
            ex.shutdown(wait=False)
        if not isinstance(result, dict):
            result = envelope("ok", str(result)[:200])
    except TimeoutError:
        cat = errcls.TOOL_TIMEOUT
        result = envelope("error", f"工具 {name} 执行超时（>{timeout}s），已放弃本次结果",
                          error_category=cat)
        clog.log_error(cat, result["message"], context=f"tool:{name}")
    except Exception as e:
        cat = errcls.classify(e, context=f"tool:{name}")
        result = envelope("error", f"工具 {name} 执行异常: {type(e).__name__}: {e}",
                          error_category=cat)
        clog.log_error(cat, result["message"], context=f"tool:{name}", exc=e)
    finally:
        duration = round(time.monotonic() - t0, 2)
        _append_log(name, kwargs, result, duration)
    result = dict(result)
    result["duration_s"] = duration
    return result


def _append_log(name, kwargs, result, duration):
    """调用留痕（agent trace 的工具侧数据源；参数截断防止日志膨胀）。"""
    try:
        brief = {k: (v if isinstance(v, (int, float, bool)) or not isinstance(v, str) else v[:80])
                 for k, v in (kwargs or {}).items()}
        entry = {"ts": datetime.now().isoformat(timespec="seconds"),
                 "tool": name, "status": result.get("status", "?"),
                 "duration_s": duration, "args": brief,
                 "message": (result.get("message") or "")[:200]}
        if result.get("error_category"):
            entry["error_category"] = result["error_category"]
        with _log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 留痕失败不影响工具结果


if __name__ == "__main__":
    print(json.dumps(get_tool_schemas(), ensure_ascii=False, indent=2))
    print(f"\n共 {len(TOOLS)} 个工具")
