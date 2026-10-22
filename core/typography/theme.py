#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""theme.py · 主题模型与变量解析（阶段一 S1）。

三层抽象：
  L1 全局 token        theme.tokens（colors / font_sizes / letter_spacing /
                        line_heights / spacing / radii / borders 七组语义变量）
  L2 组件级变量        组件 config_schema 默认值 ← 主题 component_defaults 覆盖
  L3 排版样式输出      组件 styles 槽模板中的 ${colors.title} 引用 → 渲染期
                        解析为字面值拼进内联 style（微信硬约束：导出物无 CSS 变量）

变量引用语法统一为 ``${dotted.path}``（如 ``${font_sizes.quote}``），
仅可引用 theme.tokens；schema 默认值与样式模板同语法、同一解析器。
"""
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, Optional

_TOKEN_REF = re.compile(r"\$\{([a-zA-Z_]\w*(?:\.\w+)*)\}")


class ThemeError(Exception):
    """主题数据不合法（缺 token / 缺字段 / 引用无法解析）。"""


# ===== 深合并（base 继承与 override 叠加共用） =====
def deep_merge(base: dict, diff: dict) -> dict:
    out = dict(base)
    for k, v in (diff or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Theme:
    id: str
    name: str
    version: str = "1"
    base: Optional[str] = None           # 继承的基主题 id（内置主题为 None）
    meta: dict = field(default_factory=dict)
    tokens: dict = field(default_factory=dict)
    component_defaults: dict = field(default_factory=dict)
    builtin: bool = False
    path: Optional[Path] = None

    # ===== 构建 =====
    @classmethod
    def from_dict(cls, d: dict, builtin: bool = False,
                  path: Optional[Path] = None) -> "Theme":
        """结构校验（id / name / tokens 类型）。完整性（colors / font_sizes
        硬依赖）在继承展开后由 validate_complete 校验——差分主题（base 指向
        基主题、自身只存 diff）在此处不要求 tokens 完整。"""
        if not isinstance(d, dict):
            raise ThemeError("主题必须是 JSON 对象")
        tid = d.get("id")
        name = d.get("name")
        if not tid or not str(tid).isidentifier():
            raise ThemeError(f"主题 id 非法: {tid!r}")
        if not name:
            raise ThemeError(f"主题 {tid} 缺 name")
        tokens = d.get("tokens")
        if tokens is None:
            tokens = {}
        if not isinstance(tokens, dict):
            raise ThemeError(f"主题 {tid} tokens 必须是对象")
        comp = d.get("component_defaults")
        if comp is None:
            comp = {}
        if not isinstance(comp, dict):
            raise ThemeError(f"主题 {tid} component_defaults 必须是对象")
        return cls(id=str(tid), name=str(name), version=str(d.get("version", "1")),
                   base=d.get("base"), meta=d.get("meta") or {},
                   tokens=tokens, component_defaults=comp,
                   builtin=builtin, path=path)

    def validate_complete(self) -> "Theme":
        """继承展开后的完整性校验：colors / font_sizes 是渲染与校验的硬依赖。"""
        if not self.tokens.get("colors") or not self.tokens.get("font_sizes"):
            raise ThemeError(f"主题 {self.id} 缺 tokens.colors / tokens.font_sizes"
                             "（含继承展开后仍缺失）")
        return self

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "version": self.version,
                "base": self.base, "meta": self.meta, "tokens": self.tokens,
                "component_defaults": self.component_defaults}

    # ===== token 访问 =====
    def token(self, dotted: str):
        node = self.tokens
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                raise ThemeError(f"主题 {self.id} 缺 token: {dotted}")
            node = node[part]
        return node

    # ===== 校验器派生白名单（替代原硬编码 ALLOWED_COLORS / ALLOWED_FONT_SIZES） =====
    def allowed_colors(self) -> set:
        return set(self.tokens.get("colors", {}).values())

    def allowed_font_sizes(self) -> set:
        return set(self.tokens.get("font_sizes", {}).values())


# ===== ${token} 引用解析 =====
def _resolve_string(text: str, theme: Theme, where: str) -> str:
    def sub(m):
        ref = m.group(1)
        try:
            val = theme.token(ref)
        except ThemeError:
            raise ThemeError(f"{where}：引用了不存在的 token ${{{ref}}}") from None
        if not isinstance(val, str):
            raise ThemeError(f"{where}：token ${{{ref}}} 不是标量值")
        return val
    return _TOKEN_REF.sub(sub, text)


def _resolve_value(value, theme: Theme, where: str):
    if isinstance(value, str):
        return _resolve_string(value, theme, where)
    if isinstance(value, list):
        return [_resolve_value(v, theme, where) for v in value]
    if isinstance(value, dict):
        return {k: _resolve_value(v, theme, where) for k, v in value.items()}
    return value


# ===== 组件级解析（L2 + L3） =====
def resolve_styles(spec, theme: Theme) -> Dict[str, str]:
    """组件样式槽模板 → 内联样式字面值（逐槽解析，缺失 token 即报错定位）。"""
    out = {}
    for slot, template in (spec.styles or {}).items():
        where = f"主题 {theme.id} · 组件 {spec.name} 样式槽 {slot}"
        out[slot] = _resolve_string(template, theme, where)
    return out


def resolve_config(spec, theme: Theme) -> dict:
    """配置合并优先级：组件 schema 默认 ← 主题 component_defaults（← 用户 override
    由 manager 在 Theme 层面先行合并）。${token} 引用展开为字面值。"""
    merged = {}
    for key, schema in (spec.config_schema or {}).items():
        merged[key] = schema.get("default")
    merged = deep_merge(merged, theme.component_defaults.get(spec.name) or {})
    return _resolve_value(merged, theme, f"主题 {theme.id} · 组件 {spec.name} 配置")


def apply_overrides(theme: Theme, overrides: dict) -> Theme:
    """用户 override（settings.json 的 tokens/components 两枝）叠加到主题。"""
    if not overrides:
        return theme
    return replace(theme,
                   tokens=deep_merge(theme.tokens, overrides.get("tokens") or {}),
                   component_defaults=deep_merge(theme.component_defaults,
                                                 overrides.get("components") or {}))


class ThemeRuntime:
    """主题运行时：一次解析全部组件的样式槽与配置，供整篇渲染分发复用。"""

    def __init__(self, theme: Theme, registry):
        self.theme = theme
        self._styles: Dict[str, Dict[str, str]] = {}
        self._configs: Dict[str, dict] = {}
        for spec in registry.all():
            self._styles[spec.name] = resolve_styles(spec, theme)
            self._configs[spec.name] = resolve_config(spec, theme)

    def styles(self, name: str) -> Dict[str, str]:
        return self._styles.get(name) or {}

    def config(self, name: str) -> dict:
        return self._configs.get(name) or {}

    def context(self, name: str, series_label: str = "") -> "RenderContext":
        from .registry import RenderContext
        return RenderContext(self.styles(name), self.config(name), series_label)
