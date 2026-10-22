#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/gen_calendar_data.py · 一次性生成 core/calendar_data.json（M1 时节引擎数据源）。

年度维护：每年 12 月改 YEARS 加下一年，重跑本脚本（约 10 分钟人工成本）。
运行时零依赖：calendar_ctx.py 只读 JSON，不 import cnlunar。

数据三块：
  solar_terms  二十四节气（精确到日，来自 cnlunar）
  festivals    节日（公历固定/浮动 + 农历离线预计算）
  life_cycles  生活周期窗口（开工前锚定绝对日期，含农历相关的春节返乡）

交叉验证断言（与公开万年历核对过的锚点日期）：
  2026-02-17 春节 · 2026-09-07 白露 · 2026-09-23 秋分 · 2026-09-25 中秋 · 2027-02-06 春节
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import cnlunar

YEARS = [2026, 2027]

# 农历节日表：{农历月: {农历日: 名}}
LUNAR_FESTIVALS = {
    1: {1: "春节", 15: "元宵节"},
    5: {5: "端午节"},
    7: {7: "七夕"},
    8: {15: "中秋节"},
    9: {9: "重阳节"},
    12: {8: "腊八节"},
}

# 公历固定节日：{月: {日: 名}}
SOLAR_FESTIVALS = {
    1: {1: "元旦"},
    2: {14: "情人节"},
    3: {8: "妇女节"},
    9: {10: "教师节"},
    10: {1: "国庆节", 31: "万圣夜"},
    12: {25: "圣诞节", 31: "跨年夜"},
}

# 浮动公历节日：{名: (月, 周内第几个星期几)}，星期一=0
FLOATING_FESTIVALS = [
    ("母亲节", 5, 2, 6),   # 5 月第 2 个周日
    ("父亲节", 6, 3, 6),   # 6 月第 3 个周日
]

# 固定窗口（公历），lunar=False；农历窗口在生成时按当年春节锚定，lunar=True
# 春节返乡季的特殊约定：start 偏移相对春节（sm 固定 0，sd<0 为春节前 N 天），
# end 取当年元宵节实际公历日期（生成时从 festivals 反查）
LIFE_CYCLE_DEFS = [
    ("年初立flag", 1, 1, 1, 15, False, "年初 · 立 flag 与新开始心态"),
    ("春节返乡季", 0, -7, 0, 0, True, "春运返乡 · 团聚与故土"),
    ("春困期", 3, 15, 4, 15, False, "春困 · 身体跟不上春天"),
    ("毕业季", 6, 1, 7, 10, False, "毕业季 · 告别与各奔东西"),
    ("暑期", 7, 15, 8, 25, False, "暑期 · 悠长白天与蝉鸣"),
    ("开学季", 9, 1, 9, 15, False, "开学季 · 返校与适应新环境"),
    ("秋招季", 9, 15, 11, 15, False, "秋招季 · 简历、面试与未来"),
    ("双十一", 11, 1, 11, 12, False, "双十一 · 购物车与消费情绪"),
    ("年底盘点", 12, 10, 12, 31, False, "年底 · 复盘与年终情绪"),
    ("跨年", 12, 28, 1, 3, False, "跨年 · 旧年总结与新年期待"),
]

ANCHORS = {  # 交叉验证锚点：(日期, 类型, 名称)
    ("2026-02-17", "festival", "春节"),
    ("2026-09-07", "solar_term", "白露"),
    ("2026-09-23", "solar_term", "秋分"),
    ("2026-09-25", "festival", "中秋节"),
    ("2027-02-06", "festival", "春节"),
}


def solar_terms_of(year):
    d = cnlunar.Lunar(datetime(year, 1, 1), godType="8char")
    out = {}
    for name, (m, day) in d.thisYearSolarTermsDic.items():
        out[date(year, m, day).isoformat()] = name
    return out


def scan_days(start, end):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def floating_festival_dates(year):
    out = []
    for name, month, nth, weekday in FLOATING_FESTIVALS:
        d = date(year, month, 1)
        offset = (weekday - d.weekday()) % 7
        d = d + timedelta(days=offset + (nth - 1) * 7)
        out.append((d, name))
    return out


def main():
    start, end = date(YEARS[0], 1, 1), date(YEARS[-1], 12, 31)

    # 节气（两年）
    solar_terms = {}
    for y in YEARS:
        solar_terms.update(solar_terms_of(y))

    # 节日：逐日扫描农历表 + 公历表 + 浮动表
    festivals = {}
    for d in scan_days(start, end):
        names = []
        if d.month in SOLAR_FESTIVALS and d.day in SOLAR_FESTIVALS[d.month]:
            names.append(SOLAR_FESTIVALS[d.month][d.day])
        lunar = cnlunar.Lunar(datetime(d.year, d.month, d.day), godType="8char")
        table = LUNAR_FESTIVALS.get(lunar.lunarMonth) or {}
        if lunar.lunarDay in table:
            names.append(table[lunar.lunarDay])
        # 除夕：次日是正月初一
        nxt = cnlunar.Lunar(datetime(d.year, d.month, d.day) + timedelta(days=1), godType="8char")
        if nxt.lunarMonth == 1 and nxt.lunarDay == 1 and lunar.lunarMonth != 1:
            names.append("除夕")
        if names:
            festivals[d.isoformat()] = names
    for y in YEARS:
        for d, name in floating_festival_dates(y):
            festivals.setdefault(d.isoformat(), []).append(name)

    # 生活周期窗口：公历直接展开；农历窗口按当年春节锚定
    life_cycles = []
    spring_festivals = sorted(dt for dt, ns in festivals.items() if "春节" in ns)
    for name, sm, sd, em, ed, lunar, label in LIFE_CYCLE_DEFS:
        if not lunar:
            for y in YEARS:
                s, e = date(y, sm, sd), date(y, em, ed)
                if s <= e:
                    life_cycles.append({"name": name, "start": s.isoformat(),
                                        "end": e.isoformat(), "label": label})
                else:  # 跨年窗口（如跨年 12/28→1/3）
                    life_cycles.append({"name": name, "start": s.isoformat(),
                                        "end": date(y + 1, em, ed).isoformat(), "label": label})
        else:
            # 农历窗口：start 相对春节偏移（sd<0 为春节前 N 天），end 反查当年元宵节
            lanterns = sorted(dt for dt, ns in festivals.items() if "元宵节" in ns)
            for sf in spring_festivals:
                sf_date = date.fromisoformat(sf)
                s = sf_date + timedelta(days=sd)
                e = next((date.fromisoformat(dt) for dt in lanterns
                          if sf_date < date.fromisoformat(dt) <= sf_date + timedelta(days=60)),
                         sf_date + timedelta(days=22))
                life_cycles.append({"name": name, "start": s.isoformat(),
                                    "end": e.isoformat(), "label": label})

    # 交叉验证断言
    festival_flat = {dt: ns for dt, ns in festivals.items()}
    for iso, kind, expect in ANCHORS:
        if kind == "solar_term":
            got = solar_terms.get(iso)
        else:
            got = festival_flat.get(iso)
        assert (expect in got) if got else False, f"锚点校验失败: {iso} 应为 {expect}，实得 {got}"

    out = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "generator": "scripts/gen_calendar_data.py",
            "library": f"cnlunar {getattr(cnlunar, '__version__', '')}".strip(),
            "years": YEARS,
            "note": "每年 12 月把下一年加入 YEARS 重跑本脚本；运行时只读本文件，无第三方依赖",
        },
        "solar_terms": solar_terms,
        "festivals": festivals,
        "life_cycles": life_cycles,
    }
    path = Path(__file__).resolve().parent.parent / "core" / "calendar_data.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ 已写 {path}")
    print(f"   节气 {len(solar_terms)} 条 · 节日 {sum(len(v) for v in festivals.values())} 个"
          f" · 生活周期窗口 {len(life_cycles)} 个 · 锚点校验 {len(ANCHORS)}/{len(ANCHORS)} 通过")


if __name__ == "__main__":
    sys.exit(main())
