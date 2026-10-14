# -*- coding: utf-8 -*-
"""把宠物精灵按 ASCII 打印出来 —— 终端里直接看形状。

为什么需要它：形象是代码画出来的，而渲出来的 PNG 不总能立刻看到（无图形
环境 / 自动化流程里）。ASCII 预览能一眼看出比例失调、耳朵飞出画面、五官错位
这类问题。改 `_draw_cat` / `_draw_dog` 的坐标后先跑这个，再跑 `_shot_pet.py`
出正式预览图。

用法：
    python _ascii_pet.py             # 两种宠物，默认 56 像素
    python _ascii_pet.py 96          # 指定尺寸
    python _ascii_pet.py 96 小狗     # 指定种类
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fx_effects

# 从"暗"到"亮"的字符梯度。
# **必须按亮度渲染而不是按 alpha**：精灵是不透明的卡通形象，按 alpha 打出来
# 就是一大坨实心字符，眼睛 / 描边 / 高光全都看不见（它们 alpha 一样是 1，
# 区别只在颜色）。按亮度才能看出五官画在了哪里。
_RAMP = " .:-=+*#%@"


def ascii_art(spr, cols=58):
    """把 (S,S,4) 预乘精灵渲成 ASCII 网格（按行采样，保持宽高比）。"""
    S = spr.shape[0]
    rows = max(1, int(cols * 0.5))          # 终端字符高约是宽的两倍
    a = spr[:, :, 3]
    # 预乘色还原成直通色，再取亮度（Rec.601）
    rgb = np.where(a[..., None] > 1e-6, spr[:, :, :3] / np.maximum(a[..., None], 1e-6), 0.0)
    lum = rgb[:, :, 0] * 0.299 + rgb[:, :, 1] * 0.587 + rgb[:, :, 2] * 0.114
    idx = (np.arange(rows) * S / rows).astype(np.int32)
    jdx = (np.arange(cols) * S / cols).astype(np.int32)
    g_a = a[np.ix_(idx, jdx)]
    g_l = lum[np.ix_(idx, jdx)]
    out = []
    for ra, rl in zip(g_a, g_l):
        row = []
        for va, vl in zip(ra, rl):
            if va < 0.06:
                row.append(" ")                      # 透明（桌面）
            else:
                k = int(np.clip(vl, 0.0, 1.0) * (len(_RAMP) - 1) + 0.5)
                row.append(_RAMP[k] if k > 0 else ".")
        out.append("".join(row))
    return out


def main():
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 56
    kinds = [sys.argv[2]] if len(sys.argv) > 2 else list(fx_effects.PetField.KINDS)
    cols = int(sys.argv[3]) if len(sys.argv) > 3 else 58
    p = fx_effects.PetField(64, 64)
    for kind in kinds:
        spr = p._build_sprite(kind, "#F2C27B", size)
        art = ascii_art(spr, cols)
        print(f"===== {kind}（{size} 像素，朝右，{cols} 列）=====")
        for line in art:
            print("|" + line + "|")
        # 顺手报几个能自动判的几何量
        a = spr[:, :, 3]
        ys, xs = np.nonzero(a > 0.5)
        print(f"  实心包围盒 x[{xs.min()}..{xs.max()}] "
              f"y[{ys.min()}..{ys.max()}]  画布 {spr.shape[0]}")
        print(f"  实心占比 {100.0 * (a > 0.5).mean():.1f}%  "
              f"羽化边缘 {int(((a > 0.02) & (a < 0.98)).sum())} 像素")
        print()


if __name__ == "__main__":
    main()
