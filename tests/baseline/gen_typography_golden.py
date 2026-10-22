#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_typography_golden.py · 排版引擎金样本基线生成器（阶段一验收 A1）。

用途：在改动 workbench/exporter.py 渲染逻辑【之前】运行本脚本，把规则渲染器
对一组代表性组件标记文稿的输出逐字固化为 tests/baseline/typography_golden.json；
重构后 test_typography.py 逐字节比对，保证默认主题下存量稿零迁移。

再生成策略：仅当【有意】变更默认主题（天光云影）视觉时才重新生成，
并在 PR 说明中列出每一处差异；日常重构禁止重生成。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core import exporter  # noqa: E402

OUT = Path(__file__).resolve().parent / "typography_golden.json"

# 覆盖：标题/正文/加粗/金句/信件（落款+信内金句+末行边距）/行动卡/标题候选块/
# 已废弃 :::image 丢弃/未闭合围栏容错/## 残留小标题降级/六系列 kicker/头图有无
CASES = {
    "full_features": dict(
        series="B",
        cover_url=None,
        md=(
            "# 城市夜灯下的第 7 封信\n\n"
            "深夜的出租屋里台灯还亮着，她把今天没说出口的**情绪**叠好收进抽屉。\n\n"
            ":::quote\n金句一：慢一点，也是往前走。\n:::\n\n"
            "楼下便利店的灯还亮着，玻璃上蒙着雾气。\n\n"
            ":::letter\n亲爱的创作工作台：\n"
            "**我不知道该怎么开口，但今晚特别想写点什么。**\n"
            "白天开了一整天的会，说的话比一年还多。\n"
            "> 落款：一个不敢请假的中年人\n:::\n\n"
            "## 这行是残留小标题\n\n"
            "残留小标题会降级成正文，保证规范里的一条线阅读流。\n\n"
            ":::action\n给今晚的自己写一句话，明早再读一遍。\n:::\n\n"
            ":::titles\n标题一：深夜的第 7 盏灯\n标题二：不敢请假的夜晚\n:::\n\n"
            ":::image 夜色窗台与一杯温水\n\n"
            "收尾前最后一段，情绪落定。"
        ),
    ),
    "cover_present": dict(
        series="A",
        cover_url="file:///tmp/cover.png",
        md=(
            "# 有头图的短稿\n\n"
            "一段正文就够了。\n\n"
            ":::quote\n有头图也要过校验。\n:::\n\n"
            ":::action\n今晚早点睡。\n:::"
        ),
    ),
    "unclosed_fence": dict(
        series="D",
        cover_url=None,
        md=(
            "# 未闭合围栏容错\n\n"
            "正文一段。\n\n"
            ":::quote\n围栏没闭合也会按已收集内容成块"
        ),
    ),
    "all_series": dict(
        series="F",
        cover_url=None,
        md=(
            "# 系列词测试\n\n"
            "亲密关系系列的 kicker 应显示系列名。\n\n"
            ":::action\n抱一下身边的人。\n:::"
        ),
    ),
    "letter_only_last_line": dict(
        series="C",
        cover_url=None,
        md=(
            "# 信件末行边距\n\n"
            "正文。\n\n"
            ":::letter\n**只有一行信内金句。**\n:::\n\n"
            ":::letter\n普通信内正文。\n> 落款：测试者\n:::"
        ),
    ),
    "empty_and_dense": dict(
        series="E",
        cover_url=None,
        md="无题稿直接正文，没有 # 标题行。\n\n\n\n连续空行与\n\n多段落。",
    ),
    "real_draft_2026_08_11": None,  # 真实存量稿（quote/action 标记）
}


def main():
    cases = {}
    for name, cfg in CASES.items():
        if cfg is None:
            p = Path(__file__).resolve().parent.parent.parent / "data/drafts/2026-08-11_v1.md"
            cfg = dict(series="B", cover_url=None, md=p.read_text(encoding="utf-8"))
        html = exporter.render_from_markers(cfg["md"], series=cfg["series"],
                                            cover_url=cfg["cover_url"])
        checks = exporter.validate_html(html)
        cases[name] = {
            "series": cfg["series"], "cover_url": cfg["cover_url"],
            "md": cfg["md"], "html": html,
            "checks_all_ok": all(c["ok"] for c in checks),
        }
        print(f"  [{name}] html={len(html)}B checks_all_ok={cases[name]['checks_all_ok']}")
    OUT.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n金样本已写入: {OUT}（{len(cases)} 例）")


if __name__ == "__main__":
    main()
