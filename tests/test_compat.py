#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_compat.py · cat_shop 门面符号兼容守卫（Phase 1 验收项，长期保留）。

v3 单文件时代的顶层符号 + v4 新增入口，经 `import cat_shop` 必须全部可解析。
任何 Phase 若需移除旧符号，必须先更新本清单并在 PR 说明中标注调用方迁移方案。

v4.1 变更（2026-08-29 · 红狐/飞书推送下线）：移除 9 个符号——
feishu_send / _hours_to_pub / render_top5_card / render_draft_card / _risk_label /
step_notify / step_draft / cmd_test_feishu / _pending_is_disabled
（飞书通道只保留云文档归档 step_sync）。
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cat_shop  # noqa: E402

V3_SYMBOLS = """DATA_DIR CANDIDATES_DIR DRAFTS_DIR BATCHES_DIR WORKS_DIR slot_paths
batch_file_names load_batches save_batches register_batch ensure_batch_index
load_works save_works get_slot create_slot delete_slot SELECTIONS_DIR
FEEDBACK_DIR TEMPLATES_DIR HISTORY_PATH PROFILE_PATH USER_FB_PATH USER_PREF_PATH
QUOTES_PATH add_quote load_quotes SERIES_ANGLES _BRIDGE_TERMS _split_sentences
_sent_sim _find_similar_sentences _load_past_full _load_past_drafts REDLINE_LOG
RUBRIC_REVISIONS_PATH DIMENSIONS DEFAULT_W COLORS HTML_TEMPLATE _REDLINES_BUILTIN
REDLINES_PATH validate_redlines load_redlines REDLINES _phrase_to_regex
_PROVIDER_SINGLETON _get_llm_provider call_llm
redline_check SEMANTIC_REDLINE_PROMPT redline_llm_semantic count_chinese_chars
strip_component_markers render_html md_to_html_simple escape _default_timeframe
collect_feedback parse_feedback_string record_user_pref load_excluded_series
dedup_feedback load_json write_json load_top5 _analysis_path load_user_feedback
load_all_feedback load_preference load_history load_effective_weights
_resolve_round step_collect step_score cmd_choose step_analyze
_drop_batch step_new_batch step_regenerate_batch OUTLINE_PROMPT step_outline
step_write _extract_titles _gen_titles step_titles step_format
learn_calibration_from_feedback step_learn cmd_setup cmd_redline
cmd_redline_semantic cmd_status _lark_cli_json step_sync
step_all step_full main TOPIC_GENERATOR_PROMPT SCORE_RUBRIC_PROMPT
WRITER_V1_PROMPT TITLE_GENERATOR_PROMPT FEEDBACK_ANALYZER_PROMPT""".split()

V4_NEW_SYMBOLS = ["run_write", "COST_LOG", "get_provider"]  # v4 新增：写稿编排入口 / 成本日志 / provider 工厂


def test_v3_symbols_resolve():
    missing = [s for s in V3_SYMBOLS if not hasattr(cat_shop, s)]
    assert not missing, f"门面缺 v3 符号（调用方会 NameError）: {missing}"


def test_v4_new_symbols_resolve():
    missing = [s for s in V4_NEW_SYMBOLS if not hasattr(cat_shop, s)]
    assert not missing, f"门面缺 v4 新符号: {missing}"


def test_redlines_hot_reload_writes_back():
    """app.py:760 的热更新依赖 load_redlines() 同步写回 core.redlines.REDLINES。"""
    import core.redlines as cr
    r = cat_shop.load_redlines()
    assert r is cr.REDLINES, "load_redlines 未写回 core.redlines.REDLINES（红线检查读不到新规则）"
    assert cat_shop.REDLINES is cr.REDLINES


def test_cli_entrypoint():
    """taskrunner 以 `python cat_shop.py <cmd>` 子进程调用，CLI 必须可独立运行。"""
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, str(root / "cat_shop.py"), "--help"],
                       capture_output=True, text=True, timeout=60,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, f"CLI 退出码 {r.returncode}: {r.stderr[-300:]}"
    assert "usage" in (r.stdout or "").lower()


def test_symbol_count_unchanged():
    """102 个 v3 符号一个不能少（防止清单被悄悄截断；v4.1 移除 9 个飞书/红狐符号，
    v5.1 移除 2 个死限流变量 _LLM_LAST_CALL/_LLM_LOCK——限流已收敛到 llm.provider）。"""
    assert len(V3_SYMBOLS) == 102, f"清单长度 {len(V3_SYMBOLS)} != 102"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  [PASS] {name}")
            except AssertionError as e:
                failures += 1
                print(f"  [FAIL] {name}: {e}")
    print(f"\n门面兼容守卫: {'ALL PASS' if failures == 0 else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)
