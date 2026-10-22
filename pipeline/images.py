#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline/images.py · 头图生成（v4.2 M1）。

每篇文稿只生成一张头图（21:9 · 治愈系插画风 · 无文字叠加），文中图已取消。
两段式：LLM 场景提炼（cover_scene）→ 生图 API（image_cover），两笔成本独立记账。
未配置 IMAGE_API_KEY 时返回 skipped 说明（降级不阻断写稿与发布链路）。
"""
import random
import re

from core.paths import (SELECTIONS_DIR, STYLE_REFS_DIR, cover_image_path,
                        slot_paths)
from core.storage import get_slot, load_json
from llm.client import call_llm
from llm.image_provider import COVER_SIZE, generate_image, image_configured
from llm.prompts import COVER_SCENE_PROMPT, build_cover_prompt


def _cover_context(date, slot):
    """头图上下文：选题/标题/系列/正文开头（槽位优先，回退当日决策）。"""
    sel = load_json(SELECTIONS_DIR / f"{date}.json", {}) or {}
    sm = (get_slot(date, slot) or {}) if slot else {}
    topic = sm.get("topic") or sel.get("topic") or f"{date} 的信"
    series = sm.get("motif_series") or sel.get("motif_series") or "B"
    title = sm.get("selected_title") or topic
    sp = slot_paths(date, slot)
    excerpt = ""
    for p in (sp["v1"], sp["v2"]):
        if p.exists():
            excerpt = p.read_text(encoding="utf-8")
            break
    excerpt = re.sub(r"^:::.*$|^#.*$", "", excerpt, flags=re.M).strip()
    excerpt = re.sub(r"\s+", " ", excerpt)[:300]
    return topic, title, series, excerpt


def _pick_style_ref(mood: str):
    """从风格参考库（实战验证头图）选一张同调性参考传入生图 API——
    MiniMax subject_reference 依此保持画风/小猫 IP 一致性。
    无参考库或 IMAGE_STYLE_REF=0 时返回 None（纯提示词出图）。"""
    import os
    if os.environ.get("IMAGE_STYLE_REF") == "0":
        return None
    refs = sorted(STYLE_REFS_DIR.glob("style_ref_*.png"))
    if not refs:
        return None
    same = [r for r in refs if f"_{mood}_" in r.name]
    pool = same or refs
    return random.choice(pool).read_bytes()


def generate_cover(date, slot=None, force=False):
    """生成头图：场景提炼 → 生图 → 落盘 data/images/{date}_{slot}_cover.png。
    返回 dict（path / skipped / error），供工具层与 CLI 复用。"""
    out_path = cover_image_path(date, slot)
    if out_path.exists() and not force:
        return {"path": str(out_path), "skipped": "头图已存在（force 覆盖重生成）"}

    topic, title, series, excerpt = _cover_context(date, slot)
    scene_res = call_llm(
        COVER_SCENE_PROMPT.replace("{topic}", topic).replace("{title}", title)
        .replace("{series}", series).replace("{excerpt}", excerpt or "（文稿未就绪，按标题意象设计）"),
        temperature=0.3, max_tokens=800, module="cover_scene")
    prompt = build_cover_prompt(scene_res.get("scene", ""),
                                scene_res.get("cat_pose", "sits on the windowsill"),
                                scene_res.get("mood", "night"))

    ref = _pick_style_ref(scene_res.get("mood", "night"))
    img = generate_image(prompt, size=COVER_SIZE, module="image_cover", reference=ref)
    out_path.write_bytes(img)
    print(f"  ✅ 头图已生成 {out_path.name}（{COVER_SIZE} · {len(img) // 1024}KB"
          + (" · 带风格参考图）" if ref else "）"))
    return {"path": str(out_path), "prompt": prompt,
            "style_ref": bool(ref)}


def step_generate_cover(args):
    """pipeline step 入口（CLI / 工具层共用）。未配置生图 API → 跳过不算失败。"""
    date = args.date
    slot = getattr(args, "slot", None)
    force = bool(getattr(args, "force", False))
    print(f"[INFO] 头图生成 · {date} · {slot or '默认'}")
    if not image_configured():
        print("  ⏭️  未配置 IMAGE_API_KEY，跳过头图生成（不阻断发布，可稍后补图）")
        return True
    try:
        res = generate_cover(date, slot=slot, force=force)
        if res.get("skipped"):
            print(f"  ⏭️  {res['skipped']}")
        return True
    except Exception as e:
        print(f"  ⚠️  头图生成失败（不阻断）: {e}")
        return True
