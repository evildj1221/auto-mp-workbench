#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline/writing.py · 写稿链路步骤（大纲/写稿/标题/排版/学习 + 语义红线）。

自 cat_shop.py 845-886 / 1810-2393 行原样迁出。唯一逻辑变更（Phase 1 解耦）：
step_write 末尾不再内嵌 step_sync 调用，改由 pipeline.flow.run_write / step_full
在写稿成功后编排同步（同步幂等 + 失败不阻断，净行为与 v3 一致）。
"""
import json
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timedelta

from core.paths import (SCRIPT_DIR, DRAFTS_DIR, FEEDBACK_DIR, HISTORY_PATH,
                        PROFILE_PATH, REDLINE_LOG, SELECTIONS_DIR,
                        UPLOADS_DIR, slot_paths, batch_file_names)
from core.storage import load_json, write_json, get_slot
from core.memory import (load_preference, load_user_feedback, load_history,
                         load_quotes, load_all_feedback)
from core.quality import (count_chinese_chars, strip_component_markers,
                          SERIES_ANGLES, _load_past_drafts, _load_past_full,
                          _find_similar_sentences)
from core.redlines import redline_check
from core.scoring import DEFAULT_W, learn_calibration_from_feedback
from core.render import render_html
from llm.client import call_llm
from llm.prompts import (WRITER_V1_PROMPT, OUTLINE_PROMPT,
                         TITLE_GENERATOR_PROMPT, SEMANTIC_REDLINE_PROMPT)

def redline_llm_semantic(text, step="semantic", date="unknown", file="unknown"):
    """LLM 语义红线：短语检查的补充，捕捉同义改写绕过。"""
    base = {"risk_level": "safe", "hits": [], "total_score": 0,
            "hit_counts": {"critical": 0, "high": 0, "medium": 0, "low": 0},
            "should_block": False, "step": step, "date": date, "file": file}
    if not text or not isinstance(text, str):
        return base
    prompt = SEMANTIC_REDLINE_PROMPT.replace("{text}", text[:6000])
    try:
        res = call_llm(prompt, temperature=0, max_tokens=800, module="redline_semantic")
    except Exception as e:
        print(f"  [WARN] 语义红线调用失败（跳过该检查）: {e}", file=sys.stderr)
        return {**base, "risk_level": "unknown", "reason": f"LLM 调用失败: {e}"}

    viols = res.get("violations") or []
    hits = []
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for v in viols:
        lvl = v.get("level", "medium")
        if lvl not in counts:
            lvl = "medium"
        counts[lvl] += 1
        hits.append({"rule_id": v.get("rule_id", ""), "level": lvl,
                     "matched_text": v.get("quote", ""), "message": v.get("reason", ""),
                     "context_snippet": (v.get("quote", "") or "")[:100],
                     "position": 0, "semantic": True})

    should_block = counts["critical"] >= 1 or counts["high"] >= 1
    risk = "safe"
    if counts["critical"] > 0: risk = "critical"
    elif counts["high"] > 0: risk = "high"
    elif counts["medium"] >= 1: risk = "warning"
    if should_block: risk = "blocked"

    reason = ""
    if counts["critical"] >= 1: reason = f"语义红线命中 {counts['critical']} 条 critical"
    elif counts["high"] >= 1: reason = f"语义红线命中 {counts['high']} 条 high"

    return {"risk_level": risk, "should_block": should_block, "reason": reason,
            "total_score": 0, "hit_counts": counts, "hits": hits,
            "step": step, "date": date, "file": file,
            "ts": datetime.now().isoformat(timespec="seconds")}

def step_outline(args):
    """Step 4.5 · 大纲搭建（写稿前确认结构，可选增强）。
    生成 {date}[_w{slot}]_outline.md；write 带 --use-outline 时注入。"""
    date = args.date
    slot = getattr(args, "slot", None)
    sel = load_json(SELECTIONS_DIR / f"{date}.json", {}) or {}
    if slot:
        sm = get_slot(date, slot) or {}
        topic = sm.get("topic") or sel.get("topic") or ""
        series = sm.get("motif_series") or sel.get("motif_series") or "B"
    else:
        topic = sel.get("topic") or ""
        series = sel.get("motif_series") or "B"
    if not topic:
        print("❌ 无选题信息，先到选题中心选定选题")
        return False
    prompt = OUTLINE_PROMPT + f"\n选题：{topic}\n系列：{series}\n"
    # json_mode=False：大纲是文本任务；json_object 模式会被 DeepSeek 以 400 拒绝（prompt 无 json 字样）
    raw = call_llm(prompt, temperature=0.5, max_tokens=1200, module="outline", json_mode=False)
    text = raw if isinstance(raw, str) else str(raw)
    text = text.strip()
    out_path = slot_paths(date, slot)["v1"].with_name(
        slot_paths(date, slot)["v1"].name.replace("_v1.md", "_outline.md"))
    out_path.write_text(text, encoding="utf-8")
    print(f"  ✅ 已写 {out_path.name}")
    print(text[:400])
    return True

def step_write(args):
    print(f"[INFO] Step 4 · {args.date}")
    date = args.date
    slot = getattr(args, "slot", None)
    sel_path = SELECTIONS_DIR / f"{date}.json"
    selection = load_json(sel_path, default={}) or {}
    # 槽位模式：选题/系列直接来自槽位 meta（多文稿独立写稿）
    if slot:
        sm = get_slot(date, slot)
        if not sm:
            print(f"❌ 槽位 {slot} 不存在（检查 data/works/{date}.json）")
            return False
        user_choice_rank = None
        user_note = selection.get("user_note", "")
        chosen = {"topic": sm.get("topic") or "", "motif_series": sm.get("motif_series") or "B",
                  "cand_id": sm.get("cand_id"), "total": None}
        topic = chosen["topic"]
        motif_series = chosen["motif_series"]
        if not topic:
            print(f"❌ 槽位 {slot} 无选题信息")
            return False
    else:
        if not sel_path.exists():
            print(f"❌ 未找到 {sel_path}，先跑 Step 3 并等用户决策")
            return False
        user_choice_rank = selection.get("user_choice")
        user_note = selection.get("user_note", "")
        if not user_choice_rank:
            print("❌ 用户尚未选择")
            return False
        # 决策批次存在时读对应批次 top5（修复：原先固定读 r1 文件，r2 决策会取错选题）
        sel_round = int(selection.get("round") or 1)
        _, in_path = batch_file_names(date, sel_round)
        data = load_json(in_path) or {}
        top5 = data.get("results") or []
        if not (1 <= user_choice_rank <= len(top5)):
            print(f"❌ user_choice={user_choice_rank} 超出 top5 范围（{len(top5)}），检查 selections/{date}.json")
            return False
        chosen = top5[user_choice_rank - 1]
        topic = chosen["topic"]
        motif_series = chosen.get("motif_series") or "B"

    # 注入偏好 + user_feedback
    injected = ""
    # P-03：仅注入真实发布数据校准的画像（source=real_stats）；旧自评画像不注入，防误导写稿
    pref = load_preference()
    if pref and pref.get("source") == "real_stats" and pref.get("data_quality") == "high":
        injected += f"\n## 偏好画像（真实发布数据校准）\n{json.dumps(pref, ensure_ascii=False, indent=2)}\n"
    user_fb = load_user_feedback(date)
    if user_fb:
        fb_text = "\n".join([
            f"- {e['rank']}. [{e['cand_id']}] {e['topic']} (AI={e['ai_score']}, 你={e['user_score']}, 差={e['delta']:+.1f})"
            + (f" — 💬 \"{e['user_note']}\"" if e.get('user_note') else "")
            for e in user_fb
        ])
        injected += f"\n## 当日用户评论\n{fb_text}\n"
    history = load_history(20)
    if history:
        hist_text = "\n".join([
            f"- {h.get('date', '?')}: {h.get('topic', '?')[:30]}（score={h.get('score', '?')}）"
            for h in history
        ])
        injected += f"\n## 最近 20 次用户选择\n{hist_text}\n"

    # P3 用户素材注入：上传的文稿类素材文本（md/txt/docx）作为写作上下文
    updir = (UPLOADS_DIR / f"{date}_{slot}") if slot else (UPLOADS_DIR / date)
    mf = updir / "manifest.json"
    if mf.exists():
        blocks = [f"--- 素材「{m.get('name')}」---\n{m.get('text', '')}"
                  for m in (load_json(mf, None) or [])
                  if m.get("type") == "doc" and m.get("text")]
        if blocks:
            injected += ("\n## 用户素材（本轮写作的真实素材，优先化用其中的细节与情绪，"
                         "不要照抄原句）\n" + "\n\n".join(blocks) + "\n")

    full = WRITER_V1_PROMPT.replace("{selected_topic}", topic).replace("{motif_series}", motif_series)
    # 大纲注入（--use-outline 且存在 outline 文件时）
    outline_block = ""
    if getattr(args, "use_outline", False):
        op = slot_paths(date, slot)["v1"].with_name(
            slot_paths(date, slot)["v1"].name.replace("_v1.md", "_outline.md"))
        if op.exists():
            outline_block = "\n## 已确认的大纲（严格按此结构写，可补充细节但不要偏离）\n" + \
                op.read_text(encoding="utf-8") + "\n"
    # 金句库注入（风格参考，最近 6 条，不直接复制）
    quotes_block = ""
    qs = load_quotes(6)
    if qs:
        qtext = "\n".join(f"- {q.get('text')}" for q in qs)
        quotes_block = "\n## 金句库参考（用于感受你一贯的句子质感，可化用不可照抄）\n" + qtext + "\n"
    # 反重复约束：检索历史同类文稿，注入"禁用桥段清单"（防止失眠/独居等相似主题反复用同一套桥段）
    avoid_block = ""
    past_drafts, banned, used_angles = _load_past_drafts(date, series=motif_series)
    if past_drafts or banned:
        excerpts = "\n\n".join(
            f"--- {d['file']}（{d['series']}系列）---\n{d['excerpt']}" for d in past_drafts)
        banned_text = "、".join(sorted(banned)) if banned else "无"
        # 推荐未用角度（正向引导，比"禁止"更有效）
        fresh = [a for a, _ in SERIES_ANGLES.get(motif_series, []) if a not in used_angles]
        angle_text = ""
        if fresh:
            angle_text = "\n本次请从以下未用过的角度中选一个切入（优先选第 1 个）：\n" + \
                "\n".join(f"- {a}" for a in fresh[:3]) + "\n"
        else:
            angle_text = "\n本次必须使用一个全新的切入角度（历史角度已用尽）：\n" + \
                "选择一个与上面节选完全无关的生活场景/物件/人物视角来写。\n"
        avoid_block = (
            "\n## 反重复约束（必须遵守，重要）\n"
            f"历史稿已用角度：{'、'.join(sorted(used_angles)) if used_angles else '无'}\n"
            + angle_text +
            f"历史稿中已反复出现的桥段词（严禁再次出现）：{banned_text}\n"
            "1. 选择与历史文稿不同的切入角度、场景、动作细节和意象；\n"
            "2. 不得重复使用任何具体桥段（相同食物、动作、物件、比喻、金句）；\n"
            + excerpts + "\n")
    full = full.replace("{outline_block}", outline_block).replace("{quotes_block}", quotes_block) \
        .replace("{avoid_block}", avoid_block)
    full = full.replace("</context>", injected + "\n</context>")

    print(f"  写稿 v1: {topic}" + ("（按大纲）" if outline_block else ""))
    v1_content = call_llm(full, temperature=0.4, max_tokens=6000, json_mode=False, module="write")
    # P-06：合并标题生成——从正文尾部解析 :::titles 块；无块则保留后续独立 _gen_titles 回退
    _titles_parsed, v1_content = _extract_titles(v1_content)
    paths = slot_paths(date, slot)
    paths["v1"].write_text(v1_content, encoding="utf-8")
    print(f"  ✅ 已写 {paths['v1'].name}" + (f"（解析到 {len(_titles_parsed)} 个备选标题）" if _titles_parsed else ""))

    # 自检（红线 + 字数，统一按汉字计；新规范：900-1200，优先生成 1200 字以内）
    word_count = count_chinese_chars(v1_content)
    issues = []
    if word_count < 900: issues.append(f"字数不足 ({word_count})")
    if word_count > 1300: issues.append(f"字数超限 ({word_count} > 1300，建议精简到 1200 内)")
    rl = redline_check(v1_content, step="04_write", date=date,
                        file=f"data/drafts/{slot_paths(date, slot)['v1'].name}")
    if rl["should_block"]:
        issues.append(f"🛑 红线阻断 ({rl['risk_level']}): {rl['block_reason']}")
    print(f"  v1 自检: {word_count}字, 红线={rl['risk_level']} (c={rl['hit_counts']['critical']} h={rl['hit_counts']['high']} m={rl['hit_counts']['medium']} l={rl['hit_counts']['low']})")

    # 可选：LLM 语义红线（短语检查的补充）
    sem = None
    if getattr(args, "semantic_redline", False) and not rl["should_block"]:
        print("  语义红线扫描中…")
        sem = redline_llm_semantic(v1_content, step="04_write_semantic", date=date,
                                   file=f"data/drafts/{date}_v1.md")
        if sem["should_block"]:
            issues.append(f"🛑 语义红线: {sem['reason']}")
            print(f"  [WARN] 语义红线: {sem['reason']}")
        elif sem["hits"]:
            print(f"  [WARN] 语义红线 {len(sem['hits'])} 条命中（非阻断）")

    # 程序化反重复：雷同句检测（LLM 对 prompt 约束遵循差，必须代码兜底）。
    # 用户口径：不是禁用词，是"不要出现相似的段落/长句"→ 句子级相似度检测。
    past_full = _load_past_full(date, series=motif_series)

    def _similar_hits(text):
        return _find_similar_sentences(text, past_full)

    # 自动重写循环：字数不足 或 与历史稿存在雷同句 → 重写（最多 3 轮），仍不过才硬拦截
    word_count_block = word_count < 900
    sim0 = _similar_hits(v1_content)
    if word_count_block or sim0:
        reasons = []
        if word_count_block:
            reasons.append(f"字数不足（{word_count} < 900）")
        if sim0:
            reasons.append(f"与历史稿存在 {len(sim0)} 处雷同句")
        print(f"  [INFO] 自动重写（原因：{'；'.join(reasons)}）…")
        sim_text = "\n".join(f"  - {s}" for s, _, _ in sim0)
        retry_prompt = (full +
                        f"\n\n## 修改要求\n上次全文未通过：{'；'.join(reasons)}。"
                        + (f"以下句子与历史稿高度雷同（句意/表达相似），必须全部改写为完全不同的表达，"
                           f"不得保留其中任何短语结构：\n{sim_text}\n"
                           if sim0 else "")
                        + f"目标 900-1200 字。请换一个完全不同的生活场景/动作细节（含对话或动作细节），"
                          f"保持信件口吻，直接重新输出全文，不要解释。")
        for attempt in range(1, 4):
            v1_content2 = call_llm(retry_prompt, temperature=0.4, max_tokens=5000,
                                   json_mode=False, module="write")
            word_count2 = count_chinese_chars(v1_content2)
            paths["v1"].write_text(v1_content2, encoding="utf-8")
            v1_content = v1_content2
            word_count = word_count2
            issues = [i for i in issues if not i.startswith("字数不足")]
            rl = redline_check(v1_content, step="04_write", date=date,
                        file=f"data/drafts/{slot_paths(date, slot)['v1'].name}")
            remaining = _similar_hits(v1_content)
            print(f"  ✅ 重写第 {attempt} 轮 → {word_count} 字，剩余雷同句: {len(remaining)}")
            if word_count >= 900 and not remaining:
                break
            rem_text = "\n".join(f"  - {s}" for s, _, _ in remaining)
            retry_prompt = (full +
                            f"\n\n## 修改要求\n上次仍未通过：字数={word_count}，"
                            + (f"以下句子仍与历史稿雷同，必须改写为完全不同的表达：\n{rem_text}\n"
                               if remaining else "字数不足 900。")
                            + f"请再换一个完全不同的场景重新写全文，直接输出，不要解释。")
        if word_count < 900:
            print(f"  [FATAL] 重写后仍不足 ({word_count} < 900)，停止 Step 4")
            with HISTORY_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"date": date, "cand_id": chosen.get("cand_id"), "topic": topic,
                                    "score": chosen.get("total"), "user_choice": user_choice_rank,
                                    "user_note": user_note, "emit_v2": False, "v1_word_count": word_count,
                                    "blocked_by_wordcount": True,
                                    "ts": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")
            return False
        final_hits = _similar_hits(v1_content)
        if final_hits:
            print(f"  [WARN] 重写 3 轮后仍含 {len(final_hits)} 处雷同句（建议手动调整或再次写稿）",
                  file=sys.stderr)

    if issues and not rl["should_block"] and not (sem and sem["should_block"]):
        print(f"  [INFO] 自检提示: {'; '.join(issues)}")

    if rl["should_block"] or (sem and sem["should_block"]):
        reason = rl["block_reason"] if rl["should_block"] else sem["reason"]
        print(f"  [FATAL] 触红线，停止 Step 4（{reason}）")
        with HISTORY_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"date": date, "cand_id": chosen.get("cand_id"), "topic": topic,
                                "score": chosen.get("total"), "user_choice": user_choice_rank,
                                "user_note": user_note, "emit_v2": False, "v1_word_count": word_count,
                                "blocked_by_redline": True,
                                "ts": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")
        return False

    # P1-9：v2 纯手动触发（删除"智能切换"，仅 --force-v2 / --v2-angle 时生成）
    force_v2 = getattr(args, "force_v2", False)
    should_v2 = force_v2
    if should_v2:
        print("  手动触发：写 v2")
        angle = getattr(args, "v2_angle", None) or "时间切片"
        v1_head = re.sub(r"[#*`>]", "", v1_content)[:300]
        v2_prompt = (full +
                     f"\n\n## v2 任务\n本次出 v2。切入角度：**{angle}**。\n"
                     f"v1 已生成（{word_count} 字），v2 必须做到：开头钩子、核心故事、结尾金句与 v1 完全不同；"
                     f"不得复用 v1 的段落、比喻和案例。\n"
                     f"v1 开头（仅作避让参考，不可照抄）：\n{v1_head}\n")
        v2_content = call_llm(v2_prompt, temperature=0.6, max_tokens=6000, json_mode=False, module="write_v2")
        slot_paths(date, slot)["v2"].write_text(v2_content, encoding="utf-8")
        rl2 = redline_check(v2_content, step="04_write", date=date, file=f"data/drafts/{date}_v2.md")
        if rl2["should_block"]:
            print(f"  [FATAL] v2 触红线（{rl2['block_reason']}），v2 已保存但需人工处理")
        if getattr(args, "semantic_redline", False) and not rl2["should_block"]:
            sem2 = redline_llm_semantic(v2_content, step="04_write_semantic", date=date,
                                        file=f"data/drafts/{date}_v2.md")
            if sem2["should_block"]:
                print(f"  [FATAL] v2 语义红线（{sem2['reason']}），v2 已保存但需人工处理")

    # 写 history
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"date": date, "cand_id": chosen.get("cand_id"), "topic": topic,
                            "score": chosen.get("total"), "user_choice": user_choice_rank,
                            "user_note": user_note, "emit_v2": should_v2, "v1_word_count": word_count,
                            "ts": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")
    # P0-4/P-06：标题方案——优先用写稿同批解析的标题块，缺失才独立调用 _gen_titles
    try:
        if _titles_parsed:
            out_path = slot_paths(date, slot)["titles"]
            if not out_path.exists():
                # P-05：默认选中第一个
                write_json(out_path, {"date": date, "topic": topic, "motif_series": motif_series,
                                      "generated_at": datetime.now().isoformat(timespec="seconds"),
                                      "selected": _titles_parsed[0].get("title"),
                                      "titles": _titles_parsed})
                print(f"  ✅ 已写 {out_path.name}（解析 {len(_titles_parsed)} 个，默认选中 #1）")
        else:
            _gen_titles(date, topic, motif_series, v1_content, slot=slot or None)
    except Exception as e:
        print(f"  [WARN] 标题方案生成失败（可稍后手动 titles 重试）: {e}", file=sys.stderr)

    # Phase 1 解耦：写稿后自动同步移至 pipeline.flow（run_write / step_full）编排，本函数不再内嵌

    print(f"[DONE] Step 4 完成")
    return True

# ===== Step 4.8: Titles（P0-4 · 标题多方案生成） =====
def _extract_titles(md_text):
    """P-06：从写稿输出中解析 :::titles 标题备选块。
    返回 (titles_list, cleaned_md)。无块时 titles_list 为空，md 原样返回。"""
    lines = (md_text or "").split("\n")
    out, titles, in_block = [], [], False
    for line in lines:
        s = line.strip()
        if s == ":::titles":
            in_block = True
            continue
        if in_block and s == ":::":
            in_block = False
            continue
        if in_block:
            m = re.match(r"^\d+[.、]\s*(.+)$", s)
            if m:
                parts = [p.strip() for p in re.split(r"[|｜]", m.group(1))]  # 兼容半角/全角分隔符
                if parts and parts[0]:
                    t = {"title": parts[0]}
                    if len(parts) > 1 and parts[1] in ("question", "contrast", "scene", "letter", "mystery"):
                        t["hook_type"] = parts[1]
                    if len(parts) > 2:
                        t["reason"] = parts[2][:20]
                    titles.append(t)
            continue
        out.append(line)
    return titles, "\n".join(out)


def _gen_titles(date, topic, motif_series, md_text, slot=None):
    """基于定稿文稿生成 3-5 个备选标题，存 data/drafts/{date}[_w{slot}]_titles.json。
    幂等：已存在且非 --force 时跳过（工作台标题选择依赖该文件）。"""
    out_path = slot_paths(date, slot)["titles"]
    # 反重复：收集历史标题，prompt 要求避免雷同标题
    hist_titles = []
    past_drafts, _, _ = _load_past_drafts(date, series=motif_series, max_total=4)
    for d in past_drafts:
        tp = DRAFTS_DIR / d["file"].replace("_v1.md", "_titles.json")
        if tp.exists():
            try:
                td = load_json(tp, default={})
                for t in (td.get("titles") or []):
                    if t.get("title"):
                        hist_titles.append(t["title"])
            except Exception:
                pass
    if out_path.exists():
        print(f"  ⏭ 标题方案已存在（{out_path.name}）")
        return False
    body = re.sub(r"[#*`>]", "", md_text or "")[:1800]
    prompt = TITLE_GENERATOR_PROMPT.replace("{topic}", topic).replace("{series}", str(motif_series or "B"))
    prompt = prompt.replace("{article_body}", body)
    if hist_titles:
        prompt += ("\n\n## 标题避让（必须遵守）\n历史已用标题（严禁重复或高度相似）："
                   + "、".join(hist_titles[-12:]) + "\n")
    res = call_llm(prompt, temperature=0.7, max_tokens=1200, module="titles")
    titles = res.get("titles") or res.get("candidates") or []
    if not titles:
        print("  [WARN] LLM 未返回标题方案", file=sys.stderr)
        return False
    # P-05：默认选中第一个标题（人工可在写稿页切换），减少一个必做的人工步骤
    out = {"date": date, "topic": topic, "motif_series": motif_series,
           "generated_at": datetime.now().isoformat(timespec="seconds"),
           "selected": titles[0].get("title") if titles else None, "titles": titles}
    write_json(out_path, out)
    print(f"  ✅ 已写 {out_path.name}（{len(titles)} 个备选标题，默认选中 #1）")
    return True


def step_titles(args):
    """手动（重新）生成标题方案。--force 覆盖已有；--slot 指定槽位。"""
    date = args.date
    slot = getattr(args, "slot", None)
    sel = load_json(SELECTIONS_DIR / f"{date}.json", {}) or {}
    if slot:
        sm = get_slot(date, slot) or {}
        topic = sm.get("topic") or sel.get("topic") or ""
        series = sm.get("motif_series") or sel.get("motif_series") or "B"
    else:
        topic = sel.get("topic") or ""
        series = sel.get("motif_series") or "B"
    p = slot_paths(date, slot)
    md_path = p["v1"] if p["v1"].exists() else (p["v2"] if p["v2"].exists() else None)
    if not md_path:
        print(f"❌ 未找到 v1/v2 文稿，先跑 Step 4")
        return False
    if getattr(args, "force", False) and p["titles"].exists():
        p["titles"].unlink()
    return _gen_titles(date, topic, series, md_path.read_text(encoding="utf-8"), slot=slot)

# ===== Step 5: Format =====
def step_format(args):
    version = getattr(args, "version", None) or "v1"
    print(f"[INFO] Step 5 · {args.date} · {version}")
    date = args.date
    in_path = DRAFTS_DIR / f"{date}_{version}.md"
    if not in_path.exists():
        print(f"❌ 未找到 {in_path}，先跑 Step 4")
        return False
    md = in_path.read_text(encoding="utf-8")
    # 二次红线扫描（Step 4 已记日志，这里不重复记录，避免 redline_log 计数虚高）
    rl = redline_check(md, step="05_format", date=date, file=str(in_path.relative_to(SCRIPT_DIR)),
                       auto_log=False)
    if rl["should_block"]:
        print(f"  [FATAL] 05 二次扫描发现红线阻断：{rl['block_reason']}")
        return False
    elif rl["hits"]:
        print(f"  [WARN] 05 二次扫描：{len(rl['hits'])} 条命中（非阻断）")

    if getattr(args, "semantic_redline", False):
        sem = redline_llm_semantic(md, step="05_format_semantic", date=date,
                                   file=str(in_path.relative_to(SCRIPT_DIR)))
        if sem["should_block"]:
            print(f"  [FATAL] 05 语义红线阻断：{sem['reason']}")
            return False
        elif sem["hits"]:
            print(f"  [WARN] 05 语义红线：{len(sem['hits'])} 条命中（非阻断）")

    title_m = re.search(r"^# (.+)$", md, re.MULTILINE)
    title = title_m.group(1).strip() if title_m else "无题"
    html = render_html(title, strip_component_markers(md))
    # v2 输出独立文件名，不覆盖 v1 的 {date}.html
    out_name = f"{date}.html" if version == "v1" else f"{date}_{version}.html"
    (DRAFTS_DIR / out_name).write_text(html, encoding="utf-8")
    print(f"  ✅ 已写 {DRAFTS_DIR / out_name} · 标题：{title}")
    print(f"[DONE] Step 5 完成")
    return True

def step_learn(args):
    print(f"[INFO] Step 7 · {args.date} · {args.mode}")
    date = args.date
    try:
        today = datetime.fromisoformat(date)
    except ValueError:
        today = datetime.now()
    history = load_history(1000)
    if not history:
        print("[WARN] 无 history")
        return False

    recent_7 = [h for h in history if h.get("date", "") >= (today - timedelta(days=7)).strftime("%Y-%m-%d")]
    total = len(recent_7)
    chosen = [h for h in recent_7 if h.get("user_choice")]
    acceptance = len(chosen) / total if total else 0
    avg_score = sum(h.get("score", 0) for h in chosen) / len(chosen) if chosen else 0

    stats = {"date": date, "total_choices": total, "chosen_count": len(chosen),
             "acceptance_rate": round(acceptance, 2), "avg_score_of_chosen": round(avg_score, 2)}
    stats_path = FEEDBACK_DIR / "daily_stats.json"
    existing = []
    if stats_path.exists():
        try: existing = json.loads(stats_path.read_text(encoding="utf-8"))
        except Exception: existing = []
    if not isinstance(existing, list): existing = []
    existing.append(stats)
    write_json(stats_path, existing)
    print(f"  ✅ 已写 {stats_path}")

    # 红线汇总
    if REDLINE_LOG.exists():
        cutoff = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        entries = []
        for line in REDLINE_LOG.read_text(encoding="utf-8").strip().split("\n"):
            if not line: continue
            try:
                e = json.loads(line)
                if e.get("date", "") >= cutoff: entries.append(e)
            except Exception: continue
        if entries:
            print(f"  [WARN] 当日红线命中 {len(entries)} 条")
            level_c = Counter(e["level"] for e in entries)
            print(f"     critical={level_c.get('critical', 0)} high={level_c.get('high', 0)} medium={level_c.get('medium', 0)} low={level_c.get('low', 0)}")

    if args.mode == "weekly":
        cutoff = (today - timedelta(days=14)).strftime("%Y-%m-%d")
        recent_14 = [h for h in history if h.get("date", "") >= cutoff]
        fbs = [e for e in load_all_feedback() if e.get("date", "") >= cutoff]

        cal = learn_calibration_from_feedback(fbs) if len(fbs) >= 4 else None
        if cal is None:
            n = len(recent_14)
            profile = {
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "data_window": f"{cutoff} ~ {date}",
                "total_choices": n, "total_user_comments": len(fbs),
                "data_quality": "low",
                "source": "legacy_self_rating",
                "scoring_calibration": dict(DEFAULT_W),
                "ai_notes": (f"用户评论不足（{len(fbs)} 条 < 4），沿用默认权重。"
                             f"继续用 feedback 命令打分，凑够样本后自动学习。"),
            }
        else:
            profile = {
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "data_window": f"{cutoff} ~ {date}",
                "total_choices": len(recent_14), "total_user_comments": len(fbs),
                "data_quality": "high" if len(fbs) >= 10 else "medium",
                "source": "legacy_self_rating",
                "scoring_calibration": cal,
                "ai_notes": ("基于近 14 天用户评论的区分度学习："
                             "对用户给高分的选题，AI 高分维度对应上调权重。"),
            }
        if PROFILE_PATH.exists():
            shutil.copy2(PROFILE_PATH, PROFILE_PATH.with_suffix(".json.bak"))
        write_json(PROFILE_PATH, profile)
        print(f"  ✅ 已写 {PROFILE_PATH}（data_quality={profile['data_quality']}）")
        if profile["scoring_calibration"] != DEFAULT_W:
            print(f"  ⚖️  学习到的权重: {profile['scoring_calibration']}")
    print(f"[DONE] Step 7 {args.mode} 完成")
    return True
