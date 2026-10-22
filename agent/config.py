#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/config.py · Agent 运行配置（预算护栏 / 步数上限 / 自主度）。

混合自主的「代码侧」边界全部在这里：max_steps 与 max_cost_cny 超限即暂停
（paused_budget），用户可提额恢复——LLM 永远不能自己突破护栏。
"""
from dataclasses import asdict, dataclass, field, replace


@dataclass
class AgentRunConfig:
    max_steps: int = 40            # 工具调用总数上限（防死循环烧钱）
    max_cost_cny: float = 2.0      # 单次运行 LLM 成本上限（planner 侧估算累计）
    tool_timeout_s: int = 300      # 单工具超时（registry 兜底）
    autonomy: str = "assisted"     # assisted=人工关卡 / semi=自动选题 / auto=全自动（见下）
    temperature: float = 0.2       # planner 温度
    topic_confirm: bool = True     # 选题确认关卡（路由开关；节点内再按 autonomy 决定是否自动放行）
    publish_confirm: bool = True   # 发布前确认关卡（同上）
    soft_gate: bool = True         # LLM-as-Judge 软门禁（写稿通过硬门禁后评审一次）
    soft_gate_threshold: float = 3.5  # 五维软评分均分阈值，低于则建议重写一轮

    # autonomy 档位语义（P2 · 2026-09-17）：
    #   assisted = 两次人工确认（原 hybrid）
    #   semi     = 评分完成后自动选 Top1（遵循风向标），发布前仍人工确认
    #   auto     = 自动选题 + 发布自动批准（质检全过才放行，否则回退人工）
    _AUTONOMY_ALIASES = {"hybrid": "assisted", "full_auto": "auto"}

    @property
    def auto_topic(self) -> bool:
        return self.autonomy in ("semi", "auto")

    @property
    def auto_publish(self) -> bool:
        return self.autonomy == "auto"

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        if isinstance(d, AgentRunConfig):
            return d
        known = {f for f in AgentRunConfig.__dataclass_fields__}
        clean = {k: v for k, v in (d or {}).items() if k in known}
        cfg = AgentRunConfig(**clean)
        cfg.autonomy = AgentRunConfig._AUTONOMY_ALIASES.get(cfg.autonomy, cfg.autonomy)
        if cfg.autonomy not in ("assisted", "semi", "auto"):
            cfg.autonomy = "assisted"
        return cfg

    def with_overrides(self, **kw):
        return replace(self, **{k: v for k, v in kw.items() if v is not None})


DEFAULT_CONFIG = AgentRunConfig()
