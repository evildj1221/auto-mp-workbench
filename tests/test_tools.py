#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_tools.py · 工具层单测（Phase 2 验收项）。

策略：
  - 沙箱日期 2099-01-01 隔离写入产物（teardown 全量清理，不碰真实数据）；
  - LLM 全部 mock（patch pipeline 模块的 call_llm 绑定），零真实成本；
  - 只读工具用存量数据（2026-08-11，11/12 步完成）验证真实行为；
  - 每个工具至少 1 个用例：正向 ok / 幂等 skipped / 失败 error 三类信封契约。
"""
import contextlib
import importlib
import json
import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.paths import (BATCHES_DIR, CANDIDATES_DIR, DRAFTS_DIR, FEEDBACK_DIR,
                        QUOTES_PATH, SELECTIONS_DIR, WORKS_DIR)
from core.storage import load_json, write_json

SANDBOX = "2099-01-01"        # 沙箱日期：所有写入落 2099 前缀文件，teardown 清理
REAL_DATE = "2026-08-11"      # 存量数据日（v1 已写，11/12 步完成）
TEST_QUOTE = "TESTQUOTE_unique_2099 测试金句：把情绪叠好收进抽屉"

CAND_TOPICS = [f"测试选题{i}号：深夜城市的第{i}盏灯" for i in range(1, 16)]


# ===== LLM mock =====
def _fake_collect(prompt, **kw):
    return {"candidates": [{"topic": t, "motif_series": "B", "angle": "测试角度"}
                           for t in CAND_TOPICS]}


def _fake_score(prompt, **kw):
    return {"results": [{"topic": t,
                         "scores": {"共鸣": 4, "时效": 3, "争议": 2, "独特": 4, "写作可行": 4},
                         "reasoning": "测试"} for t in CAND_TOPICS]}


_FILLER = "深夜的出租屋里台灯还亮着，她把今天没说出口的情绪叠好收进抽屉，明天还要早起赶地铁。"


def _fake_write(prompt, **kw):
    return "# 测试文稿：城市夜灯\n\n" + _FILLER * 25


def _fake_titles(prompt, **kw):
    return {"titles": [{"title": f"测试标题方案{i}：夜里的灯"} for i in range(1, 4)]}


@contextlib.contextmanager
def _mock_llm(module_name, fn):
    mod = importlib.import_module(module_name)
    old = getattr(mod, "call_llm")
    setattr(mod, "call_llm", fn)
    try:
        yield
    finally:
        setattr(mod, "call_llm", old)


# ===== 沙箱清理 =====
def _cleanup_sandbox():
    from core.paths import IMAGES_DIR
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    for f in IMAGES_DIR.glob(f"{SANDBOX}*"):
        f.unlink(missing_ok=True)
    for d in (CANDIDATES_DIR, DRAFTS_DIR, SELECTIONS_DIR, BATCHES_DIR, WORKS_DIR):
        for f in d.glob(f"{SANDBOX}*"):
            f.unlink(missing_ok=True)
    stats = load_json(FEEDBACK_DIR / "daily_stats.json", [])
    if isinstance(stats, list):
        kept = [s for s in stats if not (isinstance(s, dict) and s.get("date") == SANDBOX)]
        if len(kept) != len(stats):
            write_json(FEEDBACK_DIR / "daily_stats.json", kept)
    if QUOTES_PATH.exists():
        lines = [l for l in QUOTES_PATH.read_text(encoding="utf-8").splitlines()
                 if l and TEST_QUOTE not in l]
        QUOTES_PATH.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")
    for p in (FEEDBACK_DIR / "user_feedback.jsonl",):
        if p.exists():
            lines = [l for l in p.read_text(encoding="utf-8").splitlines()
                     if l and SANDBOX not in l]
            p.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


# ===== registry =====
def test_registry_inventory():
    from tools.registry import get_tools, get_tool_schemas, tool_names
    tools = get_tools()
    assert len(tools) == 23, f"工具数 {len(tools)} != 23"
    assert len(set(tool_names())) == 23, "工具名重复"
    assert "generate_cover_image" in tool_names(), "v4.2 头图工具应已注册"
    assert "set_topic_directive" in tool_names() and "list_topic_directives" in tool_names(), \
        "v4.3 风向标工具应已注册"
    schemas = get_tool_schemas()
    for s in schemas:
        assert s["name"] and s["description"] and isinstance(s["args"], dict), \
            f"schema 不完整: {s['name']}"
    assert "date" in schemas[0]["args"], \
        "首个工具（collect_candidates）应有 date 参数"


def test_run_tool_unknown_and_duration():
    from tools.registry import run_tool
    r = run_tool("__no_such_tool__")
    assert r["status"] == "error" and "未知工具" in r["message"]
    r2 = run_tool("get_pipeline_status", date=REAL_DATE)
    assert r2["status"] == "ok" and "duration_s" in r2, "run_tool 应附加 duration_s"


def test_tool_call_log_written():
    from tools.registry import _log_path, run_tool
    run_tool("get_pipeline_status", date=REAL_DATE)
    last = _log_path().read_text(encoding="utf-8").strip().split("\n")[-1]
    entry = json.loads(last)
    assert entry["tool"] == "get_pipeline_status" and entry["status"] == "ok"


# ===== state_tools（只读，用存量数据） =====
def test_get_pipeline_status():
    """自足沙箱：不依赖存量 data（开源克隆无 data/ 也要全绿）。"""
    from pathlib import Path as _P
    from core.paths import CANDIDATES_DIR, DRAFTS_DIR
    from tools.registry import run_tool
    d = "2099-02-05"
    (CANDIDATES_DIR / f"{d}.json").write_text('{"candidates": []}', encoding="utf-8")
    (DRAFTS_DIR / f"{d}_v1.md").write_text("# 沙箱\n\n正文。\n", encoding="utf-8")
    try:
        r = run_tool("get_pipeline_status", date=d)
        assert r["status"] == "ok"
        assert len(r["steps"]) == 10 and "next_step" in r
        assert r["steps"]["collect"] and r["steps"]["write_v1"], "沙箱已完成选题与 v1"
        assert r["completed"] == [k for k in r["steps"] if r["steps"][k]]
    finally:
        (CANDIDATES_DIR / f"{d}.json").unlink(missing_ok=True)
        (DRAFTS_DIR / f"{d}_v1.md").unlink(missing_ok=True)


@pytest.mark.skipif(not (__import__("pathlib").Path("data/drafts") / f"{REAL_DATE}_v1.md").exists(),
                    reason="需存量样例文稿（开源包不含 data/）")
def test_read_draft():
    from tools.registry import run_tool
    r = run_tool("read_draft", date=REAL_DATE, version="v1")
    assert r["status"] == "ok" and r["word_count"] > 0 and r["content"].startswith("#")
    r2 = run_tool("read_draft", date=SANDBOX)
    assert r2["status"] == "error" and "不存在" in r2["message"]


def test_list_candidates_missing():
    from tools.registry import run_tool
    r = run_tool("list_candidates", date=SANDBOX)
    assert r["status"] == "error", "沙箱日无候选应返回 error 而非异常"


# ===== 沙箱全链路（collect → score → 写决策 → outline → write → titles） =====
def test_sandbox_full_flow():
    from tools.registry import run_tool
    _cleanup_sandbox()
    try:
        with _mock_llm("pipeline.topics", _fake_collect):
            r = run_tool("collect_candidates", date=SANDBOX)
        assert r["status"] == "ok" and len(r["candidates"]) == 15, r["message"]
        # 幂等：重跑返回 skipped
        with _mock_llm("pipeline.topics", _fake_collect):
            r2 = run_tool("collect_candidates", date=SANDBOX)
        assert r2["status"] == "skipped", "产物已存在应 skipped"

        with _mock_llm("pipeline.topics", _fake_score):
            rs = run_tool("score_candidates", date=SANDBOX, runs=1)
        assert rs["status"] == "ok" and len(rs["top5"]) == 5, rs["message"]
        assert "needs_user_judgment" in rs, "评分结果应带 needs_user_judgment 标记"
        with _mock_llm("pipeline.topics", _fake_score):
            rs2 = run_tool("score_candidates", date=SANDBOX, runs=1)
        assert rs2["status"] == "skipped", "top5 已存在应 skipped"

        # 写用户决策（等同 cmd_choose 的产物）
        write_json(SELECTIONS_DIR / f"{SANDBOX}.json",
                   {"date": SANDBOX, "user_choice": 1, "user_note": "测试", "round": 1,
                    "topic": CAND_TOPICS[0], "cand_id": "test_c1"})

        with _mock_llm("pipeline.writing", lambda p, **kw: "一、开头\n二、中段\n三、结尾"):
            ro = run_tool("generate_outline", date=SANDBOX)
        assert ro["status"] == "ok", ro["message"]

        with _mock_llm("pipeline.writing", _fake_write):
            rw = run_tool("write_draft", date=SANDBOX)
        assert rw["status"] == "ok" and rw["word_count"] >= 900, rw["message"]
        assert (DRAFTS_DIR / f"{SANDBOX}_v1.md").exists()

        with _mock_llm("pipeline.writing", _fake_titles):
            rt = run_tool("generate_titles", date=SANDBOX)
        assert rt["status"] == "ok" and len(rt["titles"]) == 3, rt["message"]

        # 全链路后状态感知应能看到关键步骤完成
        rst = run_tool("get_pipeline_status", date=SANDBOX)
        assert rst["steps"]["collect"] and rst["steps"]["score"] and rst["steps"]["write_v1"]
    finally:
        _cleanup_sandbox()


# ===== write_tools =====
def test_edit_draft():
    from tools.registry import run_tool
    _cleanup_sandbox()
    try:
        short = "# 测试短稿\n\n" + "她把情绪收进抽屉。" * 3
        r = run_tool("edit_draft", date=SANDBOX, content=short)
        assert r["status"] == "ok" and r["word_count"] < 900
        assert (DRAFTS_DIR / f"{SANDBOX}_v1.md").exists()
        bad = run_tool("edit_draft", date=SANDBOX, content="没有标题头的正文")
        assert bad["status"] == "error" and "# 标题" in bad["message"]
    finally:
        _cleanup_sandbox()


# ===== quality_tools =====
def test_check_redline():
    from tools.registry import run_tool
    bad = run_tool("check_redline", text="综上所述，你应该明白，同龄人已经买基金稳赚了，不转不是中国人。",
                   date=SANDBOX, step_name="test")
    assert bad["status"] == "error" and bad["should_block"] and bad["hits"], \
        "违规样例应触发阻断"
    clean = run_tool("check_redline", text="深夜的出租屋里，台灯还亮着，这是一段平静的叙述。",
                     date=SANDBOX, step_name="test")
    assert clean["status"] == "ok" and not clean["should_block"]


def test_check_wordcount():
    from tools.registry import run_tool
    _cleanup_sandbox()
    try:
        r = run_tool("check_wordcount", date=SANDBOX)
        assert r["status"] == "error" and "不存在" in r["message"]
        (DRAFTS_DIR / f"{SANDBOX}_v1.md").write_text(
            "# 短稿\n\n" + "她把情绪收进抽屉。" * 30, encoding="utf-8")  # ~210 汉字
        r2 = run_tool("check_wordcount", date=SANDBOX)
        assert r2["status"] == "error" and r2["verdict"] == "字数不足"
    finally:
        _cleanup_sandbox()


def test_check_similarity():
    from tools.registry import run_tool
    r = run_tool("check_similarity", date=SANDBOX,
                 text="这段文字与任何历史稿都完全不同，只是用来验证工具返回契约。")
    assert r["status"] == "ok" and "similar_sentences" in r


# ===== publish_tools（外部系统全部降级/前置校验路径，零外呼） =====
def test_publish_prerequisites():
    from tools.registry import run_tool
    _cleanup_sandbox()
    try:
        rs = run_tool("sync_feishu_doc", date=SANDBOX)
        assert rs["status"] in ("error", "skipped") and rs["message"]
        re_ = run_tool("export_mp_html", date=SANDBOX)
        assert re_["status"] == "error" and "前置" in re_["message"]
        rp = run_tool("push_wechat_draft", date=SANDBOX)
        assert rp["status"] == "error" and "未排版" in rp["message"]
    finally:
        _cleanup_sandbox()


# ===== memory_tools =====
def test_record_feedback_requires_top5():
    from tools.registry import run_tool
    _cleanup_sandbox()
    try:
        r = run_tool("record_feedback", date=SANDBOX, feedback="1:5 好")
        assert r["status"] == "error" and "Top5" in r["message"]
    finally:
        _cleanup_sandbox()


def test_record_feedback_and_learn():
    from tools.registry import run_tool
    _cleanup_sandbox()
    try:
        # 造 top5 → 登记 → 学习（daily）
        write_json(DRAFTS_DIR / f"{SANDBOX}_top5.json",
                   {"results": [{"rank": 1, "cand_id": "c1", "topic": CAND_TOPICS[0],
                                 "total": 3.5, "avg_scores": {}, "ai_reasoning": "t"}]})
        r = run_tool("record_feedback", date=SANDBOX, feedback="1:5 测试好评")
        assert r["status"] == "ok" and r["feedback_count"] >= 1, r["message"]
        rl = run_tool("learn_preferences", date=SANDBOX, mode="daily")
        assert rl["status"] == "ok", rl["message"]
    finally:
        _cleanup_sandbox()


def test_add_quote_idempotent():
    from tools.registry import run_tool
    try:
        r1 = run_tool("add_quote", text=TEST_QUOTE, source="test")
        assert r1["status"] == "ok", r1["message"]
        r2 = run_tool("add_quote", text=TEST_QUOTE, source="test")
        assert r2["status"] == "skipped", "重复金句应 skipped（按文本幂等）"
    finally:
        _cleanup_sandbox()


# ===== v4.2 M1 头图（每篇唯一配图 · 文中图已取消） =====
def test_generate_cover_image_degrades_without_key():
    """未配置 IMAGE_API_KEY → skipped 信封不抛异常（降级不阻断写稿与发布链路）。"""
    import os
    from tools.image_tools import generate_cover_image
    old = os.environ.pop("IMAGE_API_KEY", None)
    try:
        r = generate_cover_image.invoke({"date": SANDBOX})
        assert r["status"] == "skipped" and "IMAGE_API_KEY" in r["message"], r
    finally:
        if old:
            os.environ["IMAGE_API_KEY"] = old


def test_generate_cover_image_ok_and_report():
    """已配置 → 走 pipeline 生成（mock）→ ok 信封带 cover_path；报告可对照。"""
    import os
    import tools.image_tools as it
    from core.paths import IMAGES_DIR, cover_image_path

    old_key = os.environ.get("IMAGE_API_KEY")
    old_gen = it.generate_cover
    os.environ["IMAGE_API_KEY"] = "test-key"
    it.generate_cover = lambda date, slot=None, force=False: {
        "path": str(cover_image_path(date, slot))}
    try:
        r = it.generate_cover_image.invoke({"date": SANDBOX})
        assert r["status"] == "ok" and r["cover_path"].endswith("_cover.png"), r
        assert str(IMAGES_DIR) in r["cover_path"], "头图应落 data/images/"
    finally:
        it.generate_cover = old_gen
        if old_key is None:
            os.environ.pop("IMAGE_API_KEY", None)
        else:
            os.environ["IMAGE_API_KEY"] = old_key


def test_exporter_cover_render_and_inline_image_dropped():
    """规则渲染：头图紧跟篇首 ✦✦✦ 后（data-cover 标记）；:::image 静默丢弃；
    有/无头图 17 项校验全过（降级不阻断）。"""
    from core import exporter
    md = ("# 头图渲染测试稿\n\n" + "深夜的窗台有一盏灯，她把今天叠好收进抽屉。\n\n"
          ":::quote\n金句一：慢一点也是往前走。\n:::\n\n"
          "楼下便利店的灯还亮着，玻璃上蒙着雾气。\n\n"
          ":::quote\n金句二：情绪不需要立刻收拾好。\n:::\n\n"
          ":::image 夜色窗台与一杯温水\n\n"
          ":::action\n给今晚的自己写一句话。\n:::")
    html = exporter.render_from_markers(md, series="B",
                                        cover_url="file:///tmp/cover.png")
    assert '<img src="file:///tmp/cover.png"' in html and 'data-cover="1"' in html
    assert html.find("✦ ✦ ✦") < html.find('<img src="file:///tmp/cover.png"'), \
        "头图必须在篇首分隔符之后"
    assert "此处建议配图" not in html, "v4.2 文中图占位不应再渲染"
    checks = exporter.validate_html(html)
    assert all(c["ok"] for c in checks), [c["name"] for c in checks if not c["ok"]]
    # 无头图（未生成/降级）：不插 img，校验同样全过
    html2 = exporter.render_from_markers(md, series="B")
    assert "<img" not in html2
    assert all(c["ok"] for c in exporter.validate_html(html2))


def test_strip_component_markers_drops_image():
    """strip_component_markers：:::image 行整体移除（不再转占位提示），组件内容保留。"""
    from core.quality import strip_component_markers
    out = strip_component_markers(
        ":::quote\n金句本体\n:::\n\n:::image 夜色窗台\n\n正文一段话。")
    assert ":::image" not in out and "配图" not in out
    assert "金句本体" in out and "正文一段话。" in out


def test_wechat_cover_swap_and_strip():
    """swap_cover_url：正文头图 src 换微信 URL + data-cover 剥除 + 封面素材 ID；
    strip_cover_img 去残留头图（网络调用全部 mock，零外呼）。"""
    from core import wechat
    html = ('<p style="x">a</p><img src="file:///d:/x.png" alt="头图" '
            'data-cover="1" style="y"><p>b</p>')
    old_up, old_add = wechat.upload_img_for_article, wechat.add_material_image
    wechat.upload_img_for_article = lambda b, f="c": ("https://mmbiz.qpic.cn/u.png", None)
    wechat.add_material_image = lambda b, f="c": ("MID123", "https://mmbiz.qpic.cn/m.png", None)
    try:
        new_html, thumb, note, err = wechat.swap_cover_url(html, b"\x89PNG")
        assert err is None and thumb == "MID123" and note == "生成头图"
        assert 'src="https://mmbiz.qpic.cn/u.png"' in new_html
        assert 'data-cover' not in new_html, "推送稿应剥掉内部标记"
        assert wechat.strip_cover_img(html).count("<img") == 0
    finally:
        wechat.upload_img_for_article, wechat.add_material_image = old_up, old_add


def test_slot_paths_alignment():
    """槽位对齐（修：Agent 不带 slot 曾写旧命名，页面读 w 槽位互不可见）：
    ①有 w 产物→活动槽位 ②选题后新写→活动槽位 ③存量旧命名日→保持旧命名。"""
    from core.paths import (DRAFTS_DIR, SELECTIONS_DIR, cover_image_path,
                            slot_paths)
    import json as _json
    from pathlib import Path as _P
    # 场景③：已选题但只有旧命名产物（存量数据形态）→ 保持旧命名
    d3 = "2099-02-03"
    (SELECTIONS_DIR / f"{d3}.json").write_text(
        _json.dumps({"active_slot": "w01"}), encoding="utf-8")
    (DRAFTS_DIR / f"{d3}_v1.md").write_text("# 旧命名存量\n", encoding="utf-8")
    try:
        assert slot_paths(d3, None)["v1"].name == f"{d3}_v1.md"
    finally:
        (SELECTIONS_DIR / f"{d3}.json").unlink(missing_ok=True)
        (DRAFTS_DIR / f"{d3}_v1.md").unlink(missing_ok=True)
    # 场景①：已有 w 槽位产物 → 活动槽位
    d1 = "2099-02-02"
    (SELECTIONS_DIR / f"{d1}.json").write_text(
        _json.dumps({"active_slot": "w01"}), encoding="utf-8")
    (DRAFTS_DIR / f"{d1}_w01_v1.md").write_text("# 槽位稿\n", encoding="utf-8")
    try:
        assert slot_paths(d1, None)["v1"].name == f"{d1}_w01_v1.md"
        assert cover_image_path(d1, None).name == f"{d1}_w01_cover.png"
    finally:
        (SELECTIONS_DIR / f"{d1}.json").unlink(missing_ok=True)
        (DRAFTS_DIR / f"{d1}_w01_v1.md").unlink(missing_ok=True)
    # 场景②：沙箱日（选题后、尚无任何文稿）
    import json as _json
    from pathlib import Path as _P
    from core.paths import SELECTIONS_DIR
    _P.mkdir(SELECTIONS_DIR, exist_ok=True)
    (SELECTIONS_DIR / "2099-01-09.json").write_text(
        _json.dumps({"active_slot": "w01"}), encoding="utf-8")
    try:
        assert slot_paths("2099-01-09", None)["v1"].name == "2099-01-09_w01_v1.md"
        assert slot_paths("2099-01-09", "w02")["v1"].name == "2099-01-09_w02_v1.md"
    finally:
        (SELECTIONS_DIR / "2099-01-09.json").unlink(missing_ok=True)


def test_cover_prompt_v3_conformance():
    """视觉设计系统 v3：六要素锚点齐全 + 避坑规则（无 palette/hex/百分比）。"""
    from llm.prompts.cover import (COVER_SCENE_PROMPT, SERIES_MOOD_HINT,
                                   build_cover_prompt)
    p = build_cover_prompt("A quiet 2 AM kitchen with warm lamp light on a wooden table",
                           "sits on the windowsill watching the night city", "night")
    for must in ("治愈系插画 inspired", "hand-drawn ink outlines", "watercolor paper",
                 "soft watercolor color blocks", "no human face",
                 "Painted in soft healing tones", "fluffy white cat",
                 "red collar", "wooden name tag", "no legible letters",
                 "abstract blurred ink strokes", "clean full-bleed edges",
                 "no color swatches", "2.35:1", "warm street lamps"):
        assert must in p, f"缺少 v3 必备元素: {must}"
    for forbidden in ("Color palette", "percent", "#1A2B4A", "#E8B66E",
                      "decorative borders", "accent"):
        assert forbidden not in p, f"违反 v3 避坑规则: {forbidden}"
    w = build_cover_prompt("An early-autumn balcony at dusk with a cardigan on the line",
                           "sits on a wooden stool looking up at the cardigan", "warm")
    assert "nostalgic" in w and "light gray" in w  # 暖橙氛围与配色
    # 系列口诀：A/E 冷蓝，B/F 暖橙
    assert SERIES_MOOD_HINT["A"] == "night" and SERIES_MOOD_HINT["E"] == "night"
    assert SERIES_MOOD_HINT["B"] == "warm" and SERIES_MOOD_HINT["F"] == "warm"
    # 场景提炼 prompt 含口诀与模板库
    assert "口诀" in COVER_SCENE_PROMPT and "T11" in COVER_SCENE_PROMPT
    assert "景别" in COVER_SCENE_PROMPT and "closeup" in COVER_SCENE_PROMPT


if __name__ == "__main__":
    _cleanup_sandbox()
    failures = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  [PASS] {name}")
            except AssertionError as e:
                failures.append(name)
                print(f"  [FAIL] {name}: {e}")
            except Exception as e:
                failures.append(name)
                print(f"  [ERROR] {name}: {type(e).__name__}: {e}")
    _cleanup_sandbox()
    total = sum(1 for n in globals() if n.startswith("test_") and callable(globals()[n]))
    print(f"\n工具层单测: {total - len(failures)}/{total} PASS")
    sys.exit(1 if failures else 0)
