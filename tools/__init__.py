#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/ · 工具层（agent 的全部能力面，19 个 @tool + registry）。

按子模块组织：topic / write / quality / publish / memory / state，
统一经 tools.registry 汇总与执行（本包 __init__ 不做 re-export，避免循环导入）。
"""
