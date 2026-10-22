#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/app/schemas.py · 请求体 Pydantic 模型（响应统一 {ok, data|error} 信封）。

约定：查询参数用 Query 直接声明；本文件只收 POST/PUT 的 JSON body 模型，
命名 <动作>Req。字段尽量宽松（原 Flask 表单语义），服务端再做业务校验。
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class DirectiveReq(BaseModel):
    date: Optional[str] = None
    text: str = ""
    scope: str = "week"


class DirectiveDeleteReq(BaseModel):
    date: Optional[str] = None
    id: str = ""


class ChooseReq(BaseModel):
    date: Optional[str] = None
    choice: int
    round: int = 1
    note: str = ""


class SlotDeleteReq(BaseModel):
    date: Optional[str] = None
    slot: str = ""
    back: str = "topics"


class PrefReq(BaseModel):
    date: Optional[str] = None
    rank: int
    round: int = 1
    action: Literal["like", "exclude"]


class FeedbackQuickReq(BaseModel):
    date: Optional[str] = None
    rank: str
    score: str
    note: str = ""


class EditDraftReq(BaseModel):
    date: Optional[str] = None
    content: str = ""
    version: Literal["v1", "v2"] = "v1"
    slot: str = ""


class TitlesChooseReq(BaseModel):
    date: Optional[str] = None
    title: str = ""
    slot: str = ""


class ReviewDoneReq(BaseModel):
    date: Optional[str] = None
    slot: str = ""


class PublishRegisterReq(BaseModel):
    date: Optional[str] = None
    slot: str = ""
    checklist: dict = Field(default_factory=dict)


class QuoteAddReq(BaseModel):
    date: Optional[str] = None
    text: str = ""
    slot: str = ""


class OneClickReq(BaseModel):
    date: Optional[str] = None
    slot: str = ""


class UploadDeleteReq(BaseModel):
    date: Optional[str] = None
    slot: str = ""
    saved_as: str = ""


class RunCmdReq(BaseModel):
    date: Optional[str] = None
    cmd: str
    back: str = "dashboard"
    force: bool = False
    force_sync: bool = False
    v2_angle: str = ""
    force_v2: bool = False
    slot: str = ""
    use_outline: bool = False
    apply_rubric: bool = False
    mode: str = "daily"
    round: str = ""


class OutlineReq(BaseModel):
    date: Optional[str] = None
    slot: str = ""


class ReviewSaveReq(BaseModel):
    date: Optional[str] = None
    reads: int = 0
    likes: int = 0
    shares: int = 0
    fans_delta: int = 0
    fans_total: int = 0
    note: str = ""


class ExportRunReq(BaseModel):
    date: Optional[str] = None
    slot: str = ""
    version: Literal["v1", "v2"] = "v1"
    with_cover: bool = True   # False=无配图渲染（纯文字排版，头图/文中图都不加）


class RedlineCheckReq(BaseModel):
    text: str = ""
    mode: Literal["phrase", "semantic"] = "phrase"


class FeishuToggleReq(BaseModel):
    feishu_sync: bool = True


class WorkbenchSettingsReq(BaseModel):
    """工作台设置（data/settings.json）。全部可选，None 字段不更新。"""
    autonomy: Optional[Literal["assisted", "semi", "auto"]] = None
    max_steps: Optional[int] = None
    max_cost_cny: Optional[float] = None
    wechat_ip_whitelist: Optional[str] = None  # 逗号/换行分隔，与公众号后台白名单同步


class TypographyActivateReq(BaseModel):
    theme_id: str = ""
    src: str = "sample"


class TypographyOverrideReq(BaseModel):
    theme_id: str = ""
    src: str = "sample"
    key: str = ""
    value: str = ""


class TypographyResetReq(BaseModel):
    theme_id: str = ""
    src: str = "sample"


class TypographySaveReq(BaseModel):
    name: str = ""
    base_id: str = ""
    src: str = "sample"


class TypographyDeleteReq(BaseModel):
    theme_id: str = ""
    src: str = "sample"


class AgentStartReq(BaseModel):
    date: Optional[str] = None
    goal: str = ""


class AgentApproveReq(BaseModel):
    date: Optional[str] = None
    choice: int = 0
    approved: Optional[bool] = None
    note: str = ""


class AgentBudgetReq(BaseModel):
    date: Optional[str] = None
    extra_steps: int = 10
    extra_cost: float = 1.0
    note: str = ""


class ChatSendReq(BaseModel):
    """中控台对话（P1 轻聊天；P2 起确认卡走 /api/chat/confirm）。"""
    date: Optional[str] = None
    text: str = ""


class ChatConfirmReq(BaseModel):
    """对话里的动作确认卡：批准执行 / 取消。choice 用于 Top5 点选类动作。"""
    date: Optional[str] = None
    msg_id: str
    approved: bool = True
    choice: int = 0
    note: str = ""


class ChatChooseReq(BaseModel):
    """对话里 Top5 卡点选选题。msg_id 用于幂等（同卡重复点选不重复执行）。"""
    date: Optional[str] = None
    choice: int
    round: int = 1
    msg_id: str = ""
