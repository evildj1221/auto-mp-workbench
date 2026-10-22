#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline/topics.py · 选题链路步骤（Step 1-3.7 + 多批次）。

自 cat_shop.py 1332-1795 行迁出；v4.3 选题智能优化（P0+M1+M3）改造 step_collect：
  P0  占位符注入断言 + 去重池扩展（近 14 天全部候选）+ 删硬编码热点兜底
  M1  注入 <time_context> 时节上下文（本地日历，零 API 成本）
  M3  注入 <user_directives> 用户风向 + directive_hit 命中统计 + 禁止清单后置过滤
"""
import json
import os
import re
import statistics
import sys
from datetime import date as date_cls
from datetime import datetime, timedelta

from core import errors as errcls
from core.calendar_ctx import render_time_context
from core.directives import (banned_keywords, list_directives,
                             record_batch_hits, render_directives_block)
from core.logging import log_error
from core.paths import CANDIDATES_DIR, DRAFTS_DIR, SELECTIONS_DIR, batch_file_names
from core.storage import (load_json, write_json, load_batches, save_batches,
                          register_batch, create_slot)
from core.topic_bank import sample_bank_line
from core.topic_families import (classify_family, classify_theme, diverse_top_k,
                                 recent_fatigue, theme_fatigue_line)
from core.trending import fetch_trending
from core.memory import (load_history, load_user_feedback, load_top5,
                         _analysis_path, load_excluded_series)
from core.scoring import DIMENSIONS, DEFAULT_W, load_effective_weights
from llm.client import call_llm
from llm.prompts import (TOPIC_GENERATOR_PROMPT, SCORE_RUBRIC_PROMPT,
                         FEEDBACK_ANALYZER_PROMPT)


def _default_timeframe():
    """根据当前日期生成动态 timeframe 默认值。"""
    now = datetime.now()
    seasons = {1: "冬季", 2: "冬季", 3: "春季", 4: "春季", 5: "春季",
               6: "夏季", 7: "夏季", 8: "夏季", 9: "秋季", 10: "秋季", 11: "秋季", 12: "冬季"}
    season = seasons.get(now.month, "")
    return f"{now.year} {season} / {now.month} 月"


def _recent_candidate_topics(days=14, today=None):
    """P0#3：近 N 天全部生成过的候选选题（含未选用），读 data/candidates/*.json。

    只防跨日复读，不需要全量历史；上限 120 条防 prompt 膨胀。
    """
    try:
        base = date_cls.fromisoformat(str(today)) if today else date_cls.today()
    except ValueError:
        base = date_cls.today()
    cutoff = base - timedelta(days=days)
    topics = []
    for p in sorted(CANDIDATES_DIR.glob("*.json")):
        m = re.match(r"(\d{4}-\d{2}-\d{2})", p.name)
        if not m:
            continue
        try:
            d = date_cls.fromisoformat(m.group(1))
        except ValueError:
            continue
        if d < cutoff:
            continue
        data = load_json(p, {}) or {}
        for c in data.get("candidates") or []:
            if isinstance(c, dict):
                t = (c.get("topic") or "").strip()
                if t:
                    topics.append(t)
    return topics

# ===== Step 1: Collect（v2：历史选题去重 + 幂等保护） =====
def _resolve_round(args, date):
    """解析批次号：--round 显式指定 > 批次索引 active_round > 1。"""
    if getattr(args, "round", None):
        return int(args.round)
    data = load_batches(date)
    return data.get("active_round") or 1


def step_collect(args):
    """Step 1 · 选题生成（支持 --round 多批次；v4.3 P0+M1+M3）"""
    print(f"[INFO] Step 1 · {args.date}")
    date = args.date
    timeframe = getattr(args, "timeframe", None) or _default_timeframe()
    trending = getattr(args, "trending", None) or []
    if not trending:
        try:
            trending = fetch_trending()   # 公开热榜自动拉取（缓存6h，失败静默空）
        except Exception:
            trending = []
    rnd = _resolve_round(args, date)
    out_path, _ = batch_file_names(date, rnd)
    if out_path.exists() and not getattr(args, "force", False):
        print(f"⚠️  {out_path} 已存在，跳过生成（用 --force 覆盖重跑）")
        return True

    # P0#4：删死兜底——trending 为空时不注入热点行（不再拿过期节气当线索）
    trending_line = (f"- 热点线索（可参考）：{'、'.join(trending)}\n" if trending else "")
    print(f"  trending: {trending if trending else '（无，不注入热点行）'}")

    # P0#3：去重池 = 已选历史 + 近 14 天全部生成候选（含未选用，防"备胎"复读）
    history = load_history(50)
    hist_topics = [h.get("topic", "") for h in history if h.get("topic")]
    recent = _recent_candidate_topics(14, today=date)
    pool = list(dict.fromkeys([*recent[-120:], *hist_topics[-20:]]))
    avoid_text = "、".join(pool) if pool else "无（首次运行）"
    print(f"  去重池：历史已选 {len(hist_topics)} + 近 14 天候选 {len(recent)}（注入 {len(pool)} 条）")

    # M1：时节上下文（本地日历，零 API；数据缺失静默降级）
    time_ctx = render_time_context(date)
    # v4.4：结构疲劳清单（近几天 Top5 用腻的骨架点名限用；本地零成本）
    fatigue = recent_fatigue(3, today=date)
    if fatigue["topics"]:
        print(f"  结构疲劳：{ {k: v for k, v in sorted(fatigue['counts'].items(), key=lambda x: -x[1])} }")
    theme_line = theme_fatigue_line(fatigue["topics"], threshold=2)
    if theme_line:
        print(f"  题材疲劳：{theme_line.split('：')[-1]}")
    bank_line = sample_bank_line(date, n=10)
    if bank_line:
        print(f"  灵感库：注入 10 条样本")
    # M3：用户风向（优先级最高；无风向时为空串）
    directives_block = render_directives_block()
    if directives_block:
        print(f"  风向标：{len(list_directives())} 条生效")

    prompt = (TOPIC_GENERATOR_PROMPT.replace("{user_directives}", directives_block)
              .replace("{timeframe}", timeframe)
              .replace("{trending_line}", trending_line)
              .replace("{avoid_topics}", avoid_text)
              .replace("{time_context}", time_ctx)
              .replace("{fatigue_line}", fatigue["line"])
              .replace("{theme_fatigue_line}", theme_line)
              .replace("{bank_line}", bank_line))
    # P0#2：注入断言——占位符残留即组装 bug，抛错留痕，杜绝静默失败复发
    leftover = [p for p in ("{user_directives}", "{timeframe}", "{trending_line}",
                            "{avoid_topics}", "{time_context}", "{fatigue_line}",
                            "{theme_fatigue_line}", "{bank_line}") if p in prompt]
    if leftover:
        msg = f"选题 prompt 占位符未替换: {leftover}"
        log_error(errcls.DATA_MISSING, msg, date=date, context="step_collect")
        raise ValueError(msg)

    result = call_llm(prompt, temperature=0.7, max_tokens=3000, module="collect")

    cands = result.get("candidates") or result.get("candidate_topics") or result.get("topics") or []
    # M3：禁止清单后置过滤（LLM 偶发违规的确定性兜底）
    banned = banned_keywords()
    if banned and cands:
        before = len(cands)
        cands = [c for c in cands
                 if not any(b in str(c.get("topic", "")) for b in banned)]
        if len(cands) < before:
            print(f"  [WARN] 禁止清单过滤：剔除 {before - len(cands)} 条含禁止词候选", file=sys.stderr)
    # M3：directive_hit 归一化（无风向时统一 false）
    for c in cands:
        if isinstance(c, dict):
            c["directive_hit"] = bool(c.get("directive_hit", False))
            fam = str(c.get("family") or "").strip().upper()
            if not fam.startswith("F") or fam not in ("F1","F2","F3","F4","F5","F6","F7","F8","F9","F10"):
                fam = classify_family(c.get("topic", ""))
            c["family"] = fam
            if not c.get("theme"):
                c["theme"] = classify_theme(c.get("topic", ""))
    fam_stats = {}
    for c in cands:
        fam_stats[c.get("family", "F0")] = fam_stats.get(c.get("family", "F0"), 0) + 1
    print(f"  家族覆盖：{len([k for k in fam_stats if k != 'F0'])} 个 → {sorted(fam_stats.items(), key=lambda x: -x[1])}")
    if not cands:
        print(f"[WARN] LLM 未生成候选（keys: {list(result.keys())}）", file=sys.stderr)
    if len(cands) < 15:
        print(f"[WARN] 候选数 {len(cands)} < 15", file=sys.stderr)
    result["candidates"] = cands

    # M3：命中统计（存在正向风向才记录）
    out_extra = {}
    if any(not d.get("negative") for d in list_directives()) and cands:
        n_hit = sum(1 for c in cands if c.get("directive_hit"))
        rec = record_batch_hits(n_hit, len(cands))
        out_extra["directive_hits"] = {"hits": n_hit, "total": len(cands),
                                       "rate": rec.get("rate")}
        print(f"  风向命中：{n_hit}/{len(cands)}（{rec['rate']:.0%}）")

    out = {"date": date, "timeframe": timeframe, "trending_keywords": trending,
           **result, **out_extra}
    write_json(out_path, out)
    print(f"  ✅ 已写 {out_path}（{len(cands)} 候选）")
    print(f"[DONE] Step 1 完成")
    return True


# ===== Step 2: Score（v2：默认 3 轮、偏好权重、保留元数据、幂等保护） =====
def step_score(args):
    rnd = _resolve_round(args, args.date)
    print(f"[INFO] Step 2 · {args.date} · r{rnd} · runs={args.runs} workers={args.workers}")
    date = args.date
    in_path, out_path = batch_file_names(date, rnd)
    if not in_path.exists():
        print(f"❌ 未找到 {in_path}，先跑 Step 1")
        return False
    data = load_json(in_path)
    candidates = data.get("candidates") or data.get("candidate_topics") or data.get("topics") or []
    if not candidates:
        print(f"❌ {in_path} 没有 candidates 字段")
        return False
    if not out_path.exists() and not in_path.exists():
        print(f"❌ 批次 r{rnd} 无候选，先跑 collect --round {rnd}")
        return False
    if out_path.exists() and not getattr(args, "force", False):
        sel_done = (SELECTIONS_DIR / f"{date}.json").exists()
        hint = "（且用户已决策，重跑会让 user_choice 失配！）" if sel_done else ""
        print(f"⚠️  {out_path} 已存在，跳过评分（用 --force 覆盖重跑{hint}）")
        return True

    # 偏好权重（偏好画像 + rubric 校准）
    try:
        today = datetime.fromisoformat(date)
    except ValueError:
        today = datetime.now()
    weights = load_effective_weights(today)
    if weights != DEFAULT_W:
        print(f"  ⚖️  已加载偏好/校准权重: {weights}")
    else:
        print(f"  ⚖️  默认权重: {weights}")

    prompt_tpl = SCORE_RUBRIC_PROMPT
    batch_size = max(1, getattr(args, "batch_size", 5))

    def _norm(r):
        """归一化 scores 键名（LLM 可能返回中文键或英文键）。"""
        m = {"共鸣": "resonance", "时效": "timeliness", "时效性": "timeliness", "争议": "controversy",
             "争议性": "controversy", "独特": "uniqueness", "独特性": "uniqueness",
             "写作可行": "writability", "可写": "writability", "写作": "writability",
             "resonance": "resonance", "timeliness": "timeliness", "controversy": "controversy",
             "uniqueness": "uniqueness", "writability": "writability"}
        n = {}
        for k, v in (r.get("scores") or {}).items():
            en = m.get(str(k).strip())
            if en:
                try:
                    n[en] = float(v)
                except (ValueError, TypeError):
                    pass
        return n

    def _score_batch(batch):
        """一批候选 × args.runs 轮评分，返回 {批内序号: [各轮 result]}（批量调用降低成本）。"""
        runs_per_cand = {i: [] for i in range(len(batch))}
        for i in range(args.runs):
            batch_json = json.dumps({"candidates": batch}, ensure_ascii=False)
            prompt = prompt_tpl.replace("{input_candidates_json}", batch_json)
            try:
                raw = call_llm(prompt, temperature=0, max_tokens=4000, module="score")
            except Exception as e:
                print(f"  ✗ 批次 run {i+1} 失败: {e}", file=sys.stderr)
                continue
            results = raw.get("results") if isinstance(raw, dict) else None
            if not isinstance(results, list):
                results = [raw] if isinstance(raw, dict) else []
            # 按 topic 精确匹配回候选；失配时按位置兜底
            by_topic = {}
            for r in results:
                if isinstance(r, dict):
                    by_topic.setdefault(str(r.get("topic", "")).strip(), []).append(r)
            for idx, cand in enumerate(batch):
                t = str(cand.get("topic", "")).strip()
                r = None
                if by_topic.get(t):
                    r = by_topic[t].pop(0)
                elif idx < len(results) and isinstance(results[idx], dict):
                    r = results[idx]
                if r is not None:
                    runs_per_cand[idx].append(r)
        return runs_per_cand

    def _aggregate(cand, runs):
        """单候选多轮结果聚合：先归一化，再校验 5 维齐全。"""
        valid = []
        normed = []
        for r in runs:
            n = _norm(r)
            if all(n.get(k) is not None for k in DIMENSIONS):
                valid.append(r)
                normed.append(n)
        if not valid:
            sample = runs[0] if runs else {}
            keys = list(((sample.get("scores") or {}).keys()))[:10]
            print(f"  ✗ {str(cand.get('topic'))[:20]}：所有 run 均缺维度分（scores 键={keys}），跳过",
                  file=sys.stderr)
            return None

        avg = {k: round(statistics.mean(n[k] for n in normed), 2) for k in DIMENSIONS}
        std = {k: (round(statistics.stdev(n[k] for n in normed), 2) if len(normed) > 1 else 0.0)
               for k in DIMENSIONS}
        totals = [round(sum(n[k] * weights[k] for k in DIMENSIONS), 2) for n in normed]
        total = round(statistics.mean(totals), 2)
        total_std = round(statistics.stdev(totals), 2) if len(totals) > 1 else 0.0
        compliance = max(float(r.get("compliance_risk", 0) or 0) for r in valid)
        confidence = round(max(0.0, 1 - total_std / 1.5), 2)

        compliance_blocked = compliance >= 7
        if compliance_blocked:
            total = 0.0
        needs_user_judgment = (total_std > 1.0) or compliance_blocked or (len(valid) == 1 and args.runs > 1)

        return {
            "cand_id": cand.get("cand_id"), "topic": cand.get("topic"),
            "source": cand.get("source", ""), "heat": cand.get("heat", 0), "url": cand.get("url", ""),
            "motif_series": cand.get("motif_series", ""), "timeliness": cand.get("timeliness", ""),
            "hook_type": cand.get("hook_type", ""), "family": cand.get("family") or "",
            "theme": cand.get("theme") or classify_theme(cand.get("topic", "")),
            "directive_hit": bool(cand.get("directive_hit", False)),
            "avg_scores": avg, "std": std, "total_std": total_std,
            "compliance_risk": round(compliance, 2),
            "compliance_blocked": compliance_blocked,
            "total": round(total, 2), "confidence": confidence,
            "ai_reasoning": valid[-1].get("ai_reasoning", ""),
            "needs_user_judgment": needs_user_judgment,
        }

    batches = [candidates[i:i + batch_size] for i in range(0, len(candidates), batch_size)]
    print(f"  共 {len(candidates)} 候选 / {len(batches)} 批 × {args.runs} 轮（P-10 全并行）")
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = []
    completed = 0

    def _score_once(batch, run):
        """单轮单批评分（P-10：batch×run 全部并行提交）。返回 (batch, runs_map)。"""
        runs_map = {i: [] for i in range(len(batch))}
        batch_json = json.dumps({"candidates": batch}, ensure_ascii=False)
        prompt = prompt_tpl.replace("{input_candidates_json}", batch_json)
        try:
            raw = call_llm(prompt, temperature=0, max_tokens=4000, module="score")
        except Exception as e:
            print(f"  ✗ 批次 run {run+1} 失败: {e}", file=sys.stderr)
            return batch, runs_map
        results_list = raw.get("results") if isinstance(raw, dict) else None
        if not isinstance(results_list, list):
            results_list = [raw] if isinstance(raw, dict) else []
        by_topic = {}
        for r in results_list:
            if isinstance(r, dict):
                by_topic.setdefault(str(r.get("topic", "")).strip(), []).append(r)
        for idx, cand in enumerate(batch):
            t = str(cand.get("topic", "")).strip()
            r = None
            if by_topic.get(t):
                r = by_topic[t].pop(0)
            elif idx < len(results_list) and isinstance(results_list[idx], dict):
                r = results_list[idx]
            if r is not None:
                runs_map[idx].append(r)
        return batch, runs_map

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(_score_once, b, r): (bi, r)
                   for bi, b in enumerate(batches) for r in range(args.runs)}
        runs_agg = {bi: {i: [] for i in range(len(batches[bi]))} for bi in range(len(batches))}
        for fut in as_completed(futures):
            bi, r = futures[fut]
            try:
                batch, runs_map = fut.result()
            except Exception as e:
                print(f"  [WARN] 批次评分异常: {e}", file=sys.stderr)
                continue
            for idx, run_list in runs_map.items():
                runs_agg[bi][idx].extend(run_list)
        for bi, batch in enumerate(batches):
            for idx, cand in enumerate(batch):
                agg = _aggregate(cand, runs_agg[bi].get(idx, []))
                if agg:
                    results.append(agg)
                else:
                    print("  [WARN] 某候选评分失败被跳过", file=sys.stderr)
            completed += len(batch)
            print(f"  [{min(completed, len(candidates))}/{len(candidates)}] 进度", file=sys.stderr)

    # P-02：用户排除过的系列 → 综合分硬降权 ×0.5（偏好信号真正进入决策）
    excluded = load_excluded_series()
    if excluded:
        for r in results:
            if r.get("motif_series") in excluded:
                r["total"] = round(r["total"] * 0.5, 2)
                r["excluded_by_pref"] = True
                print(f"  [INFO] 偏好排除系列 {r.get('motif_series')}：{r['topic'][:20]} 降权")
    # v4.4 反雷同：Top5 跨家族配额选取（每家族 ≤2，不够放宽补齐）——
    # 旧版纯总分截取曾让「秋招第N周，我把…」三同构霸榜
    top_k = diverse_top_k(results, k=getattr(args, "top_k", 5) or 5, per_family=2)
    fam_span = len({r.get("family") or classify_family(r.get("topic", "")) for r in top_k})
    print(f"  ✅ Top{len(top_k)} 跨 {fam_span} 个句式家族（旧版无此约束）")
    results.sort(key=lambda r: r["total"], reverse=True)
    out = {"date": date, "scored_at": datetime.now().isoformat(timespec="seconds"),
           "model": os.environ.get("LLM_MODEL") or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
           "weights": weights,
           "results": top_k,
           "needs_user_judgment_count": sum(1 for r in top_k if r["needs_user_judgment"])}
    write_json(out_path, out)
    print(f"  ✅ 已写 {out_path}（{len(top_k)} 候选）")
    print(f"[DONE] Step 2 完成")
    return True


# ===== Step 3.7: Choose（用户决策，写 selections/{date}.json） =====
def cmd_choose(args):
    """写入用户决策。自动从 top5 带出 topic/cand_id/total/motif_series，
    供 Step 4 写稿与 Step 4.5 同步元信息使用。"""
    date = args.date
    # 先解析批次，再读该批次的 top5 文件（修复：原先固定读 r1 文件，
    # 在 r2 批次选 #N 会错建成 r1 的 #N 选题）
    rnd = _resolve_round(args, date)
    _, tf = batch_file_names(date, rnd)
    data = load_json(tf, None)
    top5 = (data or {}).get("results") or []
    if not top5:
        print(f"❌ 未找到 top 5: {tf}，先跑 Step 2")
        return
    if not (1 <= args.choice <= len(top5)):
        print(f"❌ choice={args.choice} 超出范围（1-{len(top5)}）")
        return
    chosen = top5[args.choice - 1]
    decided_at = datetime.now().isoformat(timespec="seconds")
    sel = {
        "date": date, "user_choice": args.choice, "user_note": args.note or "",
        "cand_id": chosen.get("cand_id"), "topic": chosen.get("topic"),
        "total": chosen.get("total"), "motif_series": chosen.get("motif_series"),
        "decided_at": decided_at,
    }
    sel_path = SELECTIONS_DIR / f"{date}.json"
    # 多文稿槽位：选定选题 → 创建/复用槽位（同 round+cand_id 复用），记录 active_slot
    sel["round"] = rnd
    sid, slot, is_new = create_slot(date, args.choice, chosen, rnd)
    sel["active_slot"] = sid
    write_json(sel_path, sel)
    print(f"  ✅ 已写 {sel_path}" + (f"（新槽位 {sid}，可继续选其他选题生成多份文稿）" if is_new else f"（复用槽位 {sid}）"))
    print(f"  已选择 #{args.choice}: {chosen.get('topic')}（综合分 {chosen.get('total')}）")

    # 同步更新 pending 状态
    pending_path = SELECTIONS_DIR / f"{date}_pending.json"
    pending = load_json(pending_path, default={}) or {}
    if pending:
        pending.update({"status": "decided", "user_choice": args.choice,
                        "user_note": args.note or "", "decided_at": decided_at})
        write_json(pending_path, pending)


# ===== Feedback Analysis =====
def step_analyze(args):
    print(f"[INFO] Step 3.6 · 分析 {args.date}")
    date = args.date
    user_fb = load_user_feedback(date)
    if not user_fb:
        print(f"❌ 无用户评论：python cat_shop.py feedback --date {date} --feedback '1:5 好;2:3 一般'")
        return False

    top5 = load_top5(date) or []
    input_data = {
        "date": date, "user_comments": user_fb,
        "top5_summary": [{"rank": i + 1, "topic": r["topic"], "ai_total": r["total"],
                          "ai_scores": r["avg_scores"], "ai_reasoning": r["ai_reasoning"]} for i, r in enumerate(top5)],
    }
    input_json = json.dumps(input_data, ensure_ascii=False, indent=2)
    prompt = FEEDBACK_ANALYZER_PROMPT.replace("{user_feedback_json}", input_json)
    analysis = call_llm(prompt, temperature=0, max_tokens=2000, module="analyze")

    analysis["date"] = date
    analysis["analyzed_at"] = datetime.now().isoformat(timespec="seconds")
    analysis_path = _analysis_path(date)
    write_json(analysis_path, analysis)
    print(f"  ✅ 已写 {analysis_path}")

    # 打印
    print()
    print("=" * 60)
    print("📊 反馈分析报告")
    print("=" * 60)
    print()
    print("🎯 偏好信号：")
    for s in analysis.get("preference_signals", []):
        if isinstance(s, dict):
            print(f"   • {s.get('signal', s.get('preference', ''))}")
        else:
            print(f"   • {s}")

    print()
    print("💬 反馈给你：")
    for fb in analysis.get("feedback_to_user", []):
        if isinstance(fb, dict):
            print(f"   • [{fb.get('type', '')}] {fb.get('point', fb.get('feedback', ''))}")
        else:
            print(f"   • {fb}")
    print()

    if args.apply_rubric:
        # P2-17：自评学习（rubric_adjustments / rubric_revisions）已废弃；
        # 权重学习改为「数据复盘」按真实发布数据（阅读量）自动校准（calibrate_weights_from_stats）。
        print("  ℹ️  自评学习机制已废弃。权重学习请到工作台「数据复盘」模块，"
              "≥10 篇真实数据后自动校准。")
    return True


# ===== 批次命令（多批选题，满足按需生成/重新生成） =====
def _drop_batch(date, round_n):
    """失败回滚：从批次索引移除某批（无产物时）。"""
    data = load_batches(date)
    data["batches"] = [b for b in data.get("batches", []) if b.get("round") != round_n]
    active = data.get("active_round") or 0
    if active == round_n:
        data["active_round"] = max([b.get("round", 0) for b in data["batches"]], default=0)
    save_batches(date, data)
    print(f"  ⏭ 批次 r{round_n} 无产物，已回滚注册")


def step_new_batch(args):
    """生成新一批选题（register 新 round + collect + score）。"""
    date = args.date
    r, _ = register_batch(date)
    print(f"[INFO] 新批次 r{r} · {date}")
    old_round = getattr(args, "round", None)
    args.round = r
    try:
        ok = step_collect(args) and step_score(args)
        if not ok:
            _drop_batch(date, r)  # 失败回滚：移除空批次，避免"点了没反应"堆积
        return ok
    finally:
        if old_round is None:
            del args.round
        else:
            args.round = old_round
    return True


def step_regenerate_batch(args):
    """强制重新生成某批（--round 必填）：collect --force + score --force。"""
    date = args.date
    rnd = getattr(args, "round", None)
    if not rnd:
        print("❌ regenerate-batch 需要 --round N")
        return False
    data = load_batches(date)
    if not any(b.get("round") == rnd for b in data.get("batches", [])):
        # 允许未注册批次强制生成（自动补注册）
        data.setdefault("batches", []).append({"round": rnd,
                                               "created_at": datetime.now().isoformat(timespec="seconds"),
                                               "status": "ready"})
        data["active_round"] = max(data.get("active_round") or 0, rnd)
        save_batches(date, data)
    print(f"[INFO] 重新生成批次 r{rnd} · {date}")
    old_force = getattr(args, "force", False)
    args.force = True
    args.round = rnd
    try:
        if not step_collect(args):
            return False
        return step_score(args)
    finally:
        args.force = old_force
        if getattr(args, "round", None) is not None:
            del args.round
