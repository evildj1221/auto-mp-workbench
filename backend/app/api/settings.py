#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/app/api/settings.py · 系统设置（原 /settings 系列的 JSON 化）。

P1 伏笔：autonomy / 预算档位读写 data/settings.json（P2 阶段接入 agent/interrupts）。
"""
import os
from datetime import datetime

from fastapi import APIRouter, Request

import cat_shop
from core import exporter

from ..common import LEVELS, cost_summary, load_json, redline_log_entries, wb_log
from ..schemas import FeishuToggleReq, RedlineCheckReq, WorkbenchSettingsReq
from . import envelope, fail, q_date, body_date

router = APIRouter(prefix="/api/settings", tags=["settings"])

SETTINGS_PATH = cat_shop.DATA_DIR / "settings.json"
SETTINGS_DEFAULT = {"autonomy": "assisted",       # assisted | semi | auto
                    "max_steps": 40, "max_cost_cny": 2.0,   # 与 agent/config.py 默认对齐
                    "wechat_ip_whitelist": ""}     # 逗号/换行分隔的公网出口 IP 清单


def _whitelist_ips() -> list[str]:
    raw = load_settings().get("wechat_ip_whitelist") or ""
    return [x.strip() for x in raw.replace("\n", ",").split(",") if x.strip()]


@router.get("/ip-info")
def ip_info():
    """当前公网出口 IP + 是否在公众号白名单内（宿舍网络 IP 常变，推送前先看一眼）。
    白名单清单存 settings.json（设置页可编辑），与公众号后台填的保持一致即可。"""
    import requests as _rq
    ip, err = "", ""
    for url in ("https://api.ipify.org?format=json", "https://api.ip.sb/ip",
                "http://ifconfig.me/ip"):
        try:
            r = _rq.get(url, timeout=6)
            r.raise_for_status()
            ip = (r.json().get("ip") if "json" in r.headers.get("content-type", "")
                  else r.text).strip()
            if ip:
                break
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    ips = _whitelist_ips()
    return envelope({
        "ip": ip,
        "ip_error": "" if ip else (err or "查询失败"),
        "whitelist": ips,
        "configured": bool(ips),
        "in_whitelist": bool(ip) and ip in ips,
    })


def load_settings() -> dict:
    data = load_json(SETTINGS_PATH, None) or {}
    merged = {**SETTINGS_DEFAULT, **data}
    # 下限 8：标准全流程（选题→写稿→标题→头图→导出）最少 6 次工具调用，
    # 旧下限 5 会让一键运行永远到不了导出就停（存量 5 一并迁移）
    merged["max_steps"] = max(8, min(int(merged.get("max_steps") or 8), 200))
    return merged


def save_settings(patch: dict) -> dict:
    data = load_settings()
    data.update(patch)
    cat_shop.write_json(SETTINGS_PATH, data)
    return data


@router.get("")
@router.get("/")
def get_settings(request: Request):
    date = q_date(request)
    env_status = {
        "LLM_API_KEY": bool(os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")),
        "LLM_MODEL": os.environ.get("LLM_MODEL") or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "IMAGE_API_KEY": bool(os.environ.get("IMAGE_API_KEY")),
        "IMAGE_PROVIDER": os.environ.get("IMAGE_PROVIDER", ""),
        "FEISHU_DOC_FOLDER_TOKEN": bool(os.environ.get("FEISHU_DOC_FOLDER_TOKEN")),
    }
    effective = cat_shop.load_effective_weights(datetime.now())
    profile = cat_shop.load_preference()
    return envelope({"page": "settings", "date": date, "env_status": env_status,
                     "redlines": cat_shop.REDLINES, "effective": effective,
                     "levels": LEVELS,
                     "external_redlines": cat_shop.REDLINES_PATH.exists(),
                     "default_w": cat_shop.DEFAULT_W, "profile": profile,
                     "spec_exists": exporter.SPEC_PATH.exists(),
                     "feishu_sync": os.environ.get("FEISHU_SYNC", "1") == "1",
                     "cost_summary": cost_summary(date),
                     "redline_log": redline_log_entries(),
                     "workbench": load_settings()})


@router.put("")
@router.put("/")
def put_settings(req: WorkbenchSettingsReq):
    """更新工作台设置（autonomy 档位 / 预算）。数值做护栏钳制，防误填 0/负数。"""
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    if "autonomy" in patch and patch["autonomy"] not in ("assisted", "semi", "auto"):
        return fail("invalid_autonomy", "档位仅支持 assisted/semi/auto")
    if "max_steps" in patch:
        patch["max_steps"] = max(8, min(int(patch["max_steps"]), 200))
    if "max_cost_cny" in patch:
        patch["max_cost_cny"] = max(0.5, min(float(patch["max_cost_cny"]), 50.0))
    data = save_settings(patch)
    wb_log("settings", "-", f"更新工作台设置：{patch}")
    return envelope({"workbench": data, "updated": True})


@router.post("/redline-check")
def redline_check_route(req: RedlineCheckReq, request: Request):
    """红线手动检测（短语 / 语义）。"""
    text = req.text
    if not text.strip():
        return fail("empty", "检测内容为空")
    if req.mode == "semantic":
        result = cat_shop.redline_llm_semantic(text, step="workbench_manual")
    else:
        result = cat_shop.redline_check(text, step="workbench_manual", auto_log=False)
    wb_log("redline", "-", f"手动{req.mode}检查：{result['risk_level']}")
    return envelope({"result": result, "mode": req.mode})


@router.post("/feishu-toggle")
def feishu_toggle(req: FeishuToggleReq, request: Request):
    """飞书云文档归档开关（默认开），写入 .env。"""
    date = body_date(request, req.date)
    sync = "1" if req.feishu_sync else "0"
    env_path = cat_shop.SCRIPT_DIR / ".env"
    existed = {"FEISHU_SYNC": False}
    lines = []
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("FEISHU_SYNC="):
                lines.append(f"FEISHU_SYNC={sync}")
                existed["FEISHU_SYNC"] = True
                continue
            lines.append(line)
    if not existed["FEISHU_SYNC"]:
        lines.append(f"FEISHU_SYNC={sync}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    wb_log("feishu_toggle", date, f"飞书云文档归档开关：sync={sync}")
    return envelope({"feishu_sync": sync == "1"})


@router.post("/redlines/reset")
def redlines_reset(request: Request):
    date = q_date(request)
    path = cat_shop.REDLINES_PATH
    if path.exists():
        os.replace(path, str(path) + ".bak")  # 重命名即备份，避免硬删除
    cat_shop.REDLINES = cat_shop.load_redlines()
    wb_log("redlines", date, "恢复内置默认红线规则")
    return envelope({"reset": True, "message": "已恢复内置默认（外置文件已改名为 .bak 备份）"})
