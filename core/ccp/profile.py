#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/ccp/profile.py · CCP 读写管理 + 版本回溯 + 注入上下文生成。

职责：
  - 加载/保存（原子写 + 版本快照 + 修订说明，支持回溯）
  - 结构完整性校验（复用 model.validate_profile），坏数据优雅重建冷启动
  - 聚合现有资产：旧画像 preference_profile + 金句库 + 排版激活态（复用而非另起炉灶）
  - 生成四维注入上下文块：读取侧（选题/写稿/排版/头图 4 节点）软约束注入的载体

单一路径：所有 CCP 写操作必须经 Profile 实例；外部不直接改内存 dict。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from core.storage import load_json, write_json
from core.ccp.model import (CCP_SCHEMA_VERSION, DIMS, INJECTION_DEFAULTS,
                            ProfileError, scene_default_profile,
                            validate_profile)


def _ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ClientProfile:
    """客户内容档案实例（单租户 MVP：默认 client_id=slowpost）。"""

    def __init__(self, path: Path, history_dir: Optional[Path] = None,
                 client_id: str = "slowpost", scene: str | None = None):
        self.path = Path(path)
        self.history_dir = Path(history_dir) if history_dir else self.path.parent / "history"
        self.client_id = client_id
        self._scene = scene
        self._p: Optional[Dict] = None

    # ---------- 加载 / 保存 ----------
    def load(self, *, force_new: bool = False) -> Dict:
        if self._p is not None and not force_new:
            return self._p
        try:
            rawhash = load_json(self.path)
        except (ValueError, json.JSONDecodeError):
            rawhash = None  # 坏 JSON → 留档重建
        if rawhash is None and self.path.exists():
            # 文件存在但读不出有效 JSON（load_json 已改为损坏回退默认不抛，
            # 这里显式识别坏档留档，不静默丢失）
            self._archive_broken()
        if rawhash:
            try:
                self._p = validate_profile(rawhash)
                return self._p
            except ProfileError:
                self._archive_broken()  # 坏结构留档，不静默丢失
        self._p = self._bootstrap_new()
        self.save(reason="冷启动初始化")
        return self._p

    def _bootstrap_new(self) -> Dict:
        p = scene_default_profile(self.client_id, self._scene or "companion_letter_writing")
        return p

    def _archive_broken(self):
        if not self.path.exists():
            return
        self.history_dir.mkdir(parents=True, exist_ok=True)
        dst = self.history_dir / f"broken_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            shutil.copy2(self.path, dst)
        except OSError:
            pass

    def save(self, reason: str = ""):
        """原子写 + 追加修订说明；每次持久化累进 meta.version。"""
        if self._p is None:
            return
        self._p["schema_version"] = CCP_SCHEMA_VERSION
        self._p["meta"]["updated_at"] = _ts()
        self._p["meta"]["version"] = int(self._p.get("meta", {}).get("version") or 1) + 1
        if reason:
            notes = self._p["meta"].setdefault("revision_notes", [])
            notes.append(f"[v{self._p['meta']['version']}] {_ts()} {reason}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.path, self._p)
        self.snapshot()

    def snapshot(self) -> Path:
        """版本快照（回溯）：每次 save 在 history/ 落一份带版本号副本（最多保留 60 份）。"""
        self.history_dir.mkdir(parents=True, exist_ok=True)
        ver = self._p.get("meta", {}).get("version", 1)
        dst = self.history_dir / f"profile_v{ver}.json"
        write_json(dst, self._p)
        snaps = sorted(self.history_dir.glob("profile_v*.json"))
        for old in snaps[:-60]:
            try:
                old.unlink()
            except OSError:
                pass
        return dst

    def rollback(self, version: int) -> bool:
        """版本回溯：从 history/ 快照恢复。返回是否成功。"""
        src = self.history_dir / f"profile_v{version}.json"
        if not src.exists():
            return False
        try:
            data = validate_profile(load_json(src))
        except (ProfileError, TypeError):
            return False
        self._p = data
        self.save(reason=f"版本回溯到 v{version}")
        return True

    @property
    def profile(self) -> Dict:
        if self._p is None:
            self.load()
        return self._p

    # ---------- 读：注入上下文（软约束 §6.3） ----------
    def inject_style(self) -> str:
        st = self.profile["style"]
        lines = []
        if st.get("language_tone"):
            lines.append(f"语言基调：{st['language_tone']}")
        if st.get("sentence_habits"):
            lines.append("语句习惯：" + "；".join(str(x) for x in st["sentence_habits"][:6]))
        if st.get("frameworks"):
            lines.append("常用框架：" + "；".join(str(x) for x in st["frameworks"][:5]))
        if st.get("quote_preferences"):
            lines.append("金句偏好：" + "；".join(str(x) for x in st["quote_preferences"][:5]))
        if st.get("avoid_styles"):
            lines.append("需避免：" + "；".join(str(x) for x in st["avoid_styles"][:5]))
        block = "\n".join(lines)
        return block[:self._cap("style_block_maxlen")]

    def inject_topic(self) -> str:
        t = self.profile["topic"]
        lines = []
        w = t.get("preference_weights") or {}
        if w:
            top = sorted(w.items(), key=lambda kv: -kv[1])[:8]
            lines.append("主题偏好权重：" + "；".join(f"{k}×{v:.2f}" for k, v in top))
        if t.get("high_performance_patterns"):
            lines.append("高表现模式：" + "；".join(str(x) for x in t["high_performance_patterns"][:5]))
        if t.get("excluded_series"):
            lines.append("排除系列：" + "；".join(t["excluded_series"][:10]))
        block = "\n".join(lines)
        return block[:self._cap("topic_block_maxlen")]

    def inject_layout(self) -> str:
        l = self.profile["layout"]
        lines = []
        if l.get("active_theme"):
            lines.append(f"激活排版主题：{l['active_theme']}")
        lines.append(f"排版疏密：{l.get('density', 'medium')}")
        if l.get("component_toggles"):
            off = [k for k, v in (l["component_toggles"] or {}).items() if not v]
            if off:
                lines.append("默认停用组件：" + "、".join(off))
        if l.get("palette_notes"):
            lines.append("配色偏好：" + "；".join(l["palette_notes"][:5]))
        block = "\n".join(lines)
        return block[:self._cap("layout_block_maxlen")]

    def inject_reader(self) -> str:
        r = self.profile["reader"]
        lines = []
        a = r.get("avg_engagement") or {}
        if a:
            parts = []
            for k in ("reads", "likes", "shares", "engagement"):
                if a.get(k) is not None:
                    parts.append(f"{k}≈{a[k]}")
            if parts:
                lines.append("平均互动：" + "，".join(parts))
        if r.get("notes"):
            lines.append("读者偏好：" + "；".join(r["notes"][:5]))
        block = "\n".join(lines)
        return block[:self._cap("reader_block_maxlen")]

    def _cap(self, key: str) -> int:
        inj = self.profile.get("injection") or {}
        return int(inj.get(key, INJECTION_DEFAULTS.get(key, 300)))

    def build_context(self, dim: str) -> str:
        """按维生成注入上下文块（读取侧节点调用）。未知维/被停用 → 返回空串（软降级）。"""
        if dim not in DIMS:
            return ""
        enabled = self.profile.get("injection", {}).get("enabled_dims") or \
            INJECTION_DEFAULTS["enabled_dims"]
        if dim not in enabled:
            return ""
        return getattr(self, f"inject_{dim}")()

    def _enabled_dims(self) -> List[str]:
        return list(self.profile.get("injection", {}).get("enabled_dims")
                    or INJECTION_DEFAULTS["enabled_dims"])

    def build_full_context(self) -> str:
        """拼接全部启用的维度块：启用的维恒占位（内容为空也占），被停用的维不出现。
        反映"约束作用域"而非"当前是否有数据"。"""
        enabled = self._enabled_dims()
        parts = []
        for dim in DIMS:
            if dim not in enabled:
                continue
            blk = self.build_context(dim)
            parts.append(f"[{dim}] {blk}" if blk else f"[{dim}]")
        return "\n\n".join(parts)

    def record_injection(self, where: str, block: str):
        """trace 用：记录一次注入摘要（写入侧留痕，不进 revision_notes 防止噪音）。
        block 为空（软降级/被停用）则跳过。"""
        if not block:
            return
        self.profile.setdefault("injected_subject", [])
        self.profile["injected_subject"].append({
            "ts": _ts(), "where": where, "chars": len(block)})
        self.profile["injected_subject"] = self.profile["injected_subject"][-50:]

    # ---------- 聚合现有资产（复用而非复制） ----------
    def absorb_preference_profile(self, pref: Optional[Dict]) -> bool:
        """吸收旧画像 preference_profile.json：评分校准沿用，topic 权重从反馈学（见 feedback）。
        返回是否发生实际变更（用于启动钩子决定是否落盘）。"""
        if not pref or not isinstance(pref, dict):
            return False
        meta = self.profile["meta"]
        changed = False
        src = pref.get("source")
        if src and meta.get("pref_source") != src:
            meta["pref_source"] = src
            changed = True
        dw = pref.get("data_window")
        if dw and meta.get("pref_data_window") != dw:
            meta["pref_data_window"] = dw
            changed = True
        return changed

    def absorb_layout_state(self, layout: Dict) -> bool:
        """吸收排版激活态（排版中心 settings）：active_theme / density / toggles。
        返回是否发生实际变更。"""
        if not isinstance(layout, dict):
            return False
        l = self.profile["layout"]
        changed = False
        if layout.get("active_theme") and l["active_theme"] != layout["active_theme"]:
            l["active_theme"] = layout["active_theme"]
            changed = True
        if layout.get("density") and l["density"] != layout["density"]:
            l["density"] = layout["density"]
            changed = True
        if isinstance(layout.get("component_toggles"), dict):
            for k, v in layout["component_toggles"].items():
                if l["component_toggles"].get(k) != v:
                    l["component_toggles"][k] = v
                    changed = True
        return changed


def get_profile(path: Optional[Path] = None,
                client_id: str = "slowpost") -> ClientProfile:
    """默认单例入口（path 为空时代替 paths.CCP_PROFILE_PATH）。"""
    if path is None:
        from core.paths import CCP_PROFILE_PATH
        path = CCP_PROFILE_PATH
    return ClientProfile(path, client_id=client_id)