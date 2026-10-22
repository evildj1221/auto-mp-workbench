#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/storage.py · JSON 读写原语 + 批次索引 + 文稿槽位索引。

自 cat_shop.py 112-238 / 1227-1235 行原样迁出，行为不变。
"""
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from core.paths import (SELECTIONS_DIR, BATCHES_DIR, WORKS_DIR, DRAFTS_DIR,
                        batch_file_names)

# ===== 工具 =====
def load_json(path, default=None):
    """读 JSON。损坏时自动回退 .bak（写侧每次落盘前备份），再不行给默认值——
    曾因半截文件让 works/settings 损坏后整站 500 且无自愈。"""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default
    if not text.strip():
        return default
    try:
        return json.loads(text)
    except ValueError:
        bak = p.with_suffix(p.suffix + ".bak")
        if bak.exists():
            try:
                return json.loads(bak.read_text(encoding="utf-8", errors="replace"))
            except ValueError:
                pass
        try:
            from core.logging import log_event
            log_event("warn", "json_corrupt", detail=str(p), echo=False)
        except Exception:
            pass
        return default


def write_json(path, data):
    """原子写：先写 .tmp 再 os.replace（半截文件不可能出现），替换前留 .bak。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        if p.exists():
            shutil.copy2(p, p.with_suffix(p.suffix + ".bak"))
    except OSError:
        pass
    os.replace(tmp, p)

def load_batches(date):
    """当日批次索引。"""
    p = BATCHES_DIR / f"{date}.json"
    data = load_json(p, default=None) or {}
    data.setdefault("date", date)
    data.setdefault("batches", [])
    data.setdefault("active_round", 0)
    return data


def save_batches(date, data):
    BATCHES_DIR.mkdir(parents=True, exist_ok=True)
    write_json(BATCHES_DIR / f"{date}.json", data)


def register_batch(date):
    """注册一个新批次，返回 (round_n, batches_data)。"""
    data = load_batches(date)
    r = (data.get("active_round") or 0) + 1
    data["batches"].append({"round": r,
                            "created_at": datetime.now().isoformat(timespec="seconds"),
                            "status": "ready"})
    data["active_round"] = r
    save_batches(date, data)
    return r, data


def ensure_batch_index(date):
    """保证至少有一个批次（兼容旧数据：有 candidates/top5 但没有批次索引时补 r1）。"""
    data = load_batches(date)
    if not data["batches"]:
        c, t = batch_file_names(date, 1)
        if c.exists() or t.exists():
            data["batches"].append({"round": 1,
                                    "created_at": datetime.now().isoformat(timespec="seconds"),
                                    "status": "ready"})
            data["active_round"] = 1
            save_batches(date, data)
    return data

def load_works(date):
    """当日文稿槽位索引。旧数据兼容：有 {date}_v1.md 但无索引时补 w01。"""
    WORKS_DIR.mkdir(parents=True, exist_ok=True)
    p = WORKS_DIR / f"{date}.json"
    data = load_json(p, default=None) or {}
    data.setdefault("date", date)
    data.setdefault("slots", [])
    if not data["slots"] and (DRAFTS_DIR / f"{date}_v1.md").exists():
        data["slots"].append({"id": "w01", "round": 1, "cand_id": None,
                              "topic": "", "motif_series": "",
                              "status": "draft",
                              "created_at": datetime.now().isoformat(timespec="seconds"),
                              "updated_at": datetime.now().isoformat(timespec="seconds"),
                              "selected_title": None, "draft_media_id": None,
                              "legacy": True})
        write_json(p, data)
    return data


def save_works(date, data):
    WORKS_DIR.mkdir(parents=True, exist_ok=True)
    write_json(WORKS_DIR / f"{date}.json", data)


def get_slot(date, slot_id):
    """按 id 取槽位 meta；无则 None。"""
    for s in load_works(date).get("slots", []):
        if s.get("id") == slot_id:
            return s
    return None


def _next_slot_id(slots) -> str:
    """新槽位 id = 现有数值最大序号 +1（永不复用）。
    旧算法 len(slots)+1：删除 w01 后新建会再次算出 w02 与现存槽位撞 id，
    两篇文稿串写同一批文件互相覆盖。"""
    import re as _re
    mx = 0
    for s in slots:
        m = _re.match(r"^w(\d+)$", str(s.get("id") or ""))
        if m:
            mx = max(mx, int(m.group(1)))
    return f"w{mx + 1:02d}"


def create_slot(date, rank, chosen, round_n):
    """选定选题 → 创建/复用文稿槽位。同 (round, cand_id) 不重复建。返回 (slot_id, slot_meta, is_new)。"""
    data = load_works(date)
    cand_id = chosen.get("cand_id")
    for s in data["slots"]:
        if s.get("round") == round_n and s.get("cand_id") == cand_id:
            return s["id"], s, False
    sid = _next_slot_id(data["slots"])
    slot = {"id": sid, "round": round_n, "cand_id": cand_id,
            "topic": chosen.get("topic"), "motif_series": chosen.get("motif_series"),
            "status": "draft",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "selected_title": None, "draft_media_id": None}
    data["slots"].append(slot)
    save_works(date, data)
    return sid, slot, True


def delete_slot(date, slot_id):
    """删除文稿槽位：移除 meta + 清掉该槽全部产物文件（v1/标题/规范版/报告/发布登记等）。
    已发布槽位拒绝删除（发布数据要留档复盘）。返回 (ok, msg)。"""
    data = load_works(date)
    slot = next((s for s in data["slots"] if s.get("id") == slot_id), None)
    if not slot:
        return False, f"槽位 {slot_id} 不存在"
    pub_p = DRAFTS_DIR / f"{date}_{slot_id}_published.json"
    pub_data = load_json(pub_p, default=None) or {}
    if slot.get("status") == "published" or pub_data.get("user_published"):
        return False, f"{slot_id.upper()} 已登记发布，发布数据需留档复盘，不可删除"

    data["slots"] = [s for s in data["slots"] if s.get("id") != slot_id]
    save_works(date, data)

    removed = []
    for p in DRAFTS_DIR.glob(f"{date}_{slot_id}_*"):
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink()
            removed.append(p.name)
        except OSError:
            pass

    # active_slot 指向被删槽位时，优先改指剩余未发布的槽；没有则清空
    sel_p = SELECTIONS_DIR / f"{date}.json"
    sel = load_json(sel_p, default=None) or {}
    if sel.get("active_slot") == slot_id:
        drafts_left = [s for s in data["slots"] if s.get("status") != "published"]
        sel["active_slot"] = drafts_left[0]["id"] if drafts_left else None
        write_json(sel_p, sel)
    return True, f"已删除 {slot_id.upper()}（{slot.get('topic', '')[:20]}），清理 {len(removed)} 个文件"
