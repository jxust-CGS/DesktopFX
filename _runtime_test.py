# -*- coding: utf-8 -*-
"""实机冒烟测试：创建真实的分层覆盖窗口 + 渲染若干帧后退出。
不会创建 UI 或托盘，可用于 CI 自动化验证。"""
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fx_winlayer import Overlay                # noqa: E402
from fx_effects import RainField, RippleField, hex_to_bgr  # noqa: E402


def main():
    ov = Overlay()
    ov.create()
    print("overlay:", ov.w, "x", ov.h, "@", ov.x, ov.y, "hwnd=", ov.hwnd)

    rain = RainField(ov.w, ov.h)
    rain.set_amount(300, ov.w, ov.h)
    rain.set_color("#BFE3FF")
    rip = RippleField()
    rc = hex_to_bgr("#7FE7FF")
    cfg = dict(rain_speed=900.0, rain_length=1.0, wind=0.15, rain_opacity=0.75,
               ripple_opacity=0.85, ripple_size=130.0, ripple_life=0.9, splash=True)

    cx, cy, acc = ov.w / 2, ov.h / 2, 0.0
    last = time.perf_counter()
    n = 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 2.0:
        now = time.perf_counter()
        dt = min(max(now - last, 0.001), 0.05)
        last = now
        t = now - t0
        nx = cx + 360 * math.cos(t * 1.6)
        ny = cy + 200 * math.sin(t * 1.6)
        d = ((nx - cx) ** 2 + (ny - cy) ** 2) ** 0.5
        cx, cy = nx, ny
        acc += d
        while acc >= 24.0:
            acc -= 24.0
            rip.spawn(nx, ny, 130.0, 0.9, 0.65, rc, r0=2.0)
        if abs(t - 1.0) < dt:
            rip.spawn(nx, ny, 240.0, 1.2, 0.9, rc, r0=3.0)

        ov.u32[:] = 0
        sx = rain.update(dt, cfg)
        rain.draw(ov.u32, ov.w, ov.h, cfg)
        for k in range(min(5, sx.size)):
            rip.spawn(float(sx[k]), float(ov.h - 2),
                      rmax=22.0, life=0.45, strength=0.30, color_bgr=rc, r0=1.0, squash=0.3)
        rip.update(dt)
        rip.draw(ov.u32, ov.w, ov.h)
        ov.present()
        ov.pump()
        n += 1
    cost = (time.perf_counter() - t0) / n * 1000
    print(f"present ok: {n} frames, {cost:.2f} ms/frame (含 UpdateLayeredWindow)")
    ov.u32[:] = 0
    ov.present()
    ov.destroy()
    print("destroy ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
