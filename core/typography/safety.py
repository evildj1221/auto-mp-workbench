#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""safety.py · 排版开放的绝对安全底线（商业化 P0）。

背景（2026-09-05 实测 BUG-2）：主题色板校验是**自指**的——
``exporter.validate_html`` 用 ``theme.allowed_colors()`` 做白名单，而该集合
由主题自身的 tokens 生成。用户把 ``tokens.colors.title`` 改成任意值（实测
``zzz``）后，白名单跟着变，17 项自检依然全绿。

本模块提供**不随主题变化**的绝对底线，分三层：

  L1 格式合法性   色值必须是合法 hex；字号必须是合法 px 且在区间内
                  → 拦住 "zzz" 这类坏值，在写入 settings.json 之前
  L2 可读性与调性 正文色/底色的对比度下限、正文最小字号、禁止纯黑白
                  → 保证任何主题都还是"能读的陪伴式排版"
  L3 公众号兼容   由 exporter.validate_html 承担（本模块提供色值扫描能力）

设计原则：纯函数、无 IO、不依赖 Flask / 主题文件，可被 manager（写入前拦截）、
exporter（导出后校验）、未来的主题市场（导入前审查）三方复用。
"""
import re

# ============================================================ L1 格式

_HEX_RE = re.compile(r"^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$")
_SIZE_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)px$")

# ============================================================ L2 底线

MIN_BODY_SIZE_PX = 14.0      # 微信移动端可读性下限
MAX_SIZE_PX = 36.0           # 超过即破坏"克制"调性，也容易撑破版心
MIN_BODY_CONTRAST = 4.5      # WCAG AA（小字号正文）
FORBIDDEN_PLAIN = {"#000000", "#FFFFFF", "#000", "#FFF"}

# 需要满足可读下限的「正文类」字号键。kicker(12px) / stamp(11px) / footnote(12px)
# 等装饰性次要文字不在其中——只要求格式合法与上限，不要求 ≥14px。
MIN_READABLE_KEYS = {"body", "quote", "letter_p", "letter_q", "action_p",
                     "closing", "h2", "h3", "card_title"}

# CSS 颜色属性（只扫这些，避免误伤 font-size / letter-spacing 等）
_COLOR_PROPS = ("color", "background", "background-color", "border-color",
                "border-top-color", "border-left-color", "outline-color")
_COLOR_DECL_RE = re.compile(
    r"(?:^|;|\")\s*(?:" + "|".join(_COLOR_PROPS) + r")\s*:\s*([^;\"]*)",
    flags=re.I)

# 允许出现在颜色位置的 CSS 关键字（不构成"非法色值"）
_SAFE_COLOR_KEYWORDS = {
    "transparent", "inherit", "initial", "unset", "currentcolor", "none",
    "solid", "dashed", "dotted", "double", "rgb", "rgba", "hsl", "hsla",
}


def is_hex_color(value) -> bool:
    return isinstance(value, str) and bool(_HEX_RE.match(value.strip()))


def parse_size_px(value):
    """返回 (ok, px)。非法返回 (False, None)。"""
    if not isinstance(value, str):
        return False, None
    m = _SIZE_RE.match(value.strip())
    if not m:
        return False, None
    return True, float(m.group(1))


def is_color_path(dotted: str) -> bool:
    """tokens.colors.* → 色值路径。"""
    parts = dotted.split(".")
    return len(parts) >= 2 and parts[0] == "tokens" and parts[1] == "colors"


def is_size_path(dotted: str) -> bool:
    """tokens.font_sizes.* 或 tokens.*.size → 字号路径。"""
    parts = dotted.split(".")
    if len(parts) >= 2 and parts[0] == "tokens" and parts[1] == "font_sizes":
        return True
    return len(parts) >= 3 and parts[0] == "tokens" and parts[-1] in (
        "size", "font_size", "font-size")


def validate_token_value(dotted: str, value):
    """按 token 路径推断值类型并做 L1 格式 + L2 底线校验。

    返回 (ok, reason)。无法推断类型的路径（间距 22px / 圆角 6px 等）一律放行——
    本模块只守"色值"与"字号"两类高风险 token。
    """
    if is_color_path(dotted):
        if not is_hex_color(value):
            return False, f"色值格式非法: {value!r}（须为 #RGB 或 #RRGGBB，例 #1A2B4A）"
        if value.strip().upper() in FORBIDDEN_PLAIN:
            return False, f"不允许纯黑白: {value!r}（破坏陪伴式调性）"
        return True, ""
    if is_size_path(dotted):
        ok, px = parse_size_px(value)
        if not ok:
            return False, f"字号格式非法: {value!r}（须为 Npx，例 15px）"
        if px > MAX_SIZE_PX:
            return False, f"字号 {px}px 超过上限 {MAX_SIZE_PX}px"
        if px < MIN_BODY_SIZE_PX and (dotted.split(".")[-1] in MIN_READABLE_KEYS):
            return False, f"字号 {px}px 小于可读下限 {MIN_BODY_SIZE_PX}px（微信移动端不可读）"
        return True, ""
    return True, ""


# ============================================================ 对比度

def _luminance(hex_color: str) -> float:
    h = hex_color.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def f(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast_ratio(a: str, b: str) -> float:
    """WCAG 相对亮度对比度，返回 1.0 ~ 21.0。非法色值返回 0.0。"""
    try:
        l1, l2 = sorted((_luminance(a), _luminance(b)), reverse=True)
    except Exception:
        return 0.0
    return (l1 + 0.05) / (l2 + 0.05)


# ============================================================ 主题级审查

def audit_theme(theme) -> list:
    """对一份主题做 L1+L2 全量审查，返回 [{'name','ok','detail'}]。

    只审查**绝对底线**，不做"是否符合品牌规范"这类主观判断——后者由
    exporter.validate_html 的主题白名单承担。两者叠加才是完整防线。
    """
    out = []
    tokens = theme.tokens or {}
    colors = tokens.get("colors") or {}
    font_sizes = tokens.get("font_sizes") or {}

    def add(name, ok, detail=""):
        out.append({"name": name, "ok": bool(ok), "detail": detail})

    bad_color = [f"{k}={v!r}" for k, v in colors.items() if not is_hex_color(v)]
    add("色值格式全部合法", not bad_color, "；".join(bad_color))

    bad_size = []
    for k, v in font_sizes.items():
        ok, px = parse_size_px(v)
        if not ok:
            bad_size.append(f"{k}={v!r}")
        elif px > MAX_SIZE_PX or (px < MIN_BODY_SIZE_PX and k in MIN_READABLE_KEYS):
            bad_size.append(f"{k}={v!r}（超出允许区间）")
    add("字号格式与区间合法", not bad_size, "；".join(bad_size))

    # 正文可读性：只审 body/page 这一对，避免误伤 muted 等装饰性次要色
    body = colors.get("body")
    page = colors.get("page") or colors.get("surface") or "#FFFFFF"
    if is_hex_color(body) and is_hex_color(page):
        c = contrast_ratio(body, page)
        add(f"正文对比度 ≥ {MIN_BODY_CONTRAST}:1", c >= MIN_BODY_CONTRAST,
            f"body {body} on {page} = {c:.2f}:1")
    else:
        add(f"正文对比度 ≥ {MIN_BODY_CONTRAST}:1", False,
            f"缺 colors.body 或色值非法（body={body!r}, page={page!r}）")

    return out


# ============================================================ HTML 级扫描

def scan_bad_colors(html: str) -> list:
    """扫出 HTML 中**格式非法**的颜色写法（如 `color:zzz`、`color:#GGG`）。

    只扫描颜色类 CSS 声明，避免误伤 font-size / letter-spacing / border-style
    等属性。返回去重保序的坏值列表。
    """
    bad = []
    for decl in _COLOR_DECL_RE.findall(html or ""):
        for tok in re.findall(r"#[0-9A-Za-z]*", decl):
            if not is_hex_color(tok):            # #zzz / #GGG / #12345
                bad.append(tok)
        for word in re.findall(r"(?<![\w#.-])([A-Za-z][A-Za-z-]*)(?![\w(])", decl):
            if word.lower() in _SAFE_COLOR_KEYWORDS:
                continue
            # 函数式颜色 rgb(...) / rgba(...) 的左括号已被上面的 (?![\w(]) 排除
            bad.append(word)
    seen, out = set(), []
    for b in bad:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out
