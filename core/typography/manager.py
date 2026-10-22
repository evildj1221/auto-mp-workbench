#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""manager.py · 主题管理器（阶段一 S1）。

职责：内置主题（随包分发）+ 用户主题（data/typography/themes/*.json）的加载、
激活与微调（override），全部状态落 data/typography/settings.json。

- 优雅降级：损坏的主题 JSON / settings.json 跳过并记入 load_errors，
  激活主题缺失时回退默认主题——排版链路永不因数据损坏而中断。
- 原子写：临时文件 + os.replace，断电不留半截 JSON。
- 继承展开：base 指向的主题沿链 deep_merge（差分存储，为主题市场预铺）。
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .theme import Theme, ThemeError, ThemeRuntime, apply_overrides, deep_merge
from .safety import validate_token_value

DEFAULT_THEME_ID = "tianguang_yunying"


def _builtin_themes_dir() -> Path:
    """内置主题目录（源码与 PyInstaller frozen 模式均适用）。"""
    return Path(__file__).resolve().parent / "builtin_themes"


def _write_json_atomic(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class ThemeManager:
    """一个进程一个实例（见 get_manager）；测试可用独立 data_dir 实例化隔离。"""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.themes_dir = self.data_dir / "themes"
        self.settings_path = self.data_dir / "settings.json"
        self._themes = {}          # id -> Theme（未展开继承的原始定义）
        self._order: List[str] = []  # 展示顺序：内置在前、用户在后
        self._settings = {"active_theme": DEFAULT_THEME_ID, "overrides": {}}
        self.load_errors: List[str] = []
        self.reload()

    # ===== 加载 =====
    def reload(self):
        self._themes.clear()
        self._order.clear()
        self.load_errors = []
        self.themes_dir.mkdir(parents=True, exist_ok=True)

        for path in sorted(_builtin_themes_dir().glob("*.json")):
            self._load_theme_file(path, builtin=True)
        for path in sorted(self.themes_dir.glob("*.json")):
            self._load_theme_file(path, builtin=False)

        # 继承展开 + 全量解析校验：坏主题（差分缺硬依赖 / base 缺失 / 继承成环 /
        # 组件样式槽引用无法解析）在加载期剔除并记录——排版链路不因坏数据中断
        from . import REGISTRY  # 延迟导入：包初始化完成后才构造 manager
        for tid in list(self._order):
            try:
                ThemeRuntime(self.get_theme(tid), REGISTRY)
            except ThemeError as e:
                self.load_errors.append(f"剔除不可用主题 {tid}: {e}")
                print(f"  [WARN] 排版主题不可用，已剔除 {tid}: {e}", file=sys.stderr)
                self._themes.pop(tid, None)
                self._order.remove(tid)

        # 展示顺序：默认主题 → 其余内置（字母序） → 用户主题（字母序）
        self._order.sort(key=lambda t: (0 if t == DEFAULT_THEME_ID else
                                        1 if self._themes[t].builtin else 2, t))

        try:
            if self.settings_path.exists():
                s = _read_json(self.settings_path)
                if isinstance(s, dict):
                    self._settings = {
                        "active_theme": s.get("active_theme") or DEFAULT_THEME_ID,
                        "overrides": s.get("overrides") or {}}
        except Exception as e:
            self.load_errors.append(f"settings.json 损坏，已回退默认（{e}）")
            self._settings = {"active_theme": DEFAULT_THEME_ID, "overrides": {}}

        if self._settings["active_theme"] not in self._themes:
            if self._settings["active_theme"] != DEFAULT_THEME_ID:
                self.load_errors.append(
                    f"激活主题 {self._settings['active_theme']} 不存在，已回退默认")
            self._settings["active_theme"] = DEFAULT_THEME_ID
        if DEFAULT_THEME_ID not in self._themes:
            raise ThemeError(f"内置默认主题缺失: {DEFAULT_THEME_ID}")

    def _load_theme_file(self, path: Path, builtin: bool):
        try:
            th = Theme.from_dict(_read_json(path), builtin=builtin, path=path)
            if th.id in self._themes:
                raise ThemeError(f"主题 id 重复: {th.id}")
            self._themes[th.id] = th
            self._order.append(th.id)
        except Exception as e:
            self.load_errors.append(f"跳过损坏主题 {path.name}: {e}")
            print(f"  [WARN] 排版主题加载失败 {path.name}: {e}", file=sys.stderr)

    # ===== 查询 =====
    def theme_ids(self) -> List[str]:
        return list(self._order)

    def get_theme(self, theme_id: str) -> Theme:
        """按 id 取主题（沿 base 链展开继承 + 完整性校验；不叠加用户 override）。"""
        th = self._themes.get(theme_id)
        if th is None:
            raise ThemeError(f"主题不存在: {theme_id}")
        return self._expand(th, seen=set()).validate_complete()

    def _expand(self, th: Theme, seen: set) -> Theme:
        if th.base is None:
            return th
        if th.id in seen:
            raise ThemeError(f"主题继承成环: {th.id}")
        seen.add(th.id)
        base = self._themes.get(th.base)
        if base is None:
            raise ThemeError(f"主题 {th.id} 的 base 不存在: {th.base}")
        base = self._expand(base, seen)
        return Theme(id=th.id, name=th.name, version=th.version, base=th.base,
                     meta=th.meta,
                     tokens=deep_merge(base.tokens, th.tokens),
                     component_defaults=deep_merge(base.component_defaults,
                                                   th.component_defaults),
                     builtin=th.builtin, path=th.path)

    def active_theme_id(self) -> str:
        return self._settings["active_theme"]

    def get_overrides(self, theme_id: str) -> dict:
        return self._settings.get("overrides", {}).get(theme_id) or {}

    def effective_theme(self, theme_id: Optional[str] = None) -> Theme:
        """生效主题 = 展开继承 + 该主题的用户 override（导出与预览共用）。"""
        th = self.get_theme(theme_id or self.active_theme_id())
        return apply_overrides(th, self.get_overrides(th.id))

    def list_themes(self) -> List[dict]:
        """排版中心主题卡片数据。"""
        out = []
        active = self.active_theme_id()
        for tid in self._order:
            try:
                th = self.get_theme(tid)
                ov = self.get_overrides(tid)
                colors = th.tokens.get("colors", {})
                out.append({
                    "id": tid, "name": th.name, "builtin": th.builtin,
                    "active": tid == active, "base": th.base,
                    "meta": th.meta, "tags": th.meta.get("tags") or [],
                    "palette": [colors.get(k, "#FFFFFF") for k in
                                ("title", "accent", "surface", "card", "muted")],
                    "has_overrides": bool(ov),
                })
            except ThemeError as e:
                self.load_errors.append(f"主题 {tid} 展开失败: {e}")
        return out

    # ===== 激活 / 微调 / 另存（全部即时落盘） =====
    def activate_theme(self, theme_id: str):
        self.get_theme(theme_id)  # 不存在即 ThemeError
        self._settings["active_theme"] = theme_id
        self._save_settings()

    def _check_override(self, theme: Theme, dotted_key: str, value):
        """路径存在性 + 值类型前置校验（typo / 坏值早失败，不落盘）。"""
        parts = dotted_key.split(".")
        if len(parts) < 3 or parts[0] not in ("tokens", "components"):
            raise ThemeError(f"override 键非法: {dotted_key}"
                             "（须为 tokens.* 或 components.*）")
        if parts[0] == "tokens":
            if not isinstance(value, str):
                raise ThemeError(f"override 值必须是字符串: {dotted_key}={value!r}")
            node = theme.tokens
            for p in parts[1:]:
                if not isinstance(node, dict) or p not in node:
                    raise ThemeError(f"override 键不存在: {dotted_key}（主题无此 token 路径）")
                node = node[p]
            # 商业化 P0：格式与可读性底线（不随主题变化的绝对约束）。
            # 缺了这一层，色板白名单会自指 —— 用户把 title 改成 "zzz" 也能落盘
            # 并穿透到最终 HTML（2026-09-05 实测 BUG-2）。
            ok, reason = validate_token_value(dotted_key, value)
            if not ok:
                raise ThemeError(f"{dotted_key}: {reason}")
            return
        # components.{组件名}.{配置键}（配置扁平，恰好三段）
        if len(parts) != 3:
            raise ThemeError(f"override 键非法: {dotted_key}"
                             "（须为 components.{{组件}}.{{配置键}}）")
        from . import REGISTRY  # 延迟导入：避免包初始化期循环依赖
        spec = REGISTRY.get(parts[1])
        if spec is None:
            raise ThemeError(f"override 组件未注册: {parts[1]}")
        schema = (spec.config_schema or {}).get(parts[2])
        if schema is None:
            raise ThemeError(f"override 配置键不存在: {dotted_key}（{spec.label} 无此配置）")
        vtype = schema.get("type")
        if vtype == "bool" and not isinstance(value, bool):
            raise ThemeError(f"override 值必须是布尔: {dotted_key}={value!r}")
        if vtype == "text" and not isinstance(value, str):
            raise ThemeError(f"override 值必须是字符串: {dotted_key}={value!r}")
        if vtype == "list" and not (isinstance(value, list)
                                    and all(isinstance(x, str) for x in value)):
            raise ThemeError(f"override 值必须是字符串列表: {dotted_key}={value!r}")
        if vtype == "select" and value not in (schema.get("options") or []):
            raise ThemeError(f"override 值不在选项内: {dotted_key}={value!r}"
                             f"（可选 {schema.get('options')}）")

    def set_override(self, theme_id: str, dotted_key: str, value):
        """dotted_key：tokens.colors.accent / components.action.label_text。
        前置校验路径与值类型；落盘后再全量解析一遍（覆盖全部组件的样式槽
        与配置），坏值立即回滚，不把用户锁死在坏状态里。"""
        self._check_override(self.get_theme(theme_id), dotted_key, value)
        parts = dotted_key.split(".")
        node = {parts[-1]: value}
        for part in reversed(parts[1:-1]):
            node = {part: node}
        backup = self._settings.get("overrides") or {}
        overrides = {k: dict(v) for k, v in backup.items()}
        ov = dict(overrides.get(theme_id) or {})
        branch = parts[0]
        ov[branch] = deep_merge(ov.get(branch) or {}, node)
        overrides[theme_id] = ov
        self._settings["overrides"] = overrides
        self._save_settings()
        try:
            from . import REGISTRY  # 延迟导入：避免包初始化期循环依赖
            ThemeRuntime(self.effective_theme(theme_id), REGISTRY)
        except ThemeError:
            self._settings["overrides"] = backup
            self._save_settings()
            raise

    def reset_overrides(self, theme_id: str):
        overrides = dict(self._settings.get("overrides") or {})
        if theme_id in overrides:
            overrides.pop(theme_id)
            self._settings["overrides"] = overrides
            self._save_settings()

    def save_user_theme(self, name: str, base_id: Optional[str] = None) -> Theme:
        """把 base 主题当前 override 固化为用户主题（base + diff 结构）。"""
        name = (name or "").strip()
        if not name:
            raise ThemeError("主题名不能为空")
        base_id = base_id or self.active_theme_id()
        base_raw = self._themes.get(base_id)
        if base_raw is None:
            raise ThemeError(f"基主题不存在: {base_id}")
        ov = self.get_overrides(base_id)
        theme_id = "u" + datetime.now().strftime("%Y%m%d%H%M%S")
        data = {
            "id": theme_id, "name": name, "version": "1", "base": base_id,
            "meta": {"author": "user", "description": f"基于「{base_raw.name}」微调另存",
                     "tags": ["我的"]},
            "tokens": (ov.get("tokens") or {}),
            "component_defaults": (ov.get("components") or {}),
        }
        path = self.themes_dir / f"{theme_id}.json"
        _write_json_atomic(path, data)
        th = Theme.from_dict(data, builtin=False, path=path)
        self._themes[theme_id] = th
        self._order.append(theme_id)
        self.activate_theme(theme_id)
        return th

    def delete_user_theme(self, theme_id: str):
        th = self._themes.get(theme_id)
        if th is None:
            raise ThemeError(f"主题不存在: {theme_id}")
        if th.builtin:
            raise ThemeError("内置主题不可删除")
        del self._themes[theme_id]
        self._order.remove(theme_id)
        if th.path and th.path.exists():
            th.path.unlink()
        if self._settings["active_theme"] == theme_id:
            self._settings["active_theme"] = DEFAULT_THEME_ID
        self.reset_overrides(theme_id)
        self._save_settings()

    def _save_settings(self):
        _write_json_atomic(self.settings_path, self._settings)


# ===== 进程级单例 =====
_MANAGER: Optional[ThemeManager] = None


def get_manager(data_dir=None) -> ThemeManager:
    global _MANAGER
    if _MANAGER is None:
        if data_dir is None:
            from core.paths import DATA_DIR
            data_dir = DATA_DIR / "typography"
        _MANAGER = ThemeManager(data_dir)
    return _MANAGER


def reset_manager():
    """测试钩子：丢弃单例（隔离 data_dir 重新初始化）。"""
    global _MANAGER
    _MANAGER = None


def default_theme() -> Theme:
    """默认内置主题（无用户状态、无 override）——纯渲染与校验的确定性基准。"""
    path = _builtin_themes_dir() / f"{DEFAULT_THEME_ID}.json"
    return Theme.from_dict(_read_json(path), builtin=True, path=path).validate_complete()
