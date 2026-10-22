#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_chat.py · 中控台对话测试（P1：路由 / 人设注册表 / 持久化 / API 信封）。

约定：LLM 全 mock（零成本）；对话写沙箱日期 2099-01-08（.gitignore 已覆盖
data/**/2099-*），测后即清。
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.main import app  # noqa: E402
from core import personas as P  # noqa: E402
from core.chat_router import classify  # noqa: E402

client = TestClient(app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False)
AD = "2099-01-08"


def _ok(r):
    body = r.json()
    assert body.get("ok"), f"envelope fail: {str(body)[:200]}"
    return body["data"]


@pytest.fixture(autouse=True)
def _no_llm_key(monkeypatch):
    """路由测试环境不配 key → classify 走 chief 兜底，绝不打真实 LLM。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _sandbox_chat_file():
    yield
    from backend.app.api import chat as chat_api
    for p in (chat_api._chat_path(AD), chat_api._action_path(AD)):
        if p.exists():
            p.unlink()


# ===== 注册表 =====
def test_persona_registry_integrity():
    ids = [p["id"] for p in P.roster()]
    assert ids[0] == "chief" and len(ids) == 7
    names = [p["name"] for p in P.roster()]
    assert len(set(names)) == len(names), "花名必须唯一"
    for p in P.roster():
        assert p["name"] and p["role"] and p["color"].startswith("#") and p["duty"]


def test_keyword_routing():
    assert P.route_keyword("今天中秋，写点中秋选题") == "topic"
    assert P.route_keyword("帮我写个开头") == "writer"
    assert P.route_keyword("检查一下红线") == "reviewer"
    assert P.route_keyword("封面还没出") == "artist"
    assert P.route_keyword("推到草稿箱") == "publisher"
    assert P.route_keyword("上周阅读量怎么样") == "archivist"
    assert P.route_keyword("今天进度怎么样") == "chief"


def test_mention_routing():
    assert P.route_keyword("@撰稿人 开头太平了") == "writer"
    assert P.route_keyword("问问@选题官 候选好了吗") == "topic"
    assert P.route_keyword("@主编 安排一下") == "chief"


def test_classify_no_key_falls_back_chief():
    pid, act = classify("就是随便聊聊天气")
    assert pid == "chief" and act is False


# ===== API =====
def test_personas_endpoint():
    data = _ok(client.get("/api/chat/personas"))
    assert len(data["personas"]) == 7
    assert data["personas"][0]["id"] == "chief"
    assert all(p["busy"] is False for p in data["personas"])


def test_brief_endpoint():
    data = _ok(client.get(f"/api/chat/brief?date={AD}"))
    assert len(data["steps"]) == 10
    assert "time_ctx" in data and "agent" in data
    assert "cost_today" in data and "streak" in data


def test_chat_flow_with_mock_llm(monkeypatch):
    from backend.app.api import chat as chat_api
    monkeypatch.setattr(chat_api, "persona_reply",
                        lambda pid, msg, date, entries=None, extra_context="": f"[{pid}] 收到：{msg[:20]}")
    r = _ok(client.post("/api/chat", json={"date": AD, "text": "今天中秋，写点中秋选题"}))
    assert r["persona"] == "topic"
    assert r["user"]["role"] == "user" and r["reply"]["persona"] == "topic"
    assert r["reply"]["text"].startswith("[topic]")
    # 持久化：历史可回放
    hist = _ok(client.get(f"/api/chat/{AD}"))
    assert len(hist["messages"]) == 2
    assert hist["messages"][0]["role"] == "user"
    assert hist["messages"][1]["mode"] == "chat"


def test_chat_empty_rejected():
    body = client.post("/api/chat", json={"date": AD, "text": "   "}).json()
    assert not body["ok"]


def test_chat_history_empty_date():
    data = _ok(client.get(f"/api/chat/{AD}"))
    assert data["messages"] == []


# ===== P2 动手模式：确认卡 → 执行 → 产物卡 =====
def test_chat_act_offer_and_confirm_flow(monkeypatch):
    from backend.app.api import chat as chat_api
    monkeypatch.setattr(chat_api, "persona_reply",
                        lambda pid, msg, date, entries=None, extra_context="": f"[{pid}] ok")
    # 「生成一批候选」→ 选题官 + 动手意图 → 回复带确认卡，状态机进入 offer
    r = _ok(client.post("/api/chat", json={"date": AD, "text": "生成一批今天的候选"}))
    assert r["reply"]["status"] == "offer"
    card = r["reply"]["cards"][0]
    assert card["type"] == "confirm" and card["action"] == "new_batch"
    st = chat_api._read_action(AD)
    assert st["status"] == "offer" and st["msg_id"] == r["reply"]["id"]
    # 批准 → 走既有 CLI 命令通道（mock run_command 捕获参数）
    cmds = []
    monkeypatch.setattr(chat_api.taskrunner, "run_command",
                        lambda args, label=None: cmds.append((args, label)) or "tid1")
    r2 = _ok(client.post("/api/chat/confirm",
                         json={"date": AD, "msg_id": r["reply"]["id"], "approved": True}))
    assert r2["task_id"] == "tid1"
    assert cmds[0][0][0] == "new-batch" and "--date" in cmds[0][0]
    assert "选题官" in cmds[0][1]
    st = chat_api._read_action(AD)
    assert st["status"] == "running" and st["tid"] == "tid1"
    # 任务完成 → 读取历史时推进：产物卡落地、状态清空
    monkeypatch.setattr(chat_api.taskrunner, "get_task",
                        lambda tid: {"status": "done", "output": "20 候选"})
    hist = _ok(client.get(f"/api/chat/{AD}"))
    assert hist["action"] is None
    last = hist["messages"][-1]
    assert last["role"] == "agent" and last["mode"] == "act" and last["persona"] == "topic"
    assert last["cards"][0]["type"] == "top5"


def test_confirm_decline_clears_offer(monkeypatch):
    from backend.app.api import chat as chat_api
    monkeypatch.setattr(chat_api, "persona_reply",
                        lambda pid, msg, date, entries=None, extra_context="": "[topic] ok")
    r = _ok(client.post("/api/chat", json={"date": AD, "text": "生成一批候选"}))
    r2 = _ok(client.post("/api/chat/confirm",
                         json={"date": AD, "msg_id": r["reply"]["id"], "approved": False}))
    assert r2["cancelled"] is True
    assert chat_api._read_action(AD) is None
    hist = _ok(client.get(f"/api/chat/{AD}"))
    assert any(m["role"] == "system" and "先不动手" in m["text"] for m in hist["messages"])


def test_confirm_stale_msg_rejected():
    body = client.post("/api/chat/confirm",
                       json={"date": AD, "msg_id": "nonexistent"}).json()
    assert not body["ok"]


def test_choose_endpoint(monkeypatch):
    from backend.app.api import chat as chat_api
    cmds = []
    monkeypatch.setattr(chat_api.taskrunner, "run_command",
                        lambda args, label=None: cmds.append(args) or "t1")
    _ok(client.post("/api/chat/choose", json={"date": AD, "choice": 2, "round": 1}))
    assert cmds[0][:2] == ["choose", "--date"] and "2" in cmds[0]
    hist = _ok(client.get(f"/api/chat/{AD}"))
    assert any(m.get("event") == "chosen" for m in hist["messages"])
    # 同事互聊：撰稿人接单台词（交接可视化）
    assert any(m["role"] == "agent" and m.get("persona") == "writer" for m in hist["messages"])
    # 越界序号拒绝
    body = client.post("/api/chat/choose", json={"date": AD, "choice": 9}).json()
    assert not body["ok"]


def test_write_action_uses_active_slot(monkeypatch):
    """写稿类动作必须带上活动槽位（与手动面板同语义）。"""
    from backend.app.api import chat as chat_api
    monkeypatch.setattr(chat_api, "persona_reply",
                        lambda pid, msg, date, entries=None, extra_context="": "[writer] ok")
    r = _ok(client.post("/api/chat", json={"date": AD, "text": "@撰稿人 写 v1"}))
    assert r["reply"]["cards"][0]["action"] == "write"
    cmds = []
    monkeypatch.setattr(chat_api.taskrunner, "run_command",
                        lambda args, label=None: cmds.append(args) or "tid")
    _ok(client.post("/api/chat/confirm",
                    json={"date": AD, "msg_id": r["reply"]["id"], "approved": True}))
    assert cmds[0][0] == "write"


# ===== P5 关联性：肯定跟进直达执行 / 拒绝跟进 / 流程级动作 =====
def test_act_intent_short_commands():
    """短口令不再漏判（「现在马上写」曾绕圈子的根因）。"""
    from core.chat_router import _act_intent_heuristic as act, is_affirmative
    assert act("现在马上写") is True
    assert act("写两篇文稿") is True
    assert act("直接画") is True
    assert act("这个开头怎么写") is False      # 咨询不算动手
    assert act("今晚写什么好") is False
    assert is_affirmative("现在马上写") is True
    assert is_affirmative("好，画吧") is True
    assert is_affirmative("嗯") is True
    assert is_affirmative("先别") is False
    assert is_affirmative("换个方向试试") is False


def test_affirmative_followup_executes_directly(monkeypatch):
    """确认卡挂起时回「现在马上写」→ 直接执行，不再绕圈询问。"""
    from backend.app.api import chat as chat_api
    monkeypatch.setattr(chat_api, "persona_reply",
                        lambda pid, msg, date, entries=None, extra_context="": "[topic] ok")
    r = _ok(client.post("/api/chat", json={"date": AD, "text": "生成一批候选"}))
    assert r["reply"]["status"] == "offer"
    cmds = []
    monkeypatch.setattr(chat_api.taskrunner, "run_command",
                        lambda args, label=None: cmds.append(args) or "tidX")
    # persona_reply 若被再次调用即为绕圈（mock 会留下痕迹：这里直接断言结果）
    r2 = _ok(client.post("/api/chat", json={"date": AD, "text": "现在马上写"}))
    assert r2["executed"] is True
    assert r2["task_id"] == "tidX"
    assert cmds[0][0] == "new-batch"
    # 状态机进入 running
    st = chat_api._read_action(AD)
    assert st["status"] == "running"


def test_decline_followup_cancels(monkeypatch):
    from backend.app.api import chat as chat_api
    monkeypatch.setattr(chat_api, "persona_reply",
                        lambda pid, msg, date, entries=None, extra_context="": "[topic] ok")
    r = _ok(client.post("/api/chat", json={"date": AD, "text": "生成一批候选"}))
    r2 = _ok(client.post("/api/chat", json={"date": AD, "text": "先不急，等会再说"}))
    assert r2["declined"] is True
    assert chat_api._read_action(AD) is None


def test_oneclick_flow_action(monkeypatch):
    """「帮我写完这篇」→ 流程级动作 oneclick（写稿→标题→排版一条龙）。"""
    from backend.app.api import chat as chat_api
    monkeypatch.setattr(chat_api, "persona_reply",
                        lambda pid, msg, date, entries=None, extra_context="": "[writer] ok")
    r = _ok(client.post("/api/chat", json={"date": AD, "text": "@撰稿人 帮我把这篇写完"}))
    assert r["reply"]["cards"][0]["action"] == "oneclick"
    fns = []
    monkeypatch.setattr(chat_api.taskrunner, "run_function",
                        lambda fn, kwargs=None, label=None, key=None: fns.append(label) or "tidF")
    r2 = _ok(client.post("/api/chat", json={"date": AD, "text": "好，开写"}))
    assert r2["executed"] is True and r2["task_id"] == "tidF"
    assert "一键" in fns[0] or "写完" in fns[0]


def test_cover_action_with_force(monkeypatch):
    """「换一张头图」→ cover 动作且带 force（重画）。"""
    from backend.app.api import chat as chat_api
    monkeypatch.setattr(chat_api, "persona_reply",
                        lambda pid, msg, date, entries=None, extra_context="": "[artist] ok")
    r = _ok(client.post("/api/chat", json={"date": AD, "text": "@美编 换一张头图"}))
    assert r["reply"]["cards"][0]["action"] == "cover"
    assert chat_api._read_action(AD).get("force") is True
    fns = []
    monkeypatch.setattr(chat_api.taskrunner, "run_function",
                        lambda fn, kwargs=None, label=None: fns.append(label) or "tidC")
    _ok(client.post("/api/chat/confirm",
                    json={"date": AD, "msg_id": r["reply"]["id"], "approved": True}))
    assert "头图" in fns[0]


def test_running_action_no_repropose(monkeypatch):
    """动作运行中再发消息，语境里带「正在执行」，且不递新确认卡。"""
    from backend.app.api import chat as chat_api
    chat_api._write_action(AD, {"status": "running", "action": "new_batch",
                                "persona": "topic", "tid": "t", "msg_id": "", "slot": ""})
    seen = {}
    monkeypatch.setattr(chat_api, "persona_reply",
                        lambda pid, msg, date, entries=None, extra_context="":
                        seen.setdefault("ctx", extra_context) or "[topic] ok")
    r = _ok(client.post("/api/chat", json={"date": AD, "text": "生成一批候选"}))
    assert "正在执行" in seen["ctx"]
    assert not r["reply"].get("cards")


def test_action_refine_by_keyword():
    """同一人设下按关键词细分动作：标题→titles，大纲→outline。"""
    from backend.app.api import chat as chat_api
    assert chat_api._refine_action("writer", "生成标题方案", "write") == "titles"
    assert chat_api._refine_action("writer", "出个大纲", "write") == "outline"
    assert chat_api._refine_action("writer", "写 v1", "write") == "write"
    assert chat_api._refine_action("topic", "生成候选", "new_batch") == "new_batch"


def test_auto_run_chief_action(monkeypatch):
    """P4：主编的全套活儿 → 确认后走 Agent 主图（_start_agent_run）。"""
    from backend.app.api import chat as chat_api
    monkeypatch.setattr(chat_api, "persona_reply",
                        lambda pid, msg, date, entries=None, extra_context="": "[chief] ok")
    r = _ok(client.post("/api/chat", json={"date": AD, "text": "@主编 开始今天的全套活儿"}))
    assert r["reply"]["cards"][0]["action"] == "auto_run"
    monkeypatch.setattr(chat_api, "_start_agent_run", lambda date: "tidA")
    r2 = _ok(client.post("/api/chat/confirm",
                         json={"date": AD, "msg_id": r["reply"]["id"], "approved": True}))
    assert r2["task_id"] == "tidA" and r2["action"] == "auto_run"


def test_follow_agent_mode(monkeypatch):
    """跟随模式：tid='agent' 时看 Agent 停靠状态而非 taskrunner。"""
    from backend.app.api import chat as chat_api
    chat_api._write_action(AD, {"status": "running", "action": "auto_run",
                                "persona": "chief", "tid": "agent", "msg_id": "", "slot": ""})
    # Agent 停在发布关卡 → 冒泡 exported 卡并清状态
    monkeypatch.setattr(chat_api, "query_state",
                        lambda d: {"status": "waiting_publish", "running": False})
    hist = _ok(client.get(f"/api/chat/{AD}"))
    assert hist["action"] is None
    last = hist["messages"][-1]
    assert last["persona"] == "chief" and last["cards"][0]["type"] == "exported"
    # Agent 还在跑 → 继续等待（状态不清）
    chat_api._write_action(AD, {"status": "running", "action": "auto_run",
                                "persona": "chief", "tid": "agent", "msg_id": "", "slot": ""})
    monkeypatch.setattr(chat_api, "query_state",
                        lambda d: {"status": "acting", "running": True})
    hist = _ok(client.get(f"/api/chat/{AD}"))
    assert hist["action"]["status"] == "running"


# ===== 方案 A/B：对话内嵌操作卡 + 工作台事件冒泡 =====
def test_done_cards_oneclick_has_titles_and_checklist():
    """oneclick 完成 → 标题点选卡 + 发布检查单卡（对话里直接拍板）。"""
    from backend.app.api import chat as chat_api
    cards = chat_api._build_done_cards(AD, "oneclick", {"slot": ""})
    types = [c["type"] for c in cards]
    assert "titles" in types and "exported" in types
    ck = next(c for c in cards if c["type"] == "exported")
    assert len(ck["checklist"]) == 7


def test_done_cards_write_has_next_step():
    from backend.app.api import chat as chat_api
    cards = chat_api._build_done_cards(AD, "format", {"slot": ""})
    ck = cards[0]
    assert ck["type"] == "exported" and len(ck["checklist"]) == 7


def test_topics_choose_bubbles_into_chat(monkeypatch):
    """方案 B：选题中心手动点选 → 对话冒泡 + 撰稿人接单。"""
    from backend.app.api import chat as chat_api
    monkeypatch.setattr(chat_api.taskrunner, "run_command",
                        lambda args, label=None: "tidN")
    _ok(client.post("/api/topics/choose", json={"date": AD, "choice": 3, "round": 1}))
    hist = _ok(client.get(f"/api/chat/{AD}"))
    texts = [m["text"] for m in hist["messages"]]
    assert any("选题中心拍板" in t for t in texts)
    assert any(m.get("persona") == "writer" for m in hist["messages"])


def test_new_batch_bubbles(monkeypatch):
    from backend.app.api import chat as chat_api
    monkeypatch.setattr(chat_api.taskrunner, "run_command",
                        lambda args, label=None: "tidB")
    _ok(client.post("/api/topics/new-batch?date=" + AD))
    hist = _ok(client.get(f"/api/chat/{AD}"))
    assert any("生成新批次" in m["text"] for m in hist["messages"])


# ===== 整改第一批 =====
def test_action_selfheal_after_restart(monkeypatch):
    """服务重启后动作表丢失（内存）→ 查询历史时自愈：不再永久卡 running。"""
    from backend.app.api import chat as chat_api
    chat_api._write_action(AD, {"status": "running", "action": "new_batch",
                                "persona": "topic", "tid": "ghost-tid",
                                "msg_id": "", "slot": ""})
    hist = _ok(client.get(f"/api/chat/{AD}"))
    assert hist["action"] is None  # 自愈清状态
    assert any("服务重启" in m["text"] for m in hist["messages"])
    # 自愈后可正常再下单
    monkeypatch.setattr(chat_api, "persona_reply",
                        lambda pid, msg, date, entries=None, extra_context="": "[t] ok")
    r = _ok(client.post("/api/chat", json={"date": AD, "text": "生成一批候选"}))
    assert r["reply"]["status"] == "offer"


def test_slot_id_never_reused():
    """删 w01 后新建槽位应为 w03（旧算法 len+1 会再算出 w02 撞现存槽位）。"""
    import cat_shop
    works = {"date": AD, "slots": [
        {"id": "w01", "round": 1, "cand_id": "c1", "status": "draft"},
        {"id": "w02", "round": 1, "cand_id": "c2", "status": "draft"}],
        "active_slot": "w02"}
    cat_shop.write_json(cat_shop.WORKS_DIR / f"{AD}.json", works)
    try:
        ok, _ = cat_shop.delete_slot(AD, "w01")
        assert ok
        sid, _meta, is_new = cat_shop.create_slot(AD, 3, {"cand_id": "c9", "topic": "x",
                                                          "motif_series": "B"}, 1)
        assert is_new and sid == "w03", f"新槽位应为 w03，实际 {sid}"
        ids = [s["id"] for s in cat_shop.load_works(AD)["slots"]]
        assert len(ids) == len(set(ids)), "槽位 id 必须唯一"
    finally:
        (cat_shop.WORKS_DIR / f"{AD}.json").unlink(missing_ok=True)


def test_concurrent_confirm_single_flight(monkeypatch):
    """双标签页同时确认同一张卡 → 只执行一次，后到者 no_offer。"""
    import threading
    from backend.app.api import chat as chat_api
    monkeypatch.setattr(chat_api, "persona_reply",
                        lambda pid, msg, date, entries=None, extra_context="": "[t] ok")
    r = _ok(client.post("/api/chat", json={"date": AD, "text": "生成一批候选"}))
    mid = r["reply"]["id"]
    calls = []
    monkeypatch.setattr(chat_api.taskrunner, "run_command",
                        lambda args, label=None: calls.append(args) or "tid1")
    results = []
    def _confirm():
        from fastapi.testclient import TestClient
        c = TestClient(app, base_url="http://127.0.0.1:8765",
                       raise_server_exceptions=False)
        rr = c.post("/api/chat/confirm",
                    json={"date": AD, "msg_id": mid, "approved": True})
        results.append(rr.json().get("ok"))
    ts = [threading.Thread(target=_confirm) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(calls) == 1, f"run_command 应只执行一次，实际 {len(calls)}"
    assert sorted(results) == [False, True]  # 一成功一 no_offer


# ===== 整改第二批 =====
def test_choose_msg_id_idempotent(monkeypatch):
    """同一张 Top5 卡（同 msg_id）重复点选 → 只执行一次 choose。"""
    from backend.app.api import chat as chat_api
    r = _ok(client.post("/api/chat", json={"date": AD, "text": "生成一批候选"}))
    card_msg = r["reply"]["id"]
    cmds = []
    monkeypatch.setattr(chat_api.taskrunner, "run_command",
                        lambda args, label=None: cmds.append(args) or "t1")
    first = _ok(client.post("/api/chat/choose",
                           json={"date": AD, "choice": 2, "round": 1, "msg_id": card_msg}))
    assert first.get("task_id") == "t1" and not first.get("duplicate")
    second = _ok(client.post("/api/chat/choose",
                            json={"date": AD, "choice": 2, "round": 1, "msg_id": card_msg}))
    assert second.get("duplicate") is True
    assert len(cmds) == 1, "重复点选不应再次执行 choose"


# ===== 整改第三批 =====
def test_chat_body_date_validation():
    """chat 写路由 body date 收口：非法形状（少补零）被拒，不再静默开第二份文件。"""
    body = client.post("/api/chat", json={"date": "2026-9-7", "text": "hi"}).json()
    assert not body.get("ok")


def test_path_date_fallback_to_today():
    """/topics/2026-9-7 形状非法 → 回退今天（曾静默渲染空白页）。"""
    from backend.app.common import today
    d = _ok(client.get("/api/topics/2026-9-7"))
    assert d["date"] == today()


def test_single_flight_key():
    """taskrunner 单飞：同 key 并发只建一个任务。"""
    import time as _t
    import threading as _th
    from backend.app import taskrunner as tr
    release = _th.Event()
    tid1 = tr.run_function(lambda: release.wait(), label="job", key="k1")
    tid2 = tr.run_function(lambda: release.wait(), label="job", key="k1")
    tid3 = tr.run_function(lambda: None, label="job", key="k2")
    assert tid1 == tid2 and tid3 != tid1
    release.set()
    _t.sleep(0.2)


def test_atomic_json_and_corrupt_fallback(tmp_path):
    from core.storage import load_json, write_json
    f = tmp_path / "x.json"
    write_json(f, {"a": 1})
    write_json(f, {"a": 2})          # 第二次写才产生 .bak（首次写无旧文件可备份）
    assert not (tmp_path / "x.json.tmp").exists()
    assert load_json(f, default=None) == {"a": 2}
    # 损坏主文件 → 回退 .bak（上一版内容）
    f.write_text("{broken", encoding="utf-8")
    assert load_json(f, default=None) == {"a": 1}
    # 无 bak 的损坏 → 默认值不抛
    f2 = tmp_path / "y.json"
    f2.write_text("{broken", encoding="utf-8")
    assert load_json(f2, default={"z": 9}) == {"z": 9}


def test_step5_counts_mp_html(tmp_path):
    from core.pipeline_status import status_map
    mp = None
    import core.pipeline_status as ps
    from core import paths as cpaths
    mp = cpaths.DRAFTS_DIR / f"{AD}_w01_mp.html"
    mp.write_text("<p>x</p>", encoding="utf-8")
    try:
        steps, _, _ = status_map(AD)
        assert steps["format"] is True
    finally:
        mp.unlink(missing_ok=True)


def test_follow_agent_busy_guard(monkeypatch):
    from backend.app.api import chat as chat_api
    chat_api._write_action(AD, {"status": "running", "action": "oneclick",
                                "persona": "writer", "tid": "t9", "msg_id": "", "slot": ""})
    body = client.post("/api/chat/follow-agent", json={"date": AD}).json()
    assert not body.get("ok")


def test_today_llm_cost_merges_tool_side(tmp_path):
    from agent.executor import _today_llm_cost
    log = tmp_path / "cost.jsonl"
    log.write_text(
        '{"date": "2099-01-08", "cost_total_cny": 0.4}' + "\n" +
        '{"date": "2026-09-28", "cost_total_cny": 9.9}' + "\n" +
        'bad line' + "\n", encoding="utf-8")
    from llm import provider
    orig = provider.COST_LOG
    provider.COST_LOG = log
    try:
        assert abs(_today_llm_cost("2099-01-08") - 0.4) < 1e-9
    finally:
        provider.COST_LOG = orig


def test_start_run_preserves_counters(monkeypatch, tmp_path):
    """续跑不清零：checkpoint 里的 steps_used/cost 播种进 initial_state。"""
    from types import SimpleNamespace as NS
    from pipeline import runner

    class StubGraph:
        def __init__(self):
            self.invoked = None

        def get_state(self, key):
            return NS(values={"steps_used": 3, "cost_cny": 0.5, "status": "paused_budget"})

        def invoke(self, st, key):
            self.invoked = st
            return {"status": "done", "steps_used": 3, "cost_cny": 0.5}

    stub = StubGraph()
    monkeypatch.setattr(runner, "_graph", lambda db, model=None: stub)
    monkeypatch.setattr(runner, "_default_db", lambda: tmp_path / "a.db")
    out = runner.start_run("2099-01-08", "goal", {"max_steps": 8})
    assert stub.invoked["steps_used"] == 3 and stub.invoked["cost_cny"] == 0.5
    assert out["steps_used"] == 3


def test_agent_busy_no_fake_bubble(monkeypatch):
    """Agent busy 时对话里不留「开工」假记录。"""
    from backend.app.api import agent as agent_api
    from backend.app.api import chat as chat_api
    monkeypatch.setitem(agent_api._active, AD, "someone")
    body = client.post("/api/agent/start", json={"date": AD, "goal": "x"}).json()
    assert not body.get("ok")
    hist = _ok(client.get(f"/api/chat/{AD}"))
    assert not any("全套活儿" in m.get("text", "") for m in hist["messages"])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
