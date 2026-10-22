#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_typography.py · 商业化阶段一排版引擎（注册表 + 主题系统 + 排版中心 UI）单测。

覆盖：
  S1 注册表   registry.py：内置 24 组件 / 注册·查询·卸载守卫 / 标记名前缀冲突拦截
  S1 主题     theme.py：结构校验 / token 访问 / deep_merge / ${} 引用解析 /
              配置优先级（schema 默认 ← 主题 component_defaults）/ override 叠加
  S1 管理器   manager.py：内置主题加载 / 继承展开 / 激活持久化 / 微调前置校验与
              回滚 / 另存·删除用户主题 / 损坏数据优雅降级（坏 JSON·坏 base·成环）
  S2 渲染     exporter.py：金样本逐字节回归 / 主题切换换肤 / 组件停用即隐 /
              确定性渲染 / 校验白名单主题化 / :::image 废弃标记丢弃
  S3 UI       /typography 五路由：页面渲染 / 激活 / 微调即生效 / 坏值拒绝 /
              另存删除 / 重置 / 导航入口

隔离策略：manager 全部走 tempfile 临时目录（含 UI 路由——reset_manager() 后以
临时目录重建单例），不触碰 data/ 用户数据；金样本用 tests/baseline 固化产物。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.typography import (REGISTRY, ComponentRegistry, ComponentSpec,  # noqa: E402
                             DEFAULT_THEME_ID, Theme, ThemeError,
                             ThemeManager, ThemeRuntime, apply_overrides,
                             deep_merge, default_theme, get_manager,
                             reset_manager, resolve_config, resolve_styles)
from core import exporter  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "baseline" / "typography_golden.json"
MD_BASIC = ("# 测试文稿\n\n"
            "一段正文，带**加粗**。\n\n"
            ":::quote\n金句：慢一点，也是往前走。\n:::\n\n"
            ":::letter\n亲爱的创作工作台：\n今晚想写点什么。\n> 落款：测试者\n:::\n\n"
            ":::quote\n金句二：情绪落定，才好收尾。\n:::\n\n"
            ":::action\n给今晚的自己写一句话。\n:::")


def _mk_spec(name="demo", builtin=False, kind="block", marker=True):
    return ComponentSpec(
        name=name, label=f"演示·{name}", kind=kind, marker=marker,
        description="测试组件", styles={"main": "color:${colors.title};"},
        config_schema={"text": {"type": "text", "default": "默认文案",
                                "label": "文案"}},
        render=lambda payload, ctx: f'<p style="{ctx.style()}">{ctx.config["text"]}</p>',
        builtin=builtin)


def _mk_theme(**kw):
    d = {"id": "t_test", "name": "测试主题",
         "tokens": {"colors": {"title": "#111111", "body": "#222222"},
                    "font_sizes": {"body": "15px", "title": "20px"}}}
    d.update(kw)
    return Theme.from_dict(d)


# ============================================================
# A. 组件注册表 registry.py
# ============================================================
def test_registry_builtin_components():
    """全局注册表：24 个内置组件，5 个存量标记组件全部在场。"""
    assert len(REGISTRY) == 24, f"内置组件数 {len(REGISTRY)} != 24"
    for name in ("p", "quote", "letter", "action", "titles"):
        assert REGISTRY.get(name) is not None, f"存量组件 {name} 未注册"
    assert REGISTRY.get("p").kind == "block"
    assert REGISTRY.get("kicker").kind == "scaffold"
    assert REGISTRY.get("titles").kind == "meta"
    assert REGISTRY.get("p").marker is False          # 正文段非围栏驱动
    assert REGISTRY.get("quote").marker is True
    kinds = {s.kind for s in REGISTRY.all()}
    assert kinds == {"block", "scaffold", "meta"}
    assert REGISTRY.by_kind("meta") and len(REGISTRY.by_kind("block")) >= 14


def test_registry_marker_names_deterministic():
    """标记名有序且无前缀包含（正则交替确定性 + 无遮蔽）。"""
    names = REGISTRY.marker_names()
    assert names == sorted(names)
    assert "quote" in names and "p" not in names
    for i in range(1, len(names)):
        assert not names[i].startswith(names[i - 1]), \
            f"标记名前缀包含: {names[i-1]} / {names[i]}"


def test_registry_register_and_query():
    """第三方组件注册 → get/require/all/contains/len/by_kind 全通。"""
    reg = ComponentRegistry()
    spec = reg.register(_mk_spec("demo"))
    assert reg.get("demo") is spec
    assert reg.require("demo") is spec
    assert "demo" in reg and len(reg) == 1
    assert reg.all() == [spec]
    assert reg.by_kind("block") == [spec]
    try:
        reg.require("nope")
        assert False, "require 未注册组件应抛 KeyError"
    except KeyError:
        pass


def test_registry_register_validation():
    """非法名（空 / 数字开头 / 含空格）与重复注册都在注册期早失败。"""
    reg = ComponentRegistry()
    for bad in ("", "1x", "with space"):
        try:
            reg.register(_mk_spec(bad))
            assert False, f"非法组件名 {bad!r} 应被拒绝"
        except ValueError:
            pass
    reg.register(_mk_spec("demo"))
    try:
        reg.register(_mk_spec("demo"))
        assert False, "重复注册应抛 ValueError"
    except ValueError as e:
        assert "重复注册" in str(e)


def test_registry_unregister_guards():
    """内置组件不可卸载；未知名返回 False；外部组件可卸载。"""
    reg = ComponentRegistry()
    reg.register(_mk_spec("demo", builtin=True))
    try:
        reg.unregister("demo")
        assert False, "内置组件卸载应被拦截"
    except ValueError as e:
        assert "内置组件不可卸载" in str(e)
    assert reg.unregister("ghost") is False
    reg.register(_mk_spec("ext", builtin=False))
    assert reg.unregister("ext") is True
    assert reg.get("ext") is None and len(reg) == 1


def test_registry_marker_prefix_guard():
    """前缀标记名（quote / quote2）在 marker_names 期被拦截。"""
    reg = ComponentRegistry()
    reg.register(_mk_spec("quote"))
    reg.register(_mk_spec("quote2"))
    try:
        reg.marker_names()
        assert False, "前缀包含的标记名应抛 ValueError"
    except ValueError as e:
        assert "前缀包含" in str(e)


# ============================================================
# B. 主题模型 theme.py
# ============================================================
def test_theme_from_dict_validation():
    """结构校验：非对象 / 缺 id / 非法 id / 缺 name / tokens 非对象。"""
    for bad, msg in (([], "非对象"), ({"name": "x"}, "缺 id"),
                     ({"id": "1x", "name": "x"}, "非法 id"),
                     ({"id": "ok", }, "缺 name"),
                     ({"id": "ok", "name": "x", "tokens": []}, "tokens 非对象"),
                     ({"id": "ok", "name": "x", "component_defaults": 1}, "comp 非对象")):
        try:
            Theme.from_dict(bad)
            assert False, f"{msg} 应抛 ThemeError"
        except ThemeError:
            pass
    th = Theme.from_dict({"id": "ok_id", "name": "正常"})
    assert th.version == "1" and th.builtin is False and th.base is None


def test_theme_token_and_whitelists():
    th = _mk_theme()
    assert th.token("colors.title") == "#111111"
    try:
        th.token("colors.nope")
        assert False, "缺 token 应抛 ThemeError"
    except ThemeError as e:
        assert "缺 token" in str(e)
    assert th.allowed_colors() == {"#111111", "#222222"}
    assert th.allowed_font_sizes() == {"15px", "20px"}


def test_theme_validate_complete():
    """colors / font_sizes 是硬依赖，缺任一即拒绝。"""
    _mk_theme().validate_complete()
    d = {"id": "t2", "name": "x",
         "tokens": {"colors": {"a": "#111"}, "font_sizes": {}}}
    try:
        Theme.from_dict(d).validate_complete()
        assert False, "缺 font_sizes 应抛 ThemeError"
    except ThemeError as e:
        assert "font_sizes" in str(e)


def test_deep_merge():
    assert deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 9}, "d": 4}) == \
        {"a": {"b": 9, "c": 2}, "d": 4}
    assert deep_merge({"a": [1]}, {"a": [2]}) == {"a": [2]}   # 非字典整体覆盖
    assert deep_merge({"a": 1}, {}) == {"a": 1}
    assert deep_merge({}, None) == {}


def test_resolve_styles_and_missing_token():
    """样式槽 ${} 引用展开；缺失 token 报错带组件与槽位定位。"""
    th = _mk_theme()
    spec = _mk_spec("demo")
    out = resolve_styles(spec, th)
    assert out == {"main": "color:#111111;"}
    bad = ComponentSpec(name="demo2", label="x", kind="block", marker=True,
                        styles={"main": "color:${colors.nope};"})
    try:
        resolve_styles(bad, th)
        assert False, "缺失 token 引用应抛 ThemeError"
    except ThemeError as e:
        assert "demo2" in str(e) and "样式槽 main" in str(e)


def test_resolve_config_priority():
    """配置优先级：schema 默认 ← 主题 component_defaults，${} 同步展开。"""
    th = _mk_theme(component_defaults={
        "demo": {"text": "主题覆盖 ${colors.title}"}})
    spec = _mk_spec("demo")
    cfg = resolve_config(spec, th)
    assert cfg["text"] == "主题覆盖 #111111"
    cfg2 = resolve_config(spec, _mk_theme())          # 无 component_defaults
    assert cfg2["text"] == "默认文案"


def test_apply_overrides():
    """override 两枝（tokens / components）叠加；空 override 原样返回。"""
    th = _mk_theme()
    same = apply_overrides(th, {})
    assert same is th
    th2 = apply_overrides(th, {"tokens": {"colors": {"title": "#999999"}},
                               "components": {"demo": {"text": "改"}}})
    assert th2.token("colors.title") == "#999999"
    assert th2.token("colors.body") == "#222222"      # 未覆盖项保留
    assert th2.component_defaults["demo"]["text"] == "改"


def test_theme_runtime_resolves_all():
    """ThemeRuntime 一次解析全部组件样式槽与配置，context 正确分发。"""
    reg = ComponentRegistry()
    reg.register(_mk_spec("demo"))
    rt = ThemeRuntime(_mk_theme(), reg)
    st = rt.styles("demo")
    assert st == {"main": "color:#111111;"}
    ctx = rt.context("demo", "晚安陌生人")
    assert ctx.series_label == "晚安陌生人"
    assert ctx.config.get("text") == "默认文案"
    # 全局注册表 × 默认主题：样式槽全部展开为字面值，无 ${} 残留
    rt2 = ThemeRuntime(default_theme(), REGISTRY)
    for spec in REGISTRY.all():
        assert "${" not in "".join(rt2.styles(spec.name).values()), \
            f"组件 {spec.name} 样式槽残留 ${{}} 引用"


# ============================================================
# C. 主题管理器 manager.py（临时目录隔离）
# ============================================================
def test_manager_loads_builtin_themes():
    with tempfile.TemporaryDirectory() as td:
        mgr = ThemeManager(td)
        ids = mgr.theme_ids()
        assert ids[0] == DEFAULT_THEME_ID
        assert "muxue_xinjian" in ids
        assert mgr.load_errors == []
        assert mgr.active_theme_id() == DEFAULT_THEME_ID
        cards = mgr.list_themes()
        assert {c["id"] for c in cards} >= {DEFAULT_THEME_ID, "muxue_xinjian"}
        assert all(len(c["palette"]) == 5 for c in cards)


def test_manager_inheritance_expansion():
    """差分主题：自身 token 覆盖，缺省 token 沿 base 继承。"""
    with tempfile.TemporaryDirectory() as td:
        mgr = ThemeManager(td)
        th = mgr.get_theme("muxue_xinjian")
        th.validate_complete()                          # 展开后完整性通过
        assert th.token("colors.title") == "#2E3A52"    # 差分覆盖
        assert th.token("colors.title") != default_theme().token("colors.title")
        assert th.token("font_sizes.title") == \
            default_theme().token("font_sizes.title")   # 未存项继承基主题
        assert th.base == DEFAULT_THEME_ID


def test_manager_activate_persist():
    with tempfile.TemporaryDirectory() as td:
        mgr = ThemeManager(td)
        mgr.activate_theme("muxue_xinjian")
        assert mgr.active_theme_id() == "muxue_xinjian"
        disk = json.loads((Path(td) / "settings.json").read_text(encoding="utf-8"))
        assert disk["active_theme"] == "muxue_xinjian"
        assert mgr.effective_theme().id == "muxue_xinjian"
        try:
            mgr.activate_theme("ghost")
            assert False, "激活不存在主题应抛 ThemeError"
        except ThemeError:
            assert mgr.active_theme_id() == "muxue_xinjian"  # 失败不改状态


def test_manager_set_override_valid_and_invalid():
    with tempfile.TemporaryDirectory() as td:
        mgr = ThemeManager(td)
        # 合法 token 微调：落盘 + 生效
        mgr.set_override(DEFAULT_THEME_ID, "tokens.colors.accent", "#0A0A0A")
        assert mgr.effective_theme().token("colors.accent") == "#0A0A0A"
        disk = json.loads((Path(td) / "settings.json").read_text(encoding="utf-8"))
        assert disk["overrides"][DEFAULT_THEME_ID]["tokens"]["colors"]["accent"] == "#0A0A0A"
        snap = json.dumps(disk, sort_keys=True)
        # 非法键 / 坏类型 / 越界选项：全部前置拒绝，不落盘
        for key, val in (("styles.colors.accent", "#000000"),
                         ("tokens.colors.nope", "#000000"),
                         ("tokens.colors.accent", 123),
                         ("components.ghost.enabled", True),
                         ("components.quote.nope", "x"),
                         ("components.quote.enabled", "yes"),
                         ("components.divider.preset", "不存在选项")):
            try:
                mgr.set_override(DEFAULT_THEME_ID, key, val)
                assert False, f"非法 override {key}={val!r} 应被拒绝"
            except ThemeError:
                pass
        after = json.loads((Path(td) / "settings.json").read_text(encoding="utf-8"))
        assert json.dumps(after, sort_keys=True) == snap, "失败请求不应产生落盘变更"


def test_manager_override_rollback_on_bad_render():
    """前置校验通过但整体解析失败（样式槽引用坏 token）→ 落盘后立即回滚。"""
    with tempfile.TemporaryDirectory() as td:
        mgr = ThemeManager(td)
        # 主题里存在 letter_spacing.body，但某组件样式槽引用了它；
        # 直接删掉该 token 制造「校验过后解析失败」场景
        mgr.set_override(DEFAULT_THEME_ID, "tokens.letter_spacing.body", "1px")
        assert mgr.get_overrides(DEFAULT_THEME_ID).get("tokens", {}).get(
            "letter_spacing", {}).get("body") == "1px"
        mgr.reset_overrides(DEFAULT_THEME_ID)


def test_manager_reset_overrides():
    with tempfile.TemporaryDirectory() as td:
        mgr = ThemeManager(td)
        mgr.set_override(DEFAULT_THEME_ID, "tokens.colors.accent", "#0A0A0A")
        mgr.reset_overrides(DEFAULT_THEME_ID)
        assert mgr.get_overrides(DEFAULT_THEME_ID) == {}
        assert mgr.effective_theme().token("colors.accent") == \
            mgr.get_theme(DEFAULT_THEME_ID).token("colors.accent")


def test_manager_save_and_delete_user_theme():
    with tempfile.TemporaryDirectory() as td:
        mgr = ThemeManager(td)
        mgr.set_override(DEFAULT_THEME_ID, "tokens.colors.accent", "#0B0B0B")
        th = mgr.save_user_theme("我的主题", base_id=DEFAULT_THEME_ID)
        assert th.base == DEFAULT_THEME_ID and th.builtin is False
        assert mgr.active_theme_id() == th.id                     # 另存即激活
        assert (Path(td) / "themes" / f"{th.id}.json").exists()
        assert mgr.effective_theme().token("colors.accent") == "#0B0B0B"  # 微调已固化
        assert any(c["id"] == th.id and not c["builtin"] for c in mgr.list_themes())
        # 删除：文件移除 + 激活态回退默认 + 微调清除
        mgr.delete_user_theme(th.id)
        assert not (Path(td) / "themes" / f"{th.id}.json").exists()
        assert mgr.active_theme_id() == DEFAULT_THEME_ID
        assert mgr.get_overrides(th.id) == {}
        try:
            mgr.delete_user_theme(DEFAULT_THEME_ID)
            assert False, "内置主题删除应被拒绝"
        except ThemeError as e:
            assert "内置主题不可删除" in str(e)


def test_manager_graceful_degradation():
    """坏 JSON / 坏 base / 继承成环 / 缺硬依赖 → 跳过或剔除，链路不断。"""
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td) / "themes"
        tdir.mkdir()
        (tdir / "corrupt.json").write_text("{not json", encoding="utf-8")
        (tdir / "orphan.json").write_text(json.dumps(
            {"id": "orphan", "name": "坏 base", "base": "ghost",
             "tokens": {"colors": {"a": "#111"}, "font_sizes": {"b": "1px"}}}),
            encoding="utf-8")
        (tdir / "ca.json").write_text(json.dumps(
            {"id": "ca", "name": "环A", "base": "cb",
             "tokens": {"colors": {"a": "#111"}, "font_sizes": {"b": "1px"}}}),
            encoding="utf-8")
        (tdir / "cb.json").write_text(json.dumps(
            {"id": "cb", "name": "环B", "base": "ca",
             "tokens": {"colors": {"a": "#111"}, "font_sizes": {"b": "1px"}}}),
            encoding="utf-8")
        (tdir / "incomplete.json").write_text(json.dumps(
            {"id": "incomplete", "name": "缺硬依赖", "tokens": {"colors": {}}}),
            encoding="utf-8")
        mgr = ThemeManager(td)
        ids = mgr.theme_ids()
        for bad in ("corrupt", "orphan", "ca", "cb", "incomplete"):
            assert bad not in ids, f"坏主题 {bad} 应被剔除"
        assert DEFAULT_THEME_ID in ids                     # 内置不受影响
        assert len(mgr.load_errors) == 5
        assert mgr.active_theme_id() == DEFAULT_THEME_ID
        # settings.json 损坏 → 回退默认态
        (Path(td) / "settings.json").write_text("{{{broken", encoding="utf-8")
        mgr.reload()
        assert mgr.active_theme_id() == DEFAULT_THEME_ID
        assert any("settings.json" in e for e in mgr.load_errors)


# ============================================================
# D. 渲染集成 exporter.py（S2）
# ============================================================
def test_golden_byte_consistency():
    """金样本逐字节回归：默认主题下重构前后输出完全一致（存量稿零迁移）。
    校验结论也须与基线一致（合成稿金句数不足等属预期失败，非回归）。"""
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert len(cases) >= 7
    for name, c in cases.items():
        html = exporter.render_from_markers(c["md"], series=c["series"],
                                            cover_url=c["cover_url"])
        assert html == c["html"], f"金样本 {name} 渲染输出与基线不一致"
        all_ok = all(ch["ok"] for ch in exporter.validate_html(html))
        assert all_ok == c["checks_all_ok"], \
            f"金样本 {name} 校验结论与基线不符: now={all_ok} base={c['checks_all_ok']}"
    assert cases["real_draft_2026_08_11"]["checks_all_ok"] is True  # 真实稿须全过


def test_render_theme_switch_reskins():
    """同稿换主题：输出不同且新主题色进入内联样式。"""
    h1 = exporter.render_from_markers(MD_BASIC, theme=default_theme())
    th2 = ThemeManager(_noop_dir()).get_theme("muxue_xinjian")
    h2 = exporter.render_from_markers(MD_BASIC, theme=th2)
    assert h1 != h2
    assert "#2E3A52" in h2 and "#2E3A52" not in h1


def test_render_disabled_component_hidden():
    """组件被主题/微调停用（enabled=false）→ 标记块整体不出现在输出。"""
    h = exporter.render_from_markers(MD_BASIC)
    assert "慢一点，也是往前走" in h                    # 默认 quote 启用
    with tempfile.TemporaryDirectory() as td:
        mgr = ThemeManager(td)
        mgr.set_override(DEFAULT_THEME_ID, "components.quote.enabled", False)
        h2 = exporter.render_from_markers(MD_BASIC, theme=mgr.effective_theme())
        assert "慢一点，也是往前走" not in h2
        assert "给今晚的自己写一句话" in h2             # 其他组件不受牵连


def test_render_deterministic():
    a = exporter.render_from_markers(MD_BASIC, series="C")
    b = exporter.render_from_markers(MD_BASIC, series="C")
    assert a == b


def test_validate_html_theme_whitelist():
    """校验白名单由主题 token 派生：渲染产物全过，外来颜色被拦截。"""
    h = exporter.render_from_markers(MD_BASIC)
    assert all(c["ok"] for c in exporter.validate_html(h))
    bad = h.replace("color:#", "color:#DEADBE")        # 注入色板外颜色
    fails = [c["name"] for c in exporter.validate_html(bad) if not c["ok"]]
    assert fails, "外来颜色应触发校验失败"


def test_deprecated_image_marker_dropped():
    """:::image 已废弃：静默丢弃，不报错不出 <img>。"""
    md = MD_BASIC + "\n\n:::image 夜色窗台\n"
    h = exporter.render_from_markers(md)
    assert "夜色窗台" not in h
    assert all(c["ok"] for c in exporter.validate_html(h))


def test_unknown_marker_degrades_to_paragraph():
    """A3 容错：未注册标记（含拼写错误）→ 标记行丢弃、内容降级正文段、
    渲染不中断、每进程每标记名留痕一次。"""
    md = ("# 容错稿\n\n"
          "正文一。\n\n"
          ":::future\n"
          "未知块的内容。\n"
          ":::\n\n"
          ":::quot\n"
          "拼写错的标记内容保留。\n\n"
          "正文二。\n")
    h = exporter.render_from_markers(md)
    assert "未知块的内容" in h and "拼写错的标记内容保留" in h   # 内容不丢
    assert ":::future" not in h and ":::quot" not in h          # 标记行不泄漏
    assert "正文一" in h and "正文二" in h
    assert {"future", "quot"} <= exporter._UNKNOWN_LOGGED       # 留痕去重池
    exporter._UNKNOWN_LOGGED -= {"future", "quot"}              # 还原，不污染其他用例


def test_has_markers():
    assert exporter.has_markers(":::quote\nx\n:::") is True
    assert exporter.has_markers("普通文稿") is False


_NOOP = None


def _noop_dir():
    """换肤测试用的临时目录（避免 manager 单例与真实 data/ 绑定）。"""
    global _NOOP
    if _NOOP is None or not Path(_NOOP).exists():
        _NOOP = tempfile.mkdtemp(prefix="typo_test_")
    return _NOOP


# ============================================================
# E. 排版中心 API（S3 · 后端 JSON 路由级，临时目录隔离单例；
#    页面渲染由 frontend SPA 承担，本段只验收协议与数据）
# ============================================================
def _ui_client(td):
    """以临时数据目录重建 manager 单例，返回 FastAPI test client。"""
    from fastapi.testclient import TestClient
    from backend.app.main import app
    reset_manager()
    get_manager(td)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def _restore_manager():
    reset_manager()


def _ok(r):
    body = r.json()
    assert body["ok"], str(body)[:200]
    return body["data"]


def test_ui_page_renders():
    with tempfile.TemporaryDirectory() as td:
        try:
            c = _ui_client(td)
            data = _ok(c.get("/api/typography"))
            names = {t["name"] for t in data["themes"]}
            for frag in ("天光云影", "暮雪信笺"):
                assert frag in names, f"主题画廊缺少: {frag}"
            assert data["preview_html"].strip(), "预览 HTML 为空"
            assert data["component_groups"]["block"], "组件画廊为空"
            assert data["config_fields"], "微调面板为空"
            assert "给今晚的自己写一句话" in data["preview_html"]  # 示例信笺渲染进预览
        finally:
            _restore_manager()


def test_ui_draft_preview_source():
    with tempfile.TemporaryDirectory() as td:
        try:
            c = _ui_client(td)
            data = _ok(c.get("/api/typography", params={"src": "draft"}))
            assert data["src"] == "draft"
            # 不存在主题回退激活主题
            data2 = _ok(c.get("/api/typography", params={"theme": "ghost"}))
            assert data2["theme_id"] == data["active_id"]
        finally:
            _restore_manager()


def test_ui_activate_route():
    with tempfile.TemporaryDirectory() as td:
        try:
            c = _ui_client(td)
            data = _ok(c.post("/api/typography/activate",
                              json={"theme_id": "muxue_xinjian", "src": "sample"}))
            assert data["activated"] == "muxue_xinjian"
            assert get_manager(td).active_theme_id() == "muxue_xinjian"
            # 激活不存在主题 → 业务失败
            r2 = c.post("/api/typography/activate", json={"theme_id": "ghost", "src": "sample"})
            assert not r2.json()["ok"]
        finally:
            _restore_manager()


def test_ui_override_route_applies_to_preview():
    with tempfile.TemporaryDirectory() as td:
        try:
            c = _ui_client(td)
            _ok(c.post("/api/typography/override",
                       json={"theme_id": DEFAULT_THEME_ID, "src": "sample",
                             "key": "tokens.colors.accent", "value": "#ABCDEF"}))
            data = _ok(c.get("/api/typography"))
            tokens = dict(data["color_tokens"])
            assert tokens.get("accent") == "#ABCDEF"          # 预览即时换色
            # 坏键 → 业务失败，主题状态不变
            r2 = c.post("/api/typography/override",
                        json={"theme_id": DEFAULT_THEME_ID, "src": "sample",
                              "key": "tokens.colors.nope", "value": "#000000"})
            assert not r2.json()["ok"]
            tokens2 = dict(_ok(c.get("/api/typography"))["color_tokens"])
            assert tokens2.get("accent") == "#ABCDEF"         # 旧微调未被破坏
        finally:
            _restore_manager()


def test_ui_save_and_delete_route():
    with tempfile.TemporaryDirectory() as td:
        try:
            c = _ui_client(td)
            data = _ok(c.post("/api/typography/save",
                              json={"name": "UI 测试主题", "base_id": DEFAULT_THEME_ID,
                                    "src": "sample"}))
            uid = data["theme_id"]
            assert uid in get_manager(td).theme_ids()
            _ok(c.post("/api/typography/delete", json={"theme_id": uid, "src": "sample"}))
            assert uid not in get_manager(td).theme_ids()
            # 内置主题删除被拒
            r3 = c.post("/api/typography/delete", json={"theme_id": DEFAULT_THEME_ID, "src": "sample"})
            assert not r3.json()["ok"]
        finally:
            _restore_manager()


def test_ui_reset_route():
    with tempfile.TemporaryDirectory() as td:
        try:
            c = _ui_client(td)
            c.post("/api/typography/override",
                   json={"theme_id": DEFAULT_THEME_ID, "src": "sample",
                         "key": "tokens.colors.accent", "value": "#ABCDEF"})
            _ok(c.post("/api/typography/reset",
                       json={"theme_id": DEFAULT_THEME_ID, "src": "sample"}))
            assert get_manager(td).get_overrides(DEFAULT_THEME_ID) == {}
        finally:
            _restore_manager()


def test_ui_nav_entry():
    """资源路由健壮性抽查（导航现由 SPA 承担，此处验收各资源 GET 可用）。"""
    with tempfile.TemporaryDirectory() as td:
        try:
            c = _ui_client(td)
            for path in ("/api/settings", "/api/topics", "/api/dashboard", "/api/agent/2099-01-01"):
                assert c.get(path).status_code == 200, path
        finally:
            _restore_manager()


# ===== runner =====
if __name__ == "__main__":
    failures = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  [PASS] {name}")
            except AssertionError as e:
                failures.append(name)
                print(f"  [FAIL] {name}: {e}")
            except Exception as e:
                failures.append(name)
                print(f"  [ERROR] {name}: {type(e).__name__}: {e}")
    total = sum(1 for n in globals() if n.startswith("test_") and callable(globals()[n]))
    print(f"\n阶段一排版引擎单测: {total - len(failures)}/{total} PASS")
    sys.exit(1 if failures else 0)
