# -*- coding: utf-8 -*-
"""雪特效专项验证：
- 60 帧有动画、无 NaN / Inf、不返回 splash
- 白雪落在纯黑底上应当可见（亮像素占比 > 阈值）
- 边界：snow_amount = 0 / 1 / 1000 都不炸
- resize 后继续正常
- 换色后缓冲确实不同（颜色键真的生效）
- 与雨同屏：雪 + 雨都出现在同一帧缓冲（组合能力）
"""
import os
os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")

import numpy as np
import desktop_fx as d
from fx_effects import RainField, SnowField, hex_to_bgr


def has_nan_inf(buf):
    a = buf.view(np.int32).reshape(-1)
    return int(np.any((a == -2147483648) | (a == 0x7F800000) | (a > 0x7F800000)))


def snow_cfg(**over):
    c = dict(d.DEFAULTS)
    c["snow_enabled"] = True
    c.update(over)
    return c


def test_render_60_frames():
    W, H = 1920, 1080
    snow = SnowField(W, H, seed=5)
    cfg = snow_cfg(snow_amount=300, snow_color="#FFFFFF", snow_opacity=0.9)
    snow.set_color(cfg["snow_color"])
    snow.set_amount(int(cfg["snow_amount"]), W, H)
    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    changed = 0
    prev = None
    for f in range(60):
        u32[:] = 0
        sp = snow.update(0.016, cfg)
        if sp.size != 0:
            return False, "snow returned splash"
        snow.draw(u32, W, H, cfg)
        if has_nan_inf(buf):
            return False, f"NaN/Inf at frame {f}"
        if f > 0:
            if np.abs(buf.astype(int) - prev.astype(int)).max() > 4:
                changed += 1
        prev = buf.copy()
    if changed < 30:
        return False, f"only {changed}/59 animated frames"
    return True, f"{changed}/59 frames animated, no NaN"


def test_visible_on_dark_bg():
    W, H = 1280, 720
    snow = SnowField(W, H, seed=3)
    cfg = snow_cfg(snow_amount=400, snow_color="#FFFFFF", snow_opacity=0.95)
    snow.set_color(cfg["snow_color"])
    snow.set_amount(int(cfg["snow_amount"]), W, H)
    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    for _ in range(40):
        snow.update(0.016, cfg)
    u32[:] = 0
    snow.draw(u32, W, H, cfg)
    lit = int((buf[..., 3] > 8).sum())
    frac = lit / (W * H)
    if frac < 0.003:
        return False, f"snow barely visible on dark bg: {frac*100:.2f}% lit"
    return True, f"{frac*100:.2f}% pixels lit on black bg (visible)"


def test_edge_amounts():
    W, H = 800, 600
    for n in (0, 1, 1000):
        snow = SnowField(W, H, seed=2)
        try:
            snow.set_amount(n, W, H)
            cfg = snow_cfg(snow_amount=n)
            buf = np.zeros((H, W, 4), np.uint8)
            u32 = buf.view(np.uint32).reshape(-1)
            for _ in range(10):
                snow.update(0.016, cfg)
                u32[:] = 0
                snow.draw(u32, W, H, cfg)
            if has_nan_inf(buf):
                return False, f"snow_amount={n} produced NaN"
        except Exception as e:
            return False, f"snow_amount={n} crashed: {e}"
    return True, "snow_amount 0/1/1000 all clean"


def test_resize():
    snow = SnowField(800, 600, seed=4)
    snow.set_amount(200, 800, 600)
    cfg = snow_cfg(snow_amount=200)
    buf = np.zeros((600, 800, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    for _ in range(10):
        snow.update(0.016, cfg)
        u32[:] = 0
        snow.draw(u32, 800, 600, cfg)
    # 改分辨率
    snow.resize(1920, 1080)
    buf2 = np.zeros((1080, 1920, 4), np.uint8)
    u32b = buf2.view(np.uint32).reshape(-1)
    try:
        for _ in range(10):
            snow.update(0.016, cfg)
            u32b[:] = 0
            snow.draw(u32b, 1920, 1080, cfg)
        if has_nan_inf(buf2):
            return False, "NaN after resize"
    except Exception as e:
        return False, f"resize crashed: {e}"
    return True, "resize 800x600 -> 1920x1080 clean"


def test_color_change():
    W, H = 800, 600
    snow = SnowField(W, H, seed=1)
    snow.set_amount(200, W, H)
    cfg_a = snow_cfg(snow_amount=200, snow_color="#FFFFFF")
    cfg_b = snow_cfg(snow_amount=200, snow_color="#FF0000")
    snow.set_color(cfg_a["snow_color"])
    buf_a = np.zeros((H, W, 4), np.uint8)
    ua = buf_a.view(np.uint32).reshape(-1)
    for _ in range(20):
        snow.update(0.016, cfg_a)
    ua[:] = 0
    snow.draw(ua, W, H, cfg_a)
    snow.set_color(cfg_b["snow_color"])
    buf_b = np.zeros((H, W, 4), np.uint8)
    ub = buf_b.view(np.uint32).reshape(-1)
    for _ in range(20):
        snow.update(0.016, cfg_b)
    ub[:] = 0
    snow.draw(ub, W, H, cfg_b)
    # 红与白在缓冲区应明显不同
    diff = np.abs(buf_a.astype(int) - buf_b.astype(int)).sum()
    if diff < 1e5:
        return False, f"color change had no visible effect (diff={diff})"
    return True, f"color key took effect (diff={int(diff)})"


def test_combo_with_rain():
    W, H = 1280, 720
    rain = RainField(W, H, seed=7)
    snow = SnowField(W, H, seed=9)
    cfg = snow_cfg(rain_enabled=True, rain_amount=200, snow_amount=200,
                   rain_color="#5FA8D8", snow_color="#FFFFFF", splash=False)
    rain.set_color(cfg["rain_color"])
    rain.set_amount(int(cfg["rain_amount"]), W, H)
    snow.set_color(cfg["snow_color"])
    snow.set_amount(int(cfg["snow_amount"]), W, H)
    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    for _ in range(20):
        rain.update(0.016, cfg)
    for _ in range(20):
        snow.update(0.016, cfg)
    u32[:] = 0
    rain.draw(u32, W, H, cfg)
    snow.draw(u32, W, H, cfg)
    # 雨偏蓝、雪偏白，混合缓冲中两种色相都应出现
    b = buf[..., 0].astype(int)
    r = buf[..., 2].astype(int)
    blueish = int(((b - r) > 30).sum())
    whiteish = int(((r > 120) & (b > 120) & (abs(r - b) < 40)).sum())
    if blueish < 50 or whiteish < 50:
        return False, f"combo missing one layer (blue={blueish}, white={whiteish})"
    return True, f"rain+ snow coexist (blue={blueish}, white={whiteish} px)"


def main():
    print("== snow effect verification ==")
    tests = [
        test_render_60_frames,
        test_visible_on_dark_bg,
        test_edge_amounts,
        test_resize,
        test_color_change,
        test_combo_with_rain,
    ]
    ok_all = True
    for fn in tests:
        try:
            ok, msg = fn()
        except Exception as e:
            ok, msg = False, f"exception: {e}"
        ok_all = ok_all and ok
        print(f"  [{'OK' if ok else 'FAIL'}] {fn.__name__:22s} {msg}")
    print()
    print("== SNOW", "VERIFIED ==" if ok_all else "FAILED ==")


if __name__ == "__main__":
    main()
