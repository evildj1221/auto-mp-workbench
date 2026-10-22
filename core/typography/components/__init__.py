#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""components · 内置组件包：注册入口与依赖方向。

依赖规则：components → registry / text（组件实现不得反向依赖
exporter / manager / Flask）。register_builtin_components() 由
core.typography 包初始化时调用一次。
"""
from . import body, scaffold


def register_builtin_components(registry):
    """注册全部内置组件（14 block + 1 meta + 9 scaffold = 24 个 ComponentSpec，
    即方案 §3.3 清单 22 项 + 头图容器 cover + 正文段落 p）。"""
    body.register_body(registry)
    scaffold.register_scaffold(registry)
    return registry
