#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
创作工作台 · 内容自动化工作流（单文件版）
=====================================

完整流水线 + 红线检查 + 飞书云文档归档 + 用户评论 + LLM 反馈分析。
所有 prompt、红线规则、HTML 模板都内嵌在单文件里，无需外部 .md 文件。

用法（建议在项目根目录跑）：
  python cat_shop.py setup                    # 交互式设 key + 云文档目录
  python cat_shop.py collect --date 2026-08-18 [--force]  # Step 1
  python cat_shop.py score --date 2026-08-18 [--runs 3 --force]  # Step 2
  python cat_shop.py choose --date 2026-08-18 --choice 1 [--note "..."]  # Step 3.7 · 用户决策
  python cat_shop.py feedback --date 2026-08-18 # 收集评论
  python cat_shop.py analyze --date 2026-08-18 [--apply-rubric]  # LLM 分析 + 反馈
  python cat_shop.py write --date 2026-08-18 [--v2-angle 反例切入] [--semantic-redline]  # Step 4
  python cat_shop.py sync --date 2026-08-18 [--force-sync]  # Step 4.5 · 归档飞书云文档
  python cat_shop.py format --date 2026-08-18 [--semantic-redline] # Step 5
  python cat_shop.py learn --date 2026-08-18    # Step 7（--mode weekly 学画像）
  python cat_shop.py all --date 2026-08-18      # 跑 1-2
  python cat_shop.py full --date 2026-08-18     # 跑全流程
  python cat_shop.py redline --text "..."       # 跑红线（短语）
  python cat_shop.py redline-semantic --file path  # 跑红线（LLM 语义）
  python cat_shop.py agent --date 2026-08-29 [--goal "..."]  # 项目级 Agent 启动/续跑
  python cat_shop.py agent --date X --choice 2      # 选题确认恢复
  python cat_shop.py agent --date X --approve-publish  # 发布确认恢复（批准）
  python cat_shop.py agent --date X --reject-publish --note "意见"  # 发布确认（拒绝）
  python cat_shop.py agent --date X --status        # 查询运行状态
  python cat_shop.py agent --date X --trace         # 查看执行时间线
  python cat_shop.py agent --date X --raise-budget [--extra-steps N --extra-cost M]  # 提额续跑
  python cat_shop.py agent --date X --cancel        # 取消运行

依赖：pip install requests jinja2 markdown python-dotenv
环境变量（用 `setup` 子命令设置，自动存到 .env）：
  LLM_API_KEY / LLM_API_BASE / LLM_MODEL
  FEISHU_DOC_FOLDER_TOKEN（云文档归档用，另需安装并授权 lark-cli）

数据闭环（v3）：
  feedback → analyze(rubric_revisions) → score(权重校准)
  learn --mode weekly → preference_profile.json → score(权重)
  history.jsonl → collect(选题去重) / write(偏好注入)
"""

import argparse
import sys

from core.memory import collect_feedback, dedup_feedback
from pipeline.topics import (step_collect, step_score, cmd_choose,
                             step_analyze, step_new_batch, step_regenerate_batch)
from pipeline.writing import (step_outline, step_format, step_learn,
                              step_titles)
from pipeline.publish import (cmd_setup, cmd_redline,
                              cmd_redline_semantic, cmd_status, step_sync)
from pipeline.flow import step_all, step_full, run_write

def main():
    p = argparse.ArgumentParser(description="创作工作台 · 内容自动化工作流（单文件版）",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("setup", help="交互式设 key + 云文档目录（输入时不显示）")

    p_collect = sub.add_parser("collect", help="Step 1 · 选题生成")
    p_collect.add_argument("--date", required=True)
    p_collect.add_argument("--timeframe", default="", help="时段描述，如 '2026 夏季 / 8 月'（默认自动生成）")
    p_collect.add_argument("--trending", nargs="*", default=[])
    p_collect.add_argument("--force", action="store_true", help="覆盖已有候选")
    p_collect.add_argument("--round", type=int, default=None, help="批次号（默认当前批次）")

    p_score = sub.add_parser("score", help="Step 2 · 多维度评分")
    p_score.add_argument("--date", required=True)
    p_score.add_argument("--top-k", type=int, default=5)
    p_score.add_argument("--runs", type=int, default=1, help="每候选评分轮数（≥3 才有稳定的 std/置信度）")
    p_score.add_argument("--batch-size", type=int, default=5, help="每次 LLM 调用批量评分的候选数")
    p_score.add_argument("--workers", type=int, default=5)
    p_score.add_argument("--force", action="store_true", help="覆盖已有 top5（用户已决策时慎用）")
    p_score.add_argument("--round", type=int, default=None, help="批次号（默认当前批次）")

    p_ch = sub.add_parser("choose", help="Step 3.7 · 用户决策（写入 selections/{date}.json）")
    p_ch.add_argument("--date", required=True)
    p_ch.add_argument("--choice", type=int, required=True, help="选择的候选序号（1-5）")
    p_ch.add_argument("--note", default="", help="可选备注")
    p_ch.add_argument("--round", type=int, default=None, help="批次号（默认当前批次）")

    p_fb = sub.add_parser("feedback", help="Step 3.5 · 收集用户评论")
    p_fb.add_argument("--date", required=True)
    p_fb.add_argument("--feedback", help="预填评论（'1:5 喜欢;2:3 太老套;...'）")
    p_fb.add_argument("--from-file", help="从 JSON 文件读评论")

    p_fbd = sub.add_parser("feedback-dedup", help="P0-6 · 清理 user_feedback.jsonl 重复评论（同 date+rank 保留最新）")

    p_an = sub.add_parser("analyze", help="Step 3.6 · LLM 分析评论 + 给反馈")
    p_an.add_argument("--date", required=True)
    p_an.add_argument("--apply-rubric", action="store_true", help="应用 rubric 校准（下次 score 生效）")

    p_w = sub.add_parser("write", help="Step 4 · 写稿")
    p_w.add_argument("--date", required=True)
    p_w.add_argument("--force-v2", action="store_true")
    p_w.add_argument("--v2-angle", choices=["时间切片", "反例切入", "物件线索", "回信体", "他人视角"],
                     default=None, help="v2 的差异化切入角度")
    p_w.add_argument("--semantic-redline", action="store_true", help="额外跑 LLM 语义红线")
    p_w.add_argument("--slot", default=None, help="文稿槽位 w01（默认当日活动槽位）")
    p_w.add_argument("--use-outline", action="store_true", help="按已生成的大纲写稿（可选增强）")

    p_ol = sub.add_parser("outline", help="大纲搭建（写稿前确认结构，可选）")
    p_ol.add_argument("--date", required=True)
    p_ol.add_argument("--slot", default=None, help="文稿槽位 w01")

    p_fmt = sub.add_parser("format", help="Step 5 · HTML 排版")
    p_fmt.add_argument("--date", required=True)
    p_fmt.add_argument("--version", default="v1", choices=["v1", "v2"])
    p_fmt.add_argument("--semantic-redline", action="store_true", help="额外跑 LLM 语义红线")

    p_status = sub.add_parser("status", help="查看工作流状态")
    p_status.add_argument("--date", required=True)

    p_sync = sub.add_parser("sync", help="Step 4.5 · 同步文稿到飞书云文档")
    p_sync.add_argument("--date", required=True)
    p_sync.add_argument("--slot", default=None, help="文稿槽位 w01（默认当日活动槽位）")
    p_sync.add_argument("--force-sync", action="store_true", help="覆盖已同步的文档")

    p_l = sub.add_parser("learn", help="Step 7 · 反馈学习")
    p_l.add_argument("--date", required=True)
    p_l.add_argument("--mode", choices=["daily", "weekly"], default="daily")

    p_nb = sub.add_parser("new-batch", help="生成新一批选题（不限次数，多批共存）")
    p_nb.add_argument("--date", required=True)
    p_nb.add_argument("--trending", nargs="*", default=[])
    p_nb.add_argument("--runs", type=int, default=1)
    p_nb.add_argument("--workers", type=int, default=5)
    p_nb.add_argument("--batch-size", type=int, default=5)
    p_nb.add_argument("--top-k", type=int, default=5)

    p_rb = sub.add_parser("regenerate-batch", help="强制重新生成某批选题（--round 必填）")
    p_rb.add_argument("--date", required=True)
    p_rb.add_argument("--round", type=int, required=True)
    p_rb.add_argument("--trending", nargs="*", default=[])
    p_rb.add_argument("--runs", type=int, default=1)
    p_rb.add_argument("--workers", type=int, default=5)
    p_rb.add_argument("--batch-size", type=int, default=5)
    p_rb.add_argument("--top-k", type=int, default=5)

    p_titles = sub.add_parser("titles", help="P0-4 · 生成标题多方案（写稿后自动生成，可手动重跑）")
    p_titles.add_argument("--date", required=True)
    p_titles.add_argument("--force", action="store_true", help="覆盖已有标题方案")
    p_titles.add_argument("--slot", default=None, help="文稿槽位 w01（默认当日活动槽位）")

    p_all = sub.add_parser("all", help="跑 1-2（采集+评分）")
    p_all.add_argument("--date", required=True)
    p_all.add_argument("--timeframe", default="", help="时段描述（默认自动生成）")
    p_all.add_argument("--trending", nargs="*", default=[])
    p_all.add_argument("--top-k", type=int, default=5)
    p_all.add_argument("--runs", type=int, default=1)
    p_all.add_argument("--batch-size", type=int, default=5)
    p_all.add_argument("--workers", type=int, default=5)
    p_all.add_argument("--force", action="store_true")

    p_full = sub.add_parser("full", help="跑全流程")
    p_full.add_argument("--date", required=True)
    p_full.add_argument("--timeframe", default="", help="时段描述（默认自动生成）")
    p_full.add_argument("--trending", nargs="*", default=[])
    p_full.add_argument("--top-k", type=int, default=5)
    p_full.add_argument("--runs", type=int, default=1)
    p_full.add_argument("--batch-size", type=int, default=5)
    p_full.add_argument("--workers", type=int, default=5)
    p_full.add_argument("--force", action="store_true")
    p_full.add_argument("--force-v2", action="store_true")
    p_full.add_argument("--v2-angle", choices=["时间切片", "反例切入", "物件线索", "回信体", "他人视角"], default=None)
    p_full.add_argument("--semantic-redline", action="store_true")
    p_full.add_argument("--no-sync", action="store_true", help="跳过飞书云文档同步")
    p_full.add_argument("--force-sync", action="store_true", help="覆盖已同步的文档")

    p_rl = sub.add_parser("redline", help="跑红线检查（短语匹配）")
    p_rl.add_argument("--text")
    p_rl.add_argument("--file")

    p_rs = sub.add_parser("redline-semantic", help="跑红线检查（LLM 语义）")
    p_rs.add_argument("--text")
    p_rs.add_argument("--file")

    p_ccp = sub.add_parser("ccp", help="商业化阶段一 · 客户内容档案 CCP（引导/投喂/反馈闭环/注入查看）")
    p_ccp.add_argument("action", choices=["bootstrap", "seed", "harvest", "status", "inject", "logs",
                                          "rollback", "revert", "reset"],
                       help="bootstrap:冷启动建档 | seed:投喂(问卷+文稿) | harvest:反馈闭环 | "
                            "status:完整性校验 | inject:查看注入上下文 | logs:修订留痕 | "
                            "rollback:版本回溯 | revert:撤销学习偏好 | reset:退回冷启动")
    p_ccp.add_argument("--client", default="slowpost", help="client_id（默认 slowpost）")
    p_ccp.add_argument("--ml", default="", help="seed 时投喂的历史文稿目录（取 .md/.txt 前 N 篇）")
    p_ccp.add_argument("--file", default="", help="seed 时问卷 JSON 文件路径")
    p_ccp.add_argument("--version", type=int, default=None, help="rollback 目标版本号")
    p_ccp.add_argument("--lead-id", default="", help="revert 的学习偏好 id")
    p_ccp.add_argument("--dry-run", action="store_true", help="harvest 只算不落盘")

    p_ag = sub.add_parser("agent", help="项目级 Agent · 自主内容生产（混合自主，选题/发布两道人工关卡）")
    p_ag.add_argument("--date", required=True)
    p_ag.add_argument("--goal", default="", help="今日目标（默认：完成今日公众号内容生产）")
    p_ag.add_argument("--choice", type=int, default=None, help="选题确认恢复：候选序号 1-5（0=拒绝全部）")
    p_ag.add_argument("--note", default="", help="选题备注 / 发布拒绝时的修改意见")
    p_ag.add_argument("--approve-publish", action="store_true", help="发布确认恢复：批准")
    p_ag.add_argument("--reject-publish", action="store_true", help="发布确认恢复：拒绝（--note 写修改意见）")
    p_ag.add_argument("--raise-budget", action="store_true", help="预算超限后提额续跑")
    p_ag.add_argument("--extra-steps", type=int, default=10, help="提额：追加步数")
    p_ag.add_argument("--extra-cost", type=float, default=1.0, help="提额：追加成本（CNY）")
    p_ag.add_argument("--max-steps", type=int, default=None, help="启动时自定义步数上限")
    p_ag.add_argument("--max-cost", type=float, default=None, help="启动时自定义成本上限（CNY）")
    p_ag.add_argument("--status", action="store_true", help="仅查询运行状态")
    p_ag.add_argument("--cancel", action="store_true", help="取消运行（写标记）")
    p_ag.add_argument("--trace", action="store_true", help="打印 trace 时间线")

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return

    handlers = {
        "setup": cmd_setup,
        "collect": step_collect,
        "score": step_score,
        "choose": cmd_choose,
        "feedback": lambda a: collect_feedback(a.date, a.feedback, a.from_file) or True,
        "feedback-dedup": lambda a: dedup_feedback() or True,
        "analyze": step_analyze,
        "write": run_write,
        "format": step_format,
        "status": cmd_status,
        "sync": step_sync,
        "learn": step_learn,
        "titles": step_titles,
        "new-batch": step_new_batch,
        "regenerate-batch": step_regenerate_batch,
        "outline": step_outline,
        "all": step_all,
        "full": step_full,
        "redline": cmd_redline,
        "redline-semantic": cmd_redline_semantic,
        "ccp": cmd_ccp,
        "agent": cmd_agent,
    }
    if args.cmd in handlers:
        try:
            handlers[args.cmd](args)
        except Exception as e:
            import traceback
            print(f"\n[ERROR] {e}")
            traceback.print_exc()
            sys.exit(1)


def _agent_summary(r):
    """agent 运行摘要 → 人读输出（等待审批时给出下一步命令）。"""
    print(f"[Agent] {r['date']} · 状态: {r['status']}"
          f" · 步数 {r.get('steps_used', 0)} · planner 成本 ¥{r.get('cost_cny', 0):.4f}")
    p = r.get("pending_approval") or {}
    if p.get("type") == "topic_confirm":
        print("\n📋 请选择今日要写的选题：")
        for t in p.get("top5", []):
            mark = " ⚠️合规风险" if t.get("compliance_blocked") else ""
            print(f"  {t.get('rank')}. {t.get('topic')}（综合分 {t.get('total')}）{mark}")
        print(f"\n  选择：python cat_shop.py agent --date {p['date']} --choice N [--note 备注]")
        print(f"  拒绝：--choice 0 --note 理由（agent 会生成新一批或求助）")
    elif p.get("type") == "publish_confirm":
        print(f"\n📤 待发布确认：{p.get('title', '?')}（{p.get('word_count', '?')} 字）")
        for t in (p.get("titles") or [])[:3]:
            print(f"   标题方案：{t}")
        print(f"   排版产物：{p.get('mp_path', '')}")
        print(f"   门禁结论：{'通过' if p.get('gate_passed') else '未通过/未知'}")
        print(f"\n  批准：python cat_shop.py agent --date {p['date']} --approve-publish")
        print(f"  拒绝：--reject-publish --note 修改意见")
    elif r.get("status") == "paused_budget":
        print("\n⏸  预算护栏触发。提额续跑：python cat_shop.py agent --date "
              f"{r['date']} --raise-budget [--extra-steps N --extra-cost M]")
    if r.get("status") == "blocked":
        print("\n🆘 已升级人工处理（详见 data/agent_runs/ 报告与 trace）")


def cmd_ccp(args):
    """ccp 子命令：商业化阶段一客户内容档案（引导/投喂/闭环/注入查看，零 LLM）。"""
    import json as _json
    from pathlib import Path as _P
    from core.ccp import (ensure_startup, get_profile, seed_from_sources,
                          verify_seed, harvest, revert_lead)
    prof = get_profile(client_id=args.client)
    act = args.action

    if act == "bootstrap":
        p = ensure_startup(client_id=args.client)
        print(f"[ccp] 冷启动建档完成：client={args.client} v{p.profile['meta']['version']} "
              f"seed={p.profile['style']['seed_source']}")
        return
    if act == "status":
        s = verify_seed(prof)
        print("[ccp] 完整性校验 OK")
        for k, v in s.items():
            print(f"  {k}: {v}")
        return
    if act == "inject":
        for dim in ("style", "topic", "layout", "reader"):
            blk = prof.build_context(dim)
            print(f"── [{dim}] ─{'─' * 20}")
            print(blk if blk else "（空：维未启用或暂无内容）")
        return
    if act == "logs":
        notes = prof.profile["meta"].get("revision_notes") or []
        print(f"[ccp] 修订留痕（{len(notes)} 条，最新在前）")
        for n in reversed(notes[-20:]):
            print(f"  {n}")
        return
    if act == "rollback":
        if not args.version:
            print("用法：python cat_shop.py ccp rollback --version N")
            return
        print("[ccp] 版本回溯 " + ("成功" if prof.rollback(args.version) else "失败（快照不存在/损坏）"))
        return
    if act == "revert":
        if not args.lead_id:
            print("用法：python cat_shop.py ccp revert --lead-id <id>")
            return
        print("[ccp] 撤销学习偏好 " + ("成功" if revert_lead(prof, args.lead_id) else "未找到该 lead"))
        return
    if act == "reset":
        r = _P(prof.path)
        if r.exists():
            r.unlink()
        import shutil as _sh
        if prof.history_dir.exists():
            _sh.rmtree(prof.history_dir, ignore_errors=True)
        prof.load()
        print("[ccp] 已退回冷启动（旧档案文件物理保留于 data/ccp.broken 建议手动确认）")
        return

    if act == "seed":
        q = {}
        if args.file:
            q = _json.loads(_P(args.file).read_text(encoding="utf-8"))
        r = seed_from_sources(prof, ml_path=args.ml or None, questionnaire=q or None)
        print(f"[ccp] 投喂完成：问卷 {r['questionnaire'] if r['questionnaire'] else '（未提供）'} | "
              f"文稿 {r['articles']} | 校验 {r['verify']}")
        return
    if act == "harvest":
        out = harvest(prof, dry_run=args.dry_run)
        print("[ccp] 反馈闭环 dry-run：" if args.dry_run else "[ccp] 反馈闭环 harvest：")
        for k, v in out.items():
            print(f"  {k}: {v}")
        return


def cmd_agent(args):
    """agent 子命令入口：启动/审批恢复/提额/状态/取消/trace（逻辑全在 pipeline/runner）。"""
    from pipeline import runner
    date = args.date
    if args.cancel:
        print(runner.cancel_run(date)["message"])
        return
    if args.status:
        r = runner.get_run_state(date)
        if r.get("status") == "none":
            print(r.get("message", "无运行记录"))
            return
        _agent_summary(r)
        return
    if args.trace:
        entries = runner.read_trace(date)
        if not entries:
            print(f"无 trace（{date} 未运行过 agent）")
            return
        for e in entries:
            tool = e.get("tool") or e.get("node", "")
            print(f"  {e.get('ts', '')} [{tool}] {e.get('status', '')} "
                  f"{(e.get('message') or e.get('reason') or '')[:100]}")
        return
    if args.choice is not None:
        _agent_summary(runner.resume_run(date, {"choice": args.choice, "note": args.note}))
        return
    if args.approve_publish:
        _agent_summary(runner.resume_run(date, {"approved": True}))
        return
    if args.reject_publish:
        _agent_summary(runner.resume_run(date, {"approved": False, "note": args.note}))
        return
    if args.raise_budget:
        _agent_summary(runner.raise_budget(date, args.extra_steps, args.extra_cost, args.note))
        return
    config = {}
    if args.max_steps:
        config["max_steps"] = args.max_steps
    if args.max_cost:
        config["max_cost_cny"] = args.max_cost
    _agent_summary(runner.start_run(date, goal=args.goal, config=config or None))


if __name__ == "__main__":
    main()
