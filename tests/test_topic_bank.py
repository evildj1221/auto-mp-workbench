# -*- coding: utf-8 -*-
"""选题库/题材疲劳/热榜/家族配额 回归（v4.4-v4.5 反雷同反偏科）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.topic_bank import load_bank, sample_bank_line  # noqa: E402
from core.topic_families import (classify_family, classify_theme,  # noqa: E402
                                 diverse_top_k, theme_fatigue_line)


def test_theme_classification():
    assert classify_theme("秋招第四周，我把简历删到只剩一行") == "职场求职"
    assert classify_theme("妈妈打来电话，说家里的桂花开了") == "原生家庭"
    assert classify_theme("便利店的关东煮，热气模糊了玻璃") == "城市角落"
    assert classify_theme("洗碗的水声盖过了手机") == "日常其他"


def test_theme_fatigue_line():
    topics = ["秋招第四周，我把简历改回第一版", "秋招第三周，我把面试约在母校门口",
              "给今晚加班的人留一盏灯", "洗碗的水声盖过了手机"]
    line = theme_fatigue_line(topics, threshold=2)
    assert "职场求职×3" in line and "≤ 2 条" in line
    assert theme_fatigue_line(["洗碗", "浇花"], threshold=2) == ""


def test_sample_bank_line_stable():
    a = sample_bank_line("2026-10-08", n=5)
    b = sample_bank_line("2026-10-08", n=5)
    assert a == b, "同日期抽样必须稳定"
    assert "灵感库" in a or a == ""
    bank = load_bank()
    if bank.get("entries"):
        assert "灵感库样本" in sample_bank_line("2026-10-08", n=5)


def test_diverse_top_k_family_quota():
    def mk(topic, total):
        return {"topic": topic, "total": total, "family": classify_family(topic)}
    rs = [mk("秋招第四周，我把简历删到只剩一行", 9),
          mk("秋招第四周，我把面试约在母校门口", 8.8),
          mk("秋招第四周，我把期望薪资那栏空着", 8.6),
          mk("和伴侣散步，谁都没提寒露那天要去哪儿", 8.4),
          mk("桂花开了，我第一反应是想告诉一个不在的人", 8.2),
          mk("秋天的悲伤，是心在换一件厚衣服", 7.0)]
    top = diverse_top_k(rs, k=5)
    fams = [r["family"] for r in top]
    assert fams.count("F3") <= 2
    assert len(set(fams)) >= 3
    # 极端全同构也取满（放宽补齐）
    rs2 = [mk(f"秋招第四周，我把X{i}删掉", 9 - i * 0.1) for i in range(8)]
    assert len(diverse_top_k(rs2, k=5)) == 5


def test_choose_route_passes_real_round(monkeypatch):
    """选题按钮批次传参回归：choose 路由必须把真实 round 传给 CLI
   （前端曾硬编码 round:1 → r2/r3 点选被当成 r1 同题去重，w02 不建）。"""
    from backend.app.api import topics as topics_mod
    from fastapi.testclient import TestClient
    from backend.app.main import app
    client = TestClient(app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False)
    calls = []
    monkeypatch.setattr(topics_mod.taskrunner, "run_command",
                        lambda args, label=None: calls.append(args) or "t1")
    r = client.post("/api/topics/choose",
                    json={"date": "2099-01-12", "choice": 2, "round": 2}).json()
    assert r["ok"], r
    assert "--round" in calls[0] and "2" in calls[0], f"round 未透传: {calls[0]}"


def test_export_no_cover_flag(tmp_path):
    """无配图渲染：with_cover=False 时产物零 <img，且不触发生图。"""
    import cat_shop
    from core.exporter import export_article
    D = "2099-01-13"
    sp = cat_shop.slot_paths(D, None)
    sp["v1"].write_text(
        "# 沙箱无图测试\n\n今晚的风停了。\n\n:::quote\n测试金句，安静一点。\n:::\n\n好了。\n",
        encoding="utf-8")
    try:
        rep = export_article(D, slot=None, with_cover=False)
        html = sp["mp"].read_text(encoding="utf-8")
        assert "<img" not in html, "无配图渲染不应有任何图片"
        assert rep["cover"]["present"] is False
        assert "未加配图" in (rep["cover"].get("note") or "")
    finally:
        import shutil
        shutil.rmtree(sp["mp_history"], ignore_errors=True)
        for k in ("v1", "mp", "mp_report"):
            sp[k].unlink(missing_ok=True)



def test_load_spec_fallback(monkeypatch):
    """开源包兼容：私有品牌规范缺失时回退中性规范（LLM 渲染不因缺文件而崩）。"""
    from core import exporter
    from pathlib import Path
    monkeypatch.setattr(exporter, "SPEC_PATH", Path("不存在-品牌规范.md"))
    monkeypatch.setattr(exporter, "SPEC_PATH_FALLBACK", Path("排版视觉规范.md"))
    spec = exporter.load_spec()
    assert "排版视觉规范" in spec and "创作工作台" not in spec
