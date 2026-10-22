#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/paths.py · 全部路径常量 + slot_paths/batch_file_names。

自 cat_shop.py 77-109 / 239-250 / 480-481 / 652 行原样迁出，行为不变。
frozen 模式（PyInstaller exe）锚定 exe 所在目录，源码模式锚定项目根。
"""
import json
import sys
from pathlib import Path

# ===== 路径 =====
# frozen 模式（PyInstaller exe）用 exe 所在目录；否则用源码目录
if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).resolve().parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent.parent  # core/ 下一级 → 锚定项目根
DATA_DIR = SCRIPT_DIR / "data"
CANDIDATES_DIR = DATA_DIR / "candidates"
DRAFTS_DIR = DATA_DIR / "drafts"
BATCHES_DIR = DATA_DIR / "batches"   # 批次索引（多批选题）
WORKS_DIR = DATA_DIR / "works"       # 文稿槽位索引（多份文稿独立共存）


def active_slot_of(date):
    """当日活动槽位（selections.active_slot；无决策返回 ""）。
    供路径层统一回退：Agent 工具/CLI 不带 slot 时曾写无槽位旧命名，
    而页面读活动槽位——两套文件互不可见（2026-09-29 对话跑全流程后
    写稿/导出页看不到产物的根因）。"""
    try:
        data = json.loads((SELECTIONS_DIR / f"{date}.json").read_text(encoding="utf-8"))
        return str(data.get("active_slot") or "")
    except Exception:
        return ""


def _default_slot(date):
    """空 slot 的存在感知回退：
    - 当日已有 w 槽位产物 → 活动槽位（现代流程）
    - 无 w 产物且旧命名 v1 也不存在（选题后新写） → 活动槽位（新产物落页面读取的位置）
    - 只有旧命名产物的存量日期 → 保持旧命名（老数据可读，不迁移）"""
    act = active_slot_of(date)
    if not act:
        return ""
    if any(DRAFTS_DIR.glob(f"{date}_w*")):
        return act
    if not (DRAFTS_DIR / f"{date}_v1.md").exists():
        return act
    return ""


def slot_paths(date, slot=None):
    """槽位化文件路径。slot 为空时按 _default_slot 回退（见其注释）。
    slot 形如 "w01"。返回 dict：v1/v2/titles/mp/mp_report/html。"""
    if not slot:
        slot = _default_slot(date)
    suf = f"_{slot}" if slot else ""  # slot 形如 "w01" → {date}_w01_*（勿再加 w）
    return {
        "v1": DRAFTS_DIR / f"{date}{suf}_v1.md",
        "v2": DRAFTS_DIR / f"{date}{suf}_v2.md",
        "titles": DRAFTS_DIR / f"{date}{suf}_titles.json",
        "mp": DRAFTS_DIR / f"{date}{suf}_mp.html",
        "mp_report": DRAFTS_DIR / f"{date}{suf}_mp_report.json",
        "html": DRAFTS_DIR / f"{date}{suf}.html",
        "mp_history": DRAFTS_DIR / f"{date}{suf}_mp_history",
    }


def batch_file_names(date, round_n):
    """某批次的候选/Top5 文件（round 1 兼容旧命名）。"""
    if round_n <= 1:
        return CANDIDATES_DIR / f"{date}.json", DRAFTS_DIR / f"{date}_top5.json"
    return CANDIDATES_DIR / f"{date}_r{round_n}.json", DRAFTS_DIR / f"{date}_r{round_n}_top5.json"

SELECTIONS_DIR = DATA_DIR / "selections"
FEEDBACK_DIR = DATA_DIR / "feedback"
IMAGES_DIR = DATA_DIR / "images"     # 头图产物（v4.2 M1，每篇一张）
STYLE_REFS_DIR = DATA_DIR / "style_refs"  # 出图风格参考库（实战验证头图，生成时按调性选同调性参考传入）
UPLOADS_DIR = DATA_DIR / "uploads"   # 用户素材上传（P3：图片作生图参考 / 文稿作写作素材）
TEMPLATES_DIR = SCRIPT_DIR / "templates"

for d in [CANDIDATES_DIR, DRAFTS_DIR, SELECTIONS_DIR, FEEDBACK_DIR, IMAGES_DIR, STYLE_REFS_DIR,
          UPLOADS_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def cover_image_path(date, slot=None):
    """头图路径：data/images/{date}_{slot}_cover.png（slot 为空时按 _default_slot
    回退，与 slot_paths 同一对齐，保证 Agent 生成的头图就是页面/推送用的那张）。"""
    if not slot:
        slot = _default_slot(date)
    suf = f"_{slot}" if slot else ""
    return IMAGES_DIR / f"{date}{suf}_cover.png"

HISTORY_PATH = SELECTIONS_DIR / "history.jsonl"
TOPIC_DIRECTIVES_PATH = SELECTIONS_DIR / "topic_directives.jsonl"      # M3 选题风向标
TOPIC_DIRECTIVE_HITS_PATH = SELECTIONS_DIR / "topic_directive_hits.jsonl"  # M3 风向命中流水
PROFILE_PATH = FEEDBACK_DIR / "preference_profile.json"
USER_FB_PATH = FEEDBACK_DIR / "user_feedback.jsonl"
USER_PREF_PATH = FEEDBACK_DIR / "user_pref.jsonl"  # P-02 收藏/排除偏好信号
QUOTES_PATH = DATA_DIR / "quotes.jsonl"  # 金句库（写稿风格参考 + 高赞沉淀）

REDLINE_LOG = FEEDBACK_DIR / "redline_log.json"
RUBRIC_REVISIONS_PATH = FEEDBACK_DIR / "rubric_revisions.jsonl"

REDLINES_PATH = SCRIPT_DIR / "redlines.json"

# ===== 商业化阶段一：CCP 客户内容档案（core/ccp） =====
CCP_DIR = DATA_DIR / "ccp"
CCP_PROFILE_PATH = CCP_DIR / "profile.json"
CCP_HISTORY_DIR = CCP_DIR / "history"
CCP_AUDIT_PATH = CCP_DIR / "audit.jsonl"

# ===== 反馈闭环采集的数据源（core/ccp/feedback.py 复用） =====
ARTICLE_STATS_PATH = FEEDBACK_DIR / "article_stats.jsonl"  # P0-1 真实发布数据（读者维回流）
TYPOGRAPHY_SETTINGS_PATH = DATA_DIR / "typography" / "settings.json"  # 排版激活态（排版维）

for d in [CANDIDATES_DIR, DRAFTS_DIR, SELECTIONS_DIR, FEEDBACK_DIR, IMAGES_DIR, STYLE_REFS_DIR,
          CCP_DIR]:
    d.mkdir(parents=True, exist_ok=True)
