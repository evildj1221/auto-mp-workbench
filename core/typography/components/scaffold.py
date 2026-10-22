#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scaffold.py · 脚手架组件（9 个，阶段一 §3.3 清单 #15-22 + 头图容器）。

篇首区（kicker → 大标题 → 副题 → 分隔符 → 头图）与收尾区（分隔符 →
收尾文案 → 邮票框 → 关注引导）为固定结构：位置由渲染器编排，本模块只负责
「这块长什么样 + 文案从哪来」。品牌文案（kicker 前缀 / 三连 / 邮票框文字 /
关注引导）为主题级配置，校验器从生效主题取期望值做逐字比对。
"""
from ..registry import ComponentSpec
from ..text import escape


def _render_kicker(payload, ctx):
    prefix = ctx.config["prefix"]
    return f'<p style="{ctx.style()}">{escape(prefix)} · {escape(ctx.series_label)}篇</p>'


def _render_title(payload, ctx):
    return f'<p style="{ctx.style()}">{escape(payload)}</p>'


def _render_sub(payload, ctx):
    return f'<p style="{ctx.style()}">{escape(ctx.config["text"])}</p>'


def _render_symbol(payload, ctx):
    return f'<p style="{ctx.style()}">{ctx.config["symbol"]}</p>'


def _render_cover(payload, ctx):
    return (f'<img src="{escape(payload)}" alt="头图" data-cover="1" '
            f'style="{ctx.style()}">')


def _render_trio(payload, ctx):
    lines = ctx.config.get("lines") or []
    margins = ("margin:0 0 6px;", "margin:0 0 6px;", "margin:0 0 18px;")
    return "\n".join(
        f'<p style="{ctx.style("line")}{margin}">{escape(text)}</p>'
        for text, margin in zip(lines, margins))


def _render_stamp(payload, ctx):
    return (f'<p style="{ctx.style("outer")}">'
            f'<span style="{ctx.style("span")}">{escape(ctx.config["text"])}</span></p>')


def _render_follow(payload, ctx):
    return f'<p style="{ctx.style()}">{escape(ctx.config["text"])}</p>'


def register_scaffold(registry):
    """注册 9 个脚手架组件（清单 #15-22 八件套 + 头图容器 cover）。"""
    specs = [
        ComponentSpec(
            name="kicker", label="篇首 kicker", kind="scaffold", marker=False,
            description="系列名小标（品牌前缀为主题配置）",
            styles={"main": "text-align:center;font-size:${font_sizes.kicker};"
                            "letter-spacing:${letter_spacing.kicker};"
                            "color:${colors.kicker};margin:0 0 ${spacing.kicker_bottom};"},
            config_schema={"prefix": {"type": "text", "default": "你的公众号",
                                      "label": "品牌前缀"}},
            render=_render_kicker),
        ComponentSpec(
            name="title", label="大标题", kind="scaffold", marker=False,
            description="文稿标题（取 # 首行）",
            styles={"main": "text-align:center;font-size:${font_sizes.title};"
                            "font-weight:bold;color:${colors.title};"
                            "letter-spacing:${letter_spacing.title};"
                            "line-height:${line_heights.title};"
                            "margin:${spacing.title_margin};"},
            render=_render_title),
        ComponentSpec(
            name="sub", label="副题", kind="scaffold", marker=False,
            description="标题下引导小字（文案为主题配置）",
            styles={"main": "text-align:center;font-size:${font_sizes.sub};"
                            "color:${colors.muted};"
                            "letter-spacing:${letter_spacing.sub};"
                            "margin:0 0 ${spacing.sub_bottom};"},
            config_schema={"text": {"type": "text",
                                    "default": "每晚 22:00 · 一封写给你的信",
                                    "label": "副题文案"}},
            render=_render_sub),
        ComponentSpec(
            name="divider_head", label="篇首分隔符", kind="scaffold", marker=False,
            description="标题区与正文之间的 ✦ ✦ ✦",
            styles={"main": "text-align:center;color:${colors.accent};"
                            "font-size:${font_sizes.divider};"
                            "letter-spacing:${letter_spacing.divider};"
                            "margin:${spacing.div_head_margin};"},
            config_schema={"symbol": {"type": "text", "default": "✦ ✦ ✦",
                                      "label": "符号"}},
            render=_render_symbol),
        ComponentSpec(
            name="divider_tail", label="收尾分隔符", kind="scaffold", marker=False,
            description="正文与收尾区之间的 ✦ ✦ ✦",
            styles={"main": "text-align:center;color:${colors.accent};"
                            "font-size:${font_sizes.divider};"
                            "letter-spacing:${letter_spacing.divider};"
                            "margin:${spacing.div_tail_margin};"},
            config_schema={"symbol": {"type": "text", "default": "✦ ✦ ✦",
                                      "label": "符号"}},
            render=_render_symbol),
        ComponentSpec(
            name="cover", label="头图容器", kind="scaffold", marker=False,
            description="每篇唯一配图，紧跟篇首分隔符（v4.2）",
            styles={"main": "width:100%;border-radius:${radii.cover};"
                            "margin:0 0 ${spacing.cover_gap};display:block;"},
            render=_render_cover),
        ComponentSpec(
            name="trio", label="收尾文案", kind="scaffold", marker=False,
            description="固定三行收束语（校验器逐字比对，文案为主题配置）",
            styles={"line": "text-align:center;font-size:${font_sizes.closing};"
                            "font-weight:bold;color:${colors.title};"
                            "line-height:${line_heights.body};"},
            config_schema={"lines": {"type": "list",
                                     "default": ["我是你的笔名。",
                                                 "每晚 22:00，给你回一封信。",
                                                 "明晚见。"],
                                     "label": "三连文案"}},
            render=_render_trio),
        ComponentSpec(
            name="stamp", label="邮票框", kind="scaffold", marker=False,
            description="收尾账号印章（文字为主题配置）",
            styles={
                "outer": "text-align:center;margin:0 0 ${spacing.stamp_gap};",
                "span": "display:inline-block;border:${borders.stamp} solid "
                        "${colors.frame};border-radius:${radii.stamp};"
                        "padding:${spacing.stamp_padding};"
                        "font-size:${font_sizes.stamp};"
                        "letter-spacing:${letter_spacing.stamp};"
                        "color:${colors.muted};"},
            config_schema={"text": {"type": "text", "default": "你的公众号",
                                    "label": "邮票文字"}},
            render=_render_stamp),
        ComponentSpec(
            name="follow", label="关注引导", kind="scaffold", marker=False,
            description="最后一行关注引导（文案为主题配置）",
            styles={"main": "text-align:center;font-size:${font_sizes.follow};"
                            "color:${colors.muted};"
                            "letter-spacing:${letter_spacing.follow};margin:0;"},
            config_schema={"text": {"type": "text",
                                    "default": "关注我，每晚 22:00，收到一封只写给你的信。",
                                    "label": "引导文案"}},
            render=_render_follow),
    ]
    for spec in specs:
        registry.register(spec)
