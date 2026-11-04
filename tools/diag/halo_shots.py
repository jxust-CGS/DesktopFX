# -*- coding: utf-8 -*-
"""生成各样式光环的对比图（PNG），用来肉眼验收视觉效果。

在深色背景上按 3x2 排开六种样式，每格里画三个电平（静 / 中 / 强），
直观看出"同一款样式在不同律动强度下长什么样"。

用法：python tools/diag/halo_shots.py [输出路径]
"""
import os
import sys

import numpy as np

ROOT = __file__.rsplit("tools", 1)[0].rstrip("/\\")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fx_halo import HaloField, halo_style_names  # noqa: E402
from fx_image import _premul_pack  # noqa: E402

BG = (18, 21, 28)          # 深色底（桌面大多是深色）
CELL = 260
MARGIN = 16
LEVELS = (0.0, 0.45, 1.0)


def render_cell(style, level, size=CELL, bg=BG):
    """画一格：深色底 + 光环（预乘 alpha 合成到 RGB）。"""
    rgb = np.zeros((size, size, 3), dtype=np.float32)
    rgb[:, :] = bg
    halo = HaloField(size, size)
    halo.set_style(style)
    halo.set_color("#7EC8FF")
    halo.set_size(size * 0.28)
    halo.set_level(level)
    halo.set_cursor(size / 2.0, size / 2.0)
    u32 = np.zeros(size * size, dtype=np.uint32)
    halo.draw(u32, size, size, {}, 0.016)
    # alpha 归一化到 0~1（用来做 (1-a) 的混合系数）；
    # ⚠️ **颜色不能归一化** —— u32 里存的是 0~255 的预乘色，要与 0~255 的
    # 背景同量纲才能相加。归一化后颜色会全被压暗成近黑（这个坑踩过：
    # 图上一片黑环，而 u32 里的值 0xff7ec8ff 完全正常）。
    a = (u32 >> 24).astype(np.float32).reshape(size, size) / 255.0
    b = (u32 & 0xFF).astype(np.float32).reshape(size, size)
    g = ((u32 >> 8) & 0xFF).astype(np.float32).reshape(size, size)
    r = ((u32 >> 16) & 0xFF).astype(np.float32).reshape(size, size)
    rgb[..., 0] = rgb[..., 0] * (1 - a) + r
    rgb[..., 1] = rgb[..., 1] * (1 - a) + g
    rgb[..., 2] = rgb[..., 2] * (1 - a) + b
    return rgb


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "..", "ui-shots", "halo_styles.png")
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    names = halo_style_names()
    # 每种样式一行，三个电平并排（不能叠在一格里 —— 叠了就只能看到最后一档）
    rows = len(names)
    cols = len(LEVELS)
    W = cols * CELL + (cols + 1) * MARGIN
    H = rows * CELL + (rows + 1) * MARGIN + 24
    canvas = np.zeros((H, W, 3), dtype=np.float32)
    canvas[:, :] = (12, 14, 18)
    for i, name in enumerate(names):
        for j, lv in enumerate(LEVELS):
            cell = render_cell(name, lv)
            x0 = MARGIN + j * (CELL + MARGIN)
            y0 = MARGIN + i * (CELL + MARGIN)
            canvas[y0:y0 + CELL, x0:x0 + CELL] = cell
    try:
        from PIL import Image, ImageDraw
        img = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8))
        dr = ImageDraw.Draw(img)
        try:
            from PIL import ImageFont
            font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 15)
            small = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 12)
        except Exception:
            font = small = None
        for i, name in enumerate(names):
            y0 = MARGIN + i * (CELL + MARGIN)
            dr.text((MARGIN + 6, y0 + 6), name, fill=(235, 240, 250), font=font)
        for j, lv in enumerate(LEVELS):
            x0 = MARGIN + j * (CELL + MARGIN)
            dr.text((x0 + 6, MARGIN + rows * (CELL + MARGIN) - 4),
                    "电平 %d%%" % int(lv * 100), fill=(150, 160, 180),
                    font=small)
        img.save(out)
        print("已保存 %s (%dx%d)" % (out, img.width, img.height))
    except Exception as exc:
        print("保存失败（没有 PIL？）：%s" % exc)
        np.save(out + ".npy", canvas)
        print("已改存 npy：%s.npy" % out)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
