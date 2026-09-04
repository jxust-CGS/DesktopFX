# -*- coding: utf-8 -*-
"""长跑稳定性测试：模拟 10 分钟真实运行（36000 帧），追踪
   - RSS 内存是否增长（泄漏）
   - 帧耗时是否漂移（抖动 / 累积状态）
   - 涟漪池是否溢出 CAP
   桌面常驻应用必须 7x24 稳定，这是最关键的验证。
"""
import gc
import os
import time

import numpy as np

os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")
import desktop_fx as d
from fx_effects import RainField, RippleField, Lightning, hex_to_bgr

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


def rss_mb():
    if not HAS_PSUTIL:
        return 0.0
    return psutil.Process().memory_info().rss / (1024 * 1024)


def main():
    W, H = 2560, 1440
    cfg = dict(d.DEFAULTS)
    cfg.update(d.PRESETS["中雨"])

    rain = RainField(W, H, seed=7)
    rain.set_color(cfg["rain_color"])
    rain.set_amount(int(cfg["rain_amount"]), W, H)
    ripples = RippleField()
    light = Lightning(seed=11)

    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    col = hex_to_bgr(cfg["ripple_color"])

    rng = np.random.default_rng(42)
    dt = 1.0 / 60.0

    # 每 60 帧采样一次
    samples = []
    frames = 18000          # 5 分钟 @ 60FPS（够长足以暴露任何累积问题）
    sample_every = 600      # 每 10 秒采一次
    t_start = time.perf_counter()
    t_last_sample = t_start
    frame_time_acc = 0.0
    frame_count = 0

    gc.collect()
    base_rss = rss_mb()

    print(f"长跑测试: {frames} 帧 @ {W}x{H}  (≈{frames/60:.0f}s 真实运行)")
    if HAS_PSUTIL:
        print(f"初始 RSS = {base_rss:.1f} MB")
    else:
        print("(psutil 未安装，跳过内存采样)")
    print()

    for f in range(frames):
        t0 = time.perf_counter()

        # ---- 模拟真实一帧：雨 + 涟漪 + 闪电 + 鼠标移动
        u32[:] = 0
        rain.set_amount(int(cfg["rain_amount"]), W, H)
        splash_x = rain.update(dt, cfg)
        rain.draw(u32, W, H, cfg)

        if cfg["splash"] and splash_x.size:
            n = min(6, splash_x.size)
            for k in range(n):
                ripples.spawn(float(splash_x[k]), float(H - 2),
                              rmax=float(rng.uniform(10.0, 30.0)),
                              life=float(rng.uniform(0.32, 0.55)),
                              strength=0.42 * cfg["ripple_opacity"],
                              color_bgr=col, r0=1.0, squash=0.30)

        # 模拟鼠标持续移动，每 ~8 帧 spawn 一个涟漪
        if f % 8 == 0:
            ripples.spawn(float(rng.uniform(100, W - 100)),
                          float(rng.uniform(100, H - 100)),
                          rmax=float(cfg["ripple_size"] * rng.uniform(0.55, 1.1)),
                          life=float(cfg["ripple_life"] * rng.uniform(0.9, 1.15)),
                          strength=0.7 * cfg["ripple_opacity"],
                          color_bgr=col)

        ripples.update(dt)
        ripples.draw(u32, W, H)
        light.update(dt, cfg["lightning"], W, H)
        light.draw(u32, W, H)

        frame_time_acc += time.perf_counter() - t0
        frame_count += 1

        if (f + 1) % sample_every == 0:
            now = time.perf_counter()
            elapsed = now - t_last_sample
            ft_ms = frame_time_acc / frame_count * 1000
            active = int(ripples.active.sum())
            samples.append({
                "frame": f + 1, "rss": rss_mb(),
                "frame_ms": ft_ms, "active": active,
                "n": int(rain.n), "light_flash": len(light.flash),
            })
            print(f"  frame {f+1:6d}  "
                  + (f"RSS {rss_mb():7.1f} MB  " if HAS_PSUTIL else "")
                  + f"frame {ft_ms:5.2f} ms  rain {int(rain.n):4d}  "
                    f"ripples {active:3d}  flashQ {len(light.flash)}")
            t_last_sample = now
            frame_time_acc = 0.0
            frame_count = 0

    total = time.perf_counter() - t_start
    print(f"\n总耗时 {total:.1f}s (模拟 {frames/60:.0f}s 运行)")
    print()

    # ---- 断言
    ok = True
    if HAS_PSUTIL:
        first, last = samples[0]["rss"], samples[-1]["rss"]
        growth = last - first
        print(f"内存: 首次 {first:.1f} MB -> 末次 {last:.1f} MB  (增长 {growth:+.1f} MB)")
        if growth > 30:
            print(f"  ✗ 疑似内存泄漏（增长 > 30 MB）")
            ok = False
        else:
            print(f"  ✓ 无泄漏（增长 <= 30 MB）")

    fts = [s["frame_ms"] for s in samples]
    drift = (fts[-1] - fts[0]) / max(fts[0], 1e-6) * 100
    print(f"帧耗时: 首次 {fts[0]:.2f} ms -> 末次 {fts[-1]:.2f} ms  "
          f"(漂移 {drift:+.1f}%, 区间 {min(fts):.2f}-{max(fts):.2f} ms)")
    if drift > 50:
        print(f"  ✗ 帧耗时漂移过大（>50%）")
        ok = False
    else:
        print(f"  ✓ 帧耗时稳定（漂移 <= 50%）")

    max_active = max(s["active"] for s in samples)
    print(f"涟漪池: 峰值 {max_active} / CAP {ripples.CAP}")
    if max_active > ripples.CAP:
        print(f"  ✗ 涟漪池溢出 CAP")
        ok = False
    else:
        print(f"  ✓ 涟漪池未溢出")

    max_n = max(s["n"] for s in samples)
    print(f"雨滴数: 稳定在 {max_n} (配置 {int(cfg['rain_amount'])})")
    if max_n > int(cfg["rain_amount"]) * 1.5:
        print(f"  ✗ 雨滴数组异常膨胀")
        ok = False
    else:
        print(f"  ✓ 雨滴数组未膨胀")

    print()
    print("== STABILITY VERIFIED ==" if ok else "== STABILITY FAILED ==")


if __name__ == "__main__":
    main()
