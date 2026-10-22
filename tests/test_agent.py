#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_agent.py · Agent 核心 6 路径测试（Phase 3 验收项 + v4.1 扩展）。

planner 用剧本模型（ScriptedModel），工具内部 LLM 全部 mock（module 参数分发），
零真实 API 成本。checkpoint 落临时 sqlite 文件，沙箱日期 2099-01-0X 全量清理。

覆盖 LangGraph 图的关键决策路径：
  ① collect → score → 选题确认 interrupt（waiting_topic + Top5 载荷）
  ② 选题批准 → 写稿（短稿，门禁不过）→ edit_draft 重写 → 过门禁 →
     标题 → 导出 → 发布确认 interrupt → 批准 → done（全生命周期）+ reflect 经验落盘
  ③ edit_draft 触红线 → 门禁拦截 → escalate_to_human → blocked（ESCALATED）+ reflect
  ④ 步数预算耗尽 → paused_budget → 提额续跑 → 恢复执行
  ⑤ 进程崩溃（弃连接弃图）→ 新连接 + 新图实例从 checkpoint 恢复 → 续跑完成
  ⑥ 软门禁（v4.1 A4）：write 低分 → rewrite_once → edit 重写 → 仍低分 → pass 放行
"""
import contextlib
import importlib
import json
import shutil
import sqlite3
import sys
import tempfile
import traceback
from itertools import count
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from agent.config import AgentRunConfig
from agent.state import initial_state
from core.paths import (BATCHES_DIR, CANDIDATES_DIR, DATA_DIR, DRAFTS_DIR,
                        HISTORY_PATH, QUOTES_PATH, SELECTIONS_DIR, WORKS_DIR)
from core.storage import load_json
from pipeline.runner import (get_run_state, raise_budget, read_trace,
                             resume_run, start_run)

D1 = "2099-01-02"   # 路径①
D2 = "2099-01-03"   # 路径②
D3 = "2099-01-04"   # 路径③
D4 = "2099-01-05"   # 路径④
D5 = "2099-01-06"   # 路径⑤
D6 = "2099-01-07"   # 路径⑥
SANDBOX_PREFIX = "2099-01-0"

CAND_TOPICS = [f"Agent测试选题{i}号：深夜城市的第{i}盏灯" for i in range(1, 16)]


# ===== 剧本 planner（假模型） =====
_CALL_IDS = count(1)


def _ai_tool(name, **args):
    return AIMessage(content="", tool_calls=[
        {"name": name, "args": args, "id": f"call_{next(_CALL_IDS)}"}])


class _ScriptedBound:
    def __init__(self, model):
        self._model = model

    def invoke(self, messages, **kw):
        return self._model._next()


class ScriptedModel:
    """按剧本依次返回工具调用决策；剧本耗尽即抛错（计划外的 plan 调用会显式失败）。"""

    def __init__(self, script):
        self.script = list(script)
        self.i = 0

    def _next(self):
        if self.i >= len(self.script):
            raise AssertionError("ScriptedModel 剧本耗尽（出现计划外的 plan 调用）")
        msg = self.script[self.i]
        self.i += 1
        return msg

    def bind_tools(self, tools):
        return _ScriptedBound(self)


# ===== 工具内部 LLM mock（按 module 参数分发） =====
def _topics_llm(prompt, module="", **kw):
    if module == "collect":
        return {"candidates": [{"topic": t, "motif_series": "B", "angle": "测试角度"}
                               for t in CAND_TOPICS]}
    if module == "score":
        return {"results": [{"topic": t,
                             "scores": {"共鸣": 4, "时效": 3, "争议": 2,
                                        "独特": 4, "写作可行": 4},
                             "reasoning": "测试"} for t in CAND_TOPICS]}
    raise AssertionError(f"未预期的 topics LLM 调用: module={module}")


def _writing_llm(write_content):
    def mock(prompt, module="", **kw):
        if module == "write":
            return write_content
        if module == "titles":
            return {"titles": [{"title": f"测试标题方案{i}：夜里的灯"} for i in (1, 2, 3)]}
        if module == "redline_semantic":
            return {"violations": []}
        raise AssertionError(f"未预期的 writing LLM 调用: module={module}")
    return mock


def _judge_llm(passed=True):
    """软门禁 mock：pass → 高分放行；needs_rewrite → 低分附建议。"""
    scores_pass = {"resonance": 4, "structure": 4, "tone": 4, "uniqueness": 4, "overall": 4}
    scores_fail = {"resonance": 3, "structure": 3, "tone": 3, "uniqueness": 2, "overall": 2}

    def mock(prompt, module="", **kw):
        assert module == "judge", f"未预期的 judge LLM 调用: module={module}"
        if passed:
            return {"scores": scores_pass, "suggestions": [], "verdict": "pass"}
        return {"scores": scores_fail,
                "suggestions": ["结尾第2段劝导句换成场景留白"],
                "verdict": "needs_rewrite"}
    return mock


def _reflect_llm(lesson="测试经验：E系列配反例切入字数常不足，优先时间切片"):
    def mock(prompt, module="", **kw):
        assert module == "reflect", f"未预期的 reflect LLM 调用: module={module}"
        return {"lesson": lesson, "series": "E", "tags": ["字数"], "keep": True}
    return mock


@contextlib.contextmanager
def _sandbox_episodes():
    """episodes 指向临时文件：测试经验不污染生产 data/memory/episodes.jsonl。"""
    from agent import reflection
    old = reflection.EPISODES_PATH
    with tempfile.TemporaryDirectory() as td:
        reflection.EPISODES_PATH = Path(td) / "episodes.jsonl"
        try:
            yield reflection.EPISODES_PATH
        finally:
            reflection.EPISODES_PATH = old


@contextlib.contextmanager
def _mock_llm(module_name, fn):
    mod = importlib.import_module(module_name)
    old = getattr(mod, "call_llm")
    setattr(mod, "call_llm", fn)
    try:
        yield
    finally:
        setattr(mod, "call_llm", old)


@pytest.fixture(autouse=True)
def _isolate_past_drafts(monkeypatch):
    """沙箱隔离：真实旧稿（持续增长）不得泄入沙箱写作链路的
    相似句检测 / 风格参考 / 标题反重复——否则剧本化断言随真实数据漂移。
    （教训：2026-09-17 起真实 09-15/16 稿导致 path2/4/6 剧本耗尽。）"""
    import pipeline.writing as w
    import agent.gates as g
    import tools.quality_tools as q
    monkeypatch.setattr(w, "_load_past_drafts", lambda *a, **k: ([], [], []))
    monkeypatch.setattr(w, "_load_past_full", lambda *a, **k: [])
    monkeypatch.setattr(g, "_load_past_full", lambda *a, **k: [])
    monkeypatch.setattr(q, "_load_past_full", lambda *a, **k: [])


@pytest.fixture(autouse=True)
def _isolate_wechat(monkeypatch):
    """沙箱隔离：发布链路绝不外呼微信 API（测试环境 token 配额/白名单波动
    会反噬剧本化断言）。推送走 mock 素材库与 mock draft_add。"""
    import core.wechat as wc
    monkeypatch.setattr(wc, "api_health", lambda *a, **k: (True, "mock"))
    monkeypatch.setattr(wc, "get_access_token", lambda *a, **k: ("mock_token", None))
    monkeypatch.setattr(wc, "_api_get", lambda *a, **k: ({"item": []}, None))
    monkeypatch.setattr(wc, "_api_post",
                        lambda *a, **k: ({"errcode": 0, "media_id": "MID_DRAFT"}, None))
    monkeypatch.setattr(wc, "_multipart",
                        lambda *a, **k: ({"media_id": "MID", "url": "https://mmbiz.mock/u.png"}, None))
    monkeypatch.setattr(wc, "list_image_materials",
                        lambda *a, **k: ([{"media_id": "MID_COVER", "name": "mock"}], None))
    monkeypatch.setattr(wc, "swap_cover_url",
                        lambda html, *a, **k: (html, "MID_COVER", "生成头图", None))
    monkeypatch.setattr(wc, "draft_add",
                        lambda title, content, cover, author="创作工作台", digest="": ("MID_DRAFT", None))


@pytest.fixture(autouse=True)
def _sandbox_lifecycle(monkeypatch):
    """沙箱生命周期（P4 根治）：pytest 模式下 _cleanup_sandbox 原本只在 __main__
    调用，失败运行的 2099-01-* 产物跨运行残留 → collect/score 幂等跳过 →
    剧本错位级联失败。现在每个测试前后强制清理，保证干净起点与不留残留。

    同时隔离两类真实外呼（慢 + 花钱 + 不确定性）：
    - 导出期文中配图（P3）：LLM 选点 + MiniMax 生图（单次 export 烧 95s/¥0.1+）
    - 导出期语义红线（P1-13）：真实 DeepSeek 调用
    """
    import cat_shop as _cs
    import core.inline_images as _ii
    _cleanup_sandbox()
    monkeypatch.setattr(_ii, "ensure_inline_images", lambda md_text, *a, **k: (md_text, None))
    monkeypatch.setattr(_cs, "redline_llm_semantic",
                        lambda *a, **k: {"risk_level": "safe", "hits": [],
                                         "should_block": False})
    yield
    _cleanup_sandbox()


# ===== 测试文稿构造 =====
def _filler(tag, n):
    return (f"{tag}的第{n}个夜里，台灯把影子拉得很长，她把白天没说完的话"
            f"折成一只纸船，轻轻放进抽屉最深处，等明早的阳光来收走。")


def _good_md(tag):
    """带组件标记的合规稿：900-1300 汉字 + 规则渲染可过 17 项校验。
    :::image 行为 v4.2 已取消：保留一行验证存量稿向后兼容（渲染时静默丢弃）。"""
    paras = [_filler(tag, n) for n in range(1, 21)]
    return "\n\n".join([
        f"# {tag}夜灯信笺测试稿",
        *paras[0:6],
        ":::quote",
        f"{tag}金句一：情绪不需要立刻收拾好。",
        ":::",
        *paras[6:12],
        ":::quote",
        f"{tag}金句二：慢一点，也是往前走。",
        ":::",
        ":::image 夜色窗台与一杯温水",   # v4.2 存量稿兼容：导出应静默丢弃不渲染
        *paras[12:20],
        ":::action",
        "给今晚的自己写一句话，放进抽屉最深处，明早再看。",
        ":::",
    ])


def _redline_md(tag):
    """含 critical 级红线短语（financial）的稿，验证门禁拦截与升级人工。"""
    return (f"# {tag}红线测试稿\n\n" + _filler(tag, 1) * 3
            + "\n\n理财就买这只基金，稳赚不赔，跟着买就赚。\n\n" + _filler(tag, 2) * 3)


_SOFT_PARAS = [
    "雨停之后的巷子很安静，她把伞收起来靠在门边，鞋尖还沾着水汽。",
    "楼下便利店的灯亮着，玻璃上蒙了雾气，不知道谁画了一颗歪歪的心。",
    "电梯镜子里照出一天的疲惫，她愣了两秒，忽然对自己笑了一下。",
    "钥匙转动的声音在楼道里回响，像一句轻轻的、只有自己听得见的问候。",
    "玄关的灯是暖黄色的，光落在她的肩膀上，把影子拉得很温柔。",
    "厨房里烧了一壶水，咕嘟咕嘟的声音，替这间屋子说了几句家常话。",
    "阳台上的绿萝又长出了新叶，她伸手碰了碰，叶子凉凉的，很乖。",
    "手机亮了一下又暗下去，她没有去看，今晚的世界可以等一等。",
    "洗过的头发还带着洗发水的味道，风从窗缝里进来，轻轻拂过耳边。",
    "日记本摊开在桌上，今天的这一页，她只想写三个字：还挺好。",
    "冰箱里有一颗昨晚买的桃子，她拿出来咬了一口，甜味慢慢散开。",
    "楼上有人练琴，断断续续的，弹错了也不着急，一遍一遍地重来。",
    "窗外远处的车流像一条发光的河，慢慢流着，谁也不催谁。",
    "她把白天收到的那句谢谢翻出来又看了一遍，嘴角忍不住上扬。",
    "沙发上的毯子有太阳的味道，她把自己裹进去，像被谁轻轻抱住。",
    "桌上的茶凉了一半，她喝了一口，凉了的茶也有凉了的好。",
    "夜更深了些，城市把音量调低，她听见自己的呼吸，平稳而绵长。",
    "她给明天留了一张便签，上面写着：慢慢来，来得及。",
    "关灯之前，她又看了一眼这个小小的房间，心里说了一声晚安。",
    "黑暗温柔地落下来，像一床干净的被子，盖住了今天所有的不容易。",
    "早晨没赶上的那班地铁，此刻想起来，也不过是城市的一次深呼吸。",
    "白天的会议说了很多话，此刻都安静下来，变成窗外偶尔驶过的车灯。",
    "她把外套挂回衣柜，口袋里翻出一张旧票根，是上个月一个人看的电影。",
    "那天的散场音乐很好听，她记得自己站在影院门口，听完了才走。",
    "热水袋灌好了，塞进被窝，被褥慢慢有了让人安心的温度。",
    "室友养的猫踱进来，在床脚蜷成一团毛线，呼吸比人还轻。",
    "书翻到一半的那页夹了张书签，故事停在主角刚要转身的地方。",
    "她想，明天可以把剩下的半章读完，故事不会跑，会一直等她。",
    "台历上圈了周末的格子，那天的计划只有四个字：什么都不做。",
    "镜子边的便利贴写着朋友的电话，她说有事随时打，没事也可以打。",
    "她忽然想起自己已经很久没有发呆了，发呆也是一种休息。",
    "窗帘拉上一半，留一条缝，给月亮留一个进来的路口。",
    "楼下的猫叫了两声，又安静了，大概是找到了舒服的姿势。",
    "她把手机充上电，屏幕的光一闪，像替这一天轻轻合上了书页。",
    "加湿器吐出细细的白雾，房间里的空气慢慢变得柔软起来。",
    "桌角那盆小多肉好像又胖了一点，她决定明天给它取个名字。",
    "被窝焐热了，她伸了个懒腰，骨头缝里发出细小的、舒服的响声。",
    "晚安说出口的时候，连声音都放轻了，怕惊动了刚刚睡着的夜晚。",
]


def _soft_gate_md():
    """路径⑥专用稿：句子结构与既有测试稿完全不同，避免相似度门禁误伤。"""
    return "\n\n".join(["# 软门禁测试稿：今晚的小事", *_SOFT_PARAS,
                        ":::quote",
                        "小事不值得汇报，但值得被记住。",
                        ":::",
                        ":::action",
                        "睡前也对自己说一句：今天辛苦了。",
                        ":::"])


# ===== 沙箱清理 =====
def _cleanup_sandbox():
    for d in (CANDIDATES_DIR, DRAFTS_DIR, SELECTIONS_DIR, BATCHES_DIR, WORKS_DIR):
        for f in d.glob(f"{SANDBOX_PREFIX}*"):
            if f.is_dir():
                shutil.rmtree(f, ignore_errors=True)
            else:
                f.unlink(missing_ok=True)
    runs = DATA_DIR / "agent_runs"
    for d in runs.glob(f"{SANDBOX_PREFIX}*"):
        shutil.rmtree(d, ignore_errors=True)
    for p in (HISTORY_PATH, QUOTES_PATH):
        if p.exists():
            lines = [l for l in p.read_text(encoding="utf-8").splitlines()
                     if l and SANDBOX_PREFIX not in l]
            p.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


@contextlib.contextmanager
def _temp_db(name):
    """临时 checkpoint 库：退出前关闭 runner 缓存的全部 sqlite 连接（Windows 文件锁）。"""
    import pipeline.runner as runner_mod
    with tempfile.TemporaryDirectory() as td:
        try:
            yield Path(td) / name
        finally:
            for saver in runner_mod._Savers.values():
                try:
                    saver.conn.close()
                except Exception:
                    pass
            runner_mod._Savers.clear()
            runner_mod._Graphs.clear()


def _report(date):
    return load_json(DATA_DIR / "agent_runs" / date / "report.json", {})


# ===== 路径①：跑到选题确认 interrupt =====
def test_path1_run_to_topic_interrupt():
    with _temp_db("p1.db") as db:
        with _mock_llm("pipeline.topics", _topics_llm):
            r = start_run(D1, goal="路径①：跑到选题确认",
                          model=ScriptedModel([
                              _ai_tool("collect_candidates", date=D1),
                              _ai_tool("score_candidates", date=D1),
                          ]), db_path=db)
        assert r["status"] == "waiting_topic", f"应停在选题确认: {r}"
        pa = r["pending_approval"]
        assert pa["type"] == "topic_confirm", pa
        assert len(pa["top5"]) == 5 and pa["top5"][0]["rank"] == 1
        assert pa["top5"][0]["topic"], "Top5 卡片应带选题名"
        assert r["steps_used"] == 2, r
        st = get_run_state(D1, db_path=db)
        assert st["status"] == "waiting_topic" and st["is_active"], st
        assert (CANDIDATES_DIR / f"{D1}.json").exists(), "候选文件应落盘"
        assert (DRAFTS_DIR / f"{D1}_top5.json").exists(), "Top5 文件应落盘"
        assert any(e.get("tool") == "score_candidates" for e in read_trace(D1)), \
            "trace 应记录工具执行"


# ===== 路径②：选题批准 → 门禁重写 → 发布确认 → done + reflect 经验落盘 =====
def test_path2_full_publish_lifecycle():
    with _temp_db("p2.db") as db, _sandbox_episodes() as eps_path:
        # 阶段 A：跑到选题确认
        with _mock_llm("pipeline.topics", _topics_llm):
            ra = start_run(D2, model=ScriptedModel([
                _ai_tool("collect_candidates", date=D2),
                _ai_tool("score_candidates", date=D2),
            ]), db_path=db)
        assert ra["status"] == "waiting_topic", ra

        # 阶段 B：批准选题 → 写稿（mock 返回短稿，门禁不过）→
        #        edit_draft 重写 → 标题 → 导出 → 发布确认 interrupt
        short = "# Agent测试短稿\n\n" + _filler("青苔", 1) * 3
        good = _good_md("青苔")
        with _mock_llm("pipeline.writing", _writing_llm(short)), \
                _mock_llm("agent.judge", _judge_llm(passed=True)):
            rb = resume_run(D2, {"choice": 1, "note": "路径②批准"},
                            model=ScriptedModel([
                                _ai_tool("write_draft", date=D2),
                                _ai_tool("edit_draft", date=D2, content=good),
                                _ai_tool("generate_titles", date=D2),
                                _ai_tool("export_mp_html", date=D2),
                            ]), db_path=db)
        assert rb["status"] == "waiting_publish", f"应停在发布确认: {rb}"
        pb = rb["pending_approval"]
        assert pb["type"] == "publish_confirm", pb
        assert pb["gate_passed"] is True, "edit_draft 重写后门禁应通过"
        assert pb["word_count"] >= 900, pb
        assert pb["mp_path"] and Path(pb["mp_path"]).exists(), "导出 HTML 应落盘"
        assert pb["checks_summary"]["all_ok"] is True, pb["checks_summary"]
        assert len(pb["titles"]) == 3, pb
        from core.paths import slot_paths
        v1 = slot_paths(D2, None)["v1"].read_text(encoding="utf-8")  # 槽位对齐后随活动槽位解析
        assert _filler("青苔", 6) in v1, "edit_draft 重写内容应覆盖短稿"
        assert v1.splitlines()[0] == "# 测试标题方案1：夜里的灯", \
            "导出应把已选标题同步回文稿首行（P0-4）"

        # 阶段 C：批准发布 → reflect（经验沉淀）→ done
        with _mock_llm("agent.reflection", _reflect_llm()):
            rc = resume_run(D2, {"approved": True}, model=ScriptedModel([]), db_path=db)
        assert rc["status"] == "done", rc
        report = _report(D2)
        assert report["status"] == "done", report
        assert set(report["artifacts"]) >= {"candidates", "top5", "draft",
                                            "titles", "export"}, report["artifacts"]
        assert report["soft_gate"].get("passed") is True, report["soft_gate"]
        episodes = [json.loads(l) for l in
                    eps_path.read_text(encoding="utf-8").strip().splitlines() if l]
        assert len(episodes) == 1 and episodes[0]["keep"] is True, episodes
        assert episodes[0]["human_verdict"].get("publish") == "approved", episodes[0]
        assert "时间切片" in episodes[0]["lesson"], episodes[0]
        assert any(e.get("node") == "reflect" for e in read_trace(D2)), "trace 应记录 reflect"


# ===== 路径③：红线阻断 → escalate_to_human → blocked（reflect 仍沉淀经验） =====
def test_path3_redline_escalation():
    with _temp_db("p3.db") as db, _sandbox_episodes() as eps_path:
        with _mock_llm("agent.reflection",
                       _reflect_llm("红线测试经验：金融荐股类表述必须整段删除，不能只改词")):
            r = start_run(D3, model=ScriptedModel([
                _ai_tool("edit_draft", date=D3, content=_redline_md("灯芯绒")),
                _ai_tool("escalate_to_human", reason="门禁红线反复不过，需人工定夺",
                         question="是否换选题或人工改写？"),
            ]), db_path=db)
        assert r["status"] == "blocked", f"红线升级后应 blocked: {r}"
        report = _report(D3)
        assert report["status"] == "blocked", report
        assert report["error"]["category"] == "ESCALATED", report["error"]
        gate = report["gate_status"]
        assert gate["passed"] is False and gate["redline"]["should_block"] is True, gate
        st = get_run_state(D3, db_path=db)
        assert st["status"] == "blocked" and not st["is_active"], st
        episodes = [json.loads(l) for l in
                    eps_path.read_text(encoding="utf-8").strip().splitlines() if l]
        assert episodes and "金融" in episodes[0]["lesson"], "blocked 运行也应沉淀经验"


# ===== 路径④：预算护栏 → paused_budget → 提额续跑 =====
def test_path4_budget_guardrail_and_raise():
    with _temp_db("p4.db") as db:
        with _mock_llm("pipeline.topics", _topics_llm):
            r = start_run(D4, config={"max_steps": 1},
                          model=ScriptedModel([
                              _ai_tool("collect_candidates", date=D4),
                              _ai_tool("score_candidates", date=D4),
                          ]), db_path=db)
        assert r["status"] == "paused_budget", f"步数耗尽应暂停: {r}"
        assert r["steps_used"] == 1, r
        assert (CANDIDATES_DIR / f"{D4}.json").exists()
        assert not (DRAFTS_DIR / f"{D4}_top5.json").exists(), "score 不应执行"

        with _mock_llm("pipeline.topics", _topics_llm):
            r2 = raise_budget(D4, extra_steps=2, note="路径④提额",
                              model=ScriptedModel([
                                  _ai_tool("score_candidates", date=D4),
                              ]), db_path=db)
        assert r2["status"] == "waiting_topic", f"提额后应恢复执行: {r2}"
        assert r2["steps_used"] == 2, r2
        assert len(r2["pending_approval"]["top5"]) == 5, r2


# ===== 路径⑤：进程崩溃后从 checkpoint 恢复 =====
def test_path5_crash_recovery_from_checkpoint():
    with _temp_db("p5.db") as db, _sandbox_episodes():
        thread = {"configurable": {"thread_id": f"run-{D5}"}}

        # 进程 1：跑到选题确认 interrupt 后"崩溃"（关连接、弃图实例）
        conn1 = sqlite3.connect(str(db), check_same_thread=False)
        g1 = build_graph_for_test(ScriptedModel([
            _ai_tool("collect_candidates", date=D5),
            _ai_tool("score_candidates", date=D5),
        ]), conn1)
        with _mock_llm("pipeline.topics", _topics_llm):
            r1 = g1.invoke(initial_state(D5, "路径⑤：崩溃恢复测试",
                                         AgentRunConfig().to_dict()), thread)
        intr = r1.get("__interrupt__") or []
        assert intr and intr[0].value["type"] == "topic_confirm", "应停在选题确认"
        conn1.close()

        # 进程 2：全新连接 + 全新图实例 + 全新模型，从 checkpoint 恢复
        conn2 = sqlite3.connect(str(db), check_same_thread=False)
        g2 = build_graph_for_test(ScriptedModel([
            _ai_tool("write_draft", date=D5),
            _ai_tool("finish_run", report="崩溃恢复后完成写稿，产物已落盘"),
        ]), conn2)
        good = "# 恢复测试文稿\n\n" + "\n\n".join(
            _filler("月白", n) for n in range(1, 21))
        with _mock_llm("pipeline.writing", _writing_llm(good)), \
                _mock_llm("agent.judge", _judge_llm(passed=True)), \
                _mock_llm("agent.reflection", _reflect_llm()):
            r2 = g2.invoke(Command(resume={"choice": 2}), thread)
        assert r2["status"] == "done", f"恢复续跑应完成: {r2}"

        sel = load_json(SELECTIONS_DIR / f"{D5}.json", {})
        assert sel.get("user_choice") == 2, "恢复决策应落盘"
        from core.paths import slot_paths as _sp
        md = _sp(D5, None)["v1"].read_text(encoding="utf-8")
        assert md.startswith("# 恢复测试文稿") and _filler("月白", 20) in md
        report = _report(D5)
        assert report["status"] == "done" and report["steps_used"] == 3, report
        conn2.close()


# ===== 路径⑥：软门禁 rewrite_once → 重写一轮 → 仍低分 → pass 放行 =====
def test_path6_soft_gate_rewrite_once():
    with _temp_db("p6.db") as db, _sandbox_episodes():
        good = _soft_gate_md()
        # judge 每次都低分（needs_rewrite）：首稿 → rewrite_once；重写一轮后 → pass 放行
        judge_calls = {"n": 0}

        def judge_seq(prompt, module="", **kw):
            assert module == "judge"
            judge_calls["n"] += 1
            return {"scores": {"resonance": 3, "structure": 3, "tone": 3,
                               "uniqueness": 2, "overall": 2},
                    "suggestions": ["结尾劝导句换成场景留白"],
                    "verdict": "needs_rewrite"}

        with _mock_llm("pipeline.writing", _writing_llm(good)), \
                _mock_llm("agent.judge", judge_seq), \
                _mock_llm("agent.reflection", _reflect_llm()):
            r = start_run(D6, goal="路径⑥：软门禁重写一轮后放行",
                          model=ScriptedModel([
                              _ai_tool("edit_draft", date=D6, content=good),
                              _ai_tool("edit_draft", date=D6, content=good),
                              _ai_tool("finish_run", report="软门禁重写一轮后完成"),
                          ]), db_path=db)
        assert r["status"] == "done", r
        assert judge_calls["n"] == 2, f"软门禁应恰好评审 2 次（初稿+重写各一次）: {judge_calls}"
        report = _report(D6)
        sg = report["soft_gate"]
        assert sg["passed"] is False and sg["rewrite_used"] is True, sg
        assert sg["action"] == "pass", "重写一轮仍低分应放行（软门禁不硬拦截）"
        actions = [e.get("soft_gate_action") for e in read_trace(D6)
                   if "soft_gate_action" in e]
        assert actions == ["rewrite_once", "pass"], f"软门禁动作序列错误: {actions}"
        st = get_run_state(D6, db_path=db)
        assert st["status"] == "done", st


def build_graph_for_test(model, conn):
    from agent.graph import build_graph
    return build_graph(model=model, checkpointer=SqliteSaver(conn))


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
                traceback.print_exc()
    _cleanup_sandbox()
    total = sum(1 for n in globals() if n.startswith("test_") and callable(globals()[n]))
    print(f"\nAgent 路径测试: {total - len(failures)}/{total} PASS")
    sys.exit(1 if failures else 0)
