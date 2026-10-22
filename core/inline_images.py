#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/inline_images.py · 文中配图管线（P3 · 2026-09-17）。

写稿完成后、导出渲染前，两步走：
  1) plan：LLM 选 2-4 个情绪/场景节点（锚定正文块索引），生成视觉描述 prompt，
     调生图服务（MiniMax image-01 等 provider）生成 16:9 配图落盘 data/images/，
     写 {date}_{slot}_image_plan.json（含每张图的段落锚点 / 视觉 prompt / 图注）
  2) apply：导出时按 plan 在对应正文块后插入 :::imgtext 标记（渲染为规范图文组件，
     需运行时启用 imgtext——天光云影主题默认停用）；图片用本地 file:// URI，
     预览经 exporter._httpize_local_images 转后端路由，推送前经 wechat.replace_local_images
     换成微信素材 URL。

设计约束：
  - plan 文件存在即复用（重新渲染不重复烧钱）；"换一批"= 删 plan 后再导出
  - 任一环节失败降级跳过：不阻塞导出与推送（沿用头图降级模式）
  - 不复活 :::image 标记：LLM 输出的 :::image 仍由 quality.py 静默丢弃（防线保留）
  - 用户上传的首张图片自动作为生图参考（"创作工作台"形象一致性）
"""
import json
import re
from datetime import datetime
from pathlib import Path

from core.paths import IMAGES_DIR, UPLOADS_DIR
from core.quality import strip_component_markers
from core.storage import load_json, write_json

INLINE_PROMPT = """<role>
你是「你的公众号」公众号的视觉导演。为下面这篇情感治愈系文稿挑选配图位置并撰写画面描述。
</role>

<task>
从编号正文块中挑选 2~4 个最适合配图的位置（情绪高点 / 场景画面感强 / 独立成段的意象），
为每个位置输出一句可直接用于 AI 生图的中文画面描述。
只输出 JSON：{"images": [{"block": 块编号, "prompt": "画面描述", "caption": "图注一句"}]}
</task>

<hard_rules>
- 只能选"正文块"（编号列表里以 T 开头的纯文本段落），不得选 H（标题）/ Q（组件块）
- 画面描述 40~80 字：具体场景 + 光线 + 色调，新海诚式光影，天光云影品牌视觉（暖金/晨蓝/暮色），治愈系
- 图注 ≤ 20 字，不重复正文原句
- 最多 4 张，最少 2 张；宁缺毋滥，没有合适的画面感就少选
</hard_rules>

<blocks>
{blocks}
</blocks>
"""

MAX_INLINE = 4
INLINE_SIZE = "1280x720"  # 16:9，8 的倍数，总像素 ≤200 万


def _plan_path(date, slot):
    suf = f"_{slot}" if slot else ""
    return IMAGES_DIR / f"{date}{suf}_image_plan.json"


def _blocks_with_index(md_text):
    """切分正文块并分类：T=正文段落（可选配图）/ H=标题 / Q=组件块 / S=空块。"""
    out = []
    for i, b in enumerate(md_text.split("\n\n")):
        s = b.strip()
        if not s:
            kind = "S"
        elif s.startswith("#"):
            kind = "H"
        elif s.startswith(":::") or s.startswith(">"):
            kind = "Q"
        else:
            kind = "T"
        out.append((i, kind, s))
    return out


def _first_reference_image(date, slot):
    """用户上传素材中的首张图片（作生图参考），无则 None。"""
    updir = UPLOADS_DIR / f"{date}_{slot}" if slot else UPLOADS_DIR / date
    if not updir.exists():
        return None
    for p in sorted(updir.iterdir()):
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            return p.read_bytes()
    return None


def plan_inline_images(date, slot, md_text):
    """LLM 选节点 + 生图 + 写 plan。返回 plan 列表（每项 block/file/caption/prompt）。"""
    from llm.client import call_llm
    from llm.image_provider import generate_image, image_configured

    if not image_configured():
        raise RuntimeError("生图服务未配置（IMAGE_API_KEY）")

    blocks = _blocks_with_index(md_text)
    body = "\n".join(f"[{kind}{i}] {s[:120]}" if kind != "T" else f"[T{i}] {s}"
                     for i, kind, s in blocks if kind != "S")
    prompt = INLINE_PROMPT.replace("{blocks}", body)
    resp = call_llm(prompt, temperature=0.4, max_tokens=1200, json_mode=True,
                    module="inline_images")
    items = (resp.get("images") or [])[:MAX_INLINE]

    reference = _first_reference_image(date, slot)

    plan = []
    valid_idx = {i for i, k, _ in blocks if k == "T"}
    for n, item in enumerate(items, 1):
        try:
            # LLM 可能返回 "T1"/"1"/1 等形态，统一剥出纯数字块号
            idx = int(re.sub(r"\D", "", str(item.get("block", ""))) or -1)
        except (TypeError, ValueError):
            continue
        if idx not in valid_idx:
            continue
        visual = str(item.get("prompt", "")).strip()[:300]
        caption = str(item.get("caption", "")).strip()[:40]
        if not visual:
            continue
        fname = f"{date}_{slot}_inline_{n}.png" if slot else f"{date}_inline_{n}.png"
        img = generate_image(visual, size=INLINE_SIZE, reference=reference)
        (IMAGES_DIR / fname).write_bytes(img)
        plan.append({"block": idx, "file": f"images/{fname}",
                     "caption": caption, "prompt": visual})

    write_json(_plan_path(date, slot), {
        "date": date, "slot": slot, "count": len(plan),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "images": plan})
    return plan


def load_plan(date, slot):
    p = _plan_path(date, slot)
    if not p.exists():
        return None
    return (load_json(p, None) or {}).get("images") or None


def delete_plan(date, slot):
    """换一批：删除 plan（含已生成图片），下次导出将重新规划生成。"""
    for item in (load_plan(date, slot) or []):
        (IMAGES_DIR / Path(item.get("file", "")).name).unlink(missing_ok=True)
    _plan_path(date, slot).unlink(missing_ok=True)


def apply_inline_images(md_text, plan):
    """按 plan 在正文块后插入 :::imgtext 标记（块号从大到小插，锚点不漂移）。
    标记内部用单换行（fenced block 语法），块间仍是空行分隔。"""
    blocks = md_text.split("\n\n")
    for item in sorted(plan, key=lambda x: -int(x.get("block", 0))):
        idx = int(item.get("block", -1))
        if not (0 <= idx < len(blocks)):
            continue
        fpath = IMAGES_DIR / Path(item.get("file", "")).name
        if not fpath.exists():
            continue
        marker = (":::imgtext\n" + fpath.resolve().as_uri()
                  + "\n" + item.get("caption", "") + "\n:::")
        blocks.insert(idx + 1, marker)
    return "\n\n".join(blocks)


def ensure_inline_images(md_text, date, slot):
    """导出器唯一入口：按需 plan + 插入。返回 (md_text, meta|None)。
    meta = {"count": n}；任一失败降级返回原文（不阻塞导出）。
    仅规则引擎稿（带组件标记）生效——存量稿走 LLM 渲染回退，插标记无效。"""
    try:
        raw = strip_component_markers(md_text)
        if ":::" not in md_text or raw == md_text:
            return md_text, None

        plan = load_plan(date, slot)
        if plan is None:
            plan = plan_inline_images(date, slot, md_text)
        if not plan:
            return md_text, None
        return apply_inline_images(md_text, plan), {"count": len(plan)}
    except Exception as e:  # noqa: BLE001 —— 配图失败绝不阻塞导出
        import sys
        print(f"  [WARN] 文中配图降级跳过: {e}", file=sys.stderr)
        return md_text, None
