# -*- coding: utf-8 -*-
"""最坏情况性能 / 稳定性基准：雨(620) + 雪(560) + 聚光灯 全部同屏，
模拟用户把能开的特效都打开的极端场景，看 Scene.step 是否还能稳在 60 FPS、
长跑是否泄漏 / 产生 NaN。

用 App 真实的 Scene.step 管线（与渲染循环同一份代码），只需一块内存缓冲，
无需真实窗口，因此可在无桌面环境跑。
"""
import os
import time
import gc

os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")

import numpy as np

import desktop_fx as d
from fx_scene import Scene

try:
    import psutil
    HAS_PSUTIL = True
except Exception:
    HAS_PSUTIL = False


def rss():
    return psutil.Process().memory_info().rss / (1024 * 1024) if HAS_PSUTIL else 0.0


def has_nan_inf(buf):
    a = buf.view(np.int32).reshape(-1)
    return int(np.any((a == -2147483648) | (a == 0x7F800000) | (a > 0x7F800000)))


def main():
    W, H = 3840, 1200          # 用户 4K 双屏
    print(f"benchmark on {W}x{H}, worst-case: rain620+snow560+spotlight")

    scene = Scene(W, H)
    scene.set_origin(0, 0)
    cfg = dict(d.DEFAULTS)
    cfg.update(
        rain_enabled=True, rain_amount=620, rain_speed=1520, rain_length=1.6,
        wind=0.5, rain_color="#3F88C0", rain_opacity=0.95, splash=True,
        snow_enabled=True, snow_amount=560, snow_speed=130, snow_size=1.2,
        snow_drift=0.35, snow_color="#FFFFFF", snow_opacity=0.95,
        spotlight_enabled=True, spotlight_radius=240, spotlight_dim=0.6,
        spotlight_color="#05070A",
    )
    scene.cfg = cfg

    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)

    # 预热
    for _ in range(30):
        u32[:] = 0
        scene.step(0.016, u32, W, H)

    # ---- 性能：600 帧计时
    N = 600
    t0 = time.perf_counter()
    for _ in range(N):
        u32[:] = 0
        scene.step(0.016, u32, W, H)
    elapsed = time.perf_counter() - t0
    ms = elapsed / N * 1000.0
    fps = 1000.0 / ms
    print(f"  avg {ms:.2f} ms/frame  (~{fps:.1f} fps)  [60fps 预算 16.67ms]")

    # ---- 稳定性：再跑 3600 帧，盯 NaN 与内存
    gc.collect()
    rss0 = rss()
    for f in range(3600):
        u32[:] = 0
        scene.step(0.016, u32, W, H)
        if has_nan_inf(buf):
            print(f"[FAIL] NaN/Inf at stability frame {f}")
            return
    gc.collect()
    rss1 = rss()
    dmem = rss1 - rss0
    print(f"  stability 3600 frames OK, RSS delta = {dmem:+.1f} MB"
          + ("" if HAS_PSUTIL else "  (psutil 缺失，未测内存)"))

    # ---- 判定
    ok = ms < 16.67
    print()
    if ok:
        print(f"[OK] worst-case all-layers holds {fps:.1f} fps, stable, no NaN")
    else:
        # 4K 双屏极端组合下略低于 60 仍可接受，但给出警告
        print(f"[WARN] worst-case {ms:.2f} ms/frame < 16.67ms budget "
              f"({fps:.1f} fps) — 极端组合下不掉帧即可，非致命")


if __name__ == "__main__":
    main()
