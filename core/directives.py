#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/directives.py · M3 选题风向标：用户在生成之前掌舵的通道。

存储 data/selections/topic_directives.jsonl，字段：
  id / text / scope / created_at / expires_at / status / source / hits_last_batch
scope 到期规则：today 当日 23:59 失效 · week 7 天 · long 永久（均可手动删）。

三类能力：
  正向风向   "开学季写新开始" → 注入 <user_directives>，要求 ≥40% 候选围绕展开
  负面指令   "别再写失眠和毕业" → 解析出禁止关键词，与红线清单同级注入；
             生成后候选含禁止词的直接剔除（确定性后置过滤兜底）
  命中反馈   批次生成后记录 directive_hit 命中率；连续 2 批 <20% 提示用户写具体些

红线铁律：保存时即做红线检查，风向与反向清单冲突 → 拒收并说明原因
（plan 2026-09-03 选题智能优化方案 §5.2）。
"""
import json
import re
from datetime import date, datetime, timedelta

from core.paths import TOPIC_DIRECTIVES_PATH, TOPIC_DIRECTIVE_HITS_PATH
from core.redlines import redline_check

SCOPES = ("today", "week", "long")

# 负面指令触发词（句首，长词在前避免截断误切）：别再写 / 别写 / 不想看到 / 少写 …
_NEG_TRIGGER = re.compile(
    r"^(别再写|别再提|不要写|不要提|不想再看到|不想再见到|不想再听|"
    r"不想看到|不想见到|不想听到|拒绝再写|别写|别提|不想看|不想见|"
    r"不想听|少写|少提|拒绝写|不写)(关于|点|些|一下)?")
# 负面指令剩余部分的连接词切分
_TOKEN_SPLIT = re.compile(r"[、，,。；;和及或者与\s]+")
# 词尾冗余后缀（剥掉后才是真正的话题词）
_STRIP_SUFFIX = ("的话题", "的内容", "的东西", "的题", "选题", "话题", "内容", "主题", "相关", "题")
# 无信息量的助词 token（丢弃）
_STOP_TOKENS = {"写", "再写", "的", "了", "点", "些", "什么", "一些", "有关"}


def _now():
    return datetime.now()


def _read_all():
    if not TOPIC_DIRECTIVES_PATH.exists():
        return []
    out = []
    for line in TOPIC_DIRECTIVES_PATH.read_text(encoding="utf-8").strip().split("\n"):
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _write_all(directives):
    TOPIC_DIRECTIVES_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(d, ensure_ascii=False) for d in directives)
    TOPIC_DIRECTIVES_PATH.write_text(text + ("\n" if directives else ""), encoding="utf-8")


def _expires_at(scope, now=None):
    now = now or _now()
    if scope == "today":
        return now.replace(hour=23, minute=59, second=59).isoformat(timespec="seconds")
    if scope == "week":
        return (now + timedelta(days=7)).isoformat(timespec="seconds")
    return None  # long


def is_negative(text):
    """是否负面指令（触发词开头）。"""
    return bool(_NEG_TRIGGER.match((text or "").strip()))


def parse_negative(text):
    """负面指令 → 禁止关键词列表。'别再写失眠和毕业' → ['失眠', '毕业']。非负面指令返回 []。"""
    if not is_negative(text):
        return []
    rest = _NEG_TRIGGER.sub("", (text or "").strip())
    words = []
    for token in _TOKEN_SPLIT.split(rest):
        token = token.strip("的了！？?。.,，")
        for suf in _STRIP_SUFFIX:
            if token.endswith(suf) and len(token) > len(suf):
                token = token[: -len(suf)]
        if len(token) < 2 or len(token) > 10 or token in _STOP_TOKENS:
            continue
        words.append(token)
    return list(dict.fromkeys(words))


def add_directive(text, scope="week", source="user", today=None):
    """新增风向。返回 {ok, ...}：ok=False 时带 reason（红线冲突/参数非法）。

    today 参数仅供测试注入"今天"；真实调用走系统时钟。
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reason": "风向内容不能为空"}
    if scope not in SCOPES:
        return {"ok": False, "reason": f"scope 必须是 {'/'.join(SCOPES)}"}
    now = today or _now()
    if isinstance(now, str):
        now = datetime.fromisoformat(now)

    # 红线铁律：风向自身触红线（critical/high）→ 拒收
    chk = redline_check(text, step="directive_save", auto_log=False)
    hard = [h for h in chk.get("hits", []) if h["level"] in ("critical", "high")]
    if hard:
        return {"ok": False, "reason": f"风向触发红线被拒收：{hard[0]['message']}（命中「{hard[0]['matched_text']}」）"}

    directives = _read_all()
    for d in directives:
        if d.get("status") == "active" and d.get("text") == text:
            return {"ok": True, "directive": d, "duplicated": True}

    n = max((int(d["id"][1:]) for d in directives if re.fullmatch(r"d\d+", str(d.get("id", "")))), default=0)
    entry = {"id": f"d{n + 1}", "text": text, "scope": scope,
             "created_at": now.isoformat(timespec="seconds"),
             "expires_at": _expires_at(scope, now),
             "status": "active", "source": source or "user",
             "negative": is_negative(text),
             "hits_last_batch": None}
    directives.append(entry)
    _write_all(directives)
    return {"ok": True, "directive": entry, "duplicated": False}


def _is_active(d, now):
    if d.get("status") != "active":
        return False
    exp = d.get("expires_at")
    if not exp:
        return True
    try:
        return datetime.fromisoformat(exp) >= now
    except ValueError:
        return True


def list_directives(today=None):
    """全部生效中的风向（未过期且 status=active）。"""
    now = today or _now()
    if isinstance(now, str):
        now = datetime.fromisoformat(now)
    return [d for d in _read_all() if _is_active(d, now)]


def remove_directive(directive_id):
    """移除风向（标记 removed）。返回是否存在并移除。"""
    directives = _read_all()
    found = False
    for d in directives:
        if d.get("id") == directive_id and d.get("status") == "active":
            d["status"] = "removed"
            found = True
    if found:
        _write_all(directives)
    return found


def banned_keywords(today=None):
    """全部生效负面指令解析出的禁止关键词（去重）。"""
    words = []
    for d in list_directives(today):
        if d.get("negative"):
            words.extend(parse_negative(d["text"]))
    return list(dict.fromkeys(words))


def render_directives_block(today=None):
    """渲染 <user_directives> prompt 块。无生效风向时返回空串（静默降级）。"""
    directives = list_directives(today)
    if not directives:
        return ""
    scope_label = {"today": "今日风向", "week": "本周风向", "long": "长期风向"}
    lines = []
    for d in directives:
        if not d.get("negative"):
            lines.append(f"- 【{scope_label.get(d.get('scope'), '风向')}】{d['text']}")
    banned = banned_keywords(today)
    if banned:
        lines.append("- 【禁止清单（用户明确要求不再出现）】" + "、".join(banned))
    if not lines:
        return ""
    return ("<user_directives priority=\"highest\">\n用户显式设定的选题方向，"
            "优先级高于一切默认策略：\n" + "\n".join(lines) + "\n</user_directives>")


# ===== 命中反馈 =====

def record_batch_hits(hits, total, today=None):
    """记录一批候选的风向命中数；同时给生效中的正向风向打 hits_last_batch。

    返回 {"rate": 0.53}（无正向风向时不落盘，返回 {"rate": None}）。
    """
    directives = _read_all()
    actives = [d for d in directives if _is_active(d, today or _now()) and not d.get("negative")]
    if not actives or total <= 0:
        return {"rate": None}
    for d in actives:
        d["hits_last_batch"] = {"hits": int(hits), "total": int(total)}
    _write_all(directives)
    rate = round(int(hits) / int(total), 4)
    TOPIC_DIRECTIVE_HITS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TOPIC_DIRECTIVE_HITS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _now().isoformat(timespec="seconds"),
                            "hits": int(hits), "total": int(total), "rate": rate},
                           ensure_ascii=False) + "\n")
    return {"rate": rate}


def recent_hit_rates(n=2):
    """最近 n 批命中率（时间正序）。"""
    if not TOPIC_DIRECTIVE_HITS_PATH.exists():
        return []
    out = []
    for line in TOPIC_DIRECTIVE_HITS_PATH.read_text(encoding="utf-8").strip().split("\n"):
        if not line:
            continue
        try:
            out.append(json.loads(line).get("rate"))
        except ValueError:
            continue
    return [r for r in out[-n:] if r is not None]


def low_hit_streak(threshold=0.2, n=2):
    """连续低命中批次数（≥2 时 UI 建议用户把风向写得更具体）。"""
    rates = recent_hit_rates(n * 3)
    streak = 0
    for r in reversed(rates):
        if r < threshold:
            streak += 1
        else:
            break
    return streak


if __name__ == "__main__":
    print(render_directives_block())
