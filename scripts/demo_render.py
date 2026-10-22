#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""零密钥演示：把 demo/晚风信箱/样稿.md 用规则引擎渲染成公众号排版 HTML。

下载者 3 分钟体验：python scripts/demo_render.py --open
  - 不需要任何 API key（纯本地规则渲染）
  - 输出 demo/晚风信箱/输出-样稿.html（可直接复制进公众号编辑器）
  - --open 渲染完自动用默认浏览器打开
"""
import json
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.exporter import render_mp_html, validate_html  # noqa: E402
from core.quality import count_chinese_chars  # noqa: E402
from core.typography import REGISTRY, Theme, ThemeRuntime, default_theme  # noqa: E402

DEMO = Path(__file__).resolve().parent.parent / "demo" / "晚风信箱"


def main():
    md = (DEMO / "样稿.md").read_text(encoding="utf-8")
    theme = Theme.from_dict(json.loads((DEMO / "theme.json").read_text(encoding="utf-8")))
    html, engine = render_mp_html(md, series="B", cover_url=None, theme=theme)
    checks = validate_html(html, theme, word_count=count_chinese_chars(md))
    passed = sum(1 for c in checks if c["ok"])
    wrapped = ('<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
               '<title>晚风信箱 · 排版演示</title></head>'
               '<body style="margin:0;padding:12px;background:#ffffff;">'
               + html + "</body></html>")
    out = DEMO / "输出-样稿.html"
    out.write_text(wrapped, encoding="utf-8")
    print(f"渲染引擎: {engine} | 正文字数: {count_chinese_chars(md)}")
    print(f"校验: {passed}/{len(checks)} 通过")
    for c in checks:
        if not c["ok"]:
            print("  ✗", c["name"], "|", c.get("detail", ""))
    print(f"输出: {out}")
    if "--open" in sys.argv:
        webbrowser.open(out.as_uri())
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
