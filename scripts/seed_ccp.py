#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/seed_ccp.py · CCP 初始化投喂（历史文稿批量风格提取，零 LLM）

用法（在项目根目录跑）：
  py scripts/seed_ccp.py --ml <文稿目录> [--client slowpost] [--dry-run]
  py scripts/seed_ccp.py --questionnaire q.json --ml <目录>

说明：
  - 确定性风格提取：句长分布/标点节奏/候选金句（GetDraft 模式，3-5 篇代表文本）
  - 幂等：按内容哈希去重，重复投喂不叠加；超出 5 篇截断
  - dry-run 只计算不落盘
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ccp import seed_from_sources, verify_seed  # noqa: E402
from core.ccp.profile import get_profile  # noqa: E402


def main():
    p = argparse.ArgumentParser(description="CCP 初始化投喂（历史文稿风格提取）")
    p.add_argument("--ml", default="", help="历史文稿目录（取前 5 篇 .md）")
    p.add_argument("--questionnaire", default="", help="问卷 JSON 文件路径")
    p.add_argument("--client", default="slowpost")
    p.add_argument("--dry-run", action="store_true", help="只算不落盘")
    args = p.parse_args()

    q = {}
    if args.questionnaire:
        q = json.loads(Path(args.questionnaire).read_text(encoding="utf-8"))
    if not args.ml and not q:
        print("未提供 --ml 或 --questionnaire，无事可做")
        return

    prof = get_profile(client_id=args.client)
    if args.dry_run:
        print("[seed] dry-run：不落盘。将投喂：")
        if args.ml:
            files = sorted(Path(args.ml).rglob("*.md"))[:5]
            print(f" 文稿 {len(files)} 篇")
        if q:
            print(f" 问卷 {len(q)} 键")
        return

    r = seed_from_sources(prof, ml_path=args.ml or None, questionnaire=q or None)
    print("[seed] 投喂完成：")
    print(f"  问卷：{r['questionnaire'] if r['questionnaire'] else '（未提供）'}")
    print(f"  文稿：added={r['articles']['added']} skipped={r['articles']['skipped']} "
          f"rejected={r['articles']['rejected']}")
    print(f"  校验：{verify_seed(prof)}")


if __name__ == "__main__":
    main()