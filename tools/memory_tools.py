#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/memory_tools.py · 记忆与反馈工具（record_feedback / learn_preferences /
add_quote / recall_lessons / save_lesson）。

数据闭环的写入端：用户评论 → 偏好画像 → 评分权重；运行经验 → episodes →
planner 注入（v4.1 A1）。全部落 data/ 既有文件，格式与 v3 完全一致
（agent 与工作台 UI 读写同一份数据，互不冲突）。
"""
import contextlib
import io

from langchain_core.tools import tool

from agent import reflection
from core import memory as mem
from pipeline.writing import step_learn
from tools._base import envelope, run_step, tool_error


@tool
def record_feedback(date: str, feedback: str) -> dict:
    """登记用户对当日 Top5 候选的评分评论（格式: '1:5 喜欢; 2:3 太老套'，分数 1-5）。
    评论是权重学习的数据源，每篇发布后都应登记。按 (date, rank) 幂等去重，重复登记覆盖旧值。

    Args:
        date: 目标日期，YYYY-MM-DD
        feedback: 评论串，格式 'rank:score note'，分号或换行分隔
    """
    try:
        top5 = mem.load_top5(date)
        if not top5:
            return envelope("error", f"当日无 Top5（data/drafts/{date}_top5.json），先 score_candidates")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = mem.collect_feedback(date, feedback_str=feedback)
        entries = mem.load_user_feedback(date)
        if not ok and not entries:
            return envelope("error", "评论解析失败（格式: 1:5 喜欢; 2:3 一般）", log=buf.getvalue())
        return envelope("ok", f"已登记 {len(entries)} 条评论（同日重复自动去重）",
                        log=buf.getvalue(), feedback_count=len(entries),
                        entries=[{"rank": e.get("rank"), "topic": e.get("topic", "")[:30],
                                  "user_score": e.get("user_score"),
                                  "delta": e.get("delta")} for e in entries])
    except Exception as e:
        return tool_error(e)


@tool
def learn_preferences(date: str, mode: str = "daily") -> dict:
    """反馈学习：daily 写当日决策统计；weekly 额外汇总 14 天数据更新偏好画像
    （preference_profile.json，评分与写稿共用）。幂等：重复执行追加统计记录。

    Args:
        date: 目标日期，YYYY-MM-DD
        mode: daily（当日统计）或 weekly（画像更新，建议每周一次）
    """
    try:
        ok, log = run_step(step_learn, date=date, mode=mode)
        if not ok:
            return envelope("error", "学习失败（history.jsonl 为空或数据不足），查看 log", log=log)
        profile = mem.load_preference()
        extra = {"profile_updated": profile is not None}
        if profile:
            extra["preference_summary"] = {
                k: profile.get(k) for k in ("data_window", "total_choices",
                                            "total_user_comments", "data_quality")
                if k in profile}
        return envelope("ok", f"{mode} 学习完成", log=log, **extra)
    except Exception as e:
        return tool_error(e)


@tool
def add_quote(text: str, source: str = "agent") -> dict:
    """金句入库（写稿风格参考，按文本幂等：已存在不重复写入）。

    Args:
        text: 金句内容（至少 4 字）
        source: 来源标记（如 'agent' / 'manual' / 高赞评论出处）
    """
    try:
        added = mem.add_quote(text, source=source)
        quotes = mem.load_quotes(5)
        if not added:
            return envelope("skipped", "金句已存在（按文本幂等）")
        return envelope("ok", "金句已入库", quotes_total=len(quotes),
                        latest=[q.get("text", "")[:40] for q in quotes[-3:]])
    except Exception as e:
        return tool_error(e)


@tool
def recall_lessons(query: str = "", limit: int = 5) -> dict:
    """检索历史运行经验（episodes.jsonl）：哪类选题好写、哪种角度常触门禁等。
    规划遇到同类情况时先查经验，避免每次从零学。

    Args:
        query: 关键词（匹配经验内容/系列/标签，如 '字数' / 'E系列'），空则返回最近几条
        limit: 最多返回条数（1-10）
    """
    try:
        eps = reflection.load_episodes()
        if query:
            q = query.lower()
            eps = [e for e in eps if q in str(e.get("lesson", "")).lower()
                   or q in str(e.get("series", "")).lower()
                   or any(q in str(t).lower() for t in e.get("tags", []))]
        eps = [e for e in eps if e.get("keep", True)][-max(1, min(limit, 10)):]
        return envelope("ok", f"命中 {len(eps)} 条经验（共 {len(reflection.load_episodes())} 条存量）",
                        episodes=[{"date": e.get("date"), "series": e.get("series", ""),
                                   "lesson": e.get("lesson", "")[:80]} for e in eps])
    except Exception as e:
        return tool_error(e)


@tool
def save_lesson(lesson: str, series: str = "", tags: list[str] | None = None,
                date: str = "", source: str = "agent") -> dict:
    """保存一条运行经验供后续运行复用（运行结束的 reflect 节点会自动复盘保存；
    规划中发现重要规律时也可主动调用）。

    Args:
        lesson: 经验内容（具体可执行，≤60 字，如 'E系列配反例切入字数常不足，优先时间切片'）
        series: 涉及选题系列字母（A/B/C/D/E，可选）
        tags: 短标签列表（如 ['字数','门禁']，最多 3 个）
        date: 关联日期 YYYY-MM-DD（缺省今天）
        source: 来源标记（reflect / agent / manual）
    """
    try:
        from datetime import datetime as _dt
        entry = {"date": date or _dt.now().strftime("%Y-%m-%d"), "goal": f"manual:{source}",
                 "topic_meta": {}, "gate_results": {}, "human_verdict": {},
                 "lesson": lesson[:120], "series": (series or "")[:4],
                 "tags": [str(t)[:10] for t in (tags or [])[:3]],
                 "keep": True, "source": source,
                 "created_at": _dt.now().isoformat(timespec="seconds")}
        if not lesson or len(lesson) < 4:
            return envelope("error", "经验内容太短（至少 4 字）")
        if not reflection.append_episode(entry):
            return envelope("error", "经验写入失败（episodes.jsonl）")
        return envelope("ok", "经验已保存（同日同目标重跑会覆盖）", lesson=entry["lesson"])
    except Exception as e:
        return tool_error(e)
