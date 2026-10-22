#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/errors.py · 错误七分类（v4.0 报错维护的单一事实源）。

七个类别覆盖本工作台全部故障形态，每类绑定：
  - label     ：中文标签（日志与 UI 展示）
  - disposal  ：系统自动处置方式（auto_retry / degrade / manual）
  - hint      ：UI 行动建议文案（用户下一步该做什么）
  - match     ：识别规则（异常类型 + 消息关键词）

自动处置的执行位置：
  - auto_retry：llm/provider.call 已实现 3 次指数退避重试，分类只是如实标注
  - degrade   ：tools/registry.run_tool 超时/异常 → error 信封返回，agent 换路重规划
  - manual    ：写结构化日志 + UI 行动建议，等人介入
"""
import json

LLM_NETWORK = "LLM_NETWORK"
LLM_PARSE = "LLM_PARSE"
TOOL_TIMEOUT = "TOOL_TIMEOUT"
REDLINE_BLOCK = "REDLINE_BLOCK"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
DATA_MISSING = "DATA_MISSING"
UNEXPECTED = "UNEXPECTED"

CATEGORIES = {
    LLM_NETWORK: {
        "label": "网络/供应商故障",
        "disposal": "auto_retry",
        "hint": "供应商服务波动，已自动重试。反复出现请稍后再跑，或检查 API Key 与余额。",
    },
    LLM_PARSE: {
        "label": "模型输出解析失败",
        "disposal": "auto_retry",
        "hint": "模型未按 JSON 约定输出，已自动重试。反复出现可在设置页换模型档位。",
    },
    TOOL_TIMEOUT: {
        "label": "工具执行超时",
        "disposal": "degrade",
        "hint": "某一步耗时过长被放弃，Agent 会换路或重选。可重跑该步，或调大超时预算。",
    },
    REDLINE_BLOCK: {
        "label": "红线拦截",
        "disposal": "manual",
        "hint": "内容触发红线被拦截，这是预期的保护行为而非故障。请按提示修改后重试。",
    },
    BUDGET_EXCEEDED: {
        "label": "预算护栏触发",
        "disposal": "manual",
        "hint": "步数或成本达到上限被暂停。到 Agent 中枢「提额并继续」即可续跑，产物不丢。",
    },
    DATA_MISSING: {
        "label": "数据/文件缺失",
        "disposal": "manual",
        "hint": "找不到所需数据文件。先跑上游步骤（选题收集/评分/写稿），再重试当前操作。",
    },
    UNEXPECTED: {
        "label": "未预期异常",
        "disposal": "manual",
        "hint": "出现未分类异常，详情已写入日志。请把 data/workbench_error.log 反馈给维护者。",
    },
}

# LLM 语境专用词（llm 出现在消息/上下文时优先套用，防止被通用超时规则抢走）
_LLM_NETWORK_WORDS = ("connection", "connect", "timed out", "timeout", "429",
                      "rate limit", "请求", "network", "ssl", "tls", "http 5",
                      "server error", "api key", "余额", "quota")
_LLM_PARSE_WORDS = ("jsondecodeerror", "expecting value", "解析失败", "json 内容为空",
                    "json 输出", "输出解析")

# 通用消息关键词 → 类别（顺序即优先级：越靠前越具体）
_KEYWORD_RULES = [
    (REDLINE_BLOCK, ("红线", "redline", "合规", "敏感词")),
    (BUDGET_EXCEEDED, ("预算", "paused_budget", "超限", "budget", "提额")),
    (LLM_NETWORK, ("connection", "connect", "请求", "network", "ssl", "tls",
                   "api key", "余额", "quota")),
    (LLM_PARSE, ("jsondecodeerror", "expecting value", "解析失败", "json 内容为空",
                 "json 输出", "输出解析")),
    (TOOL_TIMEOUT, ("执行超时", "timed out", "timeout")),
    (DATA_MISSING, ("filenotfounderror", "no such file", "不存在", "not found",
                    "missing", "为空或结构非法")),
]

# 异常类型 → 类别（比关键词更可靠的短路判定）
_TYPE_RULES = {
    TimeoutError: TOOL_TIMEOUT,
    FileNotFoundError: DATA_MISSING,
    json.JSONDecodeError: LLM_PARSE,
    KeyError: DATA_MISSING,
}


def classify(exc=None, message="", context="") -> str:
    """把异常/消息归入七类之一。永不抛异常，兜底返回 UNEXPECTED。

    优先级：异常类型短路 → LLM 语境词（网络/解析）→ 通用关键词 → UNEXPECTED。
    context 是调用方补充的背景（如 "llm" / "tool:write_draft"），用于消歧：
    同样的 TimeoutError 发生在 LLM 调用里是 LLM_NETWORK，工具执行里是 TOOL_TIMEOUT。
    """
    try:
        text = " ".join([message or "", str(exc or ""), context or ""]).lower()
        llm_scope = "llm" in text
        for etype, cat in _TYPE_RULES.items():
            if exc is not None and isinstance(exc, etype):
                if etype is TimeoutError and llm_scope:
                    return LLM_NETWORK  # LLM 调用里的超时 = 供应商波动
                return cat
        try:
            import requests
            if exc is not None and isinstance(exc, requests.RequestException):
                return LLM_NETWORK  # HTTP 客户端异常几乎都是供应商/网络侧
        except ImportError:
            pass
        if llm_scope:
            if any(w in text for w in _LLM_NETWORK_WORDS):
                return LLM_NETWORK
            if any(w in text for w in _LLM_PARSE_WORDS):
                return LLM_PARSE
        for cat, words in _KEYWORD_RULES:
            if any(w in text for w in words):
                return cat
    except Exception:
        pass
    return UNEXPECTED


def meta(category):
    """类别元数据（未知类别兜底 UNEXPECTED）。"""
    return CATEGORIES.get(category, CATEGORIES[UNEXPECTED])
