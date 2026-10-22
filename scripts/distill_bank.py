#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""蒸馏题材灵感库（LLM 访谈头部情感号题材坐标）→ data/topic_bank.json。

用法：python scripts/distill_bank.py [条数，默认48]
建议：每月跑一次，或觉得选题疲劳时手动跑（去重合并，不覆盖旧样例）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cat_shop  # noqa: F401  (加载 .env)
from core.topic_bank import distill_bank, load_bank

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 48
    r = distill_bank(n)
    bank = load_bank()
    import collections
    axes = collections.Counter(e["axis"] for e in bank["entries"])
    print(f"蒸馏完成：新增 {r['added']} 条，共 {r['total']} 条，覆盖 {len(axes)} 个题材坐标")
    print("axis:", dict(axes))
