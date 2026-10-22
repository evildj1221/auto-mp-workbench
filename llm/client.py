#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llm/client.py · call_llm 统一入口（薄包装，走 provider 适配层 + 成本统计）。

自 cat_shop.py 702-725 行原样迁出，行为不变。
"""
from llm.provider import get_provider
from llm.provider import call as _provider_call

_PROVIDER_SINGLETON = None


def _get_llm_provider():
    global _PROVIDER_SINGLETON
    if _PROVIDER_SINGLETON is None:
        _PROVIDER_SINGLETON = get_provider()
    return _PROVIDER_SINGLETON


def call_llm(prompt, temperature=0, max_tokens=2000, _session=None, json_mode=True,
             retries=3, base_delay=2.0, module="unknown"):
    """LLM 调用统一入口（v3：走 provider 适配层，自动记录 token 与费用）。
    module 参数用于成本统计归因：collect/score/write/analyze/redline_semantic/mp_export。"""
    return _provider_call(_get_llm_provider(), prompt,
                          temperature=temperature, max_tokens=max_tokens,
                          json_mode=json_mode, module=module,
                          retries=retries, base_delay=base_delay)
