#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.typography · 排版引擎纯逻辑层（商业化阶段一 S1）。

组件注册表 + 主题系统 + 主题管理器。分层契约：
- 本包不 import Flask / workbench.exporter / LLM——可被导出器、排版中心 UI、
  未来 AI 自动排版三方复用；
- 依赖方向：components → registry / text；manager → theme（校验 override 时
  延迟引用全局 REGISTRY，不在模块加载期形成环）；
  交付层（exporter / app）单向依赖本包。

快速上手：
    from core import typography as typo
    html = exporter.render_from_markers(md, theme=typo.get_manager().effective_theme())
"""
from .manager import (DEFAULT_THEME_ID, ThemeManager, default_theme,
                      get_manager, reset_manager)
from .registry import ComponentRegistry, ComponentSpec, RenderContext
from .theme import (Theme, ThemeError, ThemeRuntime, apply_overrides,
                    deep_merge, resolve_config, resolve_styles)
from .safety import (audit_theme, contrast_ratio, is_hex_color,
                     parse_size_px, scan_bad_colors, validate_token_value,
                     MIN_BODY_CONTRAST, MIN_BODY_SIZE_PX, MAX_SIZE_PX)

# 全局默认注册表：进程内共享，import 即完成 24 个内置组件注册
REGISTRY = ComponentRegistry()
from .components import register_builtin_components  # noqa: E402
register_builtin_components(REGISTRY)

__all__ = [
    "ComponentRegistry", "ComponentSpec", "RenderContext", "REGISTRY",
    "Theme", "ThemeError", "ThemeRuntime", "apply_overrides", "deep_merge",
    "resolve_config", "resolve_styles",
    "ThemeManager", "DEFAULT_THEME_ID", "default_theme", "get_manager",
    "reset_manager",
    # 排版开放安全底线（商业化 P0，修 BUG-2 自指校验）
    "audit_theme", "scan_bad_colors", "validate_token_value", "contrast_ratio",
    "is_hex_color", "parse_size_px",
    "MIN_BODY_CONTRAST", "MIN_BODY_SIZE_PX", "MAX_SIZE_PX",
]
