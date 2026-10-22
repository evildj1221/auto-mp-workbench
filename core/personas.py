#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/personas.py · 中控台猫咪编辑部注册表（P1 中控台方案）。

设计约定（docs/2026-09-26_中控台对话式工作台设计方案.md §二）：
- 子 agent = 同一批流水线工具的拟人化前台，不新建独立 LangGraph 图；
- 每位 = 花名 + 职责 + 主题色 + 关键词路由表 + 工具域；
- 关键词命中优先（零成本），LLM 路由仅作兜底（core/chat_router.py）。
"""
import re

# 花名注册表：id → 形象与职责（前端同事栏/气泡渲染用）
PERSONAS = {
    "chief":     {"name": "主编", "role": "首席助手", "color": "#e0a03c",
                  "duty": "统筹协调，接住问候、闲聊与进度"},
    "topic":     {"name": "选题官", "role": "选题编辑", "color": "#4a90d9",
                  "duty": "选题方向、候选、Top5、评分、时节热点"},
    "writer":    {"name": "撰稿人", "role": "撰稿人", "color": "#4caf88",
                  "duty": "写稿、大纲、标题、改稿润色"},
    "reviewer":  {"name": "审校", "role": "审校", "color": "#d96a5a",
                  "duty": "红线违禁、字数门槛、相似句把关"},
    "artist":    {"name": "美编", "role": "美编", "color": "#a86fd6",
                  "duty": "封面头图与配图"},
    "publisher": {"name": "发行", "role": "发行", "color": "#5a6a85",
                  "duty": "导出排版、草稿箱、飞书归档、发布"},
    "archivist": {"name": "档案员", "role": "档案员", "color": "#8b98ad",
                  "duty": "复盘数据、历史经验、金句库"},
}

# 关键词路由表（顺序即优先级；命中即停）。全部小写匹配。
_KEYWORDS = {
    "reviewer": ["红线", "违禁", "违规", "审校", "审核", "相似句", "重复率", "质检", "把关"],
    "artist":   ["封面", "头图", "配图", "画一张", "插图", "重画", "换一张"],
    "archivist": ["复盘", "阅读量", "在看", "分享数据", "金句", "经验", "教训", "归档数据", "往期"],
    "publisher": ["发布", "推送", "草稿箱", "导出", "排版", "飞书", "群发", "公众号后台"],
    "writer":   ["写稿", "写一篇", "写 v1", "写v1", "大纲", "开头", "结尾", "标题", "改稿",
                 "润色", "扩写", "重写", "稿子", "正文", "开写", "写吧", "马上写", "直接写",
                 "现在写", "就写", "交稿", "写完", "两篇", "三篇", "一篇"],
    "topic":    ["选题", "候选", "top5", "TOP5", "Top5", "评分", "风向", "热点", "写什么",
                 "选题方向", "批候选", "一批"],
    "chief":    ["进度", "状态", "怎么样了", "到哪了", "安排", "分工", "在干嘛", "在吗"],
}

_MENTION_RE = re.compile(r"@(主编|选题官|撰稿人|审校|美编|发行|档案员)")


def persona_by_name(name: str) -> str | None:
    """花名 → persona id（@点名路由用）。"""
    for pid, p in PERSONAS.items():
        if p["name"] == name:
            return pid
    return None


def route_keyword(text: str) -> str | None:
    """关键词路由：返回 persona id；未命中返回 None（交给 LLM 兜底/chief）。"""
    t = (text or "").lower()
    if not t:
        return None
    # @点名最高优先
    m = _MENTION_RE.search(text or "")
    if m:
        pid = persona_by_name(m.group(1))
        if pid:
            return pid
    for pid, words in _KEYWORDS.items():
        for w in words:
            if w.lower() in t:
                return pid
    return None


def roster() -> list[dict]:
    """同事栏数据（稳定顺序：主编在前，其余按注册序）。"""
    order = ["chief", "topic", "writer", "reviewer", "artist", "publisher", "archivist"]
    return [{"id": pid, **PERSONAS[pid]} for pid in order]
