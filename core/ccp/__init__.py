#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/ccp · 客户内容档案（Client Content Profile）——商业化方案 §6.3 差异化核心。

四维结构（风格/选题/排版/读者）+ 留痕可撤销 + 版本回溯 + 软约束注入 + 冷启动默认。
模块分工：
  model    —— 结构与默认值、场景默认画像、结构完整性校验
  profile  —— 读写管理、版本快照/回溯、注入上下文生成、聚合既有资产
  seed     —— 初始化投喂（启动引导 + 问卷 + 历史文稿确定性风格提取，幂等）
  feedback —— 反馈闭环（打分/收藏排除/排版采纳/发布回流/复盘 → 写回 + 留痕）

与现有系统复用：路径在 core.paths，持久化用 core.storage，画像/金句/反馈/排版
激活态全部直接读取既有数据文件，不另起数据源。
"""
from core.ccp.model import (CCP_SCHEMA_VERSION, SCENE_DEFAULT, DIMS,  # noqa: F401
                            INJECTION_DEFAULTS, empty_profile,
                            scene_default_profile, validate_profile,
                            ProfileError)
from core.ccp.profile import ClientProfile, get_profile  # noqa: F401
from core.ccp.seed import (ensure_seeded, ingest_questionnaire,  # noqa: F401
                           ingest_article, ingest_articles,
                           seed_from_sources, verify_seed)
from core.ccp.feedback import (harvest, revert_lead,  # noqa: F401
                               collect_feedback_into_ccp, MIN_TOPIC_FEEDBACK)


def ensure_startup(client_id: str = "slowpost"):
    """系统启动钩子：冷启动建档 + 吸收既有资产（旧画像/排版激活态）。
    幂等：仅在新建或吸收产生实际变更时才落盘，避免每次启动累进版本。返回 profile。"""
    from core.paths import CCP_PROFILE_PATH, TYPOGRAPHY_SETTINGS_PATH
    from core.memory import load_preference
    from core.storage import load_json
    prof = get_profile(client_id=client_id)
    fresh = not CCP_PROFILE_PATH.exists()
    ensure_seeded(prof)
    changed_abs = 0
    changed_abs += prof.absorb_preference_profile(load_preference())
    changed_abs += prof.absorb_layout_state(load_json(TYPOGRAPHY_SETTINGS_PATH) or {})
    if fresh or changed_abs:
        prof.save(reason="startup absorb" if changed_abs else "cold start ensure")
    return prof