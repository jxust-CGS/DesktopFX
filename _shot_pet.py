# -*- coding: utf-8 -*-
"""渲染宠物跟随的预览图（README 用）。

形象与动画都是**代码画出来的**，不是图片资源 —— 改一行坐标就可能变丑，
只看代码看不出来。这个脚本把三件事渲成一张对照图：

  1. 两种宠物在几个典型尺寸、左右两个朝向下的样子；
  2. **走路循环的 6 帧**（尾巴摆动 + 前后腿交替）与**静止循环的 4 帧**，
     用来确认动画幅度够不够看得见；
  3. **眨眼**与 **4 个点击表情**。

输出 `docs/images/pet-preview.png`。改完 `_draw_cat` / `_draw_dog` /
`_build_frames` / `_expr_icon` 的坐标后重跑它。

提示：在终端里想快速看单帧形状，用 `_ascii_pet.py` 更省事。
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from PIL import Image, ImageDraw

import fx_effects

BG_LIGHT = (238, 240, 243, 255)
BG_DARK = (32, 35, 40, 255)
DIVIDER = (168, 172, 178, 255)
LABEL = (120, 124, 130, 255)
LABEL_D = (196, 202, 210, 255)
COLORS = ("#F2C27B", "#9FB6D8")


def _to_rgba(spr):
    """预乘 -> 直通 alpha（预览图不是上屏路径，不必保持预乘）。"""
    a = spr[..., 3:4]
    rgb = np.where(a > 1e-6, spr[..., :3] / np.maximum(a, 1e-6), 0.0)
    out = np.clip(np.concatenate([rgb, a], axis=2) * 255.0, 0, 255)
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def _paste(canvas, spr, cx, cy, flip=False):
    img = _to_rgba(spr)
    if flip:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    canvas.alpha_composite(img, (int(cx - img.width / 2),
                                 int(cy - img.height / 2)))


def _pet(kind, color, size):
    p = fx_effects.PetField(64, 64)
    p._ensure_sprite(dict(pet_kind=kind, pet_color=color, pet_size=float(size)))
    return p


def _blink_overlay(base, blink):
    """把闭眼覆盖层按 _blit 同一个 over 公式合到一张静止帧上（仅供预览）。"""
    if blink is None:
        return base
    bimg, bdx, bdy = blink
    out = base.copy()
    a = bimg[:, :, 3:4]
    sub = out[bdy:bdy + bimg.shape[0], bdx:bdx + bimg.shape[1], :3]
    sub[...] = bimg[:, :, :3] + sub * (1.0 - a)
    return out


def main():
    W, H = 960, 620
    canvas = Image.new("RGBA", (W, H), BG_LIGHT)
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 286, W, H], fill=BG_DARK)      # 下半段换深色底

    def label(text, x, y, col=LABEL):
        d.text((x, y), text, fill=col)

    # ---------------- 上半：尺寸与朝向
    y = 62
    x = 92
    for size in (40, 72, 116):
        _paste(canvas, _pet("小猫", COLORS[0], size)._frames_idle[0], x, y)
        label(f"{size}px", x - 16, y + 76)
        x += 126
    _paste(canvas, _pet("小狗", COLORS[1], 116)._frames_idle[0], x + 40, y,
           flip=True)
    label("小狗 / 朝左", x + 2, y + 76)

    # ---------------- 中部：走路循环 / 静止循环 / 眨眼
    p = _pet("小猫", COLORS[0], 64)
    walk, idle, blink, exprs = (p._frames_walk, p._frames_idle, p._blink,
                                p._expr)
    label("走路循环（尾巴摆动 + 前后腿交替）", 40, 166)
    x = 58
    for k in range(walk.shape[0]):
        _paste(canvas, walk[k], x, 226)
        label(str(k), x - 3, 262)
        x += 62
    label("静止循环（只摆尾）", 462, 166)
    x = 480
    for k in range(idle.shape[0]):
        _paste(canvas, idle[k], x, 226)
        x += 62
    _paste(canvas, _blink_overlay(idle[0], blink), 764, 226)
    label("眨眼", 750, 262)

    # ---------------- 下半：表情 + 跟随链
    label("点击表情", 40, 322, LABEL_D)
    x = 92
    for k, name in enumerate(fx_effects.PetField.EXPR_KINDS):
        _paste(canvas, exprs[k], x, 386)
        label(name, x - 14, 424, LABEL_D)
        x += 86
    label("跟随链排布（间距 = 体型 × 0.82）", 470, 322, LABEL_D)
    for i in range(7):
        _paste(canvas, idle[i % idle.shape[0]], 520 + i * 60, 390)
    label("虚线以上 = 浅色桌面，以下 = 深色桌面", 40, 452, LABEL_D)
    d.line([(0, 286), (W, 286)], fill=DIVIDER, width=1)

    out = os.path.join(HERE, "docs", "images", "pet-preview.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    canvas.convert("RGB").save(out)
    print(f"已输出 {out}  ({W}x{H})")
    print(f"  上半：40/72/116 像素 + 小狗朝左（浅底）")
    print(f"  中部：走路 {walk.shape[0]} 帧 + 静止 {idle.shape[0]} 帧 + 眨眼")
    print(f"  下半：4 个点击表情 + 跟随链排布（深底）")


if __name__ == "__main__":
    main()
