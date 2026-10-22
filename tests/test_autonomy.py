#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_autonomy.py · P2 Agent 自动化档位测试。

覆盖：
  A AgentRunConfig.autonomy 规范化（legacy hybrid/full_auto 别名 + 非法值回退）
  B topic_confirm_node：semi/auto 档自动代选 Top1（跳过合规拦截候选），
    全拦截时回退 interrupt；assisted 档不代选
  C publish_confirm_node：auto 档质检全过才自动批准，任一不满足回退 interrupt
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import AgentRunConfig  # noqa: E402
from agent.interrupts import publish_confirm_node, topic_confirm_node  # noqa: E402

DATE = "2099-02-01"


def _cfg(**kw):
    return AgentRunConfig(**{**{"autonomy": "assisted"}, **kw})


def _state(autonomy, *, top5=None, checks=None, gate_passed=True):
    return {
        "date": DATE,
        "config": _cfg(autonomy=autonomy).to_dict(),
        "artifacts": {
            "top5": {"top5": top5 if top5 is not None else [
                {"rank": 1, "topic": "候选甲", "total": 8.8, "compliance_blocked": False},
                {"rank": 2, "topic": "候选乙", "total": 8.2, "compliance_blocked": True}]},
            "export": {"checks_summary": checks or {"total": 20, "passed": 20, "all_ok": True},
                       "mp_path": f"drafts/{DATE}_mp.html", "cover": {"present": False}},
            "draft": {"title": "测试标题", "word_count": 1100},
            "titles": {"titles": [{"title": "测试标题"}]},
        },
        "gate_status": {"passed": gate_passed},
        "messages": [],
        "steps_used": 1, "cost_cny": 0.1, "status": "planning",
        "pending_approval": None,
    }


# ============ A. 配置规范化 ============
def test_config_autonomy_aliases():
    assert AgentRunConfig.from_dict({"autonomy": "hybrid"}).autonomy == "assisted"
    assert AgentRunConfig.from_dict({"autonomy": "full_auto"}).autonomy == "auto"
    assert AgentRunConfig.from_dict({"autonomy": "weird"}).autonomy == "assisted"
    assert AgentRunConfig.from_dict({}).autonomy == "assisted"  # 默认
    assert AgentRunConfig.from_dict({"autonomy": "semi"}).autonomy == "semi"


def test_config_autonomy_properties():
    assert not _cfg(autonomy="assisted").auto_topic
    assert _cfg(autonomy="semi").auto_topic and not _cfg(autonomy="semi").auto_publish
    assert _cfg(autonomy="auto").auto_topic and _cfg(autonomy="auto").auto_publish


# ============ B. 选题关卡自动代选 ============
def test_topic_auto_pick_top1(monkeypatch):
    calls = []
    import agent.interrupts as it
    monkeypatch.setattr(it, "_cmd_choose",
                        lambda args: {"status": "ok"}, raising=False)
    monkeypatch.setattr(it, "run_step",
                        lambda fn, **kw: (calls.append(kw), True, "ok")[1:])
    monkeypatch.setattr(it, "load_json", lambda *a, **k: {"topic": "候选甲"})

    st = _state("semi")
    out = topic_confirm_node(st)
    assert calls and calls[0]["choice"] == 1, "semi 档应自动代选 Top1"
    assert "自动代选" in calls[0]["note"]
    assert out["status"] == "planning"
    assert out["artifacts"]["selection"]["topic"] == "候选甲"


def test_topic_auto_skips_blocked_candidates(monkeypatch):
    calls = []
    import agent.interrupts as it
    monkeypatch.setattr(it, "_cmd_choose", lambda args: {"status": "ok"}, raising=False)
    monkeypatch.setattr(it, "run_step",
                        lambda fn, **kw: (calls.append(kw), True, "ok")[1:])
    monkeypatch.setattr(it, "load_json", lambda *a, **k: {"topic": "候选丙"})

    top5 = [{"rank": 1, "topic": "拦截甲", "total": 9.0, "compliance_blocked": True},
            {"rank": 2, "topic": "候选丙", "total": 8.0, "compliance_blocked": False}]
    out = topic_confirm_node(_state("auto", top5=top5))
    assert calls[0]["choice"] == 2, "合规拦截的 #1 应被跳过，代选 #2"
    assert out["artifacts"]["selection"]["topic"] == "候选丙"


def test_topic_auto_all_blocked_falls_back_to_interrupt(monkeypatch):
    import agent.interrupts as it
    monkeypatch.setattr(it, "run_step", lambda fn, **kw: (True, "ok"))

    top5 = [{"rank": 1, "topic": "拦截甲", "total": 9.0, "compliance_blocked": True}]
    with pytest.raises(Exception):
        # 全被合规拦截 → 回退人工 interrupt（graph 外调用 interrupt 会抛错，即回退信号）
        topic_confirm_node(_state("auto", top5=top5))


# ============ C. 发布关卡自动批准 ============
def test_publish_auto_approved_when_quality_ok(monkeypatch):
    import agent.interrupts as it
    monkeypatch.setattr(it, "_execute_publish", lambda state: {"push": "ok", "sync": "ok"})

    out = publish_confirm_node(_state("auto"))
    assert out["status"] == "done"
    assert out["artifacts"]["publish"]["push"] == "ok"


def test_publish_auto_blocked_by_failed_checks(monkeypatch):
    import agent.interrupts as it
    monkeypatch.setattr(it, "_execute_publish", lambda state: {"push": "ok"})

    st = _state("auto", checks={"total": 20, "passed": 18, "all_ok": False})
    with pytest.raises(Exception):
        publish_confirm_node(st)  # 自检未全过 → 回退人工 interrupt


def test_publish_auto_blocked_by_gate(monkeypatch):
    import agent.interrupts as it
    monkeypatch.setattr(it, "_execute_publish", lambda state: {"push": "ok"})

    with pytest.raises(Exception):
        publish_confirm_node(_state("auto", gate_passed=False))  # 门禁未过 → 回退人工


def test_publish_assisted_never_auto(monkeypatch):
    import agent.interrupts as it
    monkeypatch.setattr(it, "_execute_publish", lambda state: {"push": "ok"})

    with pytest.raises(Exception):
        publish_confirm_node(_state("assisted"))  # assisted 档一律人工
