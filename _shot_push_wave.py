# -*- coding: utf-8 -*-
"""生成"窗口推水"的物理模型对照图（docs/images/water-push-outward.png）。

上：旧写法（两侧对称包、只写位移）—— 波从窗缘往**内外同时**扩散，
    一分为二，一半灌进窗口里（用户看到的"同时向内"）。
下：新写法（单侧带、两端归零、单向行波）—— 波只往窗外走，窗内是平水。
白框内是窗口；红 = 水面隆起，蓝 = 塌陷。

注：这里刻意**只注入一帧再跑 45 步**。真实拖动（900px/s）比波速快得多，窗口会
追上自己刚推出去的波 —— 那是对的（尾迹本就留在船后），但会把"向内/向外"混在
一起看不清；单帧注入才是"波从窗缘往哪边传"的干净断面。

用法：python _shot_push_wave.py
"""
import os
os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")

from pathlib import Path

import numpy as np
from PIL import Image

from fx_effects import WaterRippleField
from _verify_window_ripple import _old_band

W, H = 900, 560
BAND = WaterRippleField._PUSH_W_DEF
AMP = WaterRippleField._PUSH_AMP
V_REF = WaterRippleField._PUSH_V_REF
X, Y0, Y1, WW = 480.0, 200.0, 420.0, 300.0
OUT = Path(__file__).resolve().parent / "docs" / "images" / "water-push-outward.png"


def run(use_new, steps=45, v=900.0, dt=1.0 / 60.0):
    wf = WaterRippleField(W, H, seed=3)
    sp = min(v / V_REF, 1.0)
    amp_old = AMP / (2.0 * (BAND * 0.5)) * dt * sp * v
    amp_new = AMP / (2.0 * BAND) * dt * sp * v
    if use_new:
        wf._push_band(0, X, Y0, Y1, amp_new, BAND, +1)
    else:
        _old_band(wf, X, Y0, Y1, amp_old, BAND * 0.5)
    for _ in range(steps):
        wf.step(wf.k, wf.damp)
    return wf


def render(wf, mx, up=2):
    h = wf.height
    t = np.clip(h / mx, -1.0, 1.0)
    img = np.zeros((h.shape[0], h.shape[1], 3), np.uint8)
    img[..., 0] = (np.clip(t, 0, 1) * 255).astype(np.uint8)    # 隆起 = 红
    img[..., 2] = (np.clip(-t, 0, 1) * 255).astype(np.uint8)   # 塌陷 = 蓝
    img = np.repeat(np.repeat(img, up, axis=0), up, axis=1)
    c = wf.cell / up
    for x_px in (X - WW, X):
        i = int(round(x_px / c))
        if 0 <= i < img.shape[1]:
            img[:, max(0, i - 1):i + 2] = (255, 255, 255)
    for y_px in (Y0, Y1):
        j = int(round(y_px / c))
        if 0 <= j < img.shape[0]:
            img[max(0, j - 1):j + 2, :] = (255, 255, 255)
    return img


def report(label, wf):
    c = wf.cell
    ix = int(X / c)
    f = lambda a: float(np.abs(a).max()) if a.size else 0.0
    inner, outer = wf.height[:, ix - 100:ix - 4], wf.height[:, ix + 4:ix + 100]
    print(f"{label}模型  窗内峰={f(inner):.5f}  窗外峰={f(outer):.5f}  "
          f"窗内/窗外 = {f(inner) / max(f(outer), 1e-9):.1%}")


def main():
    old, new = run(False), run(True)
    mx = max(float(np.abs(old.height).max()), float(np.abs(new.height).max()))
    report("旧", old)
    report("新", new)
    a, b = render(old, mx), render(new, mx)
    sep = np.full((6, a.shape[1], 3), 90, np.uint8)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.concatenate([a, sep, b], axis=0)).save(OUT)
    print(f"已写出 {OUT}（上=旧、下=新；白框内是窗口，右窗缘 = 注入边）")


if __name__ == "__main__":
    main()
