#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/ · 领域核心层：路径、存储、记忆、红线、质量、评分、渲染、飞书卡片。

纯逻辑与文件 IO，不感知 LLM 与 UI。依赖规则：core 只 import core.paths 及同级模块，
绝不 import llm/ pipeline/ workbench/（单向依赖，防循环）。
"""
