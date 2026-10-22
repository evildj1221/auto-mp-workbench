#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_export_slot_titles.py · 导出标题槽位隔离回归测试（修复 2026-09-17）。

背景：exporter.export_article 曾读全局 {date}_titles.json，多槽位时 W01 的
selected 标题串染到 W02 的导出预览与文稿首行。

覆盖：
  1. 双槽位各自使用自己的槽位 titles 文件，互不串染，全局残留文件不参与
  2. 槽位 titles 文件缺失时回退 works 槽位 meta，并纠偏被历史污染的文稿首行
  3. slot=None（agent 流）语义不变：仍读 {date}_titles.json

隔离：全部数据目录 monkeypatch 到 pytest tmp_path（exporter.SCRIPT_DIR 同步
patch 使 report 的 relative_to 成立）；语义红线 patch 为抛错（导出侧降级跳过），
不触碰真实 data/，不调用任何 LLM。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cat_shop  # noqa: E402
import core.memory  # noqa: E402
import core.paths  # noqa: E402
import core.redlines  # noqa: E402
import core.storage  # noqa: E402
from core import exporter  # noqa: E402

DATE = "2026-09-17"
T_W01 = "秋招第一周，我把西装挂回了衣柜"
T_W02 = "秋分前，我把冰箱理过"


def _md(title, body_extra=""):
    return (f"# {title}\n\n"
            "晚上好。\n\n"
            "现在是十点零几分，我刚从厨房出来，手上还有一点洗洁精的味道。\n\n"
            ":::quote\n慢一点，也是往前走。\n:::\n\n"
            ":::letter\n亲爱的你：\n今晚也想写点什么。\n> 落款：创作工作台\n:::\n\n"
            "你有没有过这种时刻。明明没打算做什么大事，只是路过，只是顺手。\n\n"
            ":::action\n给今晚的自己写一句话。\n:::" + body_extra)


def _patch_isolation(monkeypatch, tmp):
    """把导出链路会读写的全部数据目录指到 tmp（同名多绑定逐模块覆盖）。"""
    dirs = {
        "DRAFTS_DIR": tmp / "drafts", "SELECTIONS_DIR": tmp / "selections",
        "WORKS_DIR": tmp / "works", "BATCHES_DIR": tmp / "batches",
        "FEEDBACK_DIR": tmp / "feedback",
    }
    files = {
        "QUOTES_PATH": tmp / "quotes.jsonl",
        "HISTORY_PATH": tmp / "history.jsonl",
        "REDLINE_LOG": tmp / "redline_log.json",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    mods = [core.paths, cat_shop, core.storage, core.memory, core.redlines]
    for mod in mods:
        for name, val in {**dirs, **files}.items():
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, val)
    monkeypatch.setattr(exporter, "SCRIPT_DIR", tmp)  # report["output"] 的 relative_to
    monkeypatch.setattr(cat_shop, "redline_llm_semantic",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("测试跳过语义红线")))


def _mk_slot_data(tmp, slot, title, *, works_title=None, polluted_md=False):
    d = tmp / "drafts"
    (d / f"{DATE}_{slot}_v1.md").write_text(
        _md(T_W01 if polluted_md else title), encoding="utf-8")
    if works_title is not False:
        (d / f"{DATE}_{slot}_titles.json").write_text(
            json.dumps({"date": DATE, "selected": title,
                        "titles": [{"title": title}]}, ensure_ascii=False),
            encoding="utf-8")


# ============================================================
def test_two_slots_keep_own_titles(monkeypatch, tmp_path):
    """双槽位导出互不串染：W02 不再读到全局文件里 W01 的 selected 标题。"""
    _patch_isolation(monkeypatch, tmp_path)
    _mk_slot_data(tmp_path, "w01", T_W01)
    _mk_slot_data(tmp_path, "w02", T_W02)
    # 模拟旧版残留：全局 titles 文件里躺着 W01 的选定标题（Bug 根源）
    (tmp_path / "drafts" / f"{DATE}_titles.json").write_text(
        json.dumps({"selected": T_W01}, ensure_ascii=False), encoding="utf-8")

    r1 = exporter.export_article(DATE, slot="w01")
    r2 = exporter.export_article(DATE, slot="w02")

    html1 = (tmp_path / "drafts" / f"{DATE}_w01_mp.html").read_text(encoding="utf-8")
    html2 = (tmp_path / "drafts" / f"{DATE}_w02_mp.html").read_text(encoding="utf-8")
    assert T_W01 in html1 and T_W02 not in html1
    assert T_W02 in html2 and T_W01 not in html2
    assert r1["slot"] == "w01" and r2["slot"] == "w02"
    # 文稿源文件首行不被对方标题污染
    md2 = (tmp_path / "drafts" / f"{DATE}_w02_v1.md").read_text(encoding="utf-8")
    assert md2.split("\n", 1)[0] == f"# {T_W02}"


def test_works_meta_fallback_repairs_polluted_md(monkeypatch, tmp_path):
    """槽位 titles 缺失 → 回退 works meta；曾被打上 W01 标题的 W02 文稿纠偏。"""
    _patch_isolation(monkeypatch, tmp_path)
    _mk_slot_data(tmp_path, "w01", T_W01, works_title=False)
    _mk_slot_data(tmp_path, "w02", T_W02, works_title=False, polluted_md=True)
    cat_shop.write_json(tmp_path / "works" / f"{DATE}.json", {
        "date": DATE,
        "slots": [{"id": "w01", "selected_title": T_W01},
                  {"id": "w02", "selected_title": T_W02}]})

    exporter.export_article(DATE, slot="w02")

    html2 = (tmp_path / "drafts" / f"{DATE}_w02_mp.html").read_text(encoding="utf-8")
    md2 = (tmp_path / "drafts" / f"{DATE}_w02_v1.md").read_text(encoding="utf-8")
    assert T_W02 in html2 and T_W01 not in html2
    assert md2.split("\n", 1)[0] == f"# {T_W02}"  # 首行已纠偏


def test_slot_none_still_uses_global(monkeypatch, tmp_path):
    """agent 流（slot=None）行为不变：{date}_titles.json 即其槽位文件。"""
    _patch_isolation(monkeypatch, tmp_path)
    (tmp_path / "drafts" / f"{DATE}_v1.md").write_text(_md(T_W01), encoding="utf-8")
    (tmp_path / "drafts" / f"{DATE}_titles.json").write_text(
        json.dumps({"selected": T_W01}, ensure_ascii=False), encoding="utf-8")

    exporter.export_article(DATE)

    html = (tmp_path / "drafts" / f"{DATE}_mp.html").read_text(encoding="utf-8")
    assert T_W01 in html


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """双保险：任何漏网 LLM 调用直接失败，杜绝测试触发真实 API。"""
    import requests
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("测试不应发起网络请求")))
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("测试不应发起网络请求")))
