#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""text.py · 排版文本工具（渲染管线的唯一转义/加粗实现）。

从 workbench/exporter.py 原样迁出（_esc / _inline），行为逐字节不变：
exporter 与组件渲染共用同一份实现，杜绝两处事实源漂移。
"""
import re

_STRONG_RE = re.compile(r"\*\*(.+?)\*\*")


def escape(text):
    """HTML 转义（与原 exporter._esc 一致：仅 & < > 三字符）。"""
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def inline(text):
    """行内 **x** → strong + 内联加粗（规范双保险写法）。"""
    return _STRONG_RE.sub(r'<strong style="font-weight:bold">\1</strong>', text)
