#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/ · Agent 核心（LangGraph StateGraph）。

六模块：state（状态）/ config（预算护栏）/ planner（LLM 决策）/
executor（工具执行+门禁）/ gates（确定性质检）/ interrupts（人工关卡），
graph.py 组装，pipeline/runner.py 是唯一对外入口（CLI 与 UI 共用）。
"""
