#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_charts.py · 驾驶舱趋势图单测（v4.2 M4 验收项）。

策略：把 charts 模块三个数据源常量（COST_LOG/EVAL_DIR/DRAFTS_DIR）临时
指向 tempfile 沙箱，验证聚合口径与 SVG 契约；finally 全量还原，不碰真实 data/。
"""
import json
import sys
import tempfile
from contextlib import contextmanager
from datetime import date as date_cls, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import charts  # noqa: E402


@contextmanager
def _sandbox():
    """三数据源切到沙箱目录；退出时还原常量。"""
    import shutil
    tmp = Path(tempfile.mkdtemp(prefix="charts_test_"))
    cost_log = tmp / "llm_cost.jsonl"
    eval_dir = tmp / "eval"
    eval_dir.mkdir()
    drafts_dir = tmp / "drafts"
    drafts_dir.mkdir()
    saved = (charts.COST_LOG, charts.EVAL_DIR, charts.DRAFTS_DIR)
    charts.COST_LOG, charts.EVAL_DIR, charts.DRAFTS_DIR = cost_log, eval_dir, drafts_dir
    try:
        yield cost_log, eval_dir, drafts_dir
    finally:
        charts.COST_LOG, charts.EVAL_DIR, charts.DRAFTS_DIR = saved
        shutil.rmtree(tmp, ignore_errors=True)


def _write_cost(path, rows):
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_daily_costs_aggregates_and_pads_missing_days():
    """同日多次调用聚合一格；缺日补 0；累计单调不减；无日期行忽略。"""
    with _sandbox() as (cost_log, _, _):
        today = date_cls.today()
        d1, d2 = today.isoformat(), (today - timedelta(days=1)).isoformat()
        _write_cost(cost_log, [
            {"date": d1, "cost_total_cny": 0.10},
            {"date": d1, "cost_total_cny": 0.05},
            {"date": d2, "cost_total_cny": 0.30},
            {"date": "坏行", "cost_total_cny": 9.99},
        ])
        out = charts.daily_costs(3)
        assert len(out) == 3
        assert out[-1]["date"] == d1 and out[-1]["cost"] == 0.15
        assert out[-1]["calls"] == 2, "同日两次调用应聚合成一格"
        assert out[-2]["cost"] == 0.30
        assert out[0]["cost"] == 0.0, "缺日应补 0"
        cums = [x["cumulative"] for x in out]
        assert cums == sorted(cums) and abs(cums[-1] - 0.45) < 1e-6
        assert all(x["cost"] >= 0 for x in out), "无日期行不得计入"


def test_cost_chart_svg_contract():
    """柱 + 累计折线 + 双轴图例齐全；空数据不崩（max 兜底 0.01）。"""
    with _sandbox() as (cost_log, _, _):
        today = date_cls.today()
        _write_cost(cost_log, [{"date": today.isoformat(), "cost_total_cny": 0.5}])
        svg = charts.cost_chart_svg(7)
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        for frag in ("日成本", "累计", "polyline", "rect", today.isoformat()[5:]):
            assert frag in svg, f"成本图缺 {frag}"
        charts.COST_LOG = cost_log.with_suffix(".missing")  # 无数据
        empty = charts.cost_chart_svg(7)
        assert empty.startswith("<svg"), "全零数据不应崩溃"


def test_quality_chart_svg_empty_and_history():
    """无基线 → 空态提示；有历史 → 双折线 + 图例 + 按时间排序。"""
    with _sandbox() as (_, eval_dir, _):
        svg = charts.quality_chart_svg()
        assert "暂无 run_eval 基线记录" in svg, "空态应提示而非空白"
        (eval_dir / "2026-08-20_eval.json").write_text(json.dumps({
            "generated_at": "2026-08-20T10:00:00",
            "suites": {"writing": {"pass_rate": 0.8},
                       "redline": {"recall": 0.9},
                       "cost": {"per_article_median": 0.2}}}), encoding="utf-8")
        (eval_dir / "2026-08-28_eval.json").write_text(json.dumps({
            "generated_at": "2026-08-28T10:00:00",
            "suites": {"writing": {"pass_rate": 0.85},
                       "redline": {"recall": 0.95}}}), encoding="utf-8")
        svg = charts.quality_chart_svg()
        assert "写稿通过率" in svg and "红线召回" in svg
        assert "polyline" in svg and "08-20" in svg and "08-28" in svg
        hist = charts.eval_history()
        assert [x["ts"] for x in hist] == ["2026-08-20", "2026-08-28"]
        assert hist[1]["writing_pass"] == 0.85 and hist[0]["per_article_median"] == 0.2


def test_publish_heatmap_marks_published():
    """发布日暖金格 + title 标注；user_published=False 不计入。"""
    with _sandbox() as (_, _, drafts_dir):
        today = date_cls.today()
        past = (today - timedelta(days=10)).isoformat()
        (drafts_dir / f"{past}_published.json").write_text(
            json.dumps({"user_published": True}), encoding="utf-8")
        (drafts_dir / "2026-01-01_published.json").write_text(
            json.dumps({"user_published": False}), encoding="utf-8")
        assert len(charts.published_dates()) == 1
        svg = charts.publish_heatmap_svg(4)
        assert svg.startswith("<svg")
        assert f"{past} · 已发布" in svg, "发布日应有 title 标注"
        assert charts.C_GOLD in svg
        assert "2026-01-01 · 已发布" not in svg


def test_dashboard_charts_meta():
    """一次聚合返回三图 + meta 数字口径（活跃日均、本月发布数）。"""
    with _sandbox() as (cost_log, eval_dir, drafts_dir):
        today = date_cls.today()
        _write_cost(cost_log, [{"date": today.isoformat(), "cost_total_cny": 0.6}])
        (eval_dir / "2026-08-20_eval.json").write_text(json.dumps({
            "generated_at": "2026-08-20T10:00:00",
            "suites": {"writing": {"pass_rate": 0.8}}}), encoding="utf-8")
        (drafts_dir / f"{today.isoformat()}_published.json").write_text(
            json.dumps({"user_published": True}), encoding="utf-8")
        out = charts.dashboard_charts(7)
        assert set(out) == {"cost_svg", "quality_svg", "publish_svg", "meta"}
        assert all(out[k].startswith("<svg") for k in
                   ("cost_svg", "quality_svg", "publish_svg"))
        m = out["meta"]
        assert m["days"] == 7 and m["total_cny"] == 0.6
        assert m["active_days"] == 1 and m["avg_cny"] == 0.6
        assert m["last_cny"] == 0.6
        assert m["eval_runs"] == 1
        assert m["published_total"] == 1
        assert m["published_this_month"] == 1


def test_dashboard_route_renders_charts():
    """驾驶舱 API 注入 trend 三图（M4 集成验收：JSON 携带内联 SVG）。"""
    from fastapi.testclient import TestClient
    from backend.app.main import app
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        body = c.get("/api/dashboard").json()
        assert body["ok"]
        trend = body["data"]["trend"]
        assert "cost_svg" in trend and "quality_svg" in trend and "publish_svg" in trend
        assert "<svg" in trend["cost_svg"], "三图应服务端渲染内联 SVG"


if __name__ == "__main__":
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
    total = sum(1 for n in globals() if n.startswith("test_") and callable(globals()[n]))
    print(f"\n趋势图单测: {total - len(failures)}/{total} PASS")
    sys.exit(1 if failures else 0)
