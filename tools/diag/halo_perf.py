# -*- coding: utf-8 -*-
"""光环各样式在不同尺寸 / 电平下的单帧耗时。

为什么要量这个：**包围盒面积与半径的平方成正比**，而鼠标层每帧都要在包围盒内
逐像素算（numpy 向量化）。所以"把波动幅度调大"不只是改一个系数 —— 半径一涨，
包围盒面积按平方涨，逐像素运算量也跟着涨。改幅度之前必须先知道天花板在哪。

用法：python tools/diag/halo_perf.py
"""
import os
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.abspath(__file__))
ROOT = _ROOT
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) \
        and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fx_halo import HaloField, halo_style_names   # noqa: E402

W, H = 1920, 1200
BUDGET_MS = 16.7        # 60fps 的单帧预算


def bench(style, size, level, loops=20):
    halo = HaloField(W, H)
    halo.set_style(style)
    halo.set_color("#7EC8FF")
    halo.set_size(size)
    halo.set_level(level)
    halo.set_cursor(W / 2.0, H / 2.0)
    u32 = np.zeros(W * H, dtype=np.uint32)
    halo.draw(u32, W, H, {}, 0.016)          # 预热
    t0 = time.perf_counter()
    for _ in range(loops):
        halo.draw(u32, W, H, {}, 0.016)
    ms = (time.perf_counter() - t0) / loops * 1000
    # 包围盒面积 = 逐像素运算量的直接代理指标
    r0 = halo._base_r * (1.0 + 0.55 * halo._level)
    span = r0 * 2.6 + 12.0
    box = int(min(W, 2 * span)) * int(min(H, 2 * span))
    return ms, box


def main():
    names = halo_style_names()
    print("屏幕 %dx%d，60fps 单帧预算 %.1f ms\n" % (W, H, BUDGET_MS))
    for size in (46.0, 100.0, 160.0):
        for level in (0.0, 1.0):
            print("--- 基础半径 %.0f px，电平 %.1f ---" % (size, level))
            worst = 0.0
            worst_name = ""
            for nm in names:
                ms, box = bench(nm, size, level)
                flag = "  ⚠️ 超预算" if ms > BUDGET_MS else ""
                print("  %-8s %7.2f ms   包围盒 %6d px%s"
                      % (nm, ms, box, flag))
                if ms > worst:
                    worst, worst_name = ms, nm
            print("  最慢：%s %.2f ms\n" % (worst_name, worst))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
