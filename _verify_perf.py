# -*- coding: utf-8 -*-
"""最坏情况性能基准：暴雨 (620 drops) + 大量涟漪，看是否还能 60 FPS。"""
import os
import time
import numpy as np

# 复用真实的特效类
os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")
from fx_effects import RainField, RippleField, hex_to_bgr
import desktop_fx as d


def main():
    W, H = 3840, 1200          # 用户的 4K 双屏
    cfg = dict(d.PRESETS["暴雨"])
    print(f"benchmark on {W}x{H}, 暴雨 preset:")
    print(f"  rain_amount={cfg['rain_amount']}  rain_speed={cfg['rain_speed']}"
          f"  rain_length={cfg['rain_length']}  rain_opacity={cfg['rain_opacity']}")

    rain = RainField(W, H, seed=1)
    rain.set_color(cfg["rain_color"])
    rain.set_amount(int(cfg["rain_amount"]), W, H)
    ripples = RippleField()

    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)

    # 预热
    for _ in range(30):
        rain.update(0.016, cfg)
    for _ in range(5):
        u32[:] = 0
        rain.draw(u32, W, H, cfg)

    # 测纯雨 100 帧
    N = 100
    t0 = time.perf_counter()
    for _ in range(N):
        rain.update(0.016, cfg)
        u32[:] = 0
        rain.draw(u32, W, H, cfg)
    dt_rain = (time.perf_counter() - t0) * 1000 / N
    print(f"\n  纯雨 100 帧: {dt_rain:.2f} ms/frame  (理论 FPS = {1000/dt_rain:.0f})")

    # 测 雨+120 涟漪（最坏情况）
    color = hex_to_bgr("#7FE7FF")
    np.random.seed(2)
    for i in range(120):
        ripples.spawn(np.random.uniform(50, W-50), np.random.uniform(50, H-50),
                      rmax=200, life=1.0, strength=0.8, color_bgr=color)
    t0 = time.perf_counter()
    for _ in range(N):
        rain.update(0.016, cfg)
        ripples.update(0.016)
        u32[:] = 0
        rain.draw(u32, W, H, cfg)
        ripples.draw(u32, W, H)
    dt_full = (time.perf_counter() - t0) * 1000 / N
    print(f"  雨+120 涟漪 100 帧: {dt_full:.2f} ms/frame  (理论 FPS = {1000/dt_full:.0f})")
    print()
    if dt_full < 14:
        print(f"  ✓ 最坏情况还能 60+ FPS（{1000/dt_full:.0f} FPS）")
    elif dt_full < 20:
        print(f"  ~ 50-60 FPS（{1000/dt_full:.0f} FPS）— 满帧刚好够")
    else:
        print(f"  ✗ 掉帧（{1000/dt_full:.0f} FPS）— 需要降上限或优化")


if __name__ == "__main__":
    main()
