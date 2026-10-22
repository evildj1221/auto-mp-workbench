#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/directive_tools.py · M3 选题风向标工具（set / list）。

agent 自主模式与工作台 UI 同一条链路：都写 core/directives.py 的
data/selections/topic_directives.jsonl，collect 时统一注入 prompt。
"""
from langchain_core.tools import tool

from core.directives import (add_directive, banned_keywords, list_directives,
                             recent_hit_rates)
from tools._base import envelope, tool_error

_SCOPE_LABEL = {"today": "今日", "week": "本周", "long": "长期"}


@tool
def set_topic_directive(text: str, scope: str = "week") -> dict:
    """设置选题风向：用户显式指令，下一批候选生成时优先遵循（优先级高于时节与默认策略）。

    正向风向（如"开学季写新开始"）→ 至少 40% 候选围绕展开；
    负面指令（"别再写失眠和毕业"开头）→ 解析出禁止关键词，含禁止词的候选直接剔除。
    触发红线（性别对立/PUA 等）的风向会被拒收。

    Args:
        text: 风向内容，一句话描述想写什么或不想看什么
        scope: 生效范围 today（当日）/ week（7 天）/ long（长期）
    """
    try:
        r = add_directive(text, scope=scope, source="agent")
        if not r["ok"]:
            return envelope("error", r["reason"])
        d = r["directive"]
        dup = "（已存在，未重复添加）" if r.get("duplicated") else ""
        exp = f"，{d['expires_at']} 到期" if d.get("expires_at") else "，长期有效"
        return envelope("ok", f"风向已设置（{_SCOPE_LABEL.get(scope, scope)}）{dup}{exp}：{d['text']}",
                        directive=d)
    except Exception as e:
        return tool_error(e)


@tool
def list_topic_directives() -> dict:
    """查看全部生效中的选题风向与禁止清单（生成新一批候选前应先调用本工具感知用户方向）。"""
    try:
        ds = list_directives()
        brief = []
        for d in ds:
            kind = "禁止" if d.get("negative") else "正向"
            hits = d.get("hits_last_batch")
            hit_txt = f"，上批命中 {hits['hits']}/{hits['total']}" if hits else ""
            brief.append({"id": d["id"], "scope": d["scope"], "kind": kind,
                          "text": d["text"], "expires_at": d.get("expires_at"),
                          "hits_last_batch": hits})
        rates = recent_hit_rates(2)
        return envelope("ok", f"{len(ds)} 条风向生效（正向 {sum(1 for d in ds if not d.get('negative'))}"
                        f" / 负面 {sum(1 for d in ds if d.get('negative'))}）",
                        directives=brief, banned_keywords=banned_keywords(),
                        recent_hit_rates=rates,
                        note="生成新一批选题时这些风向会自动注入 prompt，无需手动传递")
    except Exception as e:
        return tool_error(e)
