#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/calendar_ctx.py · M1 时节引擎：让选题踩住生活时间轴。

纯函数 build_time_context(date) → dict，读本地 core/calendar_data.json
（2026-2027 预计算，scripts/gen_calendar_data.py 生成），无第三方依赖、无网络。

四类时节信号（按 plan 2026-09-03 选题智能优化方案 §4.2）：
  节日/纪念日   公历固定 + 农历预计算；提前 14 天进入预热，大节 30 天预热窗口
  二十四节气    精确到日；锚点 ±7 天窗口即提示
  生活周期节点  开学季/秋招季/年底盘点等 10 类窗口，附第 N 周进度
  周期时刻      星期节律 + 月初月末心态

降级约定：日历数据缺失/年份越界时返回空字段，不阻塞选题主流程。
"""
import json
from datetime import date, datetime, timedelta
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent / "calendar_data.json"

WEEKDAY_NOTES = {
    0: "新的一周开始，周一焦虑与重启",
    1: "节奏渐入，日常展开",
    2: "周中，撑住的一半",
    3: "一周过半，疲惫累积",
    4: "前夜的期待与松弛",
    5: "周末自由，独处与放空",
    6: "周日夜晚，明天上班的淡淡焦虑",
}
WEEK_CN = "一二三四五六日"

# 季节场景断点（月, 日）→ 场景描述；命中日期之前最近一条断点生效
SEASON_BREAKPOINTS = [
    (1, 1, "深冬，寒夜漫长，一年中最冷的时节"),
    (2, 28, "冬春之交，寒意未退，白天渐长"),
    (3, 15, "初春，春寒料峭"),
    (5, 1, "暮春，万物生长，春困渐起"),
    (6, 15, "初夏，昼长夜短"),
    (7, 20, "盛夏，暑热难眠，蝉鸣悠长"),
    (8, 25, "夏秋之交，暑气渐退，夜晚开始转凉"),
    (10, 15, "深秋，秋意渐浓，天凉好眠"),
    (11, 20, "秋冬之交，昼短夜长，草木凋零"),
    (12, 10, "初冬，寒意渐重，围炉取暖的季节"),
]
# 大节预热窗口放宽到 30 天（中秋/国庆/春节等情绪浓度高的节点）
MAJOR_FESTIVALS = {"春节", "除夕", "中秋节", "国庆节", "元旦", "圣诞节", "跨年夜", "情人节"}

_cache = {}


def _load():
    if "data" not in _cache:
        try:
            _cache["data"] = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache["data"] = {}
    return _cache["data"]


def _parse(d):
    if isinstance(d, date):
        return d
    try:
        return datetime.fromisoformat(str(d)).date()
    except ValueError:
        return date.today()


def calendar_data_years():
    return (_load().get("meta") or {}).get("years") or []


def needs_year_data(today=None):
    """驾驶舱年度维护提醒：12 月且次年数据缺失 → True。"""
    d = _parse(today)
    years = calendar_data_years()
    if not years:
        return True
    return d.month == 12 and (d.year + 1) not in years


def _season_scene(d):
    cur = (d.month, d.day)
    scene = SEASON_BREAKPOINTS[-1][2]
    for m, day, s in SEASON_BREAKPOINTS:
        if cur >= (m, day):
            scene = s
    return scene


def _festival_entries(d):
    """节日条目：今日 / 14 天预热 / 大节 30 天预热，按距离排序取前 4。"""
    festivals = _load().get("festivals") or {}
    entries = []
    for iso, names in festivals.items():
        try:
            fd = date.fromisoformat(iso)
        except ValueError:
            continue
        days = (fd - d).days
        if days == 0:
            for n in names:
                entries.append((0, n, f"{n}（今日）"))
        elif 0 < days <= 14:
            for n in names:
                entries.append((days, n, f"{n}（{fd.month}/{fd.day}，还有 {days} 天）"))
        elif 0 < days <= 30 and any(n in MAJOR_FESTIVALS for n in names):
            for n in names:
                if n in MAJOR_FESTIVALS:
                    entries.append((days, n, f"{n}（{fd.month}/{fd.day}，预热窗口）"))
    entries.sort(key=lambda x: x[0])
    return [e[2] for e in entries[:4]]


def _solar_term_entries(d):
    """节气条目：±7 天窗口内的（将至/已过/今日）+ 下一个节气，按节气名去重取前 3。"""
    terms = _load().get("solar_terms") or {}
    items = sorted((date.fromisoformat(k), v) for k, v in terms.items())
    entries = []
    for td, name in items:
        days = (td - d).days
        if days == 0:
            entries.append((0, name, f"今日{name}"))
        elif 0 < days <= 7:
            entries.append((days, name, f"{name}将至（{td.month}/{td.day}，还有 {days} 天）"))
        elif -7 <= days < 0:
            entries.append((days, name, f"{name}已过 {-days} 天"))
        else:
            continue
    future = 0
    for td, name in items:  # 未来两个节气（窗口外也提示，给出更远的时间感）
        if td > d:
            entries.append(((td - d).days, name, f"{name} {td.month}/{td.day}"))
            future += 1
            if future >= 2:
                break
    entries.sort(key=lambda x: abs(x[0]))
    out, seen = [], set()
    for _, name, text in entries:
        if name in seen:
            continue
        seen.add(name)
        out.append(text)
        if len(out) >= 3:
            break
    return out


def _life_cycle_entries(d):
    """生活周期窗口：命中的窗口 + 第 N 周进度，取前 2。"""
    out = []
    for w in _load().get("life_cycles") or []:
        try:
            s, e = date.fromisoformat(w["start"]), date.fromisoformat(w["end"])
        except (KeyError, ValueError):
            continue
        if s <= d <= e:
            week = (d - s).days // 7 + 1
            span = (e - s).days
            out.append({"name": w["name"], "label": w.get("label", w["name"]),
                        "week": week, "start": w["start"], "end": w["end"],
                        "week_text": (f"第 {week} 周" if span > 10 else "")})
    out.sort(key=lambda w: w["start"])
    return out[:2]


def _month_position(d):
    if d.day <= 7:
        return "月初 · 月度重启心态"
    if d.day >= 24:
        return "月末 · 复盘与总结心态"
    return ""


def build_time_context(day=None):
    """组装当日时节上下文（dict）。数据缺失时字段为空列表，不抛异常。"""
    d = _parse(day)
    cycles = _life_cycle_entries(d)
    return {
        "date": d.isoformat(),
        "weekday": f"周{WEEK_CN[d.weekday()]}",
        "weekday_note": WEEKDAY_NOTES[d.weekday()],
        "solar_terms": _solar_term_entries(d),
        "festivals": _festival_entries(d),
        "life_cycles": cycles,
        "month_position": _month_position(d),
        "season_scene": _season_scene(d),
    }


def render_time_context(day=None):
    """渲染 <time_context> 块（注入选题 prompt；任何一层缺失则整行省略）。"""
    ctx = build_time_context(day)
    date_line = f"{ctx['date']} {ctx['weekday']}"
    if ctx["life_cycles"]:
        date_line += " · " + " · ".join(
            f"{c['name']}{c['week_text']}" for c in ctx["life_cycles"])
    lines = [f"- 日期：{date_line}"]
    if ctx["solar_terms"]:
        lines.append("- 节气：" + " · ".join(ctx["solar_terms"]))
    if ctx["festivals"]:
        lines.append("- 节日：" + " · ".join(ctx["festivals"]))
    lines.append(f"- 周时刻：{ctx['weekday']}"
                 + (f" · {ctx['month_position']}" if ctx["month_position"] else "")
                 + f" · {ctx['weekday_note']}")
    lines.append(f"- 季节场景：{ctx['season_scene']}")
    return "<time_context>\n" + "\n".join(lines) + "\n</time_context>"


if __name__ == "__main__":
    print(render_time_context())
