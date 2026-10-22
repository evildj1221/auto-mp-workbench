#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""registry.py · 排版组件注册表（阶段一 S1）。

组件 = 「标记语法 + 渲染函数 + 配置 schema」标准三元组（ComponentSpec）：

- 注册  register(spec)      重名即 ValueError（早失败，不允许静默覆盖）
- 查询  get(name) / all() / by_kind() / marker_names()
- 卸载  unregister(name)    内置组件拦截 ValueError；外部组件可卸载

纯逻辑层：不 import Flask / exporter / LLM。输入数据，输出字符串与 dict，
可被导出器、排版中心 UI、未来 AI 自动排版三方复用。
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


class RenderContext:
    """渲染上下文：把该组件解析完成的样式槽 / 配置 / 系列词递给 render()。

    styles: {slot: 内联样式字面值}（主题 token 已展开，无 ${} 残留）
    config: 配置 dict（schema 默认 ← 主题 component_defaults 已合并）
    """

    __slots__ = ("styles", "config", "series_label")

    def __init__(self, styles: Dict[str, str], config: dict,
                 series_label: str = ""):
        self.styles = styles
        self.config = config
        self.series_label = series_label

    def style(self, slot: str = "main") -> str:
        return self.styles.get(slot, "")


@dataclass(frozen=True)
class ComponentSpec:
    """排版组件规格。styles 槽模板与 config_schema 默认值可引用主题 token
    （语法 ``${colors.title}``，theme.py 统一解析）。"""

    name: str                                   # 唯一标识 = :::围栏标记名
    label: str                                  # UI 显示名
    kind: str                                   # block 正文块 / scaffold 脚手架 / meta 元信息
    marker: bool                                # 是否由 :::name 围栏驱动
    description: str = ""                       # 组件画廊一句话说明
    styles: Dict[str, str] = field(default_factory=dict)
    config_schema: dict = field(default_factory=dict)
    render: Optional[Callable[[object, RenderContext], str]] = None
    builtin: bool = True


class ComponentRegistry:
    """组件注册表实例。全局默认实例见 core.typography.REGISTRY。"""

    def __init__(self):
        self._specs: Dict[str, ComponentSpec] = {}

    # ===== 注册 =====
    def register(self, spec: ComponentSpec):
        if not spec.name or not spec.name.isidentifier():
            raise ValueError(f"组件名非法: {spec.name!r}（需为合法标识符，即 :::标记名）")
        if spec.name in self._specs:
            raise ValueError(f"组件重复注册: {spec.name}（已注册 "
                             f"{self._specs[spec.name].label}）")
        self._specs[spec.name] = spec
        return spec

    # ===== 查询 =====
    def get(self, name: str) -> Optional[ComponentSpec]:
        return self._specs.get(name)

    def require(self, name: str) -> ComponentSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise KeyError(f"组件未注册: {name}")
        return spec

    def all(self) -> List[ComponentSpec]:
        return list(self._specs.values())

    def by_kind(self, kind: str) -> List[ComponentSpec]:
        return [s for s in self._specs.values() if s.kind == kind]

    def marker_names(self) -> List[str]:
        """全部围栏标记名（排序保证正则确定性；无前缀包含冲突）。"""
        names = sorted(s.name for s in self._specs.values() if s.marker)
        for i in range(1, len(names)):
            if names[i].startswith(names[i - 1]) and names[i] != names[i - 1]:
                # 正则交替按序匹配，前缀名会遮蔽更长名 → 注册期拦截
                raise ValueError(f"标记名存在前缀包含: {names[i - 1]} / {names[i]}")
        return names

    def __len__(self):
        return len(self._specs)

    def __contains__(self, name):
        return name in self._specs

    # ===== 卸载 =====
    def unregister(self, name: str):
        spec = self._specs.get(name)
        if spec is None:
            return False
        if spec.builtin:
            raise ValueError(f"内置组件不可卸载: {name}（{spec.label}）")
        del self._specs[name]
        return True
