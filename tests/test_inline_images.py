#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_inline_images.py · P3 文中配图管线测试（纯逻辑，不调 LLM / 不外呼）。

覆盖：
  A _blocks_with_index 块分类（正文/标题/组件/空块）
  B apply_inline_images 插入：锚点正确、块号倒序不漂移、file URI 为绝对路径
  C ensure_inline_images 降级语义：存量稿（LLM 引擎）跳过、plan 复用不重新生图
  D wechat.replace_local_images：file:// → 上传替换，单张失败跳过
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import inline_images as ii  # noqa: E402
from core.wechat import replace_local_images  # noqa: E402

DATE, SLOT = "2099-03-01", "w01"

MD = "\n\n".join([
    "# 测试配图稿",
    "第一段正文，铺垫情绪的场景描写，足够长可以配图。" * 3,
    ":::quote\n金句：慢一点，也是往前走。\n:::",
    "第二段正文，情绪高点，画面感强。" * 3,
    "第三段正文，收尾前的意象。" * 3,
])


# ============ A. 块分类 ============
def test_blocks_classification():
    blocks = ii._blocks_with_index(MD)
    kinds = {i: k for i, k, _ in blocks}
    assert kinds[0] == "H"          # 标题
    assert kinds[1] == "T"          # 正文
    assert kinds[2] == "Q"          # 组件块
    assert kinds[3] == "T" and kinds[4] == "T"


# ============ B. 插入逻辑 ============
def test_apply_inline_images_insert_and_order():
    plan = [{"block": 1, "file": "images/a.png", "caption": "图注一"},
            {"block": 3, "file": "images/b.png", "caption": "图注二"}]
    # 模拟图片文件存在
    (ii.IMAGES_DIR / "a.png").write_bytes(b"fake-a")
    (ii.IMAGES_DIR / "b.png").write_bytes(b"fake-b")
    try:
        out = ii.apply_inline_images(MD, plan)
        blocks = out.split("\n\n")
        # 两处 :::imgtext 标记，且各锚点后的第一个新块即标记
        markers = [i for i, b in enumerate(blocks) if b.startswith(":::imgtext")]
        assert len(markers) == 2
        for m in markers:
            assert blocks[m - 1].startswith(("[正文", "第一段", "第二段", "第三段")), \
                "标记必须紧跟正文块之后"
        assert "file:///" in blocks[markers[0]], "图片应为本地 file:// URI（推送前再换 URL）"
        # 原有内容顺序不变
        assert blocks[0].startswith("# 测试配图稿")
    finally:
        (ii.IMAGES_DIR / "a.png").unlink(missing_ok=True)
        (ii.IMAGES_DIR / "b.png").unlink(missing_ok=True)


def test_apply_skips_missing_image_files():
    plan = [{"block": 1, "file": "images/not_exist_xyz.png", "caption": "x"}]
    out = ii.apply_inline_images(MD, plan)
    assert ":::imgtext" not in out, "图片文件缺失的项应跳过"


# ============ C. ensure 降级语义 ============
def test_ensure_skips_llm_engine_drafts(monkeypatch):
    """存量稿（无组件标记）走 LLM 渲染回退 → 不插标记，也不触发生图。"""
    called = []
    monkeypatch.setattr(ii, "plan_inline_images",
                        lambda *a, **k: called.append(1) or [])
    out, meta = ii.ensure_inline_images("纯文本存量稿\n\n没有组件标记的内容。", DATE, SLOT)
    assert meta is None and not called
    # 带标记的稿 → 会走 plan（mock 返回空 plan）
    monkeypatch.setattr(ii, "plan_inline_images", lambda *a, **k: [])
    out, meta = ii.ensure_inline_images(MD, DATE, SLOT)
    assert meta is None and out == MD


def test_ensure_reuses_existing_plan(monkeypatch):
    """plan 已存在 → 复用，不再调生图。"""
    calls = []
    monkeypatch.setattr(ii, "load_plan", lambda d, s: [
        {"block": 1, "file": "images/a.png", "caption": "图注"}])
    monkeypatch.setattr(ii, "plan_inline_images",
                        lambda *a, **k: calls.append(1) or [])
    (ii.IMAGES_DIR / "a.png").write_bytes(b"fake")
    try:
        out, meta = ii.ensure_inline_images(MD, DATE, SLOT)
        assert not calls, "已有 plan 时不得重新生图"
        assert meta == {"count": 1}
        assert out.count(":::imgtext") == 1
    finally:
        (ii.IMAGES_DIR / "a.png").unlink(missing_ok=True)


# ============ D. 推送前本地图换装 ============
def test_replace_local_images(monkeypatch, tmp_path):
    img = tmp_path / "inline.png"
    img.write_bytes(b"fake")
    uri = img.resolve().as_uri()  # file:///D:/... 绝对 URI（与 as_uri() 产物一致）
    html = f'<section><img src="{uri}" style="z"><p>图注</p></section>'
    monkeypatch.setattr("core.wechat.upload_img_for_article",
                        lambda data, filename: ("https://mmbiz.qpic.cn/replaced.png", None))
    out, n, err = replace_local_images(html)
    assert n == 1 and err is None
    assert 'src="https://mmbiz.qpic.cn/replaced.png"' in out
    # 无本地图 → 原样返回
    out2, n2, _ = replace_local_images('<img src="https://mmbiz.qpic.cn/x.png">')
    assert n2 == 0 and out2 == '<img src="https://mmbiz.qpic.cn/x.png">'


def test_replace_local_images_missing_file_skipped():
    html = '<img src="file:///definitely/missing/zzz.png">'
    out, n, err = replace_local_images(html)
    assert n == 0 and err is None and "file:///" in out, "单张失败应跳过且不阻塞"
