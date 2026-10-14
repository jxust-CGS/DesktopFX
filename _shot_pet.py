# -*- coding: utf-8 -*-
"""渲染宠物跟随的形象预览图（README 用）。

形象是**代码画出来的**，不是图片资源 —— 所以改一行坐标就可能变丑，只看代码
看不出来。这个脚本把两种宠物在几个典型尺寸、左右两个朝向下渲成一张对照图，
输出 `docs/images/pet-preview.png`。

同时画一遍"跟随链"排布，用来检查间距与遮挡关系是否自然。
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from PIL import Image, ImageDraw

import fx_effects

_LANCZOS = Image.Resampling.LANCZOS

# 两个中性底色：宠物要同时能在浅色与深色桌面上看清
BG_LIGHT = (238, 240, 243, 255)
BG_DARK = (32, 35, 40, 255)
COLORS = ("#F2C27B", "#9FB6D8", "#E9A0A8")


def _sprite(kind, color, size):
    p = fx_effects.PetField(64, 64)
    return p._build_sprite(kind, color, size)


def _paste(canvas, spr, cx, cy, flip=False):
    """把预乘精灵还原成直通 alpha 后贴到 RGBA 画布（预览图不是上屏路径）。"""
    a = spr[..., 3:4]
    rgb = np.where(a > 1e-6, spr[..., :3] / np.maximum(a, 1e-6), 0.0)
    rgba = np.concatenate([rgb, a], axis=2)
    rgba = np.clip(rgba * 255.0, 0, 255).astype(np.uint8)
    img = Image.fromarray(rgba, "RGBA")
    if flip:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    canvas.alpha_composite(img, (int(cx - img.width / 2),
                                 int(cy - img.height / 2)))


def main():
    W, H = 900, 470
    canvas = Image.new("RGBA", (W, H), BG_LIGHT)
    d = ImageDraw.Draw(canvas)

    # 上半：浅色底；下半：深色底
    d.rectangle([0, H // 2, W, H], fill=BG_DARK)

    # ---- 第一行（浅底）：小猫的三个尺寸 + 镜像
    y = 62
    x = 90
    for size in (40, 72, 120):
        _paste(canvas, _sprite("小猫", COLORS[0], size), x, y)
        x += 130
    _paste(canvas, _sprite("小猫", COLORS[0], 120), x + 30, y, flip=True)

    # ---- 第二行（浅底）：小狗 + 换色
    y = 175
    x = 90
    for size in (40, 72, 120):
        _paste(canvas, _sprite("小狗", COLORS[1], size), x, y)
        x += 130
    _paste(canvas, _sprite("小狗", COLORS[2], 120), x + 30, y, flip=True)

    # ---- 第三行（深底）：跟随链排布 —— 检查间距、朝向、遮挡
    y = H - 92
    for i, kind in enumerate(("小猫", "小猫", "小猫")):
        _paste(canvas, _sprite(kind, COLORS[0], 72), 150 + i * 60, y)
    for i, kind in enumerate(("小狗", "小狗", "小狗")):
        _paste(canvas, _sprite(kind, COLORS[1], 72), 480 + i * 60, y)

    out = os.path.join(HERE, "docs", "images", "pet-preview.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    canvas.convert("RGB").save(out)
    print(f"已输出 {out}")
    print(f"  画布 {W}x{H}；上=浅色桌面 下=深色桌面")
    print(f"  各列尺寸 40 / 72 / 120 像素，最右一列为朝左（镜像）")
    print(f"  最下一行是跟随链排布（间距 = 体型 × 0.82）")


if __name__ == "__main__":
    main()
