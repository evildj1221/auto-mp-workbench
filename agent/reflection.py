#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/reflection.py · 运行反思与经验记忆（v4.1 A1）。

reflect 节点在 finish 前运行：LLM 复盘 trace + 门禁结论 + 人工审批结果，
产出一条结构化经验落 data/memory/episodes.jsonl；planner 系统提示词注入
近 N 条经验（防膨胀：一行一条 + 上限条数）。同日同 goal 重复运行幂等覆盖。

episodes 条目：{date, goal, topic_meta, gate_results, human_verdict, lesson,
               series, tags, keep, created_at}
"""
import json
from datetime import datetime

from llm.client import call_llm
from llm.prompts import REFLECT_PROMPT

EPISODES_PATH = None  # 延迟初始化（core.paths.DATA_DIR import 期可用，测试可改写）


def _path():
    global EPISODES_PATH
    if EPISODES_PATH is None:
        from core.paths import DATA_DIR
        EPISODES_PATH = DATA_DIR / "memory" / "episodes.jsonl"
    return EPISODES_PATH


def load_episodes(limit: int | None = None) -> list:
    """读全部（或最近 limit 条）经验，按时间升序返回。"""
    p = _path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out[-limit:] if limit else out


def append_episode(entry: dict) -> bool:
    """追加经验条目。按 (date, goal) 幂等：同日同目标重跑覆盖旧条目。"""
    try:
        episodes = load_episodes()
        episodes = [e for e in episodes
                    if not (e.get("date") == entry.get("date")
                            and e.get("goal") == entry.get("goal"))]
        episodes.append(entry)
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in episodes)
                     + "\n", encoding="utf-8")
        return True
    except Exception:
        return False  # 经验写入失败不影响主流程


def recent_lessons(n: int = 6) -> list:
    """planner 注入用的经验摘要（近 N 条 keep=true 的 lesson，一行一条）。"""
    out = []
    for e in reversed(load_episodes()):
        if not e.get("keep", True):
            continue
        lesson = str(e.get("lesson", "")).strip()
        if not lesson or lesson == "无新经验":
            continue
        tag = e.get("series") or ""
        line = f"[{e.get('date', '?')}{'·' + tag if tag else ''}] {lesson[:80]}"
        out.append(line)
        if len(out) >= n:
            break
    return out


def _run_record(state) -> dict:
    """组装复盘输入：目标 / 选题 / 门禁 / 审批 / 工具序列（结构化摘要）。"""
    from core.paths import SELECTIONS_DIR
    from core.storage import load_json

    date = state["date"]
    sel = load_json(SELECTIONS_DIR / f"{date}.json", {}) or {}
    topic_meta = {"topic": sel.get("topic", ""), "series": sel.get("motif_series", ""),
                  "user_choice": sel.get("user_choice")}
    gate = state.get("gate_status") or {}
    soft = state.get("soft_gate") or {}
    gate_results = {"passed": gate.get("passed"), "verdict": gate.get("verdict", ""),
                    "soft_scores": soft.get("scores"),
                    "soft_suggestions": (soft.get("suggestions") or [])[:3]}

    human_verdict = {"topic": "", "publish": "none"}
    tools = []
    from langchain_core.messages import ToolMessage
    for m in state.get("messages") or []:
        if isinstance(m, ToolMessage):
            try:
                st = json.loads(m.content).get("status", "?")
            except (ValueError, TypeError):
                st = "?"
            tools.append(f"{m.name}({st})")
        text = str(getattr(m, "content", "") or "")
        if "【用户已选定】" in text:
            human_verdict["topic"] = text.split("】", 1)[-1].split("。")[0][:60]
        elif "【用户拒绝了全部候选】" in text:
            human_verdict["topic"] = "全部拒绝：" + text.split("理由：", 1)[-1].split("。")[0][:40]
        elif "【用户已批准发布】" in text:
            human_verdict["publish"] = "approved"
        elif "【用户暂不批准发布】" in text:
            human_verdict["publish"] = "rejected：" + text.split("】", 1)[-1].split("。")[0][:60]

    return {
        "date": date, "goal": state["goal"], "status": state.get("status"),
        "steps_used": state.get("steps_used"), "topic_meta": topic_meta,
        "gate_results": gate_results, "human_verdict": human_verdict,
        "tool_sequence": tools,
    }


def make_reflect_node():
    """reflect 节点工厂：LLM 复盘 → episodes 落盘 → trace 留痕。不改运行状态。"""

    def reflect_node(state):
        from agent.executor import append_trace
        record = _run_record(state)
        entry = {
            "date": state["date"], "goal": state["goal"],
            "topic_meta": record["topic_meta"],
            "gate_results": record["gate_results"],
            "human_verdict": record["human_verdict"],
            "lesson": "", "keep": False,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            raw = call_llm(REFLECT_PROMPT.replace(
                "{run_record}", json.dumps(record, ensure_ascii=False, indent=1)),
                temperature=0.1, max_tokens=400, module="reflect")
            lesson = raw if isinstance(raw, dict) else {}
            entry.update({
                "lesson": str(lesson.get("lesson", ""))[:120],
                "series": str(lesson.get("series", ""))[:4],
                "tags": [str(t)[:10] for t in (lesson.get("tags") or [])[:3]],
                "keep": bool(lesson.get("keep", True)) and bool(lesson.get("lesson")),
            })
        except Exception as e:
            append_trace(state["date"], {"node": "reflect", "event": "skipped",
                                         "reason": f"复盘 LLM 调用失败: {type(e).__name__}"})
            return {}
        append_episode(entry)
        append_trace(state["date"], {"node": "reflect", "lesson": entry["lesson"][:80],
                                     "keep": entry["keep"]})
        return {}

    return reflect_node
