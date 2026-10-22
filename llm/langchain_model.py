#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llm/langchain_model.py · LangChain ChatOpenAI 封装（agent planner 专用）。

与 llm.provider 共用同一份 .env 配置与 llm_cost.jsonl 成本账本：
langgraph planner/bind_tools 走本模块，13 个 step 走 provider——
两条通道的成本记录口径一致，设置页成本统计可直接汇总。
"""
from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI

from llm.provider import get_provider, log_cost


class CostCallback(BaseCallbackHandler):
    """把 LangChain 回调里的 usage 写入 llm_cost.jsonl（与 provider 同账本）。"""

    def __init__(self, provider, module="agent"):
        self._provider = provider
        self._module = module

    def on_llm_end(self, response, **kwargs):
        try:
            msg = response.generations[0][0].message
            um = getattr(msg, "usage_metadata", None) or {}
            usage = {"prompt_tokens": um.get("input_tokens", 0),
                     "completion_tokens": um.get("output_tokens", 0),
                     "total_tokens": um.get("total_tokens", 0)}
            if usage["total_tokens"]:
                cost_in, cost_out = self._provider.cost(usage)
                log_cost(self._provider.name, self._provider.model, usage,
                         self._module, cost_in, cost_out)
        except Exception:
            pass  # 成本记录失败不影响主流程（与 provider 兜底口径一致）


_CACHE = {}


def get_chat_model(temperature: float = 0.2, module: str = "agent") -> ChatOpenAI:
    """构建（带缓存）planner 用的 ChatOpenAI。

    配置复用 get_provider 的 .env 解析与 key 校验——未设 key 时与
    step 函数同样抛 EnvironmentError，由 runner 统一转为 DATA_MISSING 类错误。
    """
    p = get_provider()
    key = (p.api_base, p.model, temperature, module)
    if key not in _CACHE:
        _CACHE[key] = ChatOpenAI(
            model=p.model, api_key=p.api_key, base_url=p.api_base,
            temperature=temperature, max_retries=2, timeout=120,
            callbacks=[CostCallback(p, module=module)])
    return _CACHE[key]
