# -*- coding: utf-8 -*-
"""最坏情况性能 / 稳定性基准：雨(620) + 雪(560) + 聚光灯 全部同屏，
模拟用户把能开的特效都打开的极端场景，看 Scene.step 是否还能稳在 60 FPS、
长跑是否泄漏 / 产生 NaN。

用 App 真实的 Scene.step 管线（与渲染循环同一份代码），只需一块内存缓冲，
无需真实窗口，因此可在无桌面环境跑。

**退出码就是判据**（`_run_all_verify.py` 只认 returncode）：
    0  性能未退化且 3600 帧无 NaN / 无内存增长
    1  任一条不满足

性能判红用的是**相对倍数**而不是绝对毫秒：这个场景是"把能开的都开到极端"的
合成负载，本身就落在 60 帧预算之上（本机 2026-10-14 实测 22.2 ms = 45 fps，
单层基线 6.4 ms）。拿 16.67 ms 判红会让它从第一天起恒红 —— 那是把"没有判据"
换成"永远红的判据"，同样看不见回归。所以标定一次同尺寸单层耗时当分母，
按倍数判（本机实测 3.5×，阈值取 5.0×）。倍数跨 CPU 可移植，绝对毫秒不行。
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

# 全层耗时 / 单层基线耗时的上限。本机 2026-10-14 实测 3.5×（22.2 / 6.4），
# 留约 1.4 倍余量：只拦"明显退化"，不拦正常波动。
_RATIO_MAX = 5.0
# 满帧预算，只用于打警告，不参与判红（理由见文件头）。
_MS_WARN = 16.67
# 3600 帧的 RSS 增长上限（MB）。实测 +0.0，留足余量。
_RSS_MAX_MB = 40.0


def rss():
    return psutil.Process().memory_info().rss / (1024 * 1024) if HAS_PSUTIL else 0.0


def has_nan_inf(buf):
    a = buf.view(np.int32).reshape(-1)
    return int(np.any((a == -2147483648) | (a == 0x7F800000) | (a > 0x7F800000)))


def base_ms(W, H):
    """单层基线：同尺寸下只开雨(620)的耗时，用作归一化分母。

    与全层场景共用同一套雨水参数，只差"多开雪与聚光灯"，所以两者的比值
    才是"层叠加的代价"。跑 120 帧（不是 60）：分母只测一次且样本小，
    噪声直接放大成比值波动，加厚一点更稳，代价约 2s。
    """
    scene = Scene(W, H)
    scene.set_origin(0, 0)
    cfg = dict(d.DEFAULTS)
    cfg.update(rain_enabled=True, rain_amount=620, rain_speed=1520,
               rain_length=1.6, wind=0.5, rain_color="#3F88C0",
               rain_opacity=0.95, splash=True)
    scene.cfg = cfg
    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    for _ in range(30):
        u32[:] = 0
        scene.step(0.016, u32, W, H)
    N = 120
    t0 = time.perf_counter()
    for _ in range(N):
        u32[:] = 0
        scene.step(0.016, u32, W, H)
    return (time.perf_counter() - t0) * 1000.0 / N


def main():
    """跑基准。返回 True = 通过（退出码 0），False = 有任一条不满足。"""
    W, H = 3840, 1200          # 用户 4K 双屏
    print(f"benchmark on {W}x{H}, worst-case: rain620+snow560+spotlight")

    bms = base_ms(W, H)
    print(f"  base (rain620 only) {bms:.2f} ms/frame  [归一化分母]")

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
    ratio = ms / max(bms, 1e-6)
    print(f"  avg {ms:.2f} ms/frame  (~{fps:.1f} fps, {ratio:.2f}x base)"
          f"  [60fps 预算 {_MS_WARN:.2f}ms]")

    # ---- 稳定性：再跑 3600 帧，盯 NaN 与内存
    gc.collect()
    rss0 = rss()
    for f in range(3600):
        u32[:] = 0
        scene.step(0.016, u32, W, H)
        if has_nan_inf(buf):
            print(f"[FAIL] NaN/Inf at stability frame {f}")
            return False
    gc.collect()
    rss1 = rss()
    dmem = rss1 - rss0
    print(f"  stability 3600 frames OK, RSS delta = {dmem:+.1f} MB"
          + ("" if HAS_PSUTIL else "  (psutil 缺失，未测内存)"))

    # ---- 判定：性能倍数 + 内存增长，两条硬线
    print()
    ok = True
    if ratio > _RATIO_MAX:
        print(f"[FAIL] 层叠加代价 {ratio:.2f}x 超过 {_RATIO_MAX:.1f}x 上限"
              f"（{ms:.2f} ms/frame）—— 相对本机基线明显退化")
        ok = False
    if ms > _MS_WARN:
        # 只警告：极端组合本来就贴着 60 帧，长期如此不算回归
        print(f"[WARN] {ms:.2f} ms/frame 高于 {_MS_WARN:.2f}ms 预算"
              f"（{fps:.1f} fps）— 极端组合下不掉帧即可，非致命")
    if dmem > _RSS_MAX_MB:
        print(f"[FAIL] 3600 帧后 RSS 增长 {dmem:+.1f} MB 超过 {_RSS_MAX_MB:.0f} MB"
              f" —— 疑似泄漏")
        ok = False
    if ok:
        print(f"[OK] worst-case all-layers {fps:.1f} fps ({ratio:.2f}x base), "
              f"stable, no NaN")
    return ok


if __name__ == "__main__":
    # 这个脚本原先通篇只打印，从不给退出码 —— 于是"4K 双屏最坏情况"这道
    # 回归在 `_run_all_verify.py` 眼里永远是绿的（它只判 returncode）。
    import sys
    sys.exit(0 if main() else 1)
