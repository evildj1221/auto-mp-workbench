#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
创作工作台 · cat_shop.py —— v4.0 门面垫片（facade shim）

v3 的 3230 行单文件已拆为 core/ llm/ pipeline/ 三层 + cli.py 入口（Phase 1）。
本文件保留两大职责，所有调用方一行不用改：
  1. `import cat_shop` 后全部旧符号照常可用（workbench/ 与 tests/ 依赖此约定）
  2. `python cat_shop.py <cmd>` CLI 照常可跑（taskrunner 以子进程调用；
     frozen 模式下 run_workbench.py 会 exec 本文件，故顶层保持幂等）

符号热更新注意：REDLINES 必须经 cat_shop.load_redlines() 重载（本模块已包装为
同步写回 core.redlines.REDLINES，redline_check 读的是后者）；
直接给 cat_shop.REDLINES 赋值不会影响红线检查。
"""
import sys
from pathlib import Path

# 三种运行模式统一锚定项目根：直接跑 / import / frozen exec（run_workbench._run_cli）
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# UTF-8 输出（CLI 子进程模式输出中文不乱码；v3 行为原样保留）
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 第三方依赖检查（v3 行为原样保留：缺依赖给安装提示而非裸 traceback）
try:
    import requests  # noqa: F401
    from dotenv import load_dotenv  # noqa: F401
    from jinja2 import Environment, DictLoader, select_autoescape  # noqa: F401
    import markdown as md_lib  # noqa: F401
except ImportError:
    print("ERROR: 缺依赖。跑：pip install requests jinja2 markdown python-dotenv")
    sys.exit(1)

# 加载 .env（须在 import llm.* 之前：provider 按环境变量取 key）
load_dotenv()

# ============================================================ core 层
from core.paths import (  # noqa: E402,F401
    SCRIPT_DIR, DATA_DIR, CANDIDATES_DIR, DRAFTS_DIR, BATCHES_DIR, WORKS_DIR,
    SELECTIONS_DIR, FEEDBACK_DIR, TEMPLATES_DIR, HISTORY_PATH, PROFILE_PATH,
    USER_FB_PATH, USER_PREF_PATH, QUOTES_PATH, REDLINES_PATH, REDLINE_LOG,
    RUBRIC_REVISIONS_PATH, slot_paths, batch_file_names, cover_image_path)
from core.storage import (  # noqa: E402,F401
    load_json, write_json, load_batches, save_batches, register_batch,
    ensure_batch_index, load_works, save_works, get_slot, create_slot,
    delete_slot)
from core.memory import (  # noqa: E402,F401
    add_quote, load_quotes, collect_feedback, parse_feedback_string,
    record_user_pref, load_excluded_series, dedup_feedback, load_top5,
    _analysis_path, load_user_feedback, load_all_feedback, load_preference,
    load_history)
from core import redlines as _core_redlines  # noqa: E402
from core.redlines import (  # noqa: E402,F401
    _REDLINES_BUILTIN, REDLINES, validate_redlines, _phrase_to_regex,
    redline_check)
from core.quality import (  # noqa: E402,F401
    SERIES_ANGLES, _BRIDGE_TERMS, _split_sentences, _sent_sim,
    _find_similar_sentences, _load_past_full, _load_past_drafts,
    count_chinese_chars, strip_component_markers)
from core.scoring import (  # noqa: E402,F401
    DIMENSIONS, DEFAULT_W, load_effective_weights,
    learn_calibration_from_feedback)
from core.render import (  # noqa: E402,F401
    COLORS, HTML_TEMPLATE, render_html, md_to_html_simple, escape)

# ============================================================ llm 层
from llm.client import (  # noqa: E402,F401
    _PROVIDER_SINGLETON, _get_llm_provider, call_llm)
from llm.provider import get_provider, call, log_cost, throttle, COST_LOG  # noqa: E402,F401
from llm.prompts import (  # noqa: E402,F401
    TOPIC_GENERATOR_PROMPT, SCORE_RUBRIC_PROMPT, WRITER_V1_PROMPT,
    OUTLINE_PROMPT, TITLE_GENERATOR_PROMPT, FEEDBACK_ANALYZER_PROMPT,
    SEMANTIC_REDLINE_PROMPT)

# ============================================================ pipeline 层
from pipeline.topics import (  # noqa: E402,F401
    _resolve_round, _default_timeframe, step_collect, step_score, cmd_choose,
    step_analyze, _drop_batch, step_new_batch, step_regenerate_batch)
from pipeline.writing import (  # noqa: E402,F401
    step_outline, step_write, _extract_titles, _gen_titles, step_titles,
    step_format, step_learn, redline_llm_semantic)
from pipeline.publish import (  # noqa: E402,F401
    cmd_setup, cmd_redline, cmd_redline_semantic,
    cmd_status, _lark_cli_json, step_sync)
from pipeline.flow import step_all, step_full, run_write  # noqa: E402,F401

# ============================================================ CLI 入口
from cli import main  # noqa: E402,F401


def load_redlines():
    """v3 兼容包装：重载红线规则并同步写回 core.redlines.REDLINES。

    redline_check 读取的是 core.redlines 模块全局，故热更新必须经本函数；
    直接给 cat_shop.REDLINES 赋值不会生效（v3 单文件时代可以，拆包后不行）。
    """
    global REDLINES
    REDLINES = _core_redlines.load_redlines()
    _core_redlines.REDLINES = REDLINES
    return REDLINES


if __name__ == "__main__":
    main()
