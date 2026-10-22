#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_topic_smart.py · v4.3 选题智能优化（P0+M1+M3）单测。

覆盖：
  M1 时节引擎   core/calendar_ctx.py：结构/预热窗口/节气窗口/越界降级/年度维护提醒
  M3 风向标     core/directives.py：负面解析/三档 scope/过期/去重/红线拒收/禁止清单/
                prompt 块渲染/命中统计与低命中连击
  M3 工具层     set_topic_directive / list_topic_directives 信封契约
  P0 注入链路   step_collect：占位符全替换断言/热点行按需注入/去重池扩展/
                占位符残留守卫抛错/风向端到端（prompt 注入 + 禁止词过滤 + 命中落盘）

隔离策略：风向文件（全局单例，非按日）整体备份→清空→恢复；候选产物用 2099 沙箱前缀，
teardown 全量清理；LLM 全 mock，零真实成本。
"""
import contextlib
import importlib
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.paths import (BATCHES_DIR, CANDIDATES_DIR,
                        TOPIC_DIRECTIVE_HITS_PATH, TOPIC_DIRECTIVES_PATH)
from core.storage import write_json

SANDBOX = "2099-01-02"          # 沙箱日期：远超日历数据年限（2026-2027），顺带覆盖降级路径
RECENT_SANDBOX = "2099-01-01"   # 去重池测试用「昨日候选」文件

POS_TEXT = "开学季写点新开始的题"
NEG_TEXT = "别再写失眠和毕业"


# ===== 共享状态备份/恢复（风向文件是全局单例） =====
def _read(path):
    return path.read_text(encoding="utf-8") if path.exists() else None


def _write(path, text):
    if text is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@contextlib.contextmanager
def _clean_directives():
    """风向双文件备份→清空→测试→恢复。"""
    st = (_read(TOPIC_DIRECTIVES_PATH), _read(TOPIC_DIRECTIVE_HITS_PATH))
    _write(TOPIC_DIRECTIVES_PATH, None)
    _write(TOPIC_DIRECTIVE_HITS_PATH, None)
    try:
        yield
    finally:
        _write(TOPIC_DIRECTIVES_PATH, st[0])
        _write(TOPIC_DIRECTIVE_HITS_PATH, st[1])


def _clean_sandbox_files():
    for d in (CANDIDATES_DIR, BATCHES_DIR):
        for f in d.glob(f"{SANDBOX}*"):
            f.unlink(missing_ok=True)
        for f in d.glob(f"{RECENT_SANDBOX}*"):
            f.unlink(missing_ok=True)


@contextlib.contextmanager
def _mock_llm(module_name, fn):
    mod = importlib.import_module(module_name)
    old = getattr(mod, "call_llm")
    setattr(mod, "call_llm", fn)
    try:
        yield
    finally:
        setattr(mod, "call_llm", old)


@contextlib.contextmanager
def _patch(module_name, attr, value):
    mod = importlib.import_module(module_name)
    old = getattr(mod, attr)
    setattr(mod, attr, value)
    try:
        yield
    finally:
        setattr(mod, attr, old)


# ===== M1 时节引擎 =====
def test_m1_time_context_structure():
    from core.calendar_ctx import build_time_context
    ctx = build_time_context("2026-09-03")
    assert ctx["date"] == "2026-09-03" and ctx["weekday"] == "周四"
    assert ctx["weekday_note"], "周时刻心态注解缺失"
    assert any("白露" in t for t in ctx["solar_terms"]), "白露（9/7）应进入 ±7 天窗口"
    assert any("开学季" in c["name"] for c in ctx["life_cycles"]), "9/3 应命中开学季窗口"
    assert ctx["life_cycles"][0]["week"] == 1, "9/3 开学季应为第 1 周"
    assert any("教师节" in f for f in ctx["festivals"]), "教师节（9/10）应在 14 天预热窗口"
    assert any("中秋节" in f and "预热" in f for f in ctx["festivals"]), \
        "中秋为大节，应进入 30 天预热窗口"
    assert ctx["month_position"], "9/3 是月初，应有月度心态"
    assert ctx["season_scene"], "季节场景缺失"


def test_m1_solar_term_windows():
    from core.calendar_ctx import build_time_context
    assert any("今日白露" in t for t in build_time_context("2026-09-07")["solar_terms"])
    assert any("白露将至" in t for t in build_time_context("2026-09-05")["solar_terms"])
    assert any("白露已过" in t for t in build_time_context("2026-09-09")["solar_terms"])
    # 节气前后皆无窗口时（7/15 距小暑/大暑各 8 天），仍给出未来节气的远期提示
    terms = build_time_context("2026-07-15")["solar_terms"]
    assert terms and all("将至" not in t and "已过" not in t and "今日" not in t for t in terms), \
        "无窗口日应只提示未来节气，不带窗口措辞"


def test_m1_festival_preheat_boundaries():
    from core.calendar_ctx import build_time_context
    # 普通节日第 15 天不在 14 天预热窗口；大节第 31 天不在 30 天预热窗口
    assert not any("教师节" in f for f in build_time_context("2026-08-26")["festivals"]), \
        "教师节 9/10 距 8/26 为 15 天，不应预热"
    assert any("中秋节" in f for f in build_time_context("2026-08-26")["festivals"]), \
        "中秋距 8/26 为 30 天，大节窗口边界应命中"
    assert not any("中秋节" in f for f in build_time_context("2026-08-25")["festivals"]), \
        "中秋距 8/25 为 31 天，超出大节预热窗口"
    # 当日节日
    assert any("中秋节" in f and "今日" in f for f in build_time_context("2026-09-25")["festivals"])


def test_m1_year_out_of_range_degrades():
    from core.calendar_ctx import build_time_context, render_time_context
    ctx = build_time_context("2035-06-01")   # 数据只到 2027，越界不抛异常
    assert ctx["festivals"] == [] and ctx["solar_terms"] == [] and ctx["life_cycles"] == []
    block = render_time_context("2035-06-01")
    assert block.startswith("<time_context>") and block.endswith("</time_context>")
    assert "- 日期：" in block and "- 季节场景：" in block, "降级时基础行仍应保留"


def test_m1_needs_year_data():
    from core.calendar_ctx import needs_year_data
    assert needs_year_data("2026-12-01") is False, "2026-12 时 2027 数据已备，无需提醒"
    assert needs_year_data("2027-12-01") is True, "2027-12 时 2028 数据缺失，应提醒"
    assert needs_year_data("2027-06-01") is False, "非 12 月不提醒"


def test_m1_render_block_format():
    from core.calendar_ctx import render_time_context
    block = render_time_context("2026-09-03")
    assert block.count("<time_context>") == 1 and block.count("</time_context>") == 1
    assert "- 节气：白露" in block and "开学季第 1 周" in block, "渲染块应含节气与生活周期"


# ===== M3 风向标 core =====
def test_m3_negative_parsing():
    from core.directives import is_negative, parse_negative
    assert is_negative(NEG_TEXT) and is_negative("不想看到星座内容") and is_negative("少写点婆媳")
    assert not is_negative(POS_TEXT) and not is_negative("多写点职场故事")
    assert parse_negative(NEG_TEXT) == ["失眠", "毕业"]
    assert parse_negative("不想看到星座内容") == ["星座"], "后缀「的内容」应剥离"
    assert parse_negative("别再提加班和内卷的话题") == ["加班", "内卷"], "后缀「的话题」应剥离"
    assert parse_negative(POS_TEXT) == [], "正向指令解析不出禁止词"


def test_m3_add_directive_scopes_and_expiry():
    from core.directives import add_directive, list_directives
    with _clean_directives():
        now = "2026-01-01T10:00:00"
        d_today = add_directive("今日轻快些", scope="today", today=now)
        d_week = add_directive(POS_TEXT, scope="week", today=now)
        d_long = add_directive("温柔底色不能丢", scope="long", today=now)
        assert all(r["ok"] for r in (d_today, d_week, d_long)), \
            [r.get("reason") for r in (d_today, d_week, d_long)]
        assert d_today["directive"]["expires_at"] == "2026-01-01T23:59:59"
        assert d_week["directive"]["expires_at"] == "2026-01-08T10:00:00"
        assert d_long["directive"]["expires_at"] is None
        # 过期边界：today 档当天 23:59 仍生效，次日失效；week 档第 8 天失效
        assert len(list_directives(today="2026-01-01T23:59:00")) == 3
        assert len(list_directives(today="2026-01-02T00:00:00")) == 2
        assert len(list_directives(today="2026-01-08T10:00:00")) == 2
        assert len(list_directives(today="2026-01-08T10:00:01")) == 1


def test_m3_add_directive_validation():
    from core.directives import add_directive
    with _clean_directives():
        assert add_directive("", scope="week")["ok"] is False
        assert add_directive("   ", scope="week")["ok"] is False
        r = add_directive(POS_TEXT, scope="year")
        assert r["ok"] is False and "scope" in r["reason"]


def test_m3_add_directive_dedup():
    from core.directives import add_directive, list_directives
    with _clean_directives():
        r1 = add_directive(POS_TEXT, scope="week")
        r2 = add_directive(POS_TEXT, scope="week")
        assert r1["ok"] and r2["ok"] and r2.get("duplicated") is True
        assert len(list_directives()) == 1, "同文本不重复入库"


def test_m3_add_directive_redline_rejected():
    from core.directives import add_directive, list_directives
    with _clean_directives():
        r = add_directive("男人都这样，别写女人了", scope="week")
        assert r["ok"] is False and "红线" in r["reason"], "critical 红线风向应拒收"
        assert list_directives() == [], "拒收的风向不应落盘"


def test_m3_remove_directive():
    from core.directives import add_directive, list_directives, remove_directive
    with _clean_directives():
        r = add_directive(POS_TEXT, scope="week")
        did = r["directive"]["id"]
        assert remove_directive(did) is True
        assert list_directives() == []
        assert remove_directive(did) is False, "重复移除应返回 False"
        assert remove_directive("d999") is False


def test_m3_banned_keywords_agg():
    from core.directives import add_directive, banned_keywords
    with _clean_directives():
        add_directive(NEG_TEXT, scope="week")
        add_directive("不想看到星座内容", scope="long")
        assert banned_keywords() == ["失眠", "毕业", "星座"], "多负面指令禁止词应去重合并"
        # 过期负面指令不再贡献禁止词
        assert banned_keywords(today="2030-01-01T00:00:00") == ["星座"], \
            "week 档过期后只剩 long 档禁止词"


def test_m3_render_directives_block():
    from core.directives import add_directive, render_directives_block
    with _clean_directives():
        assert render_directives_block() == "", "无风向时静默降级为空串"
        add_directive(NEG_TEXT, scope="week")
        block_neg = render_directives_block()
        assert "禁止清单" in block_neg and POS_TEXT not in block_neg and "风向】" not in block_neg, \
            "只有负面指令时块内应只有禁止清单行"
        add_directive(POS_TEXT, scope="week")
        add_directive("不想看到星座内容", scope="long")
        block = render_directives_block()
        assert block.startswith("<user_directives"), "风向块应以最高优先级标签开头"
        assert POS_TEXT in block and "本周风向" in block
        assert "禁止清单" in block and "失眠、毕业、星座" in block


def test_m3_record_hits_and_low_streak():
    from core.directives import (add_directive, low_hit_streak, recent_hit_rates,
                                 record_batch_hits)
    with _clean_directives():
        assert record_batch_hits(3, 10) == {"rate": None}, "无正向风向时不落盘"
        add_directive(POS_TEXT, scope="week")
        r = record_batch_hits(3, 10)
        assert r["rate"] == 0.3
        assert record_batch_hits(1, 10)["rate"] == 0.1
        assert recent_hit_rates(2) == [0.3, 0.1]
        assert low_hit_streak() == 1, "仅 1 批低命中不足连击"
        record_batch_hits(1, 10)
        assert low_hit_streak() == 2, "连续 2 批 <20% 应触发 UI 提示阈值"
        record_batch_hits(5, 10)
        assert low_hit_streak() == 0, "高命中批次应重置连击"


def test_m3_hits_written_back_to_directive():
    from core.directives import add_directive, list_directives, record_batch_hits
    with _clean_directives():
        add_directive(POS_TEXT, scope="week")
        add_directive(NEG_TEXT, scope="week")
        record_batch_hits(4, 10)
        ds = {d["text"]: d for d in list_directives()}
        assert ds[POS_TEXT]["hits_last_batch"] == {"hits": 4, "total": 10}
        assert ds[NEG_TEXT]["hits_last_batch"] is None, "负面指令不打命中标记"


# ===== M3 工具层 =====
def test_m3_tool_set_topic_directive():
    from tools.registry import run_tool
    with _clean_directives():
        r = run_tool("set_topic_directive", text=POS_TEXT, scope="week")
        assert r["status"] == "ok" and "风向已设置" in r["message"] and "本周" in r["message"]
        assert r["directive"]["text"] == POS_TEXT and r["directive"]["source"] == "agent"
        # 幂等：同文本重复设置不报错不重复入库
        r2 = run_tool("set_topic_directive", text=POS_TEXT, scope="week")
        assert r2["status"] == "ok" and "已存在" in r2["message"]
        # 参数非法与红线拒收走 error 信封
        r3 = run_tool("set_topic_directive", text=POS_TEXT, scope="year")
        assert r3["status"] == "error" and "scope" in r3["message"]
        r4 = run_tool("set_topic_directive", text="男人都这样别写了", scope="week")
        assert r4["status"] == "error" and "红线" in r4["message"]


def test_m3_tool_list_topic_directives():
    from tools.registry import run_tool
    with _clean_directives():
        r0 = run_tool("list_topic_directives")
        assert r0["status"] == "ok" and r0["directives"] == [] and r0["banned_keywords"] == []
        run_tool("set_topic_directive", text=POS_TEXT, scope="week")
        run_tool("set_topic_directive", text=NEG_TEXT, scope="long")
        r = run_tool("list_topic_directives")
        assert r["status"] == "ok" and "2 条风向" in r["message"] and "正向 1 / 负面 1" in r["message"]
        kinds = {d["text"]: d["kind"] for d in r["directives"]}
        assert kinds[POS_TEXT] == "正向" and kinds[NEG_TEXT] == "禁止"
        assert r["banned_keywords"] == ["失眠", "毕业"]


# ===== P0 注入链路（step_collect） =====
def _run_collect(date=SANDBOX, trending=None, timeframe="2099 秋季 / 9 月",
                 llm=None, prompts=None):
    from pipeline.topics import step_collect
    captured = prompts if prompts is not None else []

    def _fake(prompt, **kw):
        captured.append(prompt)
        return llm() if callable(llm) else {"candidates": llm}

    args = types.SimpleNamespace(date=date, timeframe=timeframe,
                                 trending=trending, round=None, force=True)
    with _mock_llm("pipeline.topics", _fake):
        ok = step_collect(args)
    return ok


def _mk_cands(n=15, banned_topics=(), hit_topics=()):
    out = []
    for i in range(1, n + 1):
        topic = banned_topics[i - 1] if i <= len(banned_topics) else f"沙箱候选{i}：新学期的第{i}盏灯"
        out.append({"cand_id": f"cand_{i:03d}", "topic": topic, "motif_series": "A",
                    "timeliness": "low", "hook_type": "scene",
                    "directive_hit": topic in hit_topics})
    return out


def test_p0_collect_prompt_placeholder_injection(monkeypatch):
    """P0#2：七个占位符全部替换成功，prompt 无残留。"""
    import pipeline.topics as topics_mod
    monkeypatch.setattr(topics_mod, "fetch_trending", lambda force=False: [])  # 无热榜信号
    from core.calendar_ctx import render_time_context
    from llm.prompts import TOPIC_GENERATOR_PROMPT
    _clean_sandbox_files()
    with _clean_directives():
        prompts = []
        ok = _run_collect(trending=[], llm=_mk_cands(), prompts=prompts)
        assert ok is True
        p = prompts[0]
        for ph in ("{user_directives}", "{timeframe}", "{trending_line}",
                   "{avoid_topics}", "{time_context}", "{fatigue_line}",
                   "{theme_fatigue_line}", "{bank_line}"):
            assert ph not in p, f"占位符 {ph} 未替换（P0#1 复发）"
        assert "2099 秋季 / 9 月" in p, "timeframe 应注入"
        assert "<time_context>" in p and render_time_context(SANDBOX) in p, \
            "M1 时节上下文应整块注入"
        assert "热点线索" not in p, "P0#4：trending 为空且无热榜时不得注入热点行"
        # 无风向时 user_directives 占位符替换为空串，prompt 中无风向块
        # （rules 区引用 <user_directives> 字样属模板原文，不算注入块）
        assert "<user_directives priority" not in p


def test_trending_autofetch_injection(monkeypatch):
    """v4.5：手动未给 trending 时自动拉公开热榜（成功注入；失败静默不注入）。"""
    import pipeline.topics as topics_mod
    _clean_sandbox_files()
    with _clean_directives():
        monkeypatch.setattr(topics_mod, "fetch_trending",
                            lambda force=False: ["高速免费最后一刻女子淡定缴费"])
        prompts = []
        _run_collect(trending=[], llm=_mk_cands(), prompts=prompts)
        assert "高速免费最后一刻女子淡定缴费" in prompts[0], "热榜应自动注入"
        monkeypatch.setattr(topics_mod, "fetch_trending",
                            lambda force=False: (_ for _ in ()).throw(RuntimeError("net")))
        prompts2 = []
        assert _run_collect(trending=[], llm=_mk_cands(), prompts=prompts2) is True
        assert "热点线索" not in prompts2[0], "热榜失败必须静默降级"


def test_p0_collect_trending_line_and_dedup_pool():
    """P0#3/#4：trending 按需注入；去重池=近 14 天全部候选（含未选用）。"""
    _clean_sandbox_files()
    with _clean_directives():
        # 造一份「昨日生成的候选」（未选用）
        write_json(CANDIDATES_DIR / f"{RECENT_SANDBOX}.json",
                   {"date": RECENT_SANDBOX, "candidates": _mk_cands(3)})
        prompts = []
        _run_collect(trending=["中秋", "国庆"], llm=_mk_cands(), prompts=prompts)
        p = prompts[0]
        assert "- 热点线索（可参考）：中秋、国庆" in p, "trending 非空时应注入热点行"
        for i in (1, 2, 3):
            assert f"沙箱候选{i}：新学期的第{i}盏灯" in p, \
                "近 14 天候选（含未选用）应进入去重池"


def test_p0_collect_placeholder_guard_raises():
    """P0#2 守卫：替换后仍残留占位符 → 抛 ValueError 并留痕（不静默）。"""
    from core import errors as errcls
    _clean_sandbox_files()
    with _clean_directives():
        logged = []

        def _fake_log(err, msg, **kw):
            logged.append((err, msg))

        with _patch("pipeline.topics", "render_time_context", lambda d=None: "{time_context}"), \
             _patch("pipeline.topics", "log_error", _fake_log):
            try:
                _run_collect(llm=_mk_cands())
                raise AssertionError("占位符残留应抛 ValueError")
            except ValueError as e:
                assert "{time_context}" in str(e)
        assert logged and logged[0][0] == errcls.DATA_MISSING and "占位符" in logged[0][1]


def test_m3_collect_directive_integration():
    """M3 端到端：风向入 prompt + 禁止词后置过滤 + directive_hit 归一化与命中落盘。"""
    from core.directives import add_directive, list_directives
    _clean_sandbox_files()
    with _clean_directives():
        add_directive(POS_TEXT, scope="week")
        add_directive(NEG_TEXT, scope="week")
        banned_topics = ("失眠的人如何入睡", "毕业五年还在迷茫")
        # 命中候选取第 3-8 条（前 2 条是禁止词候选，会被过滤，不计命中）
        cands = _mk_cands(15, banned_topics=banned_topics,
                          hit_topics=tuple(f"沙箱候选{i}：新学期的第{i}盏灯" for i in range(3, 9)))
        prompts = []
        ok = _run_collect(trending=[], llm=cands, prompts=prompts)
        assert ok is True
        p = prompts[0]
        assert "<user_directives" in p and POS_TEXT in p, "正向风向应注入 prompt"
        assert "禁止清单" in p and "失眠、毕业" in p, "禁止清单应注入 prompt"

        out = json.loads((CANDIDATES_DIR / f"{SANDBOX}.json").read_text(encoding="utf-8"))
        topics = [c["topic"] for c in out["candidates"]]
        assert len(topics) == 13, "含禁止词的 2 条候选应被后置过滤"
        assert not any(("失眠" in t or "毕业" in t) for t in topics)
        assert all(isinstance(c["directive_hit"], bool) for c in out["candidates"]), \
            "directive_hit 应归一化为 bool"
        assert out["directive_hits"]["hits"] == 6 and out["directive_hits"]["total"] == 13
        assert 0 < out["directive_hits"]["rate"] < 1

        ds = {d["text"]: d for d in list_directives()}
        assert ds[POS_TEXT]["hits_last_batch"] == {"hits": 6, "total": 13}, \
            "命中率应回写风向条目"


def test_m3_collect_no_directive_hit_stats():
    """无正向风向时不记录命中统计（输出无 directive_hits 字段）。"""
    from core.directives import add_directive
    _clean_sandbox_files()
    with _clean_directives():
        add_directive(NEG_TEXT, scope="week")   # 只有负面指令
        _run_collect(trending=[], llm=_mk_cands(15))
        out = json.loads((CANDIDATES_DIR / f"{SANDBOX}.json").read_text(encoding="utf-8"))
        assert "directive_hits" not in out
        assert all(c["directive_hit"] is False for c in out["candidates"]), \
            "无正向风向时 directive_hit 统一为 false"


def test_p0_collect_idempotent_skip():
    """产物已存在且未 force → skipped（不调 LLM）。"""
    from pipeline.topics import step_collect
    _clean_sandbox_files()
    with _clean_directives():
        prompts = []
        _run_collect(trending=[], llm=_mk_cands(), prompts=prompts)
        assert len(prompts) == 1

        def _boom(prompt, **kw):
            raise AssertionError("幂等跳过时不应调用 LLM")

        args = types.SimpleNamespace(date=SANDBOX, timeframe="2099 秋季 / 9 月",
                                     trending=[], round=None, force=False)
        with _mock_llm("pipeline.topics", _boom):
            assert step_collect(args) is True


# ===== runner =====
if __name__ == "__main__":
    _clean_sandbox_files()
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
    _clean_sandbox_files()
    total = sum(1 for n in globals() if n.startswith("test_") and callable(globals()[n]))
    print(f"\nv4.3 选题智能单测: {total - len(failures)}/{total} PASS")
    sys.exit(1 if failures else 0)
