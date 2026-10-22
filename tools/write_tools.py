#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/write_tools.py · 写稿链路工具（outline / write / titles / edit_draft）。

写稿本身不碰外部系统（Phase 1 已解耦 sync）；edit_draft 是 agent 修订循环的
落笔通道——planner 在门禁不过时生成新内容并经此写回。
"""
from langchain_core.tools import tool

from core.paths import slot_paths
from core.quality import count_chinese_chars
from core.storage import load_json
from pipeline.writing import step_outline, step_titles, step_write
from tools._base import envelope, run_step, tool_error


@tool
def generate_outline(date: str, slot: str | None = None) -> dict:
    """为当前选题生成信件体大纲（可选增强：先确认结构再写稿）。幂等：大纲已存在时跳过。

    Args:
        date: 目标日期，YYYY-MM-DD
        slot: 文稿槽位（如 'w01'，默认当日活动槽位）
    """
    try:
        ok, log = run_step(step_outline, date=date, slot=slot)
        status = "ok" if ok else "error"
        return envelope(status, "大纲已生成" if ok else "大纲生成失败，查看 log",
                        log=log, outline_path=str(slot_paths(date, slot)["v1"].with_name(
                            slot_paths(date, slot)["v1"].name.replace("_v1.md", "_outline.md"))))
    except Exception as e:
        return tool_error(e)


@tool
def write_draft(date: str, slot: str | None = None, v2_angle: str | None = None,
                use_outline: bool = False) -> dict:
    """按当日已选选题写正文初稿（900-1200 汉字，含偏好注入与反重复检查）。
    幂等：初稿已存在时跳过；v2_angle 用于差异化重写（第二版）。
    写稿后应由质量工具做门禁检查（红线/字数/相似度）。

    Args:
        date: 目标日期，YYYY-MM-DD
        slot: 文稿槽位（如 'w01'，默认当日活动槽位）
        v2_angle: v2 差异化角度（时间切片/反例切入/物件线索/回信体/他人视角），空则写 v1
        use_outline: 按已生成的大纲写稿
    """
    try:
        ok, log = run_step(step_write, date=date, force_v2=bool(v2_angle),
                           v2_angle=v2_angle, semantic_redline=False, slot=slot,
                           use_outline=use_outline)
        sp = slot_paths(date, slot)
        version = "v2" if v2_angle else "v1"
        md_path = sp[version]
        content = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        wc = count_chinese_chars(content)
        title = (content.split("\n", 1)[0].lstrip("# ").strip() if content else "")
        if not ok:
            if not content:
                return envelope("error", "写稿失败，查看 log", log=log)
            # step_write 无幂等跳过：False 即失败（文稿已落盘但未过自检）
            reason = "红线阻断" if "红线" in log else "字数不足（自动重写 3 轮后仍不过）"
            return envelope("error", f"{version} 已落盘但未过自检：{reason}", log=log,
                            version=version, word_count=wc, draft_path=str(md_path), title=title)
        return envelope("ok", f"{version} 已写入（{wc} 汉字）", log=log, version=version,
                        word_count=wc, draft_path=str(md_path), title=title)
    except Exception as e:
        return tool_error(e)


@tool
def generate_titles(date: str, slot: str | None = None, force: bool = False) -> dict:
    """为已写文稿生成多套标题方案（写稿后可自动生成；force 覆盖已有方案）。

    Args:
        date: 目标日期，YYYY-MM-DD
        slot: 文稿槽位（默认当日活动槽位）
        force: 覆盖已有标题方案
    """
    try:
        ok, log = run_step(step_titles, date=date, force=force, slot=slot)
        titles_path = slot_paths(date, slot)["titles"]
        data = load_json(titles_path, {})
        titles = [t.get("title", "?") for t in data.get("titles", [])]
        if not ok and not titles:
            return envelope("error", "标题生成失败，查看 log", log=log)
        status = "skipped" if not ok else "ok"
        return envelope(status, f"{len(titles)} 个标题方案", log=log, titles=titles,
                        titles_path=str(titles_path))
    except Exception as e:
        return tool_error(e)


@tool
def edit_draft(date: str, content: str, slot: str | None = None, version: str = "v1") -> dict:
    """整体替换文稿内容（agent 修订循环的落笔通道：门禁不过时重写后写回）。
    content 必须以 '# 标题' 开头；写入后返回字数与红线快检结果。

    Args:
        date: 目标日期，YYYY-MM-DD
        content: 完整文稿内容（Markdown，首行 '# 标题'）
        slot: 文稿槽位（默认当日活动槽位）
        version: 写入版本（v1 或 v2）
    """
    try:
        from core.redlines import redline_check
        content = content.strip()
        if not content.startswith("#"):
            return envelope("error", "content 必须以 '# 标题' 开头")
        md_path = slot_paths(date, slot)[version]
        md_path.write_text(content + "\n", encoding="utf-8")
        wc = count_chinese_chars(content)
        rl = redline_check(content, step="edit_draft", date=date,
                           file=md_path.name, auto_log=False)
        return envelope("ok", f"已写入 {md_path.name}（{wc} 汉字）",
                        version=version, word_count=wc, draft_path=str(md_path),
                        redline_hits=len(rl.get("hits", [])))
    except Exception as e:
        return tool_error(e)
