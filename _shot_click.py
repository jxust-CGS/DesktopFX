# -*- coding: utf-8 -*-
"""渲染点击特效的预览图（README 用）。

图案与文字都是**代码画出来的**：改一行坐标就可能变丑，只看代码看不出来。
这个脚本把三件事渲成一张对照图：

  1. 8 种图案（爱心 / 星星 / 彩带 / 雪花 / 气泡 / 火焰 / 花瓣 / 音符）；
  2. 每个图案在几个旋转档位下的样子（确认旋转真的在转，不是复制粘贴）；
  3. **点击文字**的几档字号 + 装饰，以及"爆发"的整体观感。

输出 `docs/images/click-preview.png`。改完 `_build_shape` / `_text_sprite`
的坐标后重跑它。

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


def main():
    W, H = 960, 560
    canvas = Image.new("RGBA", (W, H), BG_LIGHT)
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 300, W, H], fill=BG_DARK)      # 下半段换深色底

    def label(text, x, y, col=LABEL):
        d.text((x, y), text, fill=col)

    sizes = (56, 34, 20)        # 三档尺寸并排，用来对比细节是否还在

    # ---------------- 上半：8 种图案 × 三档尺寸
    label("8 种图案（大 / 中 / 小 三档）", 40, 16)
    x = 62
    for name in F._SHAPE_NAMES:
        col = "#FF6B8E" if name != "彩带" else F._CONFETTI_COLORS[0]
        y = 66
        for s in sizes:
            _paste(canvas, F._shape_sprite(name, col, s, 0, 12), x, y)
            y += s * 0.72 + 14
        label(name, x - 16, 158)
        x += 112

    # ---------------- 中段：旋转档位（确认真的在转）
    label("旋转档位（0 / 3 / 6 / 9 档，注意尾巴与尖角朝向）", 40, 182)
    x = 70
    for name, col in (("星星", "#FFD166"), ("花瓣", "#FF9DB6"),
                      ("音符", "#7FD1FF"), ("火焰", "#FF9A5C")):
        for step in (0, 3, 6, 9):
            _paste(canvas, F._shape_sprite(name, col, 40, step, 12), x, 244)
            x += 52
        x += 14
    label("每 12 档为一整圈，绘制时取最近档（不会每帧重采样）", 40, 272)

    # ---------------- 下半：文字 + 装饰
    label("点击文字：弹出档位（首档最小 → 中段过冲 → 稳定）", 40, 312, LABEL_D)
    x = 70
    for s in F._TEXT_POP_STEPS:
        spr = F._text_sprite("好耶", "微软雅黑", 40, "#FFFFFF", s)
        _paste(canvas, spr, x, 372)
        x += 84
    label("过冲档", 70 + 84 * 3 - 18, 402, LABEL_D)

    label("字体与颜色", 470, 312, LABEL_D)
    y = 352
    for fam, col in (("微软雅黑", "#FFFFFF"), ("雅黑粗体", "#FFE066"),
                     ("黑体", "#7FE7FF"), ("楷体", "#FF9DB6")):
        spr = F._text_sprite("桌面特效", fam, 26, col, 1.0)
        img = _to_rgba(spr)
        canvas.alpha_composite(img, (520, y - img.height // 2))
        label(fam, 470, y - 7, LABEL_D)
        y += 36

    # 装饰：彩带与星光各撒一簇
    label("装饰", 470, 460, LABEL_D)
    rng = np.random.default_rng(3)
    for base_x, shape, pal in ((560, "彩带", F._CONFETTI_COLORS),
                               (760, "星星", ("#FFD166",))):
        for _ in range(12):
            cx = base_x + rng.uniform(-44, 44)
            cy = 510 + rng.uniform(-26, 26)
            col = pal[int(rng.integers(0, len(pal)))]
            step = int(rng.integers(0, 12))
            _paste(canvas, F._shape_sprite(shape, col, 26, step, 12), cx, cy)
    label("彩带", 540, 480, LABEL_D)
    label("星光", 740, 480, LABEL_D)
    label("虚线以上 = 浅色桌面，以下 = 深色桌面", 40, 528, LABEL_D)
    d.line([(0, 300), (W, 300)], fill=DIVIDER, width=1)

    out = os.path.join(HERE, "docs", "images", "click-preview.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    canvas.convert("RGB").save(out)
    print(f"已输出 {out}  ({W}x{H})")
    print(f"  上半：{len(F._SHAPE_NAMES)} 种图案 × 三档尺寸（浅底）")
    print(f"  中段：旋转档位对比")
    print(f"  下半：文字弹出档位 / 字体颜色 / 装饰（深底）")


if __name__ == "__main__":
    main()
