#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/charts.py · 驾驶舱趋势可视化（v4.2 M4 · 服务端 SVG，零前端依赖）。

三张图（数据全部现成，本模块只做聚合与渲染）：
  ① 日成本与累计   data/llm_cost.jsonl        → 柱状 + 折线双轴
  ② 质量基线趋势   docs/eval/*.json            → 写稿通过率 + 红线召回双折线
  ③ 发布节奏热力   data/drafts/*_published.json → 近 13 周日历热力

约定：延续零前端框架——服务端生成内联 SVG（无 JS、打印友好），色板与
base.html §1 设计令牌同源（此处用字面量，保证 SVG 独立导出/截图不依赖页面样式）。
"""
import json
import re
from datetime import date as date_cls, timedelta
from pathlib import Path

from core.paths import DATA_DIR, DRAFTS_DIR, FEEDBACK_DIR, SCRIPT_DIR

# ===== 天光云影色板（与 base.html :root 同源） =====
C_MIST = "#2E79C6"      # 主线 · 晴空蓝
C_MIST_SOFT = "#E3F1FC"
C_GOLD = "#E08E3D"      # 副线 · 暮色暖阳
C_INK = "#2A3B55"       # 靛蓝墨阶
C_INK_600 = "#5D7290"
C_INK_400 = "#9FB2C8"
C_BORDER = "#D9E4F0"    # 边线（不透明版 rgba(147,177,214,.40)，打印友好）
C_CLOUD = "#E3EDF6"     # 空格子底

COST_LOG = DATA_DIR / "llm_cost.jsonl"
EVAL_DIR = SCRIPT_DIR / "docs" / "eval"
FONT = "Inter,PingFang SC,Microsoft YaHei,Noto Sans SC,sans-serif"


# ===== 数据聚合 =====
def _last_n_days(n, today=None):
    today = today or date_cls.today()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def daily_costs(days=30):
    """近 N 日成本序列（缺日补 0）：[{date, cost, calls, cumulative}]。"""
    agg = {}
    if COST_LOG.exists():
        for line in COST_LOG.read_text(encoding="utf-8").strip().splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            d = (r.get("date") or "")[:10]
            if d:
                a = agg.setdefault(d, {"cost": 0.0, "calls": 0})
                a["cost"] += float(r.get("cost_total_cny") or 0)
                a["calls"] += 1
    out, run = [], 0.0
    for d in _last_n_days(days):
        a = agg.get(d) or {"cost": 0.0, "calls": 0}
        run += a["cost"]
        out.append({"date": d, "cost": round(a["cost"], 4),
                    "calls": a["calls"], "cumulative": round(run, 4)})
    return out


def eval_history():
    """docs/eval/*.json → 按时间排序的质量基线序列（run_eval 历史连接）。"""
    out = []
    if not EVAL_DIR.exists():
        return out
    for p in sorted(EVAL_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        suites = d.get("suites") or {}
        w, r, c = suites.get("writing") or {}, suites.get("redline") or {}, suites.get("cost") or {}
        out.append({
            "ts": (d.get("generated_at") or "")[:10],
            "name": p.stem,
            "writing_pass": w.get("pass_rate"),
            "redline_recall": r.get("recall"),
            "per_article_median": c.get("per_article_median"),
        })
    out.sort(key=lambda x: x["ts"])
    return out


def published_dates():
    """所有已登记发布的日期集合（与 app._published_dates 同口径，读侧零依赖）。"""
    out = set()
    for p in DRAFTS_DIR.glob("*_published.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("user_published"):
            m = re.match(r"(\d{4}-\d{2}-\d{2})", p.name)
            if m:
                out.add(m.group(1))
    return out


# ===== SVG 渲染 =====
def _svg(w, h, body, label):
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{label}" '
            f'xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block" '
            f'font-family="{FONT}"><title>{label}</title>{body}</svg>')


def _fmt_cny(v):
    return f"¥{v:.2f}" if v < 100 else f"¥{v:.0f}"


def cost_chart_svg(days=30):
    """① 日成本柱状（晴空蓝 · 左轴）+ 累计折线（暮色金 · 右轴）。"""
    data = daily_costs(days)
    w, h = 640, 216
    pl, pr, pt, pb = 44, 50, 20, 26
    cw, ch = w - pl - pr, h - pt - pb
    max_daily = max([x["cost"] for x in data] + [0.01])
    max_cum = max([x["cumulative"] for x in data] + [0.01])
    n = len(data)
    bw = cw / n
    body = []
    for i in range(4):  # 网格 + 双轴刻度
        y = pt + ch * i / 3
        body.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{pl+cw}" y2="{y:.1f}" '
                    f'stroke="{C_BORDER}" stroke-width="1"/>')
        body.append(f'<text x="{pl-6}" y="{y+3.5:.1f}" text-anchor="end" font-size="9" '
                    f'fill="{C_INK_400}">{_fmt_cny(max_daily*(1-i/3))}</text>')
        body.append(f'<text x="{pl+cw+6}" y="{y+3.5:.1f}" font-size="9" '
                    f'fill="{C_GOLD}">{_fmt_cny(max_cum*(1-i/3))}</text>')
    for i, x in enumerate(data):
        if x["cost"] <= 0:
            continue
        bh = ch * x["cost"] / max_daily
        body.append(f'<rect x="{pl+i*bw+bw*.18:.1f}" y="{pt+ch-bh:.1f}" '
                    f'width="{bw*.64:.1f}" height="{bh:.1f}" rx="1.5" '
                    f'fill="{C_MIST}" fill-opacity=".82"/>')
    pts = [f'{pl+i*bw+bw/2:.1f},{pt+ch*(1-x["cumulative"]/max_cum):.1f}'
           for i, x in enumerate(data)]
    if len(pts) > 1:
        body.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{C_GOLD}" '
                    f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    step = max(1, n // 6)
    for i in range(0, n, step):
        body.append(f'<text x="{pl+i*bw+bw/2:.1f}" y="{h-8}" text-anchor="middle" '
                    f'font-size="9" fill="{C_INK_400}">{data[i]["date"][5:]}</text>')
    lx, ly = pl + cw, pt + ch * (1 - data[-1]["cumulative"] / max_cum)
    body.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.2" fill="{C_GOLD}"/>')
    body.append(f'<text x="{lx-6:.1f}" y="{max(ly-7, 11):.1f}" text-anchor="end" '
                f'font-size="10" font-weight="600" fill="{C_GOLD}">'
                f'累计 {_fmt_cny(data[-1]["cumulative"])}</text>')
    body.append(f'<circle cx="{pl+8}" cy="{pt-9}" r="3.5" fill="{C_MIST}" fill-opacity=".82"/>'
                f'<text x="{pl+16}" y="{pt-6}" font-size="9.5" fill="{C_INK_600}">日成本</text>'
                f'<line x1="{pl+58}" y1="{pt-9}" x2="{pl+74}" y2="{pt-9}" stroke="{C_GOLD}" stroke-width="2"/>'
                f'<text x="{pl+78}" y="{pt-6}" font-size="9.5" fill="{C_INK_600}">累计</text>')
    return _svg(w, h, "".join(body), f"近{days}日 LLM 成本与累计")


def quality_chart_svg():
    """② 质量基线趋势：写稿通过率（晴空蓝）+ 红线召回（暮色金）双折线。"""
    hist = [x for x in eval_history()
            if x["writing_pass"] is not None or x["redline_recall"] is not None]
    w, h = 640, 216
    pl, pr, pt, pb = 40, 16, 20, 26
    cw, ch = w - pl - pr, h - pt - pb
    body = []
    for i in range(5):  # 0/25/50/75/100% 网格
        y = pt + ch * i / 4
        body.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{pl+cw}" y2="{y:.1f}" '
                    f'stroke="{C_BORDER}" stroke-width="1"/>')
        body.append(f'<text x="{pl-6}" y="{y+3.5:.1f}" text-anchor="end" font-size="9" '
                    f'fill="{C_INK_400}">{100-i*25}%</text>')
    if not hist:
        body.append(f'<text x="{pl+cw/2}" y="{pt+ch/2}" text-anchor="middle" font-size="11" '
                    f'fill="{C_INK_400}">暂无 run_eval 基线记录（docs/eval/）</text>')
        return _svg(w, h, "".join(body), "质量基线趋势")

    def y_of(v):
        return pt + ch * (1 - (v or 0))

    n = max(len(hist), 2)
    xs = [pl + cw * (i / (n - 1) if n > 1 else 0.5) for i in range(len(hist))]
    for key, color in (("writing_pass", C_MIST), ("redline_recall", C_GOLD)):
        seg = [(xs[i], y_of(hist[i][key])) for i in range(len(hist))
               if hist[i][key] is not None]
        if len(seg) > 1:
            body.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in seg)}" '
                        f'fill="none" stroke="{color}" stroke-width="2" '
                        f'stroke-linejoin="round" stroke-linecap="round"/>')
        for x, y in seg:
            body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"/>')
    for i, x in enumerate(xs):  # 底部日期（最多 6 个）
        if len(hist) <= 6 or i % max(1, len(hist) // 5) == 0:
            body.append(f'<text x="{x:.1f}" y="{h-8}" text-anchor="middle" font-size="9" '
                        f'fill="{C_INK_400}">{hist[i]["ts"][5:] or "?"}</text>')
    body.append(f'<circle cx="{pl+8}" cy="{pt-9}" r="3.5" fill="{C_MIST}"/>'
                f'<text x="{pl+16}" y="{pt-6}" font-size="9.5" fill="{C_INK_600}">写稿通过率</text>'
                f'<circle cx="{pl+84}" cy="{pt-9}" r="3.5" fill="{C_GOLD}"/>'
                f'<text x="{pl+92}" y="{pt-6}" font-size="9.5" fill="{C_INK_600}">红线召回</text>')
    return _svg(w, h, "".join(body), "质量基线趋势（run_eval 历史）")


def publish_heatmap_svg(weeks=13):
    """③ 发布节奏日历热力：列=周（近 N 周），行=周一..周日；发布日暖金。"""
    pub = published_dates()
    today = date_cls.today()
    monday = today - timedelta(days=today.weekday())          # 本周周一
    start = monday - timedelta(weeks=weeks - 1)               # 起始周一
    cell, gap, label_w, top_h = 13, 3, 26, 18
    w = label_w + weeks * (cell + gap) + 6
    h = top_h + 7 * (cell + gap) + 4
    body = []
    # 月份标签：每月首个出现的新月份标在首列位置
    seen_month, last_month = "", None
    for wi in range(weeks):
        d = start + timedelta(weeks=wi)
        if d.month != last_month:
            last_month = d.month
            x = label_w + wi * (cell + gap)
            label = f"{d.month}月" if not seen_month or f"{d.year}-{d.month}" != seen_month else ""
            if f"{d.year}-{d.month}" != seen_month:
                label, seen_month = f"{d.month}月", f"{d.year}-{d.month}"
            if label:
                body.append(f'<text x="{x}" y="11" font-size="9" fill="{C_INK_400}">{label}</text>')
    weekday_marks = {0: "一", 2: "三", 4: "五", 6: "日"}
    for wi in range(weeks):
        for di in range(7):
            d = start + timedelta(weeks=wi, days=di)
            if d > today:
                fill, op = "#FFFFFF", ".0"
            elif d.isoformat() in pub:
                fill, op = C_GOLD, ".9"
            else:
                fill, op = C_CLOUD, ".7"
            x = label_w + wi * (cell + gap)
            y = top_h + di * (cell + gap)
            body.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3" '
                        f'fill="{fill}" fill-opacity="{op}">'
                        f'<title>{d.isoformat()}{" · 已发布" if d.isoformat() in pub else ""}</title></rect>')
            if wi == 0 and di in weekday_marks:
                body.append(f'<text x="{label_w-8}" y="{y+cell-3:.1f}" text-anchor="end" '
                            f'font-size="9" fill="{C_INK_400}">{weekday_marks[di]}</text>')
    return _svg(w, h, "".join(body), f"发布节奏（近{weeks}周）")


def dashboard_charts(days=30):
    """驾驶舱三图 + 摘要数字（一次聚合，模板直接内联渲染）。"""
    costs = daily_costs(days)
    hist = eval_history()
    pub = published_dates()
    active = [x for x in costs if x["cost"] > 0]
    total = sum(x["cost"] for x in costs)
    return {
        "cost_svg": cost_chart_svg(days),
        "quality_svg": quality_chart_svg(),
        "publish_svg": publish_heatmap_svg(),
        "meta": {
            "days": days,
            "total_cny": round(total, 2),
            "active_days": len(active),
            "avg_cny": round(total / len(active), 4) if active else 0.0,
            "last_cny": round(costs[-1]["cost"], 4) if costs else 0.0,
            "eval_runs": len(hist),
            "published_total": len(pub),
            "published_this_month": len([d for d in pub if d[:7] == date_cls.today().strftime("%Y-%m")]),
        },
    }
