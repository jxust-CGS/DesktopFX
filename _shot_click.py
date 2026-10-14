# -*- coding: utf-8 -*-
"""渲染点击特效的预览图（README 用）。

图案与文字都是**代码画出来的**：改一行坐标就可能变丑，只看代码看不出来。
这个脚本把四件事渲成一张对照图：

  1. 8 种图案 × 三档尺寸，**各用自己的惯用色**（爱心红 / 星星黄 / 雪花白……）；
  2. 旋转档位，以及「彩色」模式（逐粒子取整套调色板）长什么样；
  3. **点击文字**的弹出档位、9 款字体、**随机倾斜**档位；
  4. 彩带 / 星光的装饰观感。

输出 `docs/images/click-preview.png`。改完 `_build_shape` / `_text_sprite` /
`_TEXT_TILT_STEPS` 的坐标或档位后重跑它。

提示：终端里想快速看单个图案，用 `_ascii_pet.py` 的 `ascii_art()` 更快。
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from PIL import Image, ImageDraw

import fx_effects as F

BG_LIGHT = (238, 240, 243, 255)
BG_DARK = (32, 35, 40, 255)
DIVIDER = (168, 172, 178, 255)
LABEL = (120, 124, 130, 255)
LABEL_D = (196, 202, 210, 255)


def _to_rgba(spr):
    """预乘 -> 直通 alpha（预览图不是上屏路径，不必保持预乘）。"""
    a = spr[..., 3:4]
    rgb = np.where(a > 1e-6, spr[..., :3] / np.maximum(a, 1e-6), 0.0)
    out = np.clip(np.concatenate([rgb, a], axis=2) * 255.0, 0, 255)
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def _paste(canvas, spr, cx, cy):
    img = _to_rgba(spr)
    canvas.alpha_composite(img, (int(cx - img.width / 2),
                                 int(cy - img.height / 2)))


def _color_of(name, seed=0):
    """图案的显示颜色：惯用色；彩带是「彩色」哨兵，按 seed 取调色板里的某个。"""
    c = F._SHAPE_DEFAULT_COLORS[name]
    if c == F.COLOR_RAINBOW:
        return F._CONFETTI_COLORS[seed % len(F._CONFETTI_COLORS)]
    return c


def main():
    W, H = 980, 700
    canvas = Image.new("RGBA", (W, H), BG_LIGHT)
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 372, W, H], fill=BG_DARK)      # 下半段换深色底

    def label(text, x, y, col=LABEL):
        d.text((x, y), text, fill=col)

    # ---------------- 第一块：8 种图案 × 三档尺寸，各用惯用色
    label("8 种图案 × 三档尺寸（各用自己的惯用色）", 40, 14)
    sizes = (56, 34, 20)
    x = 62
    for name in F._SHAPE_NAMES:
        y = 62
        for i, s in enumerate(sizes):
            _paste(canvas, F._shape_sprite(name, _color_of(name, i), s, 0, 12),
                   x, y)
            y += s * 0.72 + 12
        label(name, x - 16, 156)
        # 惯用色写出来，省得对着图猜
        sw = F._SHAPE_DEFAULT_COLORS[name]
        label("彩色" if sw == F.COLOR_RAINBOW else sw.upper(), x - 16, 172)
        x += 112

    # ---------------- 第二块：旋转档位 + 「彩色」模式
    label("旋转档位（0 / 3 / 6 / 9 档，注意尾巴与尖角朝向）", 40, 200)
    x = 70
    for name in ("星星", "花瓣", "音符", "火焰"):
        for step in (0, 3, 6, 9):
            _paste(canvas, F._shape_sprite(name, _color_of(name), 40, step, 12),
                   x, 260)
            x += 52
        x += 14

    label("「彩色」模式（同一簇里逐粒子取调色板，任何图案都能用）", 470, 200)
    rng = np.random.default_rng(7)
    for i, (base_x, name) in enumerate(((560, "爱心"), (660, "星星"),
                                        (760, "雪花"), (860, "彩带"))):
        for _ in range(7):
            cx = base_x + rng.uniform(-34, 34)
            cy = 262 + rng.uniform(-28, 28)
            col = F._CONFETTI_COLORS[int(rng.integers(
                0, len(F._CONFETTI_COLORS)))]
            step = int(rng.integers(0, 12))
            _paste(canvas, F._shape_sprite(name, col, 24, step, 12), cx, cy)
    label("每簇 7 颗，颜色随机取自同一套调色板", 470, 300)
    label("彩带本身是弯的：宽度沿程收放，像缎带在翻卷", 470, 318)

    label("每 12 档为一整圈，绘制时取最近档（不会每帧重采样）", 40, 336)

    # ---------------- 第三块：文字弹出档位
    label("点击文字：弹出档位（首档最小 → 中段过冲 → 稳定）", 40, 386, LABEL_D)
    x = 70
    for s in F._TEXT_POP_STEPS:
        spr = F._text_sprite("好耶", "行楷", 40, "#FFFFFF", s)
        _paste(canvas, spr, x, 448)
        x += 84
    label("过冲档", 70 + 84 * 3 - 18, 478, LABEL_D)

    # ---------------- 第四块：9 款字体（两列）
    label("9 款字体（系统自带；没装的会自动回退雅黑）", 470, 386, LABEL_D)
    y = 424
    for fam in F._FONT_FILES:
        spr = F._text_sprite("桌面特效", fam, 24, "#FFFFFF", 1.0)
        img = _to_rgba(spr)
        canvas.alpha_composite(img, (556, y - img.height // 2))
        label(fam, 470, y - 7, LABEL_D)
        y += 30

    # ---------------- 第五块：随机倾斜
    label("随机倾斜档位（每次点击随机取一档，0 = 全水平）", 40, 512, LABEL_D)
    x = 78
    for idx in (0, 2, 4, 6, 8, 10, 12):
        ang = F._TEXT_TILT_STEPS[idx]
        spr = F._text_sprite("好耶", "琥珀", 34, "#FFE066", 1.0, ang)
        _paste(canvas, spr, x, 570)
        label(f"{ang:+.0f}°", x - 12, 604, LABEL_D)
        x += 92

    # ---------------- 第六块：装饰
    label("装饰（文字旁边一起撒）", 700, 512, LABEL_D)
    rng2 = np.random.default_rng(11)
    for i, (base_x, shape) in enumerate(((760, "彩带"), (880, "星星"))):
        for _ in range(11):
            cx = base_x + rng2.uniform(-40, 40)
            cy = 580 + rng2.uniform(-30, 30)
            col = (F._CONFETTI_COLORS[int(rng2.integers(
                0, len(F._CONFETTI_COLORS)))] if shape == "彩带"
                else "#FFD166")
            _paste(canvas, F._shape_sprite(shape, col, 26,
                                           int(rng2.integers(0, 12)), 12),
                   cx, cy)
    label("彩带", 742, 634, LABEL_D)
    label("星光", 866, 634, LABEL_D)

    label("虚线以上 = 浅色桌面，以下 = 深色桌面", 40, 668, LABEL_D)
    d.line([(0, 372), (W, 372)], fill=DIVIDER, width=1)

    out = os.path.join(HERE, "docs", "images", "click-preview.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    canvas.convert("RGB").save(out)
    print(f"已输出 {out}  ({W}x{H})")
    print(f"  上半：{len(F._SHAPE_NAMES)} 种图案 × 三档尺寸（各用惯用色）")
    print(f"        旋转档位 + 「彩色」模式 + 弯曲的彩带")
    print(f"  下半：弹出档位 / 9 款字体 / 随机倾斜 / 装饰")


if __name__ == "__main__":
    main()
