#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
公众号导出器：规则渲染 + LLM 回退 + 确定性校验器
（商业化阶段一 S2：渲染与校验全部由组件注册表 + 主题系统驱动）

- 渲染：组件标记 → core.typography 注册表分发 → 生效主题解析样式槽/配置 →
  全内联样式 HTML（零 LLM）；无标记存量稿回退 LLM 渲染（按默认视觉规范）
- 校验：色值/字号白名单与品牌文案期望值（kicker 前缀、行动卡标签、收尾文案、
  邮票框）全部由主题派生——rule 引擎用生效主题，llm 回退用默认主题，
  校验基准与渲染基准始终一致
- 产物：data/drafts/{date}_mp.html + data/drafts/{date}_mp_report.json
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).resolve().parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import cat_shop  # noqa: E402  (复用 call_llm / redline_check / 路径常量)
from core.typography import (REGISTRY, ThemeRuntime, default_theme,  # noqa: E402
                             contrast_ratio, is_hex_color, scan_bad_colors)
from core.typography.manager import get_manager  # noqa: E402
from core.typography.safety import MIN_BODY_CONTRAST  # noqa: E402
from core.quality import count_chinese_chars  # noqa: E402
from agent.gates import WORD_HARD_MAX, WORD_MIN  # noqa: E402（字数口径与门禁一致）

SPEC_PATH = SCRIPT_DIR / "排版视觉规范.md"   # 私有品牌规范（本地/私有部署）
SPEC_PATH_FALLBACK = SCRIPT_DIR / "排版视觉规范.md"       # 中性规范（开源分发自带）

EXPORT_PROMPT = """<role>
你是「你的公众号」公众号排版师。把一篇 Markdown 文稿转换为可直接粘贴进公众号编辑器的 HTML（全部内联样式）。
</role>

<task>
严格按下方《排版与视觉规范》输出。只输出 JSON：{"html": "..."}，html 字段为正文片段（不要 <html>/<head>/<body> 标签，不要 class，不要 <style>，容器一律用 <section> 或 <p>）。
</task>

<hard_rules>
- 所有样式写在 style 属性内；不得出现 class= 或 <style>
- 颜色只能用规范色板；字号只能用规范 13 档
- 整页结构固定：篇首 kicker → 大标题 → 副题 → 分隔符 ✦ ✦ ✦ → 正文流 → 行动卡"今晚 · 试一试" → 收尾区（分隔符 + 三连 + 邮票框 + 关注引导）
- 收尾文案必须逐字为：我是你的笔名。/ 每晚 22:00，给你回一封信。/ 明晚见。
- 行动卡恰有一张，且在收尾文案之前
- 情绪收束句 2~7 处（15.5px 深夜蓝加粗，朴素口语、不煽情）；正文无编号无小标题
- 文稿中的 ## 小标题全部去掉，内容融入正文流
- 原文内容不得增删改意思，只做结构映射与排版
</hard_rules>

<spec>
%s
</spec>

<article>
%s
</article>
"""


def _prune_history(hist_dir: Path, keep: int = 10):
    """历史版本封顶：按修改时间保留最近 keep 对 html/json，更旧的删除
    （每次导出追加两份，无限增长）。"""
    try:
        files = sorted(hist_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[keep * 2:]:
            try:
                p.unlink()
            except OSError:
                pass
    except OSError:
        pass


def load_spec():
    """私有品牌规范优先；开源分发无品牌文件时回退中性规范（LLM 渲染不因缺文件而崩）。"""
    for pth in (SPEC_PATH, SPEC_PATH_FALLBACK):
        if pth.exists():
            return pth.read_text(encoding="utf-8")
    raise FileNotFoundError(f"排版规范缺失：{SPEC_PATH.name} 或 {SPEC_PATH_FALLBACK.name}")


def render_mp_html_llm(md_text):
    """LLM 渲染（回退路径）：markdown → 规范版内联 HTML 片段。用于无组件标记的存量稿件。
    始终按默认视觉规范渲染（提示词与规范文档绑定），不随排版中心主题切换。"""
    prompt = EXPORT_PROMPT % (load_spec(), md_text)
    res = cat_shop.call_llm(prompt, temperature=0, max_tokens=8000, module="mp_export")
    html = (res.get("html") or "").strip()
    if not html:
        raise RuntimeError("LLM 返回空 html")
    return html


# ===== 规则渲染器（阶段一 S2：标记解析 → 注册表分发 → 主题解析 → 内联样式） =====
SERIES_LABELS = {"A": "独居治愈", "B": "晚安陌生人", "C": "家庭关系",
                 "D": "中年危机", "E": "身体小秘密", "F": "亲密关系"}

# 篇首区与收尾区为固定脚手架结构（视觉规范），顺序由本渲染器编排
_HEAD_ORDER = ("kicker", "title", "sub", "divider_head")
_TAIL_ORDER = ("divider_tail", "trio", "stamp", "follow")


def has_markers(md_text):
    return ":::" in (md_text or "")


def _marker_open_re():
    """围栏标记正则由注册表派生：新组件注册即可用 :::name 驱动，解析器零改动。"""
    return re.compile(r"^:::(%s)\s*$" % "|".join(REGISTRY.marker_names()))


def _parse_marked(md_text):
    """解析组件标记文稿 → (title, blocks, unknown)。
    blocks: [(type, payload), ...]；unknown: 未注册 :::标记名列表
    （内容降级为正文段、标记行丢弃，渲染不中断——验收 A3）。"""
    title = "无题"
    blocks = []
    unknown = []
    cur, buf, para = None, [], []
    open_re = _marker_open_re()

    def flush_para():
        nonlocal para
        text = " ".join(x.strip() for x in para if x.strip())
        if text:
            blocks.append(("p", text))
        para = []

    def close_cur():
        nonlocal cur, buf
        if cur is not None:
            blocks.append((cur, buf))
        cur, buf = None, []

    for raw in (md_text or "").split("\n"):
        s = raw.strip()
        m_img = re.match(r"^:::image\s*(.*)$", s)
        m_open = open_re.match(s)
        # 容错：任何新块标记（image 行或 :::x 行）出现时，先闭合未闭合的当前块
        if m_img:
            close_cur()
            flush_para()
            blocks.append(("image", m_img.group(1).strip().rstrip(":").strip()))
            continue
        if m_open:
            close_cur()
            flush_para()
            cur, buf = m_open.group(1), []
            continue
        if re.match(r"^:::\s*$", s):
            close_cur()
            flush_para()  # 未知标记降级段的收尾围栏
            continue
        if s.startswith(":::"):
            # 未注册标记（含拼写错误 / 未来组件）：标记行丢弃，内容降级正文段
            close_cur()
            flush_para()
            unknown.append(s[3:].strip() or s)
            continue
        if cur is not None:
            if s:
                buf.append(s)
            continue
        if s.startswith("# "):
            flush_para()
            title = s[2:].strip()
            continue
        if not s:
            flush_para()
            continue
        if s.startswith("##"):
            s = s.lstrip("#").strip()  # 残留小标题降级为正文，保证规范"无小标题"
        para.append(s)
    flush_para()
    if cur is not None:  # 未闭合围栏，容错：按已收集内容成块
        blocks.append((cur, buf))
    return title, blocks, unknown


_UNKNOWN_LOGGED = set()


def _log_unknown_markers(unknown):
    """未注册标记留痕（每进程每标记名记一次，避免预览反复渲染刷日志）。"""
    fresh = sorted({u for u in unknown if u not in _UNKNOWN_LOGGED})
    if not fresh:
        return
    _UNKNOWN_LOGGED.update(fresh)
    try:
        from core import logging as clog
        clog.wb_log("typo_unknown_marker", "-",
                    "未注册排版标记已降级为正文段: " + ", ".join(fresh))
    except Exception:
        pass


def render_from_markers(md_text, series="B", cover_url=None, theme=None):
    """规则渲染：组件标记 → 主题化内联 HTML（确定性，不调 LLM）。

    theme 缺省 = 内置默认主题「天光云影」（纯渲染基准，不受排版中心用户
    状态影响——金样本逐字节一致性由它保证）；export_article 与排版中心
    预览传 effective_theme()。cover_url 有值时头图紧跟篇首 ✦ ✦ ✦ 之后
    （v4.2 每篇唯一配图）；:::image 文中图标记已取消——解析容错保留，
    渲染时静默丢弃；其余未注册标记内容降级正文段并留痕（A3）。"""
    th = theme or default_theme()
    runtime = ThemeRuntime(th, REGISTRY)
    series_label = SERIES_LABELS.get(series, "晚安陌生人")
    title, blocks, unknown = _parse_marked(md_text)
    _log_unknown_markers(unknown)
    parts = []
    for name in _HEAD_ORDER:
        spec = REGISTRY.require(name)
        payload = title if name == "title" else None
        parts.append(spec.render(payload, runtime.context(name, series_label)))
    if cover_url:
        spec = REGISTRY.require("cover")
        parts.append(spec.render(cover_url, runtime.context("cover", series_label)))
    for btype, payload in blocks:
        spec = REGISTRY.get(btype)
        if spec is None or spec.render is None:
            continue  # 未注册块（已废弃 :::image）与 meta 块（:::titles 写稿期）静默跳过
        ctx = runtime.context(btype, series_label)
        if not ctx.config.get("enabled", True):
            continue  # 组件被主题/配置停用（如默认停用的 imgtext）
        parts.append(spec.render(payload, ctx))
    for name in _TAIL_ORDER:
        spec = REGISTRY.require(name)
        parts.append(spec.render(None, runtime.context(name, series_label)))
    return "\n".join(parts)


def _insert_cover_llm(html: str, cover_url: str, series: str = "B") -> str:
    """LLM 回退渲染没有 cover 参数（提示词结构固定）——渲染后在篇首分隔符
    （✦ ✦ ✦）元素之后插入与规则渲染同款的头图 <img>（data-cover="1"，
    推送换装依赖该标记）。找不到分隔符时插在最前，不阻塞导出。"""
    from core.typography import REGISTRY, ThemeRuntime, default_theme
    spec = REGISTRY.require("cover")
    cover_html = spec.render(cover_url, ThemeRuntime(default_theme(), REGISTRY).context("cover", series))
    m = re.search(r"<(p|section|div)[^>]*>[^<]*✦[^<]*</\1>", html)
    if not m:
        return cover_html + html
    return html[:m.end()] + cover_html + html[m.end():]


def render_mp_html(md_text, series="B", cover_url=None, theme=None):
    """渲染入口：有组件标记 → 规则渲染（theme 生效）；无标记（存量稿） → LLM 回退
    （按默认规范，theme 不参与）。返回 (html, engine)。两条路径都会插头图。"""
    if has_markers(md_text):
        return render_from_markers(md_text, series=series, cover_url=cover_url,
                                   theme=theme), "rule"
    html = render_mp_html_llm(md_text)
    if cover_url:
        html = _insert_cover_llm(html, cover_url, series)
    return html, "llm"


def _hex_colors(html):
    return {m.upper() if m.startswith("#") else "#" + m.upper()
            for m in re.findall(r"#[0-9A-Fa-f]{6}", html)}


def validate_html(html, theme=None, word_count=None):
    """确定性校验：返回 [{'name', 'ok', 'detail'}] 清单（默认主题 19 项 + 字数项）。

    白名单与品牌文案期望值全部由 theme 派生：rule 渲染的 HTML 用生效主题
    校验，LLM 回退（按默认规范渲染）用默认主题校验。

    商业化 P0：主题白名单是**自指**的（allowed_colors 来自主题自身 tokens），
    故另加两项不随主题变化的绝对底线（色值格式 / 正文对比度）；
    word_count 非空时再补一项字数区间校验（与写稿门禁同口径）。
    """
    th = theme or default_theme()
    rt = ThemeRuntime(th, REGISTRY)
    kicker_prefix = rt.config("kicker")["prefix"]
    div_symbol = rt.config("divider_head")["symbol"]
    action_label = rt.config("action")["label_text"]
    trio_lines = rt.config("trio")["lines"]
    stamp_text = rt.config("stamp")["text"]
    follow_text = rt.config("follow")["text"]
    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("无 <style> 标签", "<style" not in html.lower())
    add("无 class 属性", not re.search(r"\bclass\s*=", html, re.I))
    add("无 <div> 容器", not re.search(r"<div\b", html, re.I),
        "规范要求容器一律用 <section>")

    bad_colors = _hex_colors(html) - th.allowed_colors()
    add("色值全部在规范色板内", not bad_colors,
        f"违规色值: {', '.join(sorted(bad_colors))}" if bad_colors else "")

    # 商业化 P0（BUG-2）：上面那项是**自指**的——白名单来自主题自身 tokens，
    # 用户改了 token 白名单就跟着变，等于没校验。这里补绝对底线（不随主题变化）。
    bad_fmt = scan_bad_colors(html)
    add("色值格式全部合法", not bad_fmt,
        f"非法色值: {', '.join(bad_fmt)}" if bad_fmt else "")
    body_c = (th.tokens.get("colors") or {}).get("body")
    page_c = ((th.tokens.get("colors") or {}).get("page")
              or (th.tokens.get("colors") or {}).get("surface"))
    if is_hex_color(body_c) and is_hex_color(page_c):
        ratio = contrast_ratio(body_c, page_c)
        add(f"正文对比度 ≥ {MIN_BODY_CONTRAST}:1", ratio >= MIN_BODY_CONTRAST,
            f"body {body_c} on {page_c} = {ratio:.2f}:1")
    else:
        add(f"正文对比度 ≥ {MIN_BODY_CONTRAST}:1", False,
            f"缺 colors.body/page 或色值非法（body={body_c!r}, page={page_c!r}）")

    sizes = set(re.findall(r"font-size\s*:\s*([0-9.]+px)", html))
    bad_sizes = sizes - th.allowed_font_sizes()
    add("字号全部在规范 13 档内", not bad_sizes,
        f"违规字号: {', '.join(sorted(bad_sizes))}" if bad_sizes else "")

    # 商业化 P0（BUG-1）：写稿页承诺 900-1200 字，但此前导出自检不含字数项，
    # 实测 1378 字仍 17/17 全绿。传入 word_count 时才校验（存量调用不受影响）。
    if word_count is not None:
        add(f"字数在 {WORD_MIN}-{WORD_HARD_MAX} 区间",
            WORD_MIN <= word_count <= WORD_HARD_MAX,
            f"实际 {word_count} 字")

    add("篇首 kicker 在位", f"{kicker_prefix} ·" in html)
    add(f"分隔符 {div_symbol} 至少 2 处", html.count(div_symbol) >= 2,
        f"实际 {html.count(div_symbol)} 处")

    action_count = html.count(action_label)
    pos_action = html.find(action_label)
    pos_closing = html.find(trio_lines[0]) if trio_lines else -1
    add("行动卡恰有一张", action_count == 1, f"实际 {action_count} 张")
    add("行动卡在收尾文案之前",
        action_count == 1 and pos_closing != -1 and pos_action < pos_closing)

    for line in trio_lines:
        add(f"收尾文案逐字：「{line}」", line in html)
    compact = re.sub(r"\s+", "", html).upper()
    stamp_sig = (f"BORDER:{th.token('borders.stamp').upper()}"
                 f"SOLID{th.token('colors.frame').upper()}")
    add("邮票框在位", stamp_text in html and stamp_sig in compact)
    add("关注引导在位", ("关注我" in html) or (follow_text in html))

    # 头图（v4.2 每篇唯一配图）：有则必须在篇首分隔符后；无则视为未生成（可后补，不算失败）
    m_cover = re.search(r'<img[^>]+data-cover="1"', html)
    pos_div = html.find(div_symbol)
    if m_cover:
        add("头图在篇首分隔符后", pos_div != -1 and m_cover.start() > pos_div,
            "" if (pos_div != -1 and m_cover.start() > pos_div) else "头图应紧跟篇首 ✦ ✦ ✦")
    else:
        add("头图未插入（未生成或降级）", True, "生成头图后重新导出即可插入")

    quote_re = r"font-size\s*:\s*" + re.escape(th.token("font_sizes.quote"))
    quote_count = len(re.findall(quote_re, html))
    add("情绪收束句 2~7 处", 2 <= quote_count <= 7, f"实际 {quote_count} 处")

    add("无编号小标题残留", not re.search(r"<h[1-6]\b", html, re.I),
        "规范要求正文无小标题，标题层级由组件承担")

    return checks


def checks_summary(checks):
    passed = sum(1 for c in checks if c["ok"])
    return {"total": len(checks), "passed": passed, "all_ok": passed == len(checks)}


def export_article(date, version="v1", slot=None, with_cover=True):
    """完整导出流程：读稿 → 渲染 → 校验 → 红线 → 落盘。返回报告 dict。
    slot（w01/w02…）指定文稿槽位 → 读写 {date}_w{slot}_mp* 文件，多文稿独立共存。
    阶段一 S2：规则渲染使用排版中心激活主题（含用户微调）；LLM 回退按默认规范，
    校验基准与各自渲染基准一致。"""
    paths = cat_shop.slot_paths(date, slot)
    md_path = paths["v1"] if version == "v1" else paths["v2"]
    if not md_path.exists():
        raise FileNotFoundError(f"未找到文稿: {md_path}")
    md_text = md_path.read_text(encoding="utf-8")

    # P0-4：若已选定标题，导出强制使用选定标题并同步回文稿首行
    #（防止"重写后 LLM 新标题覆盖已选标题"导致导出标题漂移）
    # 修复 2026-09-17：titles 读取改为槽位化 paths["titles"]——原全局文件
    # {date}_titles.json 在多槽位时会把 W01 的选定标题串染到 W02 的预览与文稿首行；
    # slot=None（agent 流）的槽位文件名即旧全局名，行为不变。
    titles_data = cat_shop.load_json(paths["titles"], None)
    selected_title = (titles_data or {}).get("selected")
    if not selected_title and slot:
        # 兜底：works 槽位 meta 的 selected_title（历史数据无槽位 titles 文件时，
        # 顺带把曾被全局标题污染的文稿首行纠偏回自身标题）
        s = next((x for x in (cat_shop.load_works(date).get("slots") or [])
                  if x.get("id") == slot), None)
        selected_title = (s or {}).get("selected_title")
    if selected_title:
        lines = md_text.split("\n")
        if lines and lines[0].startswith("# ") and lines[0][2:].strip() != selected_title:
            lines[0] = f"# {selected_title}"
            md_text = "\n".join(lines)
            md_path.write_text(md_text, encoding="utf-8")

    # kicker 主题词取自当日决策的母题系列
    sel = cat_shop.load_json(cat_shop.SELECTIONS_DIR / f"{date}.json", {}) or {}
    series = sel.get("motif_series") or "B"

    # P3 文中配图：按需 plan + 插入 :::imgtext 标记（plan 复用不重复烧钱；失败降级跳过）
    # with_cover=False（无配图渲染）时整篇纯文字，文中图与头图都不加
    inline_meta = None
    if with_cover:
        try:
            from core.inline_images import ensure_inline_images
            md_text, inline_meta = ensure_inline_images(md_text, date, slot)
        except Exception as e:  # noqa: BLE001 —— 配图失败绝不阻塞导出
            print(f"  [WARN] 文中配图降级跳过: {e}", file=sys.stderr)

    # 头图（v4.2 / P5 深关联）：没有现成头图时按文稿内容现场生成一张再插入篇首
    # （场景提炼按槽位/当日选题+正文开头；未配置 IMAGE_API_KEY 或生成失败均降级
    # 跳过不阻断导出）。推送时 swap_cover_url 会把 file:// 换成微信 URL 并做封面。
    cover_path = cat_shop.cover_image_path(date, slot)
    cover_note = ""
    if not with_cover:
        cover_note = "按选择未加配图（无配图渲染）"
        print(f"  [INFO] {cover_note}", file=sys.stderr)
    elif not cover_path.exists():
        try:
            from llm.image_provider import image_configured
            if image_configured():
                from pipeline.images import generate_cover
                generate_cover(date, slot=slot or None)
                cover_note = "已按文稿内容自动生成头图"
            else:
                cover_note = "未配置 IMAGE_API_KEY，本次导出无头图"
        except Exception as e:  # noqa: BLE001 —— 生图失败绝不阻塞导出
            cover_note = f"头图自动生成失败（不阻断导出）: {type(e).__name__}: {e}"
        if cover_note:
            print(f"  [INFO] {cover_note}", file=sys.stderr)
    cover_url = cover_path.resolve().as_uri() if cover_path.exists() else None

    active_theme = get_manager().effective_theme()
    if inline_meta:
        # 运行时启用 imgtext 组件（天光云影主题默认停用；仅本次渲染生效，不改用户主题）
        from core.typography import apply_overrides
        active_theme = apply_overrides(
            active_theme, {"components": {"imgtext": {"enabled": True}}})
    html, engine = render_mp_html(md_text, series=series, cover_url=cover_url,
                                  theme=active_theme)
    vtheme = active_theme if engine == "rule" else default_theme()
    # 字数按正文汉字计（与写稿门禁 count_chinese_chars 同口径）
    checks = validate_html(html, vtheme, word_count=count_chinese_chars(md_text))
    summary = checks_summary(checks)

    # 导出稿再过一遍红线（短语）
    plain = re.sub(r"<[^>]+>", "", html)
    rl = cat_shop.redline_check(plain, step="mp_export", date=date,
                                file=f"data/drafts/{date}_mp.html", auto_log=False)

    # P1-13：导出为最终关卡，默认补一次 LLM 语义红线（捕捉短语漏网的同义改写；失败降级不阻断）
    sem_result = None
    try:
        sem_result = cat_shop.redline_llm_semantic(plain, step="mp_export_semantic", date=date,
                                                   file=f"data/drafts/{date}_mp.html")
        if sem_result["hits"]:
            print(f"  [WARN] 导出语义红线命中 {len(sem_result['hits'])} 条"
                  f"（{'阻断' if sem_result['should_block'] else '非阻断'}）", file=sys.stderr)
    except Exception as e:
        print(f"  [WARN] 导出语义红线调用失败（降级跳过）: {e}", file=sys.stderr)

    # 金句自动沉淀：导出时把 ::quote 组件句子入库（source=文章标题）
    try:
        for q in re.findall(r":::quote\s*\n([^\n]+)\n:::", md_text):
            cat_shop.add_quote(q.strip(), source=f"article:{date}", date=date)
    except Exception:
        pass

    # P1-11 演进（v4.2）：文中图已取消，头图信息进报告（审批卡缩略图 / 推送封面取图依据）
    # path 相对 DATA_DIR（/file 路由直接可用）
    cover_info = {"present": cover_path.exists(), "path": f"images/{cover_path.name}",
                  "note": cover_note}

    out_path = paths["mp"]
    out_path.write_text(html, encoding="utf-8")

    # 存历史版本（按时间戳命名），供版本对比
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    hist_dir = paths["mp_history"]
    hist_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "date": date, "version": version, "slot": slot,
        "engine": engine,  # rule（组件标记确定性渲染）/ llm（存量稿回退）
        "theme": {"id": vtheme.id, "name": vtheme.name},  # 阶段一：本次渲染所用主题
        "theme_fallback": bool(engine == "llm" and active_theme.id != vtheme.id),  # R3：激活主题未参与（存量稿 LLM 回退按默认规范）
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "export_ts": ts,
        "output": str(out_path.relative_to(SCRIPT_DIR)),
        "checks": checks, "summary": summary,
        "redline": {"risk_level": rl["risk_level"], "should_block": rl["should_block"],
                    "hit_counts": rl["hit_counts"]},
        "redline_semantic": (None if sem_result is None else {
            "risk_level": sem_result["risk_level"],
            "should_block": sem_result["should_block"],
            "hits": [{k: h.get(k) for k in ("rule_id", "level", "matched_text")}
                     for h in sem_result["hits"]]}),
        "image_notes": [],   # v4.2 起文中图已取消，保留字段兼容旧报告读取
        "inline_images": inline_meta,   # P3：文中配图（{"count": n}；None=未启用/降级）
        "cover": cover_info,
        "word_count": cat_shop.count_chinese_chars(plain),
    }
    report_path = paths["mp_report"]
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 同步存历史副本（HTML + 报告），不覆盖既有
    (hist_dir / f"{ts}.html").write_text(html, encoding="utf-8")
    _prune_history(hist_dir, keep=10)
    (hist_dir / f"{ts}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    return report
