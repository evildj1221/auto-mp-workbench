#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/render.py · HTML 排版渲染（5 色调色板 + 模板 + Markdown 转换）。

自 cat_shop.py 488-541 / 913-967 行原样迁出，行为不变。
"""
import re

from jinja2 import Environment, DictLoader, select_autoescape
import markdown as md_lib

# ===== 5 色调色板 =====
COLORS = {
    "primary": "#1A2B4A",     # 深蓝（理性蓝）
    "bg": "#F5F5F0",          # 月白（背景）
    "card": "#FAF7F2",        # 米白（卡片底）
    "muted": "#C8C5BD",       # 雾灰（辅助文字）
    "accent": "#E8B66E",      # 暖光（重点金句）
}

# ===== HTML 模板（5 色 + 字体） =====
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }}</title>
<style>
  body {
    max-width: 677px;
    margin: 0 auto;
    padding: 16px;
    font-family: "Source Han Sans CN", "Noto Sans CJK SC", -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 15px;
    line-height: 1.75;
    color: #3f3f3f;
    letter-spacing: 0.5px;
    background-color: #F5F5F0;
  }
  h1 { font-family: "Source Han Serif CN", "Noto Serif CJK SC", "SimSun", serif; font-size: 19px; color: #000; font-weight: bold; margin: 24px 0 16px; text-align: center; line-height: 1.4; }
  h2 { font-family: "Source Han Serif CN", "Noto Serif CJK SC", "SimSun", serif; font-size: 17px; color: #000; font-weight: bold; margin: 20px 0 12px; border-left: 4px solid #1A2B4A; padding-left: 12px; }
  h3 { font-family: "Source Han Serif CN", "Noto Serif CJK SC", "SimSun", serif; font-size: 16px; color: #000; font-weight: bold; margin: 16px 0 8px; }
  p { margin: 12px 0; text-align: justify; text-justify: inter-ideograph; }
  blockquote { border-left: 4px solid #E8B66E; background: #FAF7F2; padding: 12px 16px; margin: 16px 0; color: #888888; font-style: italic; font-size: 14px; }
  strong { color: #1A2B4A; font-weight: bold; }
  a { color: #1A2B4A; text-decoration: underline; }
  ul, ol { margin: 12px 0; padding-left: 24px; }
  li { margin: 6px 0; }
  hr { border: none; border-top: 1px dashed #C8C5BD; margin: 24px 0; }
  img { max-width: 100%; display: block; margin: 16px auto; border-radius: 4px; }
  .footer-signature { text-align: center; color: #888888; font-size: 13px; margin-top: 32px; padding-top: 16px; border-top: 1px dashed #C8C5BD; }
  .footer-signature .brand { color: #1A2B4A; font-weight: bold; }
</style>
</head>
<body>
<h1>{{ title }}</h1>
{{ content | safe }}
<hr>
<div class="footer-signature">
  <p>本文为 AI 辅助创作，仅代表作者个人观点</p>
  <p class="brand">—— 创作工作台 · 今晚 22:00 见 ——</p>
</div>
</body>
</html>
"""

# ===== HTML 渲染 =====
def render_html(title, md_content):
    plain_text = re.sub(r"[#*`>]", "", md_content)
    try:
        md = md_lib.Markdown(output_format="html5", extensions=["extra", "sane_lists", "smarty"])
        content_html = md.convert(md_content)
    except Exception:
        # 简易 fallback
        content_html = md_to_html_simple(md_content)
    env = Environment(loader=DictLoader({"t": HTML_TEMPLATE}), autoescape=select_autoescape(["html"]))
    template = env.get_template("t")
    return template.render(title=title, content=content_html)


def md_to_html_simple(md):
    lines = md.split("\n")
    out = []
    in_bq, in_ul = False, False
    for line in lines:
        line = line.rstrip()
        if line.startswith("# "):
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<h1>{escape(line[2:])}</h1>")
        elif line.startswith("## "):
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<h2>{escape(line[3:])}</h2>")
        elif line.startswith("### "):
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<h3>{escape(line[4:])}</h3>")
        elif line.strip() == "---":
            if in_ul: out.append("</ul>"); in_ul = False
            out.append("<hr>")
        elif line.startswith("> "):
            if in_ul: out.append("</ul>"); in_ul = False
            if not in_bq: out.append("<blockquote>"); in_bq = True
            out.append(f"<p>{escape(line[2:])}</p>")
        elif line.startswith("- "):
            if in_bq: out.append("</blockquote>"); in_bq = False
            if not in_ul: out.append("<ul>"); in_ul = True
            out.append(f"<li>{escape(line[2:])}</li>")
        elif line == "":
            if in_bq: out.append("</blockquote>"); in_bq = False
            if in_ul: out.append("</ul>"); in_ul = False
        else:
            if in_bq: out.append("</blockquote>"); in_bq = False
            if in_ul: out.append("</ul>"); in_ul = False
            line_esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escape(line))
            out.append(f"<p>{line_esc}</p>")
    if in_bq: out.append("</blockquote>")
    if in_ul: out.append("</ul>")
    return "\n".join(out)


def escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
