#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/quality.py · 字数统计 + 反重复（相似句检测 / 历史稿检索 / 桥段词禁用）。

自 cat_shop.py 298-479 / 889-910 行原样迁出，行为不变。
"""
import re

from core.paths import DRAFTS_DIR, WORKS_DIR, slot_paths
from core.storage import load_json

# 反重复：系列差异化角度库（写相似主题时强制换角度）
SERIES_ANGLES = {
    "B": [  # 失眠
        ("失眠与城市", ["城市", "路灯", "夜班", "地铁", "广场"]),
        ("失眠的身体地图", ["身体", "后脑勺", "太阳穴", "心跳", "耳朵"]),
        ("失眠者的深夜仪式", ["仪式", "写信", "整理", "发呆", "洗澡"]),
        ("和清晨的和解", ["清晨", "天亮", "日出", "鸟叫", "早餐"]),
        ("失眠里的记忆回放", ["记忆", "过去", "童年", "某个夏天", "高中"]),
        ("别人的失眠", ["别人", "楼下", "对门", "邻居", "家人"]),
    ],
    "A": [  # 独居
        ("独居的第一百天", ["一百天", "日历", "搬家", "刚搬来"]),
        ("一个人的餐桌仪式", ["餐桌", "做饭", "餐具", "一个人吃"]),
        ("独居时的意外连接", ["邻居", "快递", "外卖", "保安", "收件"]),
        ("独居与自由", ["自由", "想几点", "门锁", "安静"]),
        ("独居生病的夜晚", ["生病", "发烧", "药", "医院"]),
        ("独居者的声音", ["声音", "电视", "音乐", "安静", "说话"]),
    ],
    "G": [  # 成长 / 自我提升
        ("很小的一步", ["今天只做", "三分钟", "开始", "打卡"]),
        ("笨拙地开始", ["第一次", "学不会", "出错", "从头"]),
        ("和过去的自己和解", ["原谅", "以前的", "放下", "翻篇"]),
        ("习惯的力量", ["每天", "坚持", "重复", "微习惯"]),
        ("独处时偷偷变好", ["独处", "学习", "笔记", "安静进步"]),
        ("成长的小证据", ["进步", "对比", "上一年", "清单"]),
        ("慢一点也没关系", ["节奏", "不急", "调整", "重新开始"]),
    ],
}


# 反重复：常见高频桥段词表（在历史稿中出现过的将明确禁用）
_BRIDGE_TERMS = [
    "煮面", "面条", "香油", "热牛奶", "蜂蜜水", "数羊", "数绵羊", "白噪音",
    "翻来覆去", "热可可", "泡面", "胃痛", "闹钟", "凌晨三点", "窗台", "台灯",
    "奶茶", "外卖", "失眠药", "安眠药", "褪黑素", "热毛巾", "泡脚",
]


def _split_sentences(text):
    """切句（中英文句号/叹号/问号/换行），忽略过短片段。"""
    parts = re.split(r"(?<=[。！？!?])|\n+", text or "")
    return [p.strip() for p in parts if len(p.strip()) >= 10]


def _sent_sim(a, b):
    """句子相似度：字符 3-gram Jaccard（0~1）。"""
    def grams(s):
        s = re.sub(r"\s", "", s)
        return {s[i:i + 3] for i in range(max(0, len(s) - 2))}
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _find_similar_sentences(md, past_full, threshold=0.45):
    """当前稿 vs 历史稿全文：找雷同句（句意/表达高度相似）。
    双信号：① 字符 3-gram Jaccard ≥ 阈值；② 共同 6-gram 短语 ≥ 3（字面重复）。
    返回 [(当前句, 历史句, 相似度)]，每句取最高，最多 6 条。"""
    def clean(t):
        return "\n".join(
            ln for ln in (t or "").split("\n")
            if not ln.strip().startswith(":::")
            and not re.match(r"^\d+[.、]\s*.+\|[a-z]+\|", ln.strip()))
    pairs = []
    sents = _split_sentences(clean(md))
    for p in past_full:
        ps = _split_sentences(clean(p))
        for s in sents:
            best_q, best_sim = None, 0.0
            for q in ps:
                sim = _sent_sim(s, q)
                # 字面重复信号：共同 6-gram 短语数
                g6 = lambda t: {t[i:i + 6] for i in range(max(0, len(t) - 5))}
                overlap = len(g6(s) & g6(q))
                eff = max(sim, 0.25 + 0.12 * min(overlap, 4))  # 短语命中拉高有效分
                if eff > best_sim:
                    best_q, best_sim = q, eff
            if best_q and best_sim >= threshold:
                pairs.append((s, best_q, round(best_sim, 2)))
    best = {}
    for s, q, sim in pairs:
        if s not in best or sim > best[s][1]:
            best[s] = (q, sim)
    return [(s, q, sim) for s, (q, sim) in best.items()][:6]


def _load_past_full(date, series=None, max_total=4):
    """取历史稿全文（同系列优先 + 任意兜底），供句子相似检测。排除当天自身。"""
    import glob as _glob
    out = []
    files = sorted(_glob.glob(str(DRAFTS_DIR / "*_v1.md")), reverse=True)
    same, others = [], []
    for p in files:
        name = p.replace("\\", "/").split("/")[-1]
        d = name.split("_")[0]
        if d == date:
            continue
        try:
            md = open(p, encoding="utf-8").read()
        except Exception:
            continue
        if not md.strip():
            continue
        s_guess = None
        for d2 in _glob.glob(str(WORKS_DIR / "*.json")):
            try:
                ws = load_json(d2, default={})
                for s in ws.get("slots", []):
                    sp = slot_paths(d2.replace("\\", "/").split("/")[-1][:10], s.get("id"))
                    if sp["v1"].name == name:
                        s_guess = s.get("motif_series")
                        break
            except Exception:
                continue
            if s_guess:
                break
        rec = (md, s_guess)
        if series and s_guess == series:
            same.append(rec)
        else:
            others.append(rec)
    for md, sg in (same[:2] + others[:2])[:max_total]:
        out.append(md)
    return out


def _load_past_drafts(date, series=None, max_total=4):
    """反重复检索：找历史文稿（同系列优先 + 任意系列兜底），截取节选供写稿避开。
    排除当天（date）自身，避免与本次写稿冲突。返回 [{file, series, excerpt}]。
    excerpt 取开头 500 字 + 结尾 260 字（覆盖开场意象与收尾金句）。"""
    import glob as _glob
    out = []
    all_files = sorted(_glob.glob(str(DRAFTS_DIR / "*_v1.md")),
                       key=lambda p: p, reverse=True)
    same_series, others = [], []
    for p in all_files:
        name = p.replace("\\", "/").split("/")[-1]
        d = name.split("_")[0]
        if d == date:
            continue
        try:
            md = open(p, encoding="utf-8").read()
        except Exception:
            continue
        if not md.strip():
            continue
        # 尝试从 works 索引取系列，取不到用名字猜测（v1 同批次的 top5 系列）
        rec = {"file": name, "excerpt": (md[:500] + "\n……\n" + md[-260:]).strip()}
        s_guess = None
        for d2 in _glob.glob(str(WORKS_DIR / "*.json")):
            try:
                ws = load_json(d2, default={})
                for s in ws.get("slots", []):
                    sp = slot_paths(d2.replace("\\", "/").split("/")[-1][:10], s.get("id"))
                    if sp["v1"].name == name:
                        s_guess = s.get("motif_series")
                        break
            except Exception:
                continue
            if s_guess:
                break
        rec["series"] = s_guess or "?"
        if series and s_guess == series:
            same_series.append(rec)
        else:
            others.append(rec)
    # 同系列最多 2 篇 + 任意系列最多 2 篇（避免 prompt 过长）
    for rec in (same_series[:2] + others[:2])[:max_total]:
        out.append(rec)
    # 明确禁用词：扫描全部历史稿（全文）统计桥段词；used_angles 只统计"选中节选"的
    # 最近相关稿（短期重复更伤体验），避免 11 篇历史稿把所有角度都判成已用
    banned = set()
    used_angles = set()
    selected_texts = [d["excerpt"] for d in out]
    for p in all_files:
        name = p.replace("\\", "/").split("/")[-1]
        d = name.split("_")[0]
        if d == date:
            continue
        try:
            md = open(p, encoding="utf-8").read()
        except Exception:
            continue
        for t in _BRIDGE_TERMS:
            if t in md:
                banned.add(t)
    for ang, kws in SERIES_ANGLES.get(series, []):
        if any(k in txt for txt in selected_texts for k in kws):
            used_angles.add(ang)
    return out, banned, used_angles

# ===== 字数统计（v2：全流程统一按汉字计） =====
def count_chinese_chars(text):
    t = re.sub(r"[#*`>\[\]()!\\\-]", "", text or "")
    return len([c for c in t if "\u4e00" <= c <= "\u9fff"])


def strip_component_markers(md_text):
    """剥离写稿组件标记（:::quote/:::letter/:::action），供飞书版排版使用。
    :::image 行直接移除（v4.2 起文中图已取消，头图由生图管线承担）；
    其余围栏行直接移除，内容保留为普通段落。"""
    out = []
    for line in (md_text or "").split("\n"):
        s = line.strip()
        if re.match(r"^:::image\b", s):
            continue
        if re.match(r"^:::(quote|letter|action|titles)?\s*$", s):
            continue
        out.append(line)
    return "\n".join(out)


# ===== 标题反模板化（v4.8，依 docs/2026-09-14_选题标题优化方案 §6 落码） =====
# L1：句型级硬淘汰黑名单（命中即视为低质，应重写）
TITLE_BLOCK_RULES = [
    ("晚安致意", r"^(晚安[，,.]?)", "以「晚安…」开头"),
    ("晚安致意", r"(，晚安|，?说晚安)$", "以「…，晚安/说晚安」收尾"),
    ("设问-为什么", r"^为什么", "以「为什么」设问"),
    ("设问-时间跨度", r"你有多久|多久没|多久以前|上次.{0,3}多久", "时间跨度追问"),
    ("设问-封闭式", r"(吗|对不对|是不是|好不好)([？?]|$)", "封闭式设问"),
    ("写给句式", r"^写给.{0,20}(的你|自己)", "以「写给…的你/自己」起头"),
    ("年龄开头", r"^(第?\d{1,2}岁|(第)?[一二三四五六七八九十]{1,2}岁)[,，]?我", "「N岁，我…」开头"),
    ("时长开头", r"^第\d+[年天]，我", "「第N年/天，我…」开头"),
    ("深夜场景开头", r"^(深夜|凌晨|半夜|失眠)", "深夜/失眠开场"),
    ("独居/一个人开头", r"^(一个人|独居)", "一个人/独居开场"),
    ("家人亮身份开头", r"^(妈妈|爸爸|父亲|母亲)[^。\n]{0,12}?(发|说|住|退休|去世|更年|手机|突然|开始|发来|也是|的白发)", "家人身份+事件开场"),
]


def _title_block_hits(title):
    """返回标题命中的 L1 黑名单标签列表（空列表=通过）。"""
    t = title or ""
    return [name for name, pat, _desc in TITLE_BLOCK_RULES if re.search(pat, t)]


# L2：句式指纹（软去重）。比单一标签更细，用于识别同批内"结构雷同"。
_OPEN_KIND_RULES = [
    ("时间", re.compile(r"^(今天|此刻|昨晚|清晨|傍晚|午后|关灯|下班|周日的|那个|那晚)")),
    ("物件", re.compile(r"^(半杯|旧手机|行李箱|窗台|便利店|体检|咖啡|那盏|一张|一盆|一锅|冰箱|备忘录|工位|阳台上)")),
    ("动作", re.compile(r"^(辞职|删|买|泡|种|加|允许|停下来|打包|给自己|学会|开始|去看)")),
    ("人称", re.compile(r"^[我他她你它]")),
]

_TURN_WORDS = ("但", "却", "反而", "才", "原来", "不是", "只是", "那天", "之后", "终于", "后来", "反而")


def _title_fingerprint(title):
    """把标题编码成句式指纹串：开头类别|是否问号|是否带转折|主语人称。"""
    t = (title or "").strip()
    q = "Q" if t.endswith(("？", "?")) else "N"
    op = "O"
    for kind, rx in _OPEN_KIND_RULES:
        if rx.match(t):
            op = kind
            break
    turn = "T" if any(w in t for w in _TURN_WORDS) else "N"
    subj = ("我" if re.match(r"^我", t) else "他" if re.match(r"^[他她]", t)
            else "你" if re.match(r"^你", t) else "N")
    return "%s|%s|%s|%s" % (op, q, turn, subj)


def check_title_diversity(titles, max_same_ratio=0.3):
    """对一组候选标题做反模板化体检。

    返回 {"blocked": {title: [L1 tags]},
           "family": {fingerprint: {"count": n, "over": bool, "titles": [...]}}}
    blocked 的标题命中 L1 黑名单，应淘汰重写；family 按句式指纹统计——
    count > len(titles)*max_same_ratio 时 over=True，表示该结构同批偏多，应回调。
    """
    blocked = {}
    fam = {}
    for t in titles:
        if not t:
            continue
        hits = _title_block_hits(t)
        if hits:
            blocked[t] = hits
            continue
        fp = _title_fingerprint(t)
        fam.setdefault(fp, []).append(t)
    limit = max(1, int(len(titles) * max_same_ratio)) if titles else 1
    family = {fp: {"fingerprint": fp, "count": len(ts), "over": len(ts) > limit,
                   "titles": ts} for fp, ts in fam.items()}
    return {"blocked": blocked, "family": family}
