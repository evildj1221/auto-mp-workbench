#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llm/prompts/cover.py · 头图生成 prompt（视觉设计系统 v3 固化）。

依据：创作工作台视觉设计系统v3-完整版.md（2026-09-29，项目根目录）。
两段式：
  1. COVER_SCENE_PROMPT（LLM 场景提炼）：按第 1.2 系列口诀定调性 →
     按第 9 节 19 个模板选最近模板 → 从正文真实细节替换场景槽位，
     并按第 7.4 节做景别轮换（特写/在场/远景）。
  2. build_cover_prompt（生图模板）：场景 + 小猫 IP + 氛围 +
     5 色自然语言（第 4 节）+ 六要素（第 3 节）+ 避坑（第 8.3/16 节）。

避坑规则（第 8.3 / 16 节，终身受用）：
  - 不用 "Color palette" 关键词 / hex 码 / 百分比——颜色一律自然语言文字名
  - 不加 "accent" 类暗示词，防 AI 画装饰边框/色卡
  - AI 不生成中文文字（无 text/letters/watermark 约束），标题由排版层承担
"""

# 系列口诀（v3 第 1.2 节）：账号 motif_series 字母 → 调性
# A 独居治愈 / E 身体小秘密 → 冷蓝 night；B 晚安陌生人 / F 亲密关系 → 暖橙 warm；
# C 家庭关系 / D 中年危机按内容判（"我和我自己"→night，其余→warm）
SERIES_MOOD_HINT = {
    "A": "night", "E": "night",      # 我和我自己 / 我和我的身体 → 冷蓝
    "B": "warm", "F": "warm",        # 我和你·我们 / 我和他们·过去 → 暖橙
}

COVER_SCENE_PROMPT = """你是创作工作台的头图美术指导。为下面这篇公众号回信设计一张头图的场景描述（视觉设计系统 v3）。

第一步 · 定系列与调性（口诀）：
- 主题是「我和我自己 / 我和我的身体」（独处、失眠、胃痛、肩膀、觉察）→ 冷蓝夜调，mood=night
- 主题是「我和你 / 我们 / 他们 / 过去」（关系、整理、童年、回忆、陌生人、节令）→ 暖橙调，mood=warm
- 拿不准时按正文时间氛围定：深夜/凌晨→night，黄昏/清晨/雨天斜光→warm

第二步 · 选模板（从最接近的骨架改造，不要凭空发明结构）：
T1 深夜厨房暖光焦点 | T2 夏晚折窗 | T3 独居小窝 | T4 金句留白 | T5 纯夜景城市
T6 公园长椅 | T7 海边黄昏 | T8 山间小道 | T9 老街巷子 | T10 雨后露台
T11 深夜客厅沙发+胃部不适 | T12 深夜床+亮屏手机 | T13 深夜阳台+空气凤梨
T14 黄昏教室+旧书包邮票 | T15 初秋阳台+晾衣架外套 | T16 雨后教室+语文课本
T17 夜晚床头柜+温水杯+走廊灯 | T18 深夜办公室+肩膀紧绷 | T19 节令夜景+缺角月
（节令变体：中秋月饼盒/元宵红灯笼/春节雪月/清明细雨/端午艾草/冬至雪痕）

第三步 · 写场景（硬约束）：
- 场景元素从正文真实细节取（那碗面/那盆植物/那盏灯/那张邮票），不凭空发明
- 场景必须具象化标题核心意象；主体占画面 1/3-1/2，极简留白
- 人物只允许背影/剪影，或完全无人（不露脸）
- 景别三选一并注明：closeup 物件特写 / in_scene 在场感 / wide 远景仰望——同系列连续出图时轮换景别
- 小猫是固定 IP：室内在窗台/桌角/床头柜/椅子扶手（陪伴姿态），户外在长椅/石阶/砖墙/码头（望景姿态）——不要写进 scene，单独放 cat_pose
- 文稿提到写字/日记/课本时，页面上的字迹一律描述为"soft abstract ink strokes, no legible words"（AI 写实文字必变乱码）
- 光源必须明确（台灯/月光/夕阳/便利店暖光），画面里要有窗或天空的远景

输入：
选题：{topic}
标题：{title}
系列：{series}
正文开头：
{excerpt}

输出 JSON：
{{"template": "T11 这样的模板编号",
  "shot": "closeup 或 in_scene 或 wide",
  "scene": "英文场景描述 60-100 词，按所选模板骨架写：地点、时间、光源、2-3 件核心道具、窗或天空远景，具象可画，不要出现猫",
  "cat_pose": "英文一句话：小猫的位置与姿态（室内陪伴/户外望景，如 sits on the windowsill watching the night city）",
  "mood": "night 或 warm"}}"""

# 生图模板：{scene} {cat_pose} {atmosphere} {colors} 替换；
# 六要素 + 5 色自然语言 + 避坑全部内嵌（v3 第 8.2/9 节模板实测句式）。
# 风格签名（2026-09-29 从 10 张实战验证图 #019-#028 视觉学习补充）：
#   - 手绘微颤墨线 + 水彩纸纹理（不是平滑数码线）
#   - 小猫戴红色项圈 + 木牌「创作工作台」名牌（多数实战图的 IP 记忆点）
#   - 夜景是低层居民区/街灯/便利店暖光，不是高楼大厦天际线
#   - 可选：米白画纸底 + 细手绘圆角边框（装裱感，部分实战图有）
COVER_IMAGE_TEMPLATE = (
    "A hand-drawn healing-style Japanese watercolor illustration in wide horizontal "
    "banner format (2.35:1), painted on textured watercolor paper with slightly "
    "wobbly hand-drawn ink outlines. "
    "{scene}. A small fluffy white cat wearing a small red collar with a tiny "
    "wooden name tag {cat_pose}, a quiet gentle companion. "
    "{atmosphere} "
    "Painted in soft healing tones of {colors}. "
    "Style: 治愈系插画 inspired, thin hand-drawn linework, soft watercolor color "
    "blocks, cozy Japanese residential night scenery with warm street lamps and "
    "glowing windows, no human face, back view only or no people, healing cozy "
    "atmosphere. "
    "Minimalist composition, subject occupies one third to half of the frame "
    "in the lower part, sky and moon above, ample negative space. "
    "Any written marks appear only as soft abstract blurred ink strokes, "
    "no legible letters or words. No watermark, no color swatches, "
    "clean full-bleed edges.")

MOOD_COLORS = {
    "night": "deep navy, warm amber, and soft cream",                       # v3 冷蓝：深蓝60+暖光30+月白10
    "warm": "deep navy, warm amber, soft cream, and light gray",            # v3 暖橙：深蓝30+暖光40+月白20+雾灰10
}

MOOD_ATMOSPHERE = {
    "night": "Atmosphere: intimate, lonely yet healing, a private late-night moment of self-care.",
    "warm": "Atmosphere: nostalgic, warm and gentle, like a quiet memory softly lit.",
}


def build_cover_prompt(scene: str, cat_pose: str, mood: str = "night") -> str:
    """场景要素 → 完整生图 prompt（v3：氛围与配色随调性，颜色自然语言）。"""
    m = "night" if mood not in MOOD_COLORS else mood
    return COVER_IMAGE_TEMPLATE.format(
        scene=scene.strip().rstrip("."),
        cat_pose=cat_pose.strip().rstrip("."),
        atmosphere=MOOD_ATMOSPHERE[m],
        colors=MOOD_COLORS[m])
