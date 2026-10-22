#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline/flow.py · 流水线编排（all / full）与写稿后的飞书同步衔接。

Phase 1 解耦说明：v3 的 step_write 末尾内嵌 step_sync 调用，导致写稿结果与外部
系统（飞书）耦合。v4 起写稿本身不碰外部系统，同步统一在本模块编排：
  - CLI `write` 子命令 → run_write（写稿成功 → 同步，同步失败仅 WARN，与 v3 一致）
  - CLI `full` 子命令  → step_full（步骤序列原样保留，含显式 step_sync）
"""
import sys

from core.paths import SELECTIONS_DIR
from core.storage import load_json
from pipeline.topics import step_collect, step_score
from pipeline.writing import step_write, step_format, step_learn
from pipeline.publish import step_sync


def run_write(args):
    """写稿 + 写稿后自动同步飞书云文档（承接 v3 step_write 末尾的内嵌同步语义）。

    同步幂等（已同步过自动跳过）且失败不阻断写稿结果。"""
    ok = step_write(args)
    if ok:
        try:
            step_sync(args)
        except Exception as e:
            print(f"  [WARN] 写稿后自动同步飞书失败（可稍后在写稿页手动点「同步」重试）: {e}",
                  file=sys.stderr)
    return ok


# ===== All/Full（v2：任一步失败即终止，避免带病往下跑） =====
def step_all(args):
    for s in [step_collect, step_score]:
        r = s(args)
        if r is False:
            print(f"⏹️  {s.__name__} 失败，流水线终止")
            return False
    return True


def step_full(args):
    no_sync = getattr(args, "no_sync", False)
    steps = [step_collect, step_score, step_write]
    if not no_sync:
        steps.append(step_sync)
    steps.extend([step_format, step_learn])
    for s in steps:
        result = s(args)
        if result is False and s is step_write:
            # 区分失败原因：未决策 → 暂停等待；已决策但失败 → 硬拦截（字数/红线）
            sel = load_json(SELECTIONS_DIR / f"{args.date}.json", default={}) or {}
            if not sel.get("user_choice"):
                print()
                print("⏸️  Step 4 需要用户决策")
                print(f"   python cat_shop.py choose --date {args.date} --choice <1-5> [--note \"...\"]")
                print("   可选（给候选打分 + LLM 反馈，喂养学习闭环）：")
                print(f"     python cat_shop.py feedback --date {args.date}")
                print(f"     python cat_shop.py analyze --date {args.date} --apply-rubric")
                print("   决策后继续：")
                print(f"     python cat_shop.py write --date {args.date}")
                if not no_sync:
                    print(f"     python cat_shop.py sync --date {args.date}")
                print(f"     python cat_shop.py format --date {args.date}")
                print(f"     python cat_shop.py learn --date {args.date}")
            else:
                print(f"⏹️  {s.__name__} 被硬拦截（字数不足或触红线），流水线终止，检查上方 [FATAL] 日志")
            return
        if result is False and s is step_sync:
            print("  [WARN] 飞书云文档同步失败，继续后续步骤")
            continue
        if result is False:
            print(f"⏹️  {s.__name__} 失败，流水线终止")
            return
