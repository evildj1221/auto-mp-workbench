#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""body.py · 正文块组件（14 个 block + 1 个 meta，阶段一 §3.3 清单 #1-14）。

存量迁移（p / quote / letter / action / titles）：渲染输出与原 exporter.py
if-elif 分支逐字节一致——样式改由主题槽提供，结构逻辑原样保留。
新增组件（h2 / h3 / divider / card / imgtext / timeline / emphasis / sign /
footnote / svgslot）：仅注册可用，默认不进 writer prompt 写作契约。
"""
import re

from ..registry import ComponentSpec
from ..text import escape, inline

_SIGN_RE = re.compile(r"^>\s*落款[：:]\s*(.*)$")


# ===== 存量迁移：正文段落（解析器默认块，非围栏驱动）与金句引用（:::quote） =====
def _render_text(payload, ctx):
    text = " ".join(payload) if isinstance(payload, list) else str(payload)
    return f'<p style="{ctx.style()}">{inline(escape(text))}</p>'


# ===== 存量迁移：信件块（:::letter，自定义解析：落款行 / 信内金句 / 末行边距） =====
def _render_letter(payload, ctx):
    rows = []
    items = list(payload)
    for i, ln in enumerate(items):
        last = i == len(items) - 1
        m_sign = _SIGN_RE.match(ln)
        if m_sign:
            sign = escape(m_sign.group(1).strip())
            rows.append(f'<p style="{ctx.style("sign")}">——{sign}</p>')
        elif ln.startswith("**") and ln.endswith("**") and len(ln) > 4:
            style = ctx.style("quote" if not last else "quote_last")
            rows.append(f'<p style="{style}">{escape(ln[2:-2])}</p>')
        else:
            style = ctx.style("para" if not last else "para_last")
            rows.append(f'<p style="{style}">{inline(escape(ln))}</p>')
    return f'<section style="{ctx.style("box")}">' + "".join(rows) + "</section>"


# ===== 存量迁移：行动卡（:::action，标签文案来自主题配置） =====
def _render_action(payload, ctx):
    text = escape(" ".join(payload))
    return (f'<section style="{ctx.style("box")}">'
            f'<p style="{ctx.style("label")}">{escape(ctx.config["label_text"])}</p>'
            f'<p style="{ctx.style("para")}">{text}</p></section>')


# ===== 新增：H2 / H3 标题层级 =====
# ===== 新增：分隔线（三档 preset） =====
def _render_divider(payload, ctx):
    preset = ctx.config.get("preset", "stars")
    if preset == "line":
        return f'<section style="{ctx.style("line")}"></section>'
    if preset == "gradient":
        return f'<section style="{ctx.style("gradient")}"></section>'
    return f'<p style="{ctx.style("stars")}">{ctx.config["symbol"]}</p>'


# ===== 新增：通用信息卡（首行标题 + 余行正文） =====
def _render_card(payload, ctx):
    lines = [ln for ln in list(payload) if ln.strip()]
    if not lines:
        return ""
    title, body = lines[0], " ".join(lines[1:])
    out = f'<section style="{ctx.style("box")}">'
    out += f'<p style="{ctx.style("title")}">{inline(escape(title))}</p>'
    if body:
        out += f'<p style="{ctx.style("para")}">{inline(escape(body))}</p>'
    return out + "</section>"


# ===== 新增：图文并排（默认停用；启用时首行图片 URL、余行图注） =====
def _render_imgtext(payload, ctx):
    lines = [ln.strip() for ln in list(payload) if ln.strip()]
    if not lines:
        return ""
    url, caption = lines[0], " ".join(lines[1:])
    out = f'<section style="{ctx.style("box")}">'
    out += f'<img src="{escape(url)}" alt="配图" style="{ctx.style("img")}">'
    if caption:
        out += f'<p style="{ctx.style("caption")}">{inline(escape(caption))}</p>'
    return out + "</section>"


# ===== 新增：时间线（- 前缀解析为节点） =====
def _render_timeline(payload, ctx):
    nodes = [ln[2:].strip() if ln.startswith("- ") else ln.strip()
             for ln in list(payload) if ln.strip()]
    if not nodes:
        return ""
    rows = "".join(f'<p style="{ctx.style("node")}">{inline(escape(n))}</p>'
                   for n in nodes)
    return f'<section style="{ctx.style("box")}">' + rows + "</section>"


# ===== 新增：轻量强调块 =====
# ===== 新增：独立签名档 =====
def _render_sign(payload, ctx):
    text = " ".join(payload) if isinstance(payload, list) else str(payload)
    return f'<p style="{ctx.style()}">——{escape(text)}</p>'


# ===== 新增：尾注（小字备注区） =====
def _render_footnote(payload, ctx):
    text = " ".join(payload) if isinstance(payload, list) else str(payload)
    return (f'<section style="{ctx.style("box")}">'
            f'<p style="{ctx.style("text")}">{inline(escape(text))}</p></section>')


# ===== 新增：SVG 互动占位（一期占位框形态，素材管线归二期） =====
def _render_svgslot(payload, ctx):
    lines = [ln.strip() for ln in list(payload) if ln.strip()]
    desc = lines[0] if lines else "互动素材"
    size = lines[1] if len(lines) > 1 else "自适应"
    return (f'<section style="{ctx.style("box")}">'
            f'<p style="{ctx.style("label")}">互动素材位 · {escape(desc)} · {escape(size)}</p>'
            f"</section>")


def register_body(registry):
    """注册 14 个正文块组件 + 1 个 meta 组件（清单 #1-14）。"""
    specs = [
        ComponentSpec(
            name="p", label="正文段落", kind="block", marker=False,
            description="普通正文段（解析器默认块，非围栏驱动），存量迁移",
            styles={"main": "font-size:${font_sizes.body};color:${colors.body};"
                            "line-height:${line_heights.body};"
                            "letter-spacing:${letter_spacing.body};"
                            "margin:0 0 ${spacing.block_gap};"},
            config_schema={"enabled": {"type": "bool", "default": True, "label": "启用"}},
            render=_render_text),
        ComponentSpec(
            name="h2", label="标题 · H2", kind="block", marker=True,
            description="章节级标题，供人工组合与预览（默认不进写作契约）",
            styles={"main": "font-size:${font_sizes.h2};font-weight:bold;"
                            "color:${colors.title};letter-spacing:${letter_spacing.title};"
                            "line-height:1.8;margin:0 0 ${spacing.h2_bottom};"},
            config_schema={"enabled": {"type": "bool", "default": True, "label": "启用"}},
            render=_render_text),
        ComponentSpec(
            name="h3", label="标题 · H3", kind="block", marker=True,
            description="小节级标题，与 H2 构成标题层级",
            styles={"main": "font-size:${font_sizes.h3};font-weight:bold;"
                            "color:${colors.title};letter-spacing:${letter_spacing.title};"
                            "line-height:1.8;margin:0 0 ${spacing.h3_bottom};"},
            config_schema={"enabled": {"type": "bool", "default": True, "label": "启用"}},
            render=_render_text),
        ComponentSpec(
            name="quote", label="金句引用", kind="block", marker=True,
            description="情绪收束句（写作契约 :::quote 2~5 处），存量迁移",
            styles={"main": "font-size:${font_sizes.quote};color:${colors.title};"
                            "font-weight:bold;line-height:${line_heights.body};"
                            "letter-spacing:${letter_spacing.body};"
                            "margin:0 0 ${spacing.block_gap};"},
            config_schema={"enabled": {"type": "bool", "default": True, "label": "启用"}},
            render=_render_text),
        ComponentSpec(
            name="letter", label="信件", kind="block", marker=True,
            description="读者来信/回信块：落款行（> 落款：）与 **信内金句** 自动识别",
            styles={
                "box": "background:${colors.surface};"
                       "border-left:${borders.letter_left} solid ${colors.accent};"
                       "border-radius:${radii.letter};padding:${spacing.letter_padding};"
                       "margin:0 0 ${spacing.letter_gap};",
                "para": "font-size:${font_sizes.letter_p};color:${colors.letter_ink};"
                        "line-height:${line_heights.body};margin:0 0 ${spacing.letter_p_gap};",
                "para_last": "font-size:${font_sizes.letter_p};color:${colors.letter_ink};"
                             "line-height:${line_heights.body};margin:0;",
                "quote": "font-size:${font_sizes.letter_q};color:${colors.title};"
                         "font-weight:bold;line-height:${line_heights.body};"
                         "margin:0 0 ${spacing.letter_p_gap};",
                "quote_last": "font-size:${font_sizes.letter_q};color:${colors.title};"
                              "font-weight:bold;line-height:${line_heights.body};margin:0;",
                "sign": "font-size:${font_sizes.letter_sign};color:${colors.muted};"
                        "line-height:${line_heights.body};text-align:right;margin:0;"},
            config_schema={"enabled": {"type": "bool", "default": True, "label": "启用"}},
            render=_render_letter),
        ComponentSpec(
            name="action", label="行动卡", kind="block", marker=True,
            description="「今晚 · 试一试」行动邀请，每篇一张（标签文案为主题配置）",
            styles={
                "box": "background:${colors.card};border:${borders.card} solid "
                       "${colors.card_border};border-top:${borders.card_top} solid "
                       "${colors.accent};border-radius:${radii.card};"
                       "padding:${spacing.card_padding};margin:0 0 ${spacing.card_gap};",
                "label": "font-size:${font_sizes.action_label};"
                         "letter-spacing:${letter_spacing.action_label};"
                         "color:${colors.kicker};font-weight:bold;"
                         "margin:0 0 ${spacing.action_label_gap};",
                "para": "font-size:${font_sizes.action_p};color:${colors.body};"
                        "line-height:${line_heights.body};margin:0;"},
            config_schema={"enabled": {"type": "bool", "default": True, "label": "启用"},
                           "label_text": {"type": "text", "default": "今晚 · 试一试",
                                          "label": "标签文案"}},
            render=_render_action),
        ComponentSpec(
            name="divider", label="分隔线", kind="block", marker=True,
            description="文中分隔：星辰 ✦ / 细线 / 渐变三档样式",
            styles={
                "stars": "text-align:center;color:${colors.accent};"
                         "font-size:${font_sizes.divider};"
                         "letter-spacing:${letter_spacing.divider};"
                         "margin:0 0 ${spacing.block_gap};",
                "line": "border-top:${borders.card} solid ${colors.card_border};"
                        "margin:0 0 ${spacing.block_gap};",
                "gradient": "background:linear-gradient(90deg,transparent,"
                            "${colors.accent},transparent);height:2px;"
                            "margin:0 0 ${spacing.block_gap};"},
            config_schema={"preset": {"type": "select", "default": "stars",
                                      "options": ["stars", "line", "gradient"],
                                      "label": "样式"},
                           "symbol": {"type": "text", "default": "✦ ✦ ✦", "label": "符号"},
                           "enabled": {"type": "bool", "default": True, "label": "启用"}},
            render=_render_divider),
        ComponentSpec(
            name="card", label="卡片", kind="block", marker=True,
            description="通用信息卡：首行标题 + 余行正文",
            styles={
                "box": "background:${colors.surface};border:${borders.card} solid "
                       "${colors.card_border};border-radius:${radii.card};"
                       "padding:${spacing.card_padding};margin:0 0 ${spacing.block_gap};",
                "title": "font-size:${font_sizes.card_title};font-weight:bold;"
                         "color:${colors.title};line-height:${line_heights.body};"
                         "margin:0 0 ${spacing.action_label_gap};",
                "para": "font-size:${font_sizes.body};color:${colors.body};"
                        "line-height:${line_heights.body};margin:0;"},
            config_schema={"enabled": {"type": "bool", "default": True, "label": "启用"}},
            render=_render_card),
        ComponentSpec(
            name="imgtext", label="图文并排", kind="block", marker=True,
            description="首行图片 URL + 图注；默认停用（v4.2 每篇一张头图策略不变）",
            styles={
                "box": "margin:0 0 ${spacing.block_gap};",
                "img": "width:100%;border-radius:${radii.cover};display:block;"
                       "margin:0 0 ${spacing.action_label_gap};",
                "caption": "text-align:center;font-size:${font_sizes.placeholder};"
                           "color:${colors.muted};letter-spacing:${letter_spacing.sub};"
                           "margin:0;"},
            config_schema={"enabled": {"type": "bool", "default": False, "label": "启用"}},
            render=_render_imgtext),
        ComponentSpec(
            name="timeline", label="时间线", kind="block", marker=True,
            description="节点列表（- 前缀一行一节点）",
            styles={
                "box": "border-left:${borders.timeline_left} solid ${colors.accent};"
                       "padding-left:${spacing.timeline_indent};"
                       "margin:0 0 ${spacing.block_gap};",
                "node": "font-size:${font_sizes.timeline};color:${colors.letter_ink};"
                        "line-height:${line_heights.body};"
                        "margin:0 0 ${spacing.timeline_gap};"},
            config_schema={"enabled": {"type": "bool", "default": True, "label": "启用"}},
            render=_render_timeline),
        ComponentSpec(
            name="emphasis", label="重点强调", kind="block", marker=True,
            description="轻量强调块，介于正文与金句之间",
            styles={"main": "background:${colors.surface};border-radius:${radii.emphasis};"
                            "padding:${spacing.emphasis_padding};"
                            "font-size:${font_sizes.body};color:${colors.title};"
                            "line-height:${line_heights.body};"
                            "margin:0 0 ${spacing.emphasis_gap};"},
            config_schema={"enabled": {"type": "bool", "default": True, "label": "启用"}},
            render=_render_text),
        ComponentSpec(
            name="sign", label="签名档", kind="block", marker=True,
            description="独立签名（信件落款之外的轻量形态）",
            styles={"main": "text-align:right;font-size:${font_sizes.sign};"
                            "color:${colors.muted};line-height:${line_heights.body};"
                            "margin:0 0 ${spacing.block_gap};"},
            config_schema={"enabled": {"type": "bool", "default": True, "label": "启用"}},
            render=_render_sign),
        ComponentSpec(
            name="footnote", label="尾注", kind="block", marker=True,
            description="小字备注区（参考来源 / 往期链接）",
            styles={
                "box": "border-top:${borders.footnote_top} solid ${colors.card_border};"
                       "padding-top:${spacing.footnote_padding_top};"
                       "margin:0 0 ${spacing.block_gap};",
                "text": "font-size:${font_sizes.footnote};color:${colors.muted};"
                        "line-height:1.8;margin:0;"},
            config_schema={"enabled": {"type": "bool", "default": True, "label": "启用"}},
            render=_render_footnote),
        ComponentSpec(
            name="svgslot", label="SVG 互动占位", kind="block", marker=True,
            description="互动素材占位框（首行描述、次行尺寸；真实素材管线归二期）",
            styles={
                "box": "border:${borders.svgslot} dashed ${colors.frame};"
                       "border-radius:${radii.svgslot};padding:18px;"
                       "margin:0 0 ${spacing.block_gap};text-align:center;",
                "label": "font-size:${font_sizes.placeholder};color:${colors.frame};"
                         "letter-spacing:${letter_spacing.sub};margin:0;"},
            config_schema={"enabled": {"type": "bool", "default": True, "label": "启用"}},
            render=_render_svgslot),
        # meta：写稿期标题候选，导出渲染静默跳过（现状行为原样保留）
        ComponentSpec(
            name="titles", label="标题候选", kind="meta", marker=True,
            description="写稿期供选标题，导出时静默跳过",
            config_schema={}, render=None),
    ]
    for spec in specs:
        registry.register(spec)
