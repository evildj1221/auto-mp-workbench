#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/make_icon.py · 生成创作工作台 exe 图标（assets/cat.ico）。

橘猫头：PIL 矢量绘制（无外部素材、无版权问题），可重复执行覆盖。
产物：assets/cat.png（预览/备用） + assets/cat.ico（多尺寸，PyInstaller 用）。
用法：python scripts/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

S = 512
ORANGE = (245, 176, 78, 255)      # 主毛色（暖橘）
ORANGE_DARK = (217, 143, 43, 255)  # 条纹/胡须
CREAM = (255, 243, 219, 255)       # 口鼻区
INK = (58, 46, 32, 255)            # 线稿墨色
PINK = (244, 154, 143, 255)        # 内耳/鼻/腮红

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

cx, cy, r = 256, 300, 190  # 头部圆心与半径


def ear(sx: int):
    """一只耳朵：外三角（主色）+ 内三角（粉），sx=-1 左 / 1 右。"""
    tip = (cx + sx * 196, 42)
    outer = (cx + sx * 108, 190)
    inner = (cx + sx * 232, 132)
    d.polygon([tip, outer, inner], fill=ORANGE)
    d.polygon([(cx + sx * 176, 78), (cx + sx * 134, 172), (cx + sx * 208, 128)], fill=PINK)


ear(-1)
ear(1)

# 头
d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ORANGE)

# 口鼻区（下中央奶油色椭圆）
d.ellipse([cx - 96, cy + 18, cx + 96, cy + 172], fill=CREAM)

# 额头条纹 ×3
for i, w in enumerate((64, 92, 64)):
    x0 = cx - w / 2
    y0 = cy - r + 30 + i * 30
    d.rounded_rectangle([x0, y0, x0 + w, y0 + 16], radius=8, fill=ORANGE_DARK)

# 眼睛：眯眼笑（∩ 形弧线）
for sx in (-1, 1):
    ex = cx + sx * 92
    d.arc([ex - 38, 288, ex + 38, 352], start=195, end=345, fill=INK, width=16)

# 鼻子（小三角）
d.polygon([(cx - 16, cy + 44), (cx + 16, cy + 44), (cx, cy + 68)], fill=PINK)

# 嘴：ω 两段弧
d.arc([cx - 32, cy + 48, cx - 2, cy + 92], start=15, end=165, fill=INK, width=9)
d.arc([cx + 2, cy + 48, cx + 32, cy + 92], start=15, end=165, fill=INK, width=9)

# 胡须 ×3/侧（先画，腮红盖在上面更柔和）
for sx in (-1, 1):
    for i, dy in enumerate((-18, 2, 22)):
        y1 = cy + 60 + dy
        y2 = cy + 46 + dy * 2
        d.line([(cx + sx * 82, y1), (cx + sx * 186, y2)], fill=ORANGE_DARK, width=7)

# 腮红（半透明盖住胡须根部）
for sx in (-1, 1):
    d.ellipse([cx + sx * 152 - 27, cy + 44, cx + sx * 152 + 27, cy + 88],
              fill=(250, 196, 186, 170))

out = Path(__file__).resolve().parent.parent / "assets"
out.mkdir(exist_ok=True)
img.save(out / "cat.png")
img.save(out / "cat.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
print(f"written: {out/'cat.ico'} (+cat.png)")
