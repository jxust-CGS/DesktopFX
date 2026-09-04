# -*- coding: utf-8 -*-
"""量化雨滴在浅色 / 深色背景下的可见度，并报告动画幅度。

预设数据一律从 desktop_fx 真实导入，此处不做任何副本 ——
副本会和产品配色漂移，最后变成一份看着像真、其实是假的参考。
"""
import numpy as np
from fx_effects import RainField, RippleField, hex_to_bgr

# 硬门槛：任何预设（含默认配置）在两种底色上都必须达到这个分数。
# 低于 50 意味着"在白桌面/深色桌面上基本看不见"，等于该预设不可用。
MIN_SCORE = 50


def composite_over(bgra, bg_rgb):
    """BGRA premultiplied over solid bg -> RGB uint8."""
    a = bgra[..., 3:4].astype(np.float32) / 255.0
    out = bgra[..., :3].astype(np.float32) + bg_rgb[None, None, :].astype(np.float32) * (1.0 - a)
    return np.clip(out, 0, 255).astype(np.uint8)


def metrics(rgb, bg_rgb):
    diff = rgb.astype(np.int32) - bg_rgb[None, None, :].astype(np.int32)
    delta = np.abs(diff).max(axis=2)
    pixels_changed = (delta > 4).sum()
    mean_delta = delta[delta > 4].mean() if pixels_changed else 0.0
    return pixels_changed, mean_delta, delta.max()


def main():
    W, H = 1920, 1080
    rain = RainField(W, H, seed=1)
    rain.set_color("#BFE3FF")

    # 两个典型桌面底色：纯白（Windows 默认桌面） 和 深夜深色
    white = np.array([255, 255, 255], np.uint8)
    dark  = np.array([24, 28, 36], np.uint8)

    # 模拟 30 帧、取中段
    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    rain.set_amount(280, W, H)
    cfg = dict(rain_amount=280, rain_speed=900, rain_length=1.0, wind=0.12,
               rain_opacity=0.75, rain_color="#BFE3FF")
    rain.set_color(cfg["rain_color"])
    for _ in range(15):
        rain.update(0.016, cfg)
    for _ in range(2):
        rain.draw(u32, W, H, cfg)

    def report(preset_name, cfg):
        nonlocal buf, u32
        buf.fill(0)
        rain.set_color(cfg["rain_color"])
        rain.set_amount(int(cfg["rain_amount"]), W, H)
        for _ in range(20):
            rain.update(0.016, cfg)
        for _ in range(2):
            rain.draw(u32, W, H, cfg)

        over_white = composite_over(buf, white)
        over_dark  = composite_over(buf, dark)

        pw, mw, xw = metrics(over_white, white)
        pd, md, xd = metrics(over_dark,  dark)

        pct_w = pw / (W * H) * 100
        pct_d = pd / (W * H) * 100
        sw = min(100, int(mw * 0.7 + xw * 0.2))
        sd = min(100, int(md * 0.7 + xd * 0.2))
        print(f"{preset_name:14s} | 浅底  改色像素 {pct_w:5.1f}%  均差 {mw:5.1f}  最大 {xw:3d}  "
              f"| 深底  改色像素 {pct_d:5.1f}%  均差 {md:5.1f}  最大 {xd:3d}  "
              f"| 可见度评分(浅/深) {sw:3d}/{sd:3d}")
        return sw, sd

    print(f"{'preset':14s} | 浅色桌面 (RGB 255,255,255)                "
          f"| 深色桌面 (RGB 24,28,36)")
    print("-" * 130)

    # 预设与默认值一律从产品代码真实导入，保证测的就是用户会拿到的那一份
    from desktop_fx import DEFAULTS as APP_DEFAULTS, PRESETS as APP_PRESETS

    failures = []
    sw, sd = report("【默认】", dict(APP_DEFAULTS))
    if min(sw, sd) < MIN_SCORE:
        failures.append(("默认配置", sw, sd))
    for name, p in APP_PRESETS.items():
        cfg = dict(APP_DEFAULTS)
        cfg.update(p)          # 稀疏预设（如仅雪）也要有完整默认键
        sw, sd = report(name, cfg)
        if min(sw, sd) < MIN_SCORE:
            failures.append((name, sw, sd))

    print("-" * 130)
    print(f"门槛: 两种底色都需 >= {MIN_SCORE} 分")
    if failures:
        print(f"[FAIL] {len(failures)} 个预设在某些底色上几乎看不见:")
        for name, sw, sd in failures:
            which = []
            if sw < MIN_SCORE:
                which.append(f"浅底 {sw}")
            if sd < MIN_SCORE:
                which.append(f"深底 {sd}")
            print(f"       {name}: {' / '.join(which)}")
        print("\n== VISIBILITY FAILED ==")
        raise SystemExit(1)
    print(f"[OK] 默认配置 + 全部 {len(APP_PRESETS)} 个预设在浅底 / 深底均达标")
    print("\n== VISIBILITY VERIFIED ==")


if __name__ == "__main__":
    main()
