#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_ccp.py · 商业化阶段一 客户内容档案（CCP）单测。

覆盖：
  模型     model: 场景默认/空档结构校验 · 坏档报错点
  档案     profile: 冷启动 · 版本快照/回溯 · 坏档留档重建 · 注入上下文生成与上限
  投喂     seed: 问卷覆盖/合并 · 文稿确定性提取·内容哈希幂等 · 5 篇上限 ·
           ensure_seeded 幂等不累进版本
  闭环     feedback: 打分/收藏排除→选题权重(阈值触发) · 排除系列 · 排版采纳 ·
           发布回流读者维 · harvest 光标幂等 · 撤销留痕 · dry-run

隔离：全部用 tempfile 临时目录；反馈数据源路径通过 monkeypatch core.paths 动态替换
（feedback 用 _SRC 动态读取，这正是为可测性设计的单点）。不触碰 data/ 真实数据。
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import paths as _paths  # noqa: E402
import core.ccp as ccp  # noqa: E402
from core.ccp import (get_profile, model, profile as prof_mod, seed as seed_mod,  # noqa: E402
                      feedback as fb_mod)

SAMPLE = ("今天又是在地铁上写完的倒数第二篇。\n\n"
          "车厢摇摇晃晃，像一座慢速的摇篮。我抬头看见对面那个加班到深夜的姑娘，"
          "正对着手机屏幕打字，打着打着就笑了。\n\n"
          "我想，有些话，大概只有在深夜才说得出口。\n\n"
          "所以今晚，我给你回一封信。愿你明天，也能给自己留一盏灯。")


def _tmp_ctx():
    """返回 (tempdir, tmp paths dict, restore_fn)。"""
    td = Path(tempfile.mkdtemp(prefix="ccp_test_"))
    prof_path = td / "profile.json"
    hist = td / "history"
    src = {
        "USER_FB_PATH": td / "user_feedback.jsonl",
        "USER_PREF_PATH": td / "user_pref.jsonl",
        "QUOTES_PATH": td / "quotes.jsonl",
        "TYPOGRAPHY_SETTINGS_PATH": td / "typography_settings.json",
        "ARTICLE_STATS_PATH": td / "article_stats.jsonl",
    }
    saved = {k: getattr(_paths, k) for k in src}

    def restore():
        for k, v in saved.items():
            setattr(_paths, k, v)
        shutil.rmtree(td, ignore_errors=True)
    for k, v in src.items():
        setattr(_paths, k, v)
    return td, prof_path, hist, src, restore


def _mk_profile(prof_path, hist, client="slowpost"):
    return prof_mod.ClientProfile(prof_path, history_dir=hist, client_id=client)


def _write_lines(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


# ============================================================ 模型
def test_model_scene_default_validates():
    p = model.scene_default_profile()
    model.validate_profile(p)
    assert p["style"]["seed_source"] == "cold_start"
    assert p["meta"]["client_id"] == "slowpost"


def test_model_validate_rejects_bad():
    p = model.scene_default_profile()
    p["schema_version"] = 99
    try:
        model.validate_profile(p)
        assert False, "应抛 ProfileError"
    except model.ProfileError:
        pass
    p2 = model.scene_default_profile()
    del p2["style"]
    try:
        model.validate_profile(p2)
        assert False
    except model.ProfileError:
        pass


# ============================================================ 档案
def test_profile_cold_start_and_version():
    _, pp, hist, _, restore = _tmp_ctx()
    try:
        pr = _mk_profile(pp, hist)
        d0 = pr.load()
        v0 = d0["meta"]["version"]
        pr.save(reason="t1")
        pr.load(force_new=True)
        assert pr.profile["meta"]["version"] == v0 + 1
        assert (hist / f"profile_v{v0 + 1}.json").exists(), "应有版本快照"
    finally:
        restore()


def test_profile_rollback_and_broken_rebuild():
    _, pp, hist, _, restore = _tmp_ctx()
    try:
        pr = _mk_profile(pp, hist)
        pr.load()
        pr.save(reason="a")
        v1 = pr.profile["meta"]["version"]
        pr.save(reason="b")
        v2 = pr.profile["meta"]["version"]
        assert v2 == v1 + 1
        assert pr.rollback(v1), "应能回溯到 v1"
        assert pr.profile["meta"]["version"] == v1 + 1  # rollback 本身记一版
        # 坏档但有 .bak（save 产生）→ load_json 静默恢复上一版，无需重建
        pp.write_text("{broken json", encoding="utf-8")
        from core.storage import load_json as _lj
        pr2 = _mk_profile(pp, hist)
        pr2.load(force_new=True)
        assert pr2.profile is not None
        assert pr2.profile["meta"]["version"] >= 1  # 从 bak 恢复或重建均可
        # 坏档且无 .bak → 留档重建（不静默丢失）
        bak = Path(str(pp) + ".bak")
        if bak.exists():
            bak.unlink()
        pp.write_text("{broken json", encoding="utf-8")
        pr3 = _mk_profile(pp, hist)
        pr3.load(force_new=True)  # 应重建而不抛
        assert pr3.profile is not None
        assert any(p.name.startswith("broken_") for p in hist.iterdir()), "坏档应留档"
    finally:
        restore()


def test_profile_build_context_and_caps():
    _, pp, hist, _, restore = _tmp_ctx()
    try:
        pr = _mk_profile(pp, hist)
        pr.load()
        pr.profile["injection"]["enabled_dims"] = ["style", "topic"]
        pr.profile["style"]["sentence_habits"] = ["习惯A"] * 50
        blk = pr.build_context("style")
        assert len(blk) <= pr._cap("style_block_maxlen")
        assert pr.build_context("reader") == ""  # 被停用维度 → 空串
        assert pr.build_context("bogus") == ""
        full = pr.build_full_context()
        assert "[style]" in full and "[topic]" in full
        assert "[layout]" not in full  # 停用维不进入 full
    finally:
        restore()


# ============================================================ 投喂
def test_seed_questionnaire_override_and_merge():
    _, pp, hist, _, restore = _tmp_ctx()
    try:
        pr = _mk_profile(pp, hist)
        pr.load()
        ccp.ingest_questionnaire(pr, {"tone": "冷静克制", "sentence_habits": ["短句"],
                                      "topic_weights": {"读书": 0.5}})
        assert pr.profile["style"]["language_tone"] == "冷静克制"
        assert pr.profile["style"]["seed_source"] == "questionnaire"
        # merge=二次问卷增量
        ccp.ingest_questionnaire(pr, {"sentence_habits": ["长句多"]}, merge=True)
        assert "短句" in pr.profile["style"]["sentence_habits"]
        assert "长句多" in pr.profile["style"]["sentence_habits"]
        # 空 → 无事
        r = ccp.ingest_questionnaire(pr, {})
        assert r == {}
    finally:
        restore()


def test_seed_article_idempotent_and_limit():
    _, pp, hist, _, restore = _tmp_ctx()
    try:
        pr = _mk_profile(pp, hist)
        pr.load()
        r1 = ccp.ingest_article(pr, SAMPLE, title="第一篇")
        assert r1["added"] is True
        q1 = len(pr.profile["style"]["quote_preferences"])
        # 重复投喂 → 不叠加
        r2 = ccp.ingest_article(pr, SAMPLE)
        assert r2["added"] is False and r2["reason"] == "duplicate"
        assert len(pr.profile["style"]["ingested_articles"]) == 1
        assert len(pr.profile["style"]["quote_preferences"]) == q1
        # 过短 → 跳过
        r3 = ccp.ingest_article(pr, "太短")
        assert r3["reason"] == "too_short"
        # 5 篇上限
        for i in range(5):
            ccp.ingest_article(pr, SAMPLE + f"\n第{i}个变体" * 30)
        rlim = ccp.ingest_article(pr, SAMPLE + "\n超出" * 30)
        assert rlim["reason"] == "limit"
        assert len(pr.profile["style"]["ingested_articles"]) <= seed_mod.ARTICLE_SEED_MAX
    finally:
        restore()


def test_ensure_seeded_idempotent():
    _, pp, hist, _, restore = _tmp_ctx()
    try:
        pr = _mk_profile(pp, hist)
        pr._p = None
        ccp.ensure_seeded(pr)
        pr._p = None
        v1 = pr.load()["meta"]["version"]
        # 第二次 ensure 不应累进版本
        pr._p = None
        ccp.ensure_seeded(pr)
        assert pr.load(force_new=True)["meta"]["version"] == v1
    finally:
        restore()


# ============================================================ 反馈闭环
def _seed_feedback(pr, src):
    fb = [
        {"date": "2026-09-01", "rank": 1, "topic": "深夜孤独", "ai_score": 4.0,
         "user_score": 5, "delta": 1.0},
        {"date": "2026-09-02", "rank": 1, "topic": "深夜孤独", "ai_score": 4.0,
         "user_score": 5, "delta": 1.0},
        {"date": "2026-09-03", "rank": 1, "topic": "深夜孤独", "ai_score": 4.0,
         "user_score": 5, "delta": 1.0},  # 3 条 → 达阈值
        {"date": "2026-09-03", "rank": 2, "topic": "毕业季", "ai_score": 4.0,
         "user_score": 2, "delta": -2.0},  # 1 条 → 未达阈值
    ]
    _write_lines(src["USER_FB_PATH"], fb)
    pref = [{"date": "2026-09-01", "topic": "深夜孤独", "action": "like"},
            {"date": "2026-09-01", "topic": "榴莲", "action": "exclude",
             "motif_series": "猎奇"}]
    _write_lines(src["USER_PREF_PATH"], pref)


def test_feedback_harvest_topic_threshold_and_style_quotes():
    _, pp, hist, src, restore = _tmp_ctx()
    try:
        pr = _mk_profile(pp, hist)
        pr.load()
        _seed_feedback(pr, src)
        _write_lines(src["QUOTES_PATH"], [
            {"text": "慢一点，也是往前走。", "source": "manual"},
            {"text": "愿你拥抱自己。", "source": "manual"},
        ])
        out = fb_mod.harvest(pr)
        w = pr.profile["topic"]["preference_weights"]
        assert "深夜孤独" in w, "达阈值应写回主题权重"
        assert 0 < w["深夜孤独"] <= 3
        assert "毕业季" not in w, "未达阈值不写回"
        assert "猎奇" in pr.profile["topic"]["excluded_series"]
        assert "慢一点，也是往前走。" in pr.profile["style"]["quote_preferences"]
        leads = [x for x in pr.profile["learned_leads"] if not x.get("reverted")]
        assert any("深夜孤独" in x["lead"] for x in leads)
        assert out["topic_changed"] > 0
    finally:
        restore()


def test_feedback_harvest_cursor_idempotent():
    _, pp, hist, src, restore = _tmp_ctx()
    try:
        pr = _mk_profile(pp, hist)
        pr.load()
        _seed_feedback(pr, src)
        fb_mod.harvest(pr)
        v1 = pr.profile["meta"]["version"]
        w1 = dict(pr.profile["topic"]["preference_weights"])
        n1 = len(pr.profile["learned_leads"])
        # 第二次 harvest：无新信号 → 不重复累计
        fb_mod.harvest(pr)
        assert pr.profile["topic"]["preference_weights"] == w1, "重复 harvest 不应再累计权重"
        assert len(pr.profile["learned_leads"]) == n1, "不应重复产生留痕"
        # 追加新信号同主题 → 累计且版本前进（真实数据源为增量 append）
        p = src["USER_FB_PATH"]
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"date": "2026-09-04", "topic": "深夜孤独",
                                "ai_score": 5.0, "user_score": 1, "delta": -4.0},
                               ensure_ascii=False) + "\n")
        fb_mod.harvest(pr)
        assert pr.profile["meta"]["version"] > v1
    finally:
        restore()


def test_feedback_layout_reader_and_revert():
    from core.storage import write_json
    _, pp, hist, src, restore = _tmp_ctx()
    try:
        pr = _mk_profile(pp, hist)
        pr.load()
        write_json(src["TYPOGRAPHY_SETTINGS_PATH"],
                   {"active_theme": "muxue_xinjian", "overrides": {}})
        _write_lines(src["ARTICLE_STATS_PATH"], [
            {"date": "2026-09-01", "reads": 100, "likes": 10, "shares": 3},
            {"date": "2026-09-02", "reads": 120, "likes": 12, "shares": 4},
        ])
        fb_mod.harvest(pr)
        assert pr.profile["layout"]["active_theme"] == "muxue_xinjian"
        assert len(pr.profile["reader"]["trend"]) == 2
        assert pr.profile["reader"]["avg_engagement"]["sample_days"] == 2
        # 撤销一条留痕
        lead = next(x for x in pr.profile["learned_leads"] if x["dim"] == "layout")
        assert fb_mod.revert_lead(pr, lead["id"])
        assert next(x for x in pr.profile["learned_leads"] if x["id"] == lead["id"])["reverted"]
    finally:
        restore()


def test_feedback_dry_run_no_write():
    _, pp, hist, src, restore = _tmp_ctx()
    try:
        pr = _mk_profile(pp, hist)
        pr.load()
        _seed_feedback(pr, src)
        v0 = pr.profile["meta"]["version"]
        fb_mod.harvest(pr, dry_run=True)
        assert pr.profile["meta"]["version"] == v0, "dry-run 不应落盘"
        assert not pr.profile["topic"]["preference_weights"], "dry-run 不应写回权重"
    finally:
        restore()


def test_collect_feedback_into_ccp():
    _, pp, hist, _, restore = _tmp_ctx()
    try:
        pr = _mk_profile(pp, hist)
        pr.load()
        n = fb_mod.collect_feedback_into_ccp(pr, ["读者更爱看短句段落", "日记体比议论文好"])
        assert n == 2
        assert len(pr.profile["reader"]["notes"]) == 2
        # 重复录入幂等
        n2 = fb_mod.collect_feedback_into_ccp(pr, ["读者更爱看短句段落"])
        assert n2 == 0
    finally:
        restore()


# ============================================================ 运行
def _run():
    import traceback
    tests = [v for k, v in globals().items()
             if k.startswith("test_") and callable(v)]
    total, failed = len(tests), []
    for nm in sorted(n for n in globals() if n.startswith("test_") and callable(globals()[n])):
        try:
            globals()[nm]()
            print(f"  [PASS] {nm}")
        except AssertionError as e:
            failed.append(nm)
            print(f"  [FAIL] {nm}: {e}")
        except Exception as e:
            failed.append(nm)
            print(f"  [ERROR] {nm}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n商业化阶段一 CCP 单测: {total - len(failed)}/{total} PASS")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    _run()