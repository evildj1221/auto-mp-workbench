#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline/publish.py · 发布链路与配置命令（sync / setup / redline / status）。

自 cat_shop.py 2396-2554 / 2557-2776 行原样迁出。v4.1 起飞书只保留云文档归档通道
（webhook 推送与草稿卡片已移除，FEISHU_DOC_FOLDER_TOKEN 为唯一飞书配置项）。
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from core.paths import (SCRIPT_DIR, DRAFTS_DIR, SELECTIONS_DIR, slot_paths)
from core.storage import load_json, write_json, get_slot
from core.quality import count_chinese_chars
from core.redlines import redline_check
from pipeline.writing import redline_llm_semantic

# ===== Setup =====
def cmd_setup(args):
    print("=" * 60)
    print("  创作工作台 · 一次性环境配置")
    print("=" * 60)
    print()
    print("输入时不显示（防偷看）")
    subprocess.run([sys.executable, "-m", "pip", "install", "requests", "jinja2", "markdown", "python-dotenv", "-q"],
                   capture_output=True)
    print("  ✅ 依赖已装好（requests / jinja2 / markdown / python-dotenv）")

    try:
        import getpass
        api_key = getpass.getpass("  DEEPSEEK_API_KEY: ").strip()
        folder_token = getpass.getpass("  FEISHU_DOC_FOLDER_TOKEN（飞书云文档归档用，skip 直接回车）: ").strip()
    except Exception:
        api_key = input("  DEEPSEEK_API_KEY: ").strip()
        folder_token = input("  FEISHU_DOC_FOLDER_TOKEN（飞书云文档归档用，skip 直接回车）: ").strip()

    if not api_key:
        print("  ❌ 未输入 key，退出")
        return
    if not folder_token: folder_token = ""

    env_path = SCRIPT_DIR / ".env"
    env_content = (f"# 创作工作台 · 环境变量（setup 生成 {datetime.now().strftime('%Y-%m-%d %H:%M')})\n"
                   f"LLM_PROVIDER=deepseek\n"
                   f"LLM_API_KEY={api_key}\n"
                   f"LLM_API_BASE=https://api.deepseek.com/v1\n"
                   f"LLM_MODEL=deepseek-chat\n\n"
                   f"DEEPSEEK_API_KEY={api_key}\n"
                   f"DEEPSEEK_API_BASE=https://api.deepseek.com/v1\n"
                   f"DEEPSEEK_MODEL=deepseek-chat\n\n"
                   f"FEISHU_DOC_FOLDER_TOKEN={folder_token}\n")
    env_path.write_bytes(env_content.encode("utf-8"))
    print(f"  ✅ 已写 {env_path}（无 BOM）")
    print()
    if folder_token:
        print("  📂 飞书云文档归档已配置")
    print("✅ 配置完成")


# ===== Redline =====
def cmd_redline(args):
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    if not text:
        print("用法: --text '...' 或 --file path")
        return
    rl = redline_check(text, step="cli", date=datetime.now().strftime("%Y-%m-%d"))
    print(json.dumps(rl, ensure_ascii=False, indent=2))
    sys.exit(2 if rl["should_block"] else 0)


def cmd_redline_semantic(args):
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    if not text:
        print("用法: --text '...' 或 --file path")
        return
    rl = redline_llm_semantic(text, step="cli_semantic", date=datetime.now().strftime("%Y-%m-%d"))
    print(json.dumps(rl, ensure_ascii=False, indent=2))
    sys.exit(2 if rl["should_block"] else 0)


# ===== Status =====
def cmd_status(args):
    """查看当前工作流状态（各步骤完成情况；判据委托 core.pipeline_status）。"""
    from core.pipeline_status import STEP_LABELS, STEP_ORDER, status_map
    date = args.date
    print(f"[INFO] 工作流状态 · {date}")
    print()
    steps, _pub, _choice = status_map(date)
    all_done = True
    for key in STEP_ORDER:
        name = STEP_LABELS[key]
        if steps[key]:
            print(f"  ✅ {name}")
        else:
            print(f"  ⬜ {name}")
            all_done = False
    print()
    if all_done:
        print("  ✅ 全流程已完成")
    else:
        print("  💡 未完成步骤请按需执行")
        print(f"     查看完整命令：python cat_shop.py full --date {date}")

    # 显示基本信息
    pub_path = DRAFTS_DIR / f"{date}_published.json"
    pub = load_json(pub_path, {})
    if pub:
        print()
        print(f"  标题：{pub.get('title', '?')}")
        print(f"  字数：{pub.get('word_count', '?')}")
        print(f"  飞书文档：{pub.get('feishu_doc_url', '未同步')}")

# ===== Step 4.5: Sync（同步文稿到飞书云文档） =====
def _resolve_lark_cli():
    """定位 lark-cli 可执行文件：PATH → 环境变量 LARK_CLI → TRAE 插件目录。
    工作台以子进程跑 sync 时 PATH 常不含插件 bin（lark-cli 由 TRAE 插件安装），
    需兜底搜索，否则 shutil.which 落空导致「lark-cli 未安装」误报。"""
    exe = shutil.which("lark-cli")
    if exe:
        return exe
    exe = os.environ.get("LARK_CLI", "").strip()
    if exe and Path(exe).exists():
        return exe
    home = Path.home()
    for base in (home / ".trae-cn" / "plugins" / "trae-remote-official" / "lark",
                 home / ".trae" / "plugins" / "trae-remote-official" / "lark",
                 home / ".trae-cn" / "plugins" / "lark"):
        if not base.exists():
            continue
        for v in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True):
            cand = v / "bin" / ("lark-cli.exe" if os.name == "nt" else "lark-cli")
            if cand.exists():
                return str(cand)
    return None


def _lark_cli_json(args_list, timeout=120):
    """运行 lark-cli 命令并从输出中解析 JSON。
    Windows 下 lark-cli 是 npm 包的 .CMD 包装（非 .exe），CreateProcess 无法直接执行，
    需先用 shutil.which 解析真实路径，再经 cmd /c 调用。"""
    lark = _resolve_lark_cli()
    if not lark:
        return {"ok": False,
                "error": {"message": "lark-cli 未找到（已查 PATH、LARK_CLI 与 TRAE 插件目录）"}}
    base = (["cmd", "/c", lark] if lark.lower().endswith((".cmd", ".bat")) else [lark])
    result = subprocess.run(
        base + args_list,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        cwd=str(SCRIPT_DIR)
    )
    combined = result.stdout + result.stderr
    start = combined.find('{')
    if start == -1:
        return {"ok": False, "error": {"message": f"No JSON in output: {combined[:300]}"}}
    depth = 0
    end = -1
    for i in range(start, len(combined)):
        if combined[i] == '{':
            depth += 1
        elif combined[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        return {"ok": False, "error": {"message": "Malformed JSON in output"}}
    try:
        return json.loads(combined[start:end])
    except json.JSONDecodeError as e:
        return {"ok": False, "error": {"message": f"JSON parse error: {e}"}}


def step_sync(args):
    """将写好的文稿同步到飞书云文档（按月归档，命名 日期_标题）。
    使用 lark-cli（用户身份授权）替代 OpenAPI tenant_access_token。
    支持 --slot：槽位模式下同步槽位文稿（{date}_{slot}_v1.md）与对应 published.json。"""
    date = args.date
    force = getattr(args, "force_sync", False)
    slot = getattr(args, "slot", None)
    print(f"[INFO] Step 4.5 · {date} · 同步飞书云文档" + (f" [{slot}]" if slot else ""))
    # P1-7：云文档同步降为可选（默认开；FEISHU_SYNC=0 关闭，本地档案库已覆盖归档）
    if os.environ.get("FEISHU_SYNC", "1") != "1":
        print("  ⏭ 飞书云文档同步已关闭（FEISHU_SYNC=0），本地档案库可查看产物。")
        return True

    # 优先 v1，不存在则尝试 v2；槽位文件缺失时回退到非槽位 {date}_v1.md（agent 一键跑以无槽位产物产出）
    version = "v1"
    md_path = slot_paths(date, slot)["v1"] if slot else DRAFTS_DIR / f"{date}_v1.md"
    if not md_path.exists():
        md_path = DRAFTS_DIR / f"{date}_v1.md"  # 槽位回退：无槽位 v1 与 slot 模式共用同一份文稿
    if not md_path.exists():
        version = "v2"
        md_path = (slot_paths(date, slot)["v2"] if slot else DRAFTS_DIR / f"{date}_v2.md")
        if not md_path.exists():
            md_path = DRAFTS_DIR / f"{date}_v2.md"
        if not md_path.exists():
            print(f"  ✗ 找不到 v1/v2 文稿，先跑 Step 4", file=sys.stderr)
            return False

    # 读取选题元数据（slot 模式取槽位 meta）
    if slot:
        sm = get_slot(date, slot) or {}
        selection = {"cand_id": sm.get("cand_id"),
                     "topic": sm.get("topic") or "",
                     "motif_series": sm.get("motif_series") or "B",
                     "total": sm.get("total", "-")}
    else:
        sel_path = SELECTIONS_DIR / f"{date}.json"
        selection = load_json(sel_path, default={}) or {}
    # 从 top5 取排名
    top5_path = DRAFTS_DIR / f"{date}_top5.json"
    rank = "-"
    if top5_path.exists():
        top5_data = load_json(top5_path, default={}) or {}
        top5_list = top5_data.get("results", []) if isinstance(top5_data, dict) else top5_data
        for i, r in enumerate(top5_list, 1):
            if isinstance(r, dict) and r.get("cand_id") == selection.get("cand_id"):
                rank = f"{i}/{len(top5_list)}"
                break

    # 幂等检查
    pub_path = DRAFTS_DIR / f"{date}{'_' + slot if slot else ''}_published.json"
    pub_data = load_json(pub_path, default={}) or {}
    if pub_data.get("feishu_doc_url") and not force:
        print(f"  ⏭ 已同步过：{pub_data['feishu_doc_url']}")
        print(f"     （--force-sync 可覆盖）")
        return True

    # 检查 lark-cli 是否可用
    if not _resolve_lark_cli():
        print("  ✗ 找不到 lark-cli（已查 PATH / LARK_CLI / TRAE 插件目录），无法同步", file=sys.stderr)
        return False

    root_folder_token = os.getenv("FEISHU_DOC_FOLDER_TOKEN")
    if not root_folder_token:
        print("  ✗ FEISHU_DOC_FOLDER_TOKEN 未配置", file=sys.stderr)
        return False

    temp_md = None
    try:
        # 准备带元信息头的 markdown
        content = md_path.read_text(encoding="utf-8")
        wc = count_chinese_chars(content)
        series = selection.get("motif_series") or selection.get("series") or "-"
        total = selection.get("total", "-")
        topic = selection.get("topic", md_path.stem.replace("_v1", ""))
        meta_header = (
            f"> 📋 **选题信息**\n"
            f"> 日期：{date} | 系列：{series} | 评分：{total}\n"
            f"> 字数：{wc} | 候选排名：{rank}\n"
            f"\n---\n\n"
        )
        full_md = meta_header + content

        # 写临时文件（lark-cli 需要相对路径）
        temp_md = DRAFTS_DIR / f"{date}_sync_temp.md"
        temp_md.write_text(full_md, encoding="utf-8")
        rel_path = f"data/drafts/{date}_sync_temp.md"

        # 创建/获取月度子文件夹
        month_str = date[:7]  # e.g. "2026-08"
        print(f"  查找/创建月度文件夹 {month_str}…")

        # 先查找是否已存在同名文件夹
        month_token = None
        resp = _lark_cli_json([
            "drive", "files", "list",
            "--folder-token", root_folder_token,
            "--page-size", "200",
        ], timeout=30)

        if resp.get("ok"):
            for f in resp.get("data", {}).get("files", []):
                if f.get("type") == "folder" and f.get("name") == month_str:
                    month_token = f["token"]
                    print(f"  找到月度文件夹: {month_str}")
                    break

        # 不存在则创建
        if not month_token:
            resp = _lark_cli_json([
                "drive", "+create-folder",
                "--name", month_str,
                "--folder-token", root_folder_token,
            ], timeout=30)

            if not resp.get("ok"):
                err = resp.get("error", {}).get("message", "unknown")
                print(f"  ✗ 创建月度文件夹失败: {err}", file=sys.stderr)
                temp_md.unlink(missing_ok=True)
                return False

            month_token = resp["data"]["folder_token"]
            print(f"  创建月度文件夹: {month_str}")

        # 导入文档
        doc_name = f"{date}_{topic}"
        print(f"  导入中… → {doc_name}")

        # --force-sync 时先删除同名旧文档
        if force:
            resp_list = _lark_cli_json([
                "drive", "files", "list",
                "--folder-token", month_token,
                "--page-size", "200",
            ], timeout=30)
            if resp_list.get("ok"):
                for f in resp_list.get("data", {}).get("files", []):
                    if f.get("name") == doc_name and f.get("type") == "docx":
                        _lark_cli_json([
                            "drive", "+delete",
                            "--file-token", f["token"],
                            "--type", "docx",
                            "--yes",
                        ], timeout=30)
                        print(f"  删除旧文档: {doc_name}")
                        break

        resp = _lark_cli_json([
            "drive", "+import",
            "--file", rel_path,
            "--folder-token", month_token,
            "--name", doc_name,
            "--type", "docx",
        ], timeout=120)

        if not resp.get("ok"):
            err = resp.get("error", {}).get("message", "unknown")
            print(f"  ✗ 导入失败: {err}", file=sys.stderr)
            temp_md.unlink(missing_ok=True)
            return False

        doc_token = resp["data"]["token"]
        doc_url = resp["data"]["url"]

        print(f"  ✅ 已同步：{doc_name}")
        print(f"     🔗 {doc_url}")

        # 更新 published.json
        pub_data.update({
            "feishu_doc_url": doc_url,
            "feishu_doc_token": doc_token,
            "feishu_doc_name": doc_name,
            "feishu_synced_at": datetime.now().isoformat(timespec="seconds"),
        })
        write_json(pub_path, pub_data)

        # 清理临时文件
        temp_md.unlink(missing_ok=True)

    except Exception as e:
        print(f"  ✗ 同步失败: {e}", file=sys.stderr)
        # 清理临时文件（temp_md 可能在异常前未定义）
        if temp_md:
            temp_md.unlink(missing_ok=True)
        return False

    print(f"[DONE] Step 4.5 完成")
    return True
