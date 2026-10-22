#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_interactions.py · 工作台 API 交互测试（前后端分离版，FastAPI TestClient）。

前身为 Flask 页面交互测试（2026-08-28）；后端 JSON 化后按资源路由重写，
行为语义保持等价：状态码 / 信封 ok / 动作副作用 / 沙箱数据测后即清。

覆盖：
  A 各资源 GET（dashboard/topics/drafts/export/review/archive/settings/agent）
  B 任务轮询（taskrunner 真实子命令 status）
  C 槽位管理（已发布拒删 / 草稿删除 / 剩余槽位链）
  D r2 批次选题回归（r2 选 #N 必须取 r2 的候选）+ pref 按批记录
  E 红线手动检测（短语命中 blocked；语义检测 mock，不打真实 LLM）
  F Agent 路由（FakeRunner：选题/发布审批载荷契约、nowait 拒绝、预算提额、取消）
  G 档案文件查看器路径穿越防护
"""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace as _NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cat_shop as _cs  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import app  # noqa: E402

client = TestClient(app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False)
D_STASH = "2026-08-11"  # 存量数据日期（只读断言）


@pytest.fixture(autouse=True)
def _disable_agent_state_cache(monkeypatch):
    """Agent 快照/状态 TTL 缓存会让快速连发的用例读到上一用例的旧状态，
    测试期一律置零（生产保持 1.5s 缓存）。"""
    from backend.app.api import agent as _agent_api
    monkeypatch.setattr(_agent_api, "_SNAP_TTL", 0.0)
    monkeypatch.setattr(_agent_api, "_STATE_TTL", 0.0)


@pytest.fixture(autouse=True)
def _guard_today_data():
    """安全网：任何测试若改动"今天"的真实数据，测试后立即原样回滚。
    （教训：动作路由 date 缺省会回退今天，曾致真实 W02 槽位被误删——写操作必须显式传日期。）"""
    snap_files = {}
    for p in list(_cs.WORKS_DIR.glob("*.json")) + list(_cs.DRAFTS_DIR.glob("*")):
        if p.is_file():
            snap_files[p] = p.read_bytes()
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    snap_files[f] = f.read_bytes()
    today = _cs.slot_paths(__import__("datetime").datetime.now().strftime("%Y-%m-%d"), None)
    yield
    for p, data in snap_files.items():
        if not p.exists() or p.read_bytes() != data:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)


def _ok(r) -> dict:
    assert r.status_code == 200, f"status={r.status_code} body={r.text[:200]}"
    body = r.json()
    assert body.get("ok"), f"envelope fail: {str(body)[:200]}"
    return body["data"]


# ============ A. 资源 GET ============
def test_resource_reads():
    _ok(client.get("/api/health"))
    _ok(client.get("/api/dashboard"))
    _ok(client.get(f"/api/topics/{D_STASH}"))
    _ok(client.get(f"/api/drafts/{D_STASH}"))
    _ok(client.get(f"/api/export/{D_STASH}"))
    _ok(client.get(f"/api/review/{D_STASH}"))
    _ok(client.get("/api/archive"))
    _ok(client.get(f"/api/archive/{D_STASH}"))
    _ok(client.get("/api/settings"))
    _ok(client.get("/api/typography"))
    _ok(client.get(f"/api/agent/{D_STASH}"))


def test_dashboard_shape():
    data = _ok(client.get(f"/api/dashboard/{D_STASH}"))
    assert data["page"] == "dashboard" and len(data["steps"]) == 10
    assert data["agent_state"]["date"] == D_STASH
    assert "cost_svg" in data["trend"]


# ============ B. 任务轮询 ============
def test_task_polling():
    from backend.app import taskrunner
    tid = taskrunner.run_command(["status", "--date", D_STASH], label="itest")
    for _ in range(30):
        t = taskrunner.get_task(tid)
        if t and t["status"] in ("done", "error"):
            break
        time.sleep(0.3)
    data = _ok(client.get(f"/api/tasks/{tid}"))
    assert data["status"] in ("done", "error")


# ============ C. 槽位管理（2099 假数据，测后即清） ============
TD = "2099-01-01"


@pytest.fixture()
def sandbox_slots():
    for i, (cid, st) in enumerate([("c1", "published"), ("c2", "draft"), ("c3", "draft")], 1):
        data = _cs.load_works(TD)
        data["slots"].append({"id": f"w{i:02d}", "round": 1, "cand_id": cid, "topic": f"测试{i}",
                              "motif_series": "A", "status": st,
                              "created_at": "2099-01-01T00:00:00", "updated_at": "2099-01-01T00:00:00",
                              "selected_title": None, "draft_media_id": None})
        _cs.save_works(TD, data)
        (_cs.DRAFTS_DIR / f"{TD}_w{i:02d}_v1.md").write_text("测试稿", encoding="utf-8")
    yield
    (_cs.WORKS_DIR / f"{TD}.json").unlink(missing_ok=True)
    for p in _cs.DRAFTS_DIR.glob(f"{TD}_*"):
        import shutil
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()


def test_slot_delete_rules(sandbox_slots):
    # 已发布槽拒绝删除
    r = client.post("/api/topics/slots/delete", json={"date": TD, "slot": "w01", "back": "topics"})
    body = r.json()
    assert not body["ok"] and "已发布" in body["error"]["message"]
    # 删除草稿槽 → 返回剩余草稿槽
    data = _ok(client.post("/api/topics/slots/delete", json={"date": TD, "slot": "w02", "back": "write"}))
    assert data["remaining_slot"] == "w03"
    assert not (_cs.DRAFTS_DIR / f"{TD}_w02_v1.md").exists()
    # 删到只剩已发布槽 → 无剩余
    data = _ok(client.post("/api/topics/slots/delete", json={"date": TD, "slot": "w03", "back": "write"}))
    assert data["remaining_slot"] is None


# ============ D. r2 批次选题回归 ============
TD2 = "2099-01-02"


@pytest.fixture()
def sandbox_r2():
    _cs.DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    (_cs.DRAFTS_DIR / f"{TD2}_top5.json").write_text(json.dumps({"results": [
        {"cand_id": "a1", "topic": "R1甲选题", "total": 4.0, "motif_series": "A"},
        {"cand_id": "a2", "topic": "R1乙选题", "total": 3.9, "motif_series": "B"}]},
        ensure_ascii=False), encoding="utf-8")
    (_cs.DRAFTS_DIR / f"{TD2}_r2_top5.json").write_text(json.dumps({"results": [
        {"cand_id": "b1", "topic": "R2甲选题", "total": 4.2, "motif_series": "C"},
        {"cand_id": "b2", "topic": "R2乙选题", "total": 4.1, "motif_series": "D"}]},
        ensure_ascii=False), encoding="utf-8")
    _cs.save_batches(TD2, {"date": TD2, "batches": [{"round": 1}, {"round": 2}], "active_round": 2})
    yield
    (_cs.WORKS_DIR / f"{TD2}.json").unlink(missing_ok=True)
    (_cs.SELECTIONS_DIR / f"{TD2}.json").unlink(missing_ok=True)
    (_cs.BATCHES_DIR / f"{TD2}.json").unlink(missing_ok=True)
    for p in _cs.DRAFTS_DIR.glob(f"{TD2}_*"):
        p.unlink()
    if _cs.USER_PREF_PATH.exists():
        keep = [l for l in _cs.USER_PREF_PATH.read_text(encoding="utf-8").strip().split("\n")
                if l and TD2 not in l]
        _cs.USER_PREF_PATH.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")


def test_r2_choose_and_pref(sandbox_r2):
    _cs.cmd_choose(_NS(date=TD2, choice=2, round=2, note=""))
    sel = json.loads((_cs.SELECTIONS_DIR / f"{TD2}.json").read_text(encoding="utf-8"))
    assert sel.get("topic") == "R2乙选题" and sel.get("cand_id") == "b2"
    slots = _cs.load_works(TD2)["slots"]
    assert slots and slots[-1]["topic"] == "R2乙选题"
    _ok(client.post("/api/topics/pref", json={"date": TD2, "round": 2, "rank": 1, "action": "like"}))
    pref_last = _cs.USER_PREF_PATH.read_text(encoding="utf-8").strip().split("\n")[-1]
    assert "R2甲选题" in pref_last and "R1甲选题" not in pref_last


# ============ E. 红线手动检测 ============
def test_redline_phrase_check():
    data = _ok(client.post("/api/settings/redline-check",
                           json={"text": "男人都这样，买基金稳赚", "mode": "phrase"}))
    assert data["result"]["should_block"]


def test_redline_semantic_mock(monkeypatch):
    monkeypatch.setattr(_cs, "redline_llm_semantic",
                        lambda text, **kw: {"risk_level": "safe", "hits": [],
                                            "should_block": False})
    import backend.app.api.settings as st
    monkeypatch.setattr(st.cat_shop, "redline_llm_semantic", _cs.redline_llm_semantic)
    data = _ok(client.post("/api/settings/redline-check",
                           json={"text": "今晚给自己泡一杯热茶，好好休息。", "mode": "semantic"}))
    assert data["result"]["risk_level"] == "safe"


# ============ F. Agent 路由（FakeRunner） ============
class _FakeRunner:
    """替身 runner：记录调用、返回脚本化状态，不碰真实 checkpoint/LLM。"""

    def __init__(self):
        self.calls = []
        self.state = {"date": "", "status": "none", "steps_used": 3, "cost_cny": 0.42,
                      "goal": "测试目标", "pending_approval": None, "is_active": False}

    def get_run_state(self, date, **kw):
        return dict(self.state, date=date)

    def read_trace(self, date, limit=100):
        return [{"ts": "2099-01-08T10:00:00", "node": "init", "goal": "测试目标"},
                {"ts": "2099-01-08T10:00:20", "node": "act", "tool": "write_draft",
                 "status": "ok", "message": "写稿完成", "gate_passed": False}]

    def start_run(self, date, goal="", **kw):
        self.calls.append(("start", date, goal))
        return {"status": "waiting_topic"}

    def resume_run(self, date, payload, **kw):
        self.calls.append(("resume", date, payload))
        return {"status": "done"}

    def raise_budget(self, date, **kw):
        self.calls.append(("budget", date))
        return {"status": "done"}

    def cancel_run(self, date, **kw):
        self.calls.append(("cancel", date))
        return {"status": "cancelled"}


AD = "2099-01-08"


@pytest.fixture()
def fake_runner(monkeypatch):
    import backend.app.api.agent as agapi
    fake = _FakeRunner()
    monkeypatch.setattr(agapi, "runner", fake)
    yield fake
    import shutil
    shutil.rmtree(str(_cs.DATA_DIR / "agent_runs" / AD), ignore_errors=True)


def test_agent_state_and_snapshot(fake_runner):
    data = _ok(client.get(f"/api/agent/{AD}"))
    assert data["state"]["status"] == "none" and data["agent_ready"]
    assert data["meta"]["label"] == "未运行"


def test_agent_topic_approval_payload(fake_runner):
    fake_runner.state.update(status="waiting_topic", pending_approval={
        "type": "topic_confirm", "date": AD,
        "top5": [{"rank": 1, "topic": "深夜路灯下的猫", "total": 8.7, "compliance_blocked": False},
                 {"rank": 2, "topic": "给四年前的自己写信", "total": 8.2, "compliance_blocked": True}],
        "question": "请选择"})
    snap = _ok(client.get(f"/api/agent/{AD}"))
    assert snap["state"]["pending_approval"]["top5"][1]["compliance_blocked"]
    _ok(client.post("/api/agent/approve", json={"date": AD, "choice": 2, "note": "就它了"}))
    time.sleep(0.2)
    kind, date, payload = fake_runner.calls[0]
    assert kind == "resume" and payload == {"choice": 2, "note": "就它了"}


def test_agent_publish_approval_and_nowait(fake_runner):
    fake_runner.state.update(status="waiting_publish", pending_approval={
        "type": "publish_confirm", "date": AD, "title": "夜里的灯", "word_count": 1100,
        "checks_summary": {"total": 17, "passed": 17, "all_ok": True}, "gate_passed": True})
    _ok(client.post("/api/agent/approve", json={"date": AD, "approved": True}))
    time.sleep(0.3)
    _ok(client.post("/api/agent/approve", json={"date": AD, "approved": False, "note": "开头太平"}))
    time.sleep(0.3)
    # 非等待态审批被拒
    fake_runner.state.update(status="done", pending_approval=None)
    r = client.post("/api/agent/approve", json={"date": AD, "choice": 1})
    assert not r.json()["ok"] and r.json()["error"]["message"].find("无需审批") >= 0
    kinds = [c[0] for c in fake_runner.calls]
    assert kinds == ["resume", "resume"]


def test_agent_start_budget_cancel(fake_runner):
    _ok(client.post("/api/agent/start", json={"date": AD, "goal": "测试目标"}))
    time.sleep(0.3)
    _ok(client.post("/api/agent/budget", json={"date": AD, "extra_steps": 5, "extra_cost": 2}))
    time.sleep(0.3)
    _ok(client.post("/api/agent/cancel", json={"date": AD}))
    time.sleep(0.6)
    kinds = [c[0] for c in fake_runner.calls]
    assert kinds == ["start", "budget", "cancel"]
    assert fake_runner.calls[0][2] == "测试目标"


# ============ G. 档案文件查看器安全 ============
def test_archive_file_traversal_guard():
    r = client.get("/api/archive/file", params={"path": "../../.env"})
    assert not r.json()["ok"]
    # 自足样例：沙箱日期写文件再经查看器读回（不依赖存量 data/）
    from core.paths import DRAFTS_DIR
    stash = DRAFTS_DIR / "2099-02-04_top5.json"
    stash.write_text('{"results": [{"rank": 1}]}', encoding="utf-8")
    try:
        data = _ok(client.get("/api/archive/file", params={"path": "drafts/2099-02-04_top5.json"}))
        assert "results" in data.get("content", "") or data.get("content")
    finally:
        stash.unlink(missing_ok=True)


# ============ H. P4 对抗性审查回归 ============
def test_adversarial_host_and_origin_guard():
    """DNS rebinding（伪造 Host）与跨站 CSRF（外部 Origin）必须被 403。"""
    r = client.get("/api/dashboard", headers={"Host": "evil.example.com"})
    assert r.status_code == 403
    r = client.post("/api/agent/start", json={"date": D_STASH},
                    headers={"Host": "127.0.0.1:8765", "Origin": "http://evil.example.com"})
    assert r.status_code == 403
    # 正常同源请求不受影响
    assert client.get("/api/health").status_code == 200


def test_adversarial_date_and_slot_validation():
    """非法日期（body）→ 400；非法槽位（进入 unlink 路径）→ 400。"""
    r = client.post("/api/drafts/edit",
                    json={"date": "not-a-date", "slot": "w01", "content": "x"})
    assert r.status_code == 400
    r = client.post(f"/api/export/plan-reset?slot=../evil", headers={"Host": "127.0.0.1:8765"})
    assert r.status_code == 400


def test_adversarial_uploads_saved_as_traversal():
    r = client.post("/api/uploads/delete",
                    json={"date": "2099-03-03", "slot": "w01",
                          "saved_as": "../../.env"})
    assert not r.json()["ok"]


def test_upload_without_slot_hits_date_route():
    """回归：空槽位上传曾拼出尾随空段 /api/uploads/{date}/ → 404 兜底。
    修复后 POST /{date} 与 GET 列表对称承接，素材落 {date} 目录。"""
    import shutil
    from core.paths import UPLOADS_DIR
    try:
        r = client.post("/api/uploads/2099-03-04",
                        files={"files": ("note.md", b"hello material", "text/markdown")})
        data = _ok(r)
        assert data["added"][0]["type"] == "doc"
        assert (UPLOADS_DIR / "2099-03-04" / data["added"][0]["saved_as"]).exists()
    finally:
        shutil.rmtree(UPLOADS_DIR / "2099-03-04", ignore_errors=True)


def test_adversarial_settings_clamped():
    r = client.put("/api/settings", json={"max_steps": 0, "max_cost_cny": -5})
    data = _ok(r)
    assert data["workbench"]["max_steps"] >= 8
    assert data["workbench"]["max_cost_cny"] >= 0.5
