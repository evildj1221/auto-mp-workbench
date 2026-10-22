#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/redlines.py · 红线规则表 + 短语红线检查（纯规则匹配，无 LLM）。

自 cat_shop.py 543-699 / 729-806 行原样迁出，行为不变。
注意：模块级 REDLINES 是 redline_check 的唯一读取点；运行期热更新规则
必须写回本模块属性（见 cat_shop.py 门面的 load_redlines 包装），
直接给 cat_shop.REDLINES 赋值不会生效。
"""
import json
import re
import sys
from datetime import datetime

from core.paths import REDLINES_PATH, REDLINE_LOG

# ===== 红线规则（结构化；v2 拆分医疗/法律/财务，支持 XX 通配） =====
# phrase 含 "XX" 时按通配正则匹配（如 "买 XX 稳赚" 可命中 "买基金稳赚"）
# v4：支持外置 redlines.json 覆盖（工作台可视化编辑），结构校验失败回退本内置表
_REDLINES_BUILTIN = {
    "gender_stereotype": {
        "level": "critical",
        "phrases": ["男人都这样", "女人都这样", "男的都这样", "女的都这样",
                    "男人都一样", "女人都一样", "男的都一样", "女的都一样",
                    "男人没一个好东西", "女人没一个好东西", "男人没好东西", "女人没好东西",
                    "天下乌鸦一般黑", "天下男人", "天下女人", "娶老婆就是", "嫁老公就是",
                    "男人都是", "女人都是", "男人永远", "女人永远"],
        "message": "❌ 触红线：性别群体标签化",
    },
    "marriage_pua": {
        "level": "critical",
        "phrases": ["听话的人才幸福", "驭夫术", "驭妻术", "鉴渣", "鉴渣学", "鉴婊",
                    "让他离不开", "让她离不开", "驯服他", "驯服她", "控制他", "控制她", "操纵他", "操纵她",
                    "情场高手", "PUA 话术", "情感操控"],
        "message": "❌ 触红线：婚恋 PUA 话术",
    },
    "self_harm": {
        "level": "critical",
        "phrases": ["自杀方法", "自残方法", "怎么自杀", "怎么自残",
                    "活着没意思", "不如死了", "一了百了", "解脱方法",
                    "割腕方法", "跳楼方法", "安眠药自杀"],
        "exception_phrases": ["请咨询专业人士", "拨打心理热线", "请寻求专业帮助"],
        "message": "❌ 触红线：教唆自伤/极端",
    },
    "medical_advice": {
        "level": "critical",
        "phrases": ["吃褪黑素", "吃安眠药", "吃维生素", "吃中药",
                    "不看医生", "不用去医院", "自己治好", "偏方治", "自己吃药"],
        "message": "❌ 触红线：医疗具体方案",
    },
    "legal_advice": {
        "level": "critical",
        "phrases": ["离婚就这么办", "一定能拿到", "官司稳赢", "不用请律师",
                    "律师都不用请", "起诉必赢", "打官司包赢"],
        "message": "❌ 触红线：法律具体方案",
    },
    "financial_advice": {
        "level": "critical",
        "phrases": ["买 XX 稳赚", "理财就买", "稳赚不赔", "包你赚", "闭眼入",
                    "跟着买就赚", "XX 必涨"],
        "message": "❌ 触红线：财务具体方案",
    },
    "clickbait": {
        "level": "critical",
        "phrases": ["震惊！", "99%", "99% 的人", "不转不是", "不转不是中国人",
                    "惊呆了", "太可怕了", "传疯了", "必看", "不看后悔", "速看！", "刚刚！", "突发！"],
        "message": "❌ 触红线：标题党",
    },
    "pseudoscience": {
        "level": "critical",
        "phrases": ["XX 星座注定", "命里注定", "命里带", "八字不合",
                    "塔罗牌说", "星座分析", "算命说", "看相说", "水逆", "土星逆行", "冥王星", "星盘说",
                    "前世今生", "轮回转世", "因果报应"],
        "message": "❌ 触红线：假科学/星座/算命",
    },
    "revenge": {
        "level": "critical",
        "phrases": ["跟踪他", "跟踪她", "骚扰他", "骚扰她", "同态复仇",
                    "找人打他", "找人打她", "让他社会性死亡", "让她社会性死亡", "曝光他", "曝光她", "搞臭他", "搞臭她"],
        "message": "❌ 触红线：教唆报复/跟踪/骚扰",
    },
    "minor": {
        "level": "critical",
        "phrases": ["未成年人打赏", "小孩打赏", "骗孩子", "骗未成年", "诱导孩子", "诱导未成年"],
        "message": "❌ 触红线：未成年保护",
    },
    "anxiety_talk": {
        "level": "high",
        "phrases": ["同龄人已经", "现在不做就晚了", "再不 XX 就晚了", "再不 XX 就来不及",
                    "你的同龄人", "同龄人在", "被同龄人抛弃", "被时代抛弃", "你看看别人", "你看人家"],
        "message": "🟧 调性禁区：焦虑话术",
    },
    "fake_positive": {
        "level": "high",
        "phrases": ["加油鸭", "奥利给", "今天也要加油", "打工人加油",
                    "努力就能成功", "你不成功是因为不够努力", "越努力越幸运"],
        "message": "🟧 调性禁区：假正能量/廉价鸡汤",
    },
    "group_label": {
        "level": "high",
        "phrases": ["成年人都", "年轻人都是", "老人都", "中年人都", "90 后都", "00 后都", "80 后都",
                    "北上广都", "小城市都", "农村都", "县城都", "打工人都是", "上班族都", "宝妈都", "程序员都"],
        "message": "🟧 调性禁区：群体标签（年龄/地域/职业）",
    },
    "should_dare": {
        "level": "medium",
        "phrases": ["你应该", "你必须", "你不能", "你不能不去", "你怎么能",
                    "你给我", "你要学会", "你必须学会", "你不得不",
                    "每个人都应该", "每个人都必须", "所有人都"],
        "message": "🟨 调性禁区：说教/命令语气（温柔调性 4 词之反）",
    },
    "cliche": {
        "level": "low",
        "phrases": ["综上所述", "总而言之", "在当今社会", "每个人都知道",
                    "我们生活在一个", "随着社会的发展", "在 XX 的今天", "值得一提的是", "首先", "其次", "再次",
                    "在这个快节奏的时代", "在繁忙的工作之余", "在这个充满挑战的时代"],
        "message": "🟦 套话：AI 味儿过重",
    },
    "pseudoscience_soft": {
        "level": "medium",
        "phrases": ["心理学证明", "研究表明", "科学证明", "左脑型人格", "右脑型人格", "血型决定", "星座分析"],
        "message": "🟨 调性禁区：软性假科学",
    },
}

def validate_redlines(data):
    """外置红线规则结构校验：每条需 level 合法、phrases 非空、message 为字符串。"""
    if not isinstance(data, dict) or not data:
        return False
    for rid, rule in data.items():
        if not isinstance(rid, str) or not isinstance(rule, dict):
            return False
        if rule.get("level") not in ("critical", "high", "medium", "low"):
            return False
        phrases = rule.get("phrases")
        if not isinstance(phrases, list) or not phrases or not all(isinstance(p, str) and p.strip() for p in phrases):
            return False
        if not isinstance(rule.get("message"), str) or not rule["message"].strip():
            return False
        exc = rule.get("exception_phrases")
        if exc is not None and not (isinstance(exc, list) and all(isinstance(p, str) for p in exc)):
            return False
    return True


def load_redlines():
    """红线规则：优先外置 redlines.json；缺失或非法时回退内置表。"""
    if REDLINES_PATH.exists():
        try:
            data = json.loads(REDLINES_PATH.read_text(encoding="utf-8"))
            if validate_redlines(data):
                return data
            print("[WARN] redlines.json 结构非法，回退内置红线规则", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] redlines.json 读取失败（{e}），回退内置红线规则", file=sys.stderr)
    return _REDLINES_BUILTIN


REDLINES = load_redlines()


def _phrase_to_regex(phrase):
    """含 XX 的短语 → 通配正则（如 '买 XX 稳赚' → 买\\s*\\S{1,8}\\s*稳赚）；不含 XX 返回 None。"""
    if "XX" not in phrase:
        return None
    left, right = phrase.split("XX", 1)
    rx = re.escape(left).replace(r"\ ", r"\s*")
    rx += r"\S{1,8}"
    rx += re.escape(right).replace(r"\ ", r"\s*")
    return rx

def redline_check(text, step="unknown", date="unknown", file="unknown", auto_log=True):
    if not text or not isinstance(text, str):
        return {"risk_level": "safe", "hits": [], "total_score": 0,
                "hit_counts": {"critical": 0, "high": 0, "medium": 0, "low": 0},
                "should_block": False, "step": step, "date": date, "file": file}

    level_score = {"critical": 10, "high": 7, "medium": 5, "low": 2}
    hits = []
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    total_score = 0

    for rule_id, rule in REDLINES.items():
        level = rule["level"]
        for phrase in rule.get("phrases", []):
            rx = _phrase_to_regex(phrase)
            if rx:
                mlist = [(m, phrase) for m in re.finditer(rx, text)]
            else:
                mlist = [(m, phrase) for m in re.finditer(re.escape(phrase), text)]
            for m, hit_text in mlist:
                # exception 处理（窗口内出现引导语则豁免）
                is_exc = False
                for exc in rule.get("exception_phrases", []):
                    s, e = max(0, m.start() - 50), min(len(text), m.end() + 50)
                    if exc in text[s:e]:
                        is_exc = True
                        break
                if is_exc:
                    continue
                hits.append({
                    "rule_id": rule_id, "level": level,
                    "matched_text": hit_text, "message": rule["message"],
                    "position": m.start(),
                    "context_snippet": text[max(0, m.start() - 20):min(len(text), m.end() + 20)],
                })
                counts[level] += 1
                total_score += level_score[level]

    # 阈值：low / 总量按文本长度比例缩放，避免长文被套话计数误杀
    n_chars = max(1, len(text))
    low_limit = max(3, n_chars // 300)
    medium_limit = 3
    total_limit = max(5, n_chars // 200)

    # P1-13：仅 critical/high 阻断；medium/low 调性类只提示不阻断
    should_block = (counts["critical"] >= 1 or counts["high"] >= 1)

    risk_level = "safe"
    if counts["critical"] > 0: risk_level = "critical"
    elif counts["high"] > 0: risk_level = "high"
    elif counts["medium"] >= 1 or counts["low"] >= 1: risk_level = "warning"
    if should_block: risk_level = "blocked"

    block_reason = ""
    if should_block:
        if counts["critical"] >= 1: block_reason = f"硬红线命中 {counts['critical']} 条（critical）"
        elif counts["high"] >= 1: block_reason = f"调性硬禁区命中 {counts['high']} 条（high）"
        elif counts["medium"] >= medium_limit: block_reason = f"调性软禁区命中 {counts['medium']} 条（medium ≥ {medium_limit}）"
        elif counts["low"] >= low_limit: block_reason = f"套话命中 {counts['low']} 条（low ≥ {low_limit}，按字数比例）"
        else: block_reason = f"总命中 {sum(counts.values())} 条（≥ {total_limit}，按字数比例）"

    result = {"risk_level": risk_level, "should_block": should_block, "block_reason": block_reason,
              "total_score": total_score, "hit_counts": counts, "hits": hits,
              "limits": {"medium": medium_limit, "low": low_limit, "total": total_limit},
              "step": step, "date": date, "file": file,
              "ts": datetime.now().isoformat(timespec="seconds")}

    if auto_log and hits:
        REDLINE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with REDLINE_LOG.open("a", encoding="utf-8") as f:
            for h in hits:
                f.write(json.dumps({
                    "ts": result["ts"], "step": step, "date": date, "file": file,
                    "level": h["level"], "rule_id": h["rule_id"], "message": h["message"],
                    "matched_text": h["matched_text"], "context_snippet": h["context_snippet"][:100],
                    "position": h["position"],
                }, ensure_ascii=False) + "\n")
    return result
