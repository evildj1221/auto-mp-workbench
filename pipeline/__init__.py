#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline/ · 流水线编排层（v3 legacy 步骤，Phase 3 后与 agent 并存）。

topics.py   选题链路：collect / score / choose / analyze / 批次
writing.py  写稿链路：outline / write / titles / format / learn + 语义红线
publish.py  发布与配置：sync / setup / redline CLI / status
flow.py     编排入口：all / full / run_write（写稿后同步衔接）
"""
