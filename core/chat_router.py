#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/chat_router.py · 中控台对话路由与轻聊天回复。

链路（设计方案 §二）：关键词路由（零成本）→ LLM 意图兜底（1 次小调用，
仅未命中时）→ 人设轻聊天回复（1 次调用，不碰流水线）。
动手模式不在本层——动作确认与工具执行由 backend/app/api/chat.py 编排。
"""
import os
import re

from core.personas import PERSONAS, route_keyword
from llm.client import call_llm
from llm.prompts.personas import build_persona_prompt, build_route_prompt

ROUTE_INVALID = ("chief", False)


def _recent_text(entries, limit=6):
    """最近几条对话 → 路由/回复 prompt 的语境串。"""
    lines = []
    for e in entries[-limit:]:
        who = PERSONAS.get(e.get("persona", ""), {}).get("name") if e.get("role") == "agent" else "台长"
        lines.append(f"{who}：{(e.get('text') or '')[:120]}")
    return "\n".join(lines)


def classify(message: str, recent_entries=None) -> tuple[str, bool]:
    """消息 → (persona_id, act_intent)。关键词优先；未命中且配了 key 才走 LLM 兜底。"""
    hit = route_keyword(message)
    if hit:
        return hit, _act_intent_heuristic(message)
    import os
    if not (os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")):
        return ROUTE_INVALID
    try:
        prompt = build_route_prompt(message, _recent_text(recent_entries or []))
        data = call_llm(prompt, temperature=0, max_tokens=60, json_mode=True,
                        module="chat_router")
        pid = (data or {}).get("persona", "chief")
        if pid not in PERSONAS:
            pid = "chief"
        return pid, bool(data.get("act")) and _act_intent_heuristic(message)
    except Exception:
        return ROUTE_INVALID


def _act_intent_heuristic(message: str) -> bool:
    """轻量动词判断：是否带「动手做」的口吻（决定回复要不要附确认卡）。

    2026-09-28 修绕圈子：原表漏了「现在马上写」这类短口令（只有"写一/写v1"），
    动手指令被判成闲聊 → 猫咪只能再次口头询问 → 无限绕圈。
    疑问句（怎么写/吗/？）是咨询，不算动手。
    """
    t = (message or "").strip()
    if not t or re.search(r"怎么|吗[？?。！!]*$|？|\?", t):
        return False
    acts = ["生成", "跑", "写一", "写v1", "写 v1", "起草", "出图", "配一张", "排版", "导出",
            "推送", "发布", "过一遍", "检查一遍", "来一批", "选一下", "定", "开始",
            "开工", "全套", "写稿", "写完", "交稿", "出稿", "一篇", "两篇", "三篇",
            "头图", "重画", "换一张", "画一张", "一键"]
    if any(a in t for a in acts):
        return True
    # 「现在马上写」「直接画」：动词收尾的短口令
    return bool(re.search(r"(写|画|排|发|跑|导)$", t.rstrip("吧呢啊呀哦！!。～~ ~")))


def is_affirmative(text: str) -> bool:
    """肯定跟进判定：待确认卡挂起时，台长回这类话 = 同意执行（不再绕圈再问）。
    「现在马上写」「好，画吧」「嗯」都算；带否定词的不算。"""
    t = (text or "").strip().lower()
    if not t or len(t) > 24:
        return False
    if re.search(r"不|别|算了|先等|等会|再说|取消|暂停|换|改", t):
        return False
    if re.search(r"^(好|好啊|好嘞|好的|好呀|行|行吧|嗯+|哦+|对|对滴|是的|是|要|要的|中|"
                 r"可以|ok|okay|没问题|安排|安排上|批准|准了|确认|同意|执行|开干|开工|"
                 r"去(吧|呗)?|干(吧|呗)?|整|来吧|走起|速速|赶紧|立刻|马上|这就|直接|现在就)", t):
        return True
    return bool(re.search(r"(马上写|直接写|现在就写|现在写|开写|写吧|画吧|排吧|发吧|跑吧|"
                          r"生成吧|就这样|就这么办|照这个来|按这个来|就写)", t))


def pipeline_brief_lines(date: str) -> list[str]:
    """当日流水线一句话语境（供人设回复引用，避免凭空编造进度）。"""
    try:
        from core.pipeline_status import STEP_LABELS, STEP_ORDER, status_map
        steps, pub, _choice = status_map(date)
        done = sum(1 for k in STEP_ORDER if steps[k])
        lines = [f"今日流水线：{done}/10 步完成"]
        if steps["choose"]:
            lines.append("台长已选定今日选题")
        if steps["write_v2"]:
            lines.append("v2 文稿已就绪")
        elif steps["write_v1"]:
            lines.append("v1 文稿已写好")
        if pub.get("user_published"):
            lines.append("今日已登记发布")
        return lines
    except Exception:
        return []


def persona_reply(persona_id: str, message: str, date: str,
                  recent_entries=None, extra_context: str = "") -> str:
    """轻聊天回复：人设 prompt + 当日时节/流水线语境 → 1 次 LLM 调用。"""
    p = PERSONAS[persona_id]
    context = []
    if extra_context:
        context.append(extra_context)
    try:
        from core.calendar_ctx import build_time_context
        tc = build_time_context(date)
        ctx_line = f"{tc['date']} {tc['weekday']} · {tc['weekday_note']}"
        if tc["festivals"]:
            ctx_line += " · 节日：" + "、".join(tc["festivals"])
        if tc["solar_terms"]:
            ctx_line += " · 节气：" + "、".join(tc["solar_terms"])
        if tc["season_scene"]:
            ctx_line += f" · {tc['season_scene']}"
        context.append(ctx_line)
    except Exception:
        pass
    context += pipeline_brief_lines(date)
    prompt = build_persona_prompt(persona_id, p["name"], p["role"], message,
                                  context, _recent_text(recent_entries or []))
    try:
        reply = call_llm(prompt, temperature=0.8, max_tokens=400, json_mode=False,
                         module=f"chat_{persona_id}")
        return (reply or "").strip() or "（我走神了一下，再说一遍？）"
    except Exception as e:
        return f"（线路不太通畅：{type(e).__name__}。稍后再叫我一次？）"


__all__ = ["classify", "persona_reply", "pipeline_brief_lines", "is_affirmative"]
