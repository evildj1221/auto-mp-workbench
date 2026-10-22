#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_logging_errors.py · Phase 6 验收：错误七分类 + 结构化日志。

策略：
  - 三条日志通道全部重定向到临时目录（沙箱隔离，不碰真实 data/）；
  - classify 纯函数直接断言七类；
  - registry 异常路径用注入的假工具触发（零 LLM 成本）；
  - app.py 全局兜底用临时路由触发，验证 500 响应携带分类文案。
"""
import json
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import errors as errcls
from core import logging as clog

PASS = 0
FAILS = []


# ===== 沙箱：重定向日志通道 =====
class _RedirectedLogs:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="wb_logs_")
        self.saved = {}
        self.paths = {
            "APP_LOG": Path(self.tmp) / "app.jsonl",
            "WB_LOG": Path(self.tmp) / "workbench_log.jsonl",
            "ERROR_LOG": Path(self.tmp) / "workbench_error.log",
        }

    def __enter__(self):
        for attr, p in self.paths.items():
            self.saved[attr] = getattr(clog, attr)
            setattr(clog, attr, p)
        return self

    def __exit__(self, *exc):
        for attr, old in self.saved.items():
            setattr(clog, attr, old)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


def _last_json(path):
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    return json.loads(lines[-1])


# ===== 七分类 =====
def test_classify_network():
    import requests
    assert errcls.classify(requests.ConnectionError("Connection refused"),
                           context="llm call") == errcls.LLM_NETWORK
    assert errcls.classify(
        message="LLM 调用在 3 次尝试后仍失败: HTTPSConnectionPool Read timed out") == errcls.LLM_NETWORK


def test_classify_parse():
    assert errcls.classify(json.JSONDecodeError("Expecting value", "x", 0)) == errcls.LLM_PARSE
    assert errcls.classify(message="JSON 内容为空或结构非法") == errcls.LLM_PARSE


def test_classify_timeout():
    assert errcls.classify(TimeoutError(), context="tool:write_draft") == errcls.TOOL_TIMEOUT
    assert errcls.classify(message="工具 write_draft 执行超时（>300s）") == errcls.TOOL_TIMEOUT


def test_classify_redline_budget_missing():
    assert errcls.classify(message="触发红线词被拦截") == errcls.REDLINE_BLOCK
    assert errcls.classify(message="预算护栏触发：步数用尽（30/30）") == errcls.BUDGET_EXCEEDED
    assert errcls.classify(FileNotFoundError("no such file: 2099_top5.json")) == errcls.DATA_MISSING
    assert errcls.classify(message="候选文件不存在") == errcls.DATA_MISSING


def test_classify_unexpected_and_meta():
    assert errcls.classify(ValueError("怪事")) == errcls.UNEXPECTED
    assert errcls.classify(None, "", "") == errcls.UNEXPECTED
    for cat, m in errcls.CATEGORIES.items():
        assert m["label"] and m["hint"], f"{cat} 缺 label/hint"
        assert m["disposal"] in ("auto_retry", "degrade", "manual"), f"{cat} 处置方式非法"
    assert errcls.meta("__nope__") == errcls.CATEGORIES[errcls.UNEXPECTED]


# ===== 结构化日志 =====
def test_log_event_jsonl():
    with _RedirectedLogs() as logs:
        clog.log_event("info", "unit_test", date="2099-01-01", detail="hello", echo=False)
        rec = _last_json(logs.paths["APP_LOG"])
        assert rec["event"] == "unit_test" and rec["level"] == "info"
        assert rec["date"] == "2099-01-01" and rec["detail"] == "hello"


def test_wb_log_format_compat():
    """业务日志格式必须与 v3 完全一致（ts/action/date/detail 四键）。"""
    with _RedirectedLogs() as logs:
        clog.wb_log("agent_start", "2099-01-01", "启动")
        rec = _last_json(logs.paths["WB_LOG"])
        assert set(rec) == {"ts", "action", "date", "detail"}
        assert rec["action"] == "agent_start" and rec["date"] == "2099-01-01"
        app = _last_json(logs.paths["APP_LOG"])
        assert app["event"] == "agent_start", "wb_log 应转发 info 事件到 app.jsonl"


def test_log_error_structured():
    with _RedirectedLogs() as logs:
        cat = clog.log_error(None, "ReadTimeout: LLM 调用失败", date="2099-01-01",
                             context="llm call", exc=RuntimeError("boom"))
        assert cat == errcls.LLM_NETWORK, "LLM 调用超时应归入网络/供应商故障"
        rec = _last_json(logs.paths["ERROR_LOG"])
        assert rec["category"] == "LLM_NETWORK" and rec["label"] and rec["hint"]
        assert rec["disposal"] == "auto_retry" and "traceback" in rec


def test_read_errors_skips_legacy_and_filters():
    with _RedirectedLogs() as logs:
        p = logs.paths["ERROR_LOG"]
        p.write_text(
            "===== 2099-01-01T10:00:00 GET /x =====\nTraceback (most recent call last):\n"
            + json.dumps({"ts": "2099-01-01T10:00:01", "category": "UNEXPECTED",
                          "label": "未预期异常", "hint": "h", "date": "2099-01-01"},
                         ensure_ascii=False) + "\n"
            + json.dumps({"ts": "2099-01-02T10:00:01", "category": "UNEXPECTED",
                          "label": "未预期异常", "hint": "h", "date": "2099-01-02"},
                         ensure_ascii=False) + "\n",
            encoding="utf-8")
        today = clog.read_errors(date="2099-01-01")
        assert len(today) == 1 and today[0]["date"] == "2099-01-01", "旧纯文本行应被跳过"
        assert len(clog.read_errors()) == 2


def test_wb_log_reader():
    with _RedirectedLogs() as logs:
        clog.wb_log("a1", "2099-01-01", "d1")
        clog.wb_log("a2", "2099-01-01", "d2")
        out = clog.read_wb_log()
        assert out[0]["action"] == "a2", "倒序返回，最新在前"


# ===== registry 异常路径携带分类 =====
def test_run_tool_error_category():
    from langchain_core.tools import tool as lc_tool
    from tools import registry

    @lc_tool
    def _boom(date: str) -> dict:
        """测试用：必然抛超时。"""
        raise TimeoutError("simulated timeout")

    saved = dict(registry._BY_NAME)
    try:
        registry._BY_NAME["_boom"] = _boom
        with _RedirectedLogs():
            r = registry.run_tool("_boom", date="2099-01-01", timeout=2)
        assert r["status"] == "error", r
        assert r["error_category"] == "TOOL_TIMEOUT", "异常信封应携带七分类"
        last = _last_json(registry._log_path())
        assert last["error_category"] == "TOOL_TIMEOUT", "调用留痕应携带七分类"
    finally:
        registry._BY_NAME.clear()
        registry._BY_NAME.update(saved)


# ===== 全局异常兜底：结构化 + 分类文案 =====
def test_workbench_error_log_upgraded(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    import backend.app.api.settings as st

    def _boom(*a, **kw):
        raise FileNotFoundError("no such file: xxx_top5.json")

    monkeypatch.setattr(st.cat_shop, "load_effective_weights", _boom)
    with TestClient(app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False) as client:
        with _RedirectedLogs() as logs:
            r = client.get("/api/settings")
            assert r.status_code == 500
            body = r.json()
            msg = body["error"]["message"]
            assert "数据/文件缺失" in msg, "分类标签应进入用户可见文案"
            rec = _last_json(logs.paths["ERROR_LOG"])
            assert rec["category"] == "DATA_MISSING", "FileNotFoundError 应归入数据缺失"
            assert "traceback" in rec, "兜底错误应存 traceback"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                PASS += 1
                print(f"  [PASS] {name}")
            except AssertionError as e:
                FAILS.append(name)
                print(f"  [FAIL] {name}: {e}")
            except Exception as e:
                FAILS.append(name)
                print(f"  [ERROR] {name}: {type(e).__name__}: {e}")
                traceback.print_exc()
    total = sum(1 for n in globals() if n.startswith("test_") and callable(globals()[n]))
    print(f"\n日志与七分类测试: {PASS}/{total} PASS")
    sys.exit(1 if FAILS else 0)
