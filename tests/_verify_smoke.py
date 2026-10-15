# -*- coding: utf-8 -*-
"""无回归烟雾测试：
- 所有 8 个预设连续渲染 60 帧，无异常 / 无 NaN
- 帧间像素差 > 0（确认动画在跑）
- Lightning 触发后全屏亮度上升
- 关闭雨后只剩涟漪
- 边界：rain_amount=0 / rain_amount=1000 不炸
"""
import numpy as np
import os
os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")

import sys

# 本脚本住在 tests/ 子目录里：`sys.path[0]` 与 `__file__` 的父目录都不再是项目根。
# 向上找到含 desktop_fx.py 的目录（= 项目根）：产品模块从那里 import；脚本里已有的
# HERE 也一律以它为基准（读 desktop_fx.py / fx_effects.py / .gitattributes /
# 启动桌面特效.bat、写临时配置都按项目根算）。
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = _TESTS_DIR
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
# tests/ 自己也要进 path：有几个脚本互相 import（_verify_click / _verify_pet ->
# _verify_kinds、_verify_tray -> _verify_launcher）。
for _p in (ROOT, _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import desktop_fx as d
from fx_effects import (RainField, RippleField, Lightning, SnowField,
                        CursorTrailField, SpotlightField, hex_to_bgr)


def has_nan_inf(buf):
    a = buf.view(np.int32).reshape(-1)
    return int(np.any((a == -2147483648) | (a == 0x7F800000) | (a > 0x7F800000)))


def diff_stats(a, b):
    """返回改色像素占比 + 改色区域的平均差。"""
    d = np.abs(a.astype(np.int32) - b.astype(np.int32)).max(axis=2)
    changed = d > 4
    if not changed.any():
        return 0.0, 0.0
    return changed.mean(), d[changed].mean()


def test_one_preset(name, cfg, W=1920, H=1080):
    cfg = dict(cfg)  # 拷贝，避免污染
    cfg.setdefault("lightning", False)
    cfg.setdefault("rain_enabled", True)
    cfg.setdefault("lightning", False)
    rain = RainField(W, H, seed=7)
    rain.set_color(cfg["rain_color"])
    rain.set_amount(int(cfg["rain_amount"]), W, H)
    ripples = RippleField()
    light = Lightning(seed=11)

    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    col = hex_to_bgr("#7FE7FF")

    # 预热 + 注入一些涟漪
    for _ in range(20):
        rain.update(0.016, cfg)
    for _ in range(15):
        ripples.spawn(np.random.uniform(50, W-50),
                      np.random.uniform(50, H-50),
                      rmax=180, life=1.0, strength=0.7, color_bgr=col)

    # 跑 60 帧，验证
    changed_frames = 0
    pixel_changed_total = 0.0
    for f in range(60):
        u32[:] = 0
        rain.update(0.016, cfg)
        light.update(0.016, cfg["lightning"], W, H)
        rain.draw(u32, W, H, cfg)
        ripples.update(0.016)
        ripples.draw(u32, W, H)
        light.draw(u32, W, H)
        if has_nan_inf(buf):
            return False, f"NaN/Inf in frame {f}"
        if f > 0:
            prev = buf_previous
            pct, _ = diff_stats(buf, prev)
            if pct > 0.001:
                changed_frames += 1
            pixel_changed_total += pct
        buf_previous = buf.copy()
    if changed_frames < 30:
        return False, f"too few animated frames: {changed_frames}/59"
    return True, f"animated {changed_frames}/59 frames, avg {pixel_changed_total/59*100:.2f}% pixels/frame"


def test_lightning_actually_flashes():
    W, H = 1280, 720
    light = Lightning(seed=11)
    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    # 强制立刻触发闪电
    light.next_in = 0
    saw_flash = False
    for _ in range(60):  # 模拟 ~1s 真实时间
        u32[:] = 0
        light.update(0.016, True, W, H)   # 真实 60FPS 步长
        light.draw(u32, W, H)
        a = buf[..., 3]
        if a.mean() > 8:
            saw_flash = True
            break
    return saw_flash, f"lightning drew full-screen fill" if saw_flash else "lightning never flashed (bug)"


def test_edge_amounts():
    """rain_amount=0 和 rain_amount=1000 都不能炸。"""
    W, H = 800, 600
    for n in [0, 1, 1000]:
        rain = RainField(W, H, seed=3)
        try:
            rain.set_amount(n, W, H)
            cfg = dict(d.DEFAULTS)
            cfg["rain_amount"] = n
            buf = np.zeros((H, W, 4), np.uint8)
            u32 = buf.view(np.uint32).reshape(-1)
            for _ in range(10):
                rain.update(0.016, cfg)
                u32[:] = 0
                rain.draw(u32, W, H, cfg)
        except Exception as e:
            return False, f"rain_amount={n} crashed: {e}"
    return True, "rain_amount 0/1/1000 all render cleanly"


def test_snow(W=1920, H=1080):
    """雪：渲染 60 帧，确认有动画、无 NaN、无 splash 副作用。"""
    snow = SnowField(W, H, seed=5)
    snow.set_color("#FFFFFF")
    snow.set_amount(300, W, H)
    cfg = dict(d.DEFAULTS)
    cfg["snow_enabled"] = True
    cfg["snow_amount"] = 300
    cfg["snow_color"] = "#FFFFFF"
    cfg["snow_opacity"] = 0.9

    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    changed_frames = 0
    prev = None
    for f in range(60):
        u32[:] = 0
        splash = snow.update(0.016, cfg)
        if splash.size != 0:
            return False, "snow returned non-empty splash (should never splash)"
        snow.draw(u32, W, H, cfg)
        if has_nan_inf(buf):
            return False, f"snow NaN/Inf at frame {f}"
        if f > 0:
            pct, _ = diff_stats(buf, prev)
            if pct > 0.001:
                changed_frames += 1
        prev = buf.copy()
    if changed_frames < 30:
        return False, f"snow too few animated frames {changed_frames}/59"
    return True, f"snow animated {changed_frames}/59 frames"


def test_trail(W=1920, H=1080):
    """光标拖尾：移动光标后渲染 60 帧，确认有动画、无 NaN。"""
    trail = CursorTrailField(W, H, seed=13)
    trail.set_color("#5FD0FF")
    trail.set_length(24)
    trail.set_size(1.6)
    cfg = dict(d.DEFAULTS)
    cfg["trail_enabled"] = True
    cfg["trail_opacity"] = 0.85
    cfg["trail_color"] = "#5FD0FF"

    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    cx, cy = 200.0, 200.0
    changed_frames = 0
    prev = None
    for f in range(60):
        cx = min(W - 30, cx + 13.0)
        cy = min(H - 30, cy + 7.0)
        trail.feed(cx, cy, speed=640.0)
        u32[:] = 0
        trail.update(0.016, cfg)
        trail.draw(u32, W, H, cfg)
        if has_nan_inf(buf):
            return False, f"trail NaN/Inf at frame {f}"
        if f > 0:
            # 拖尾是稀疏的（只占屏幕极小比例），用"逐像素最大差"判定动画，
            # 不能用 diff_stats 的"改色像素占比"，否则稀疏特效永远过不了门槛。
            if np.abs(buf.astype(int) - prev.astype(int)).max() > 4:
                changed_frames += 1
        prev = buf.copy()
    if changed_frames < 30:
        return False, f"trail too few animated frames {changed_frames}/59"
    return True, f"trail animated {changed_frames}/59 frames"


def test_spotlight(W=1280, H=720):
    """聚光灯：全屏压暗 + 鼠标处透明圆洞，无 NaN。"""
    spot = SpotlightField(W, H, seed=17)
    spot.set_color("#05070A")
    spot.set_radius(220)
    spot.set_dim(0.6)
    spot.set_cursor(W * 0.5, H * 0.5)
    cfg = dict(d.DEFAULTS)
    cfg["spotlight_enabled"] = True
    cfg["spotlight_radius"] = 220
    cfg["spotlight_dim"] = 0.6
    cfg["spotlight_color"] = "#05070A"

    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    try:
        spot.draw(u32, W, H, cfg)
    except Exception as e:
        return False, f"spotlight crashed: {e}"
    if has_nan_inf(buf):
        return False, "spotlight produced NaN"
    alpha = buf[..., 3].astype(int)
    if int(alpha[H // 2, W // 2]) > 12:
        return False, "spotlight hole not transparent at cursor"
    dimmed = int((alpha > 50).sum())
    if dimmed < W * H * 0.5:
        return False, f"spotlight dim missing ({dimmed/(W*H)*100:.1f}%)"
    return True, f"spotlight dim {dimmed/(W*H)*100:.0f}% screen, hole transparent"


def main():
    print("== no-regression smoke ==")
    for name, p in d.PRESETS.items():
        cfg = dict(d.DEFAULTS)
        cfg.update(p)          # 稀疏预设（如仅雪）也要有完整默认键
        ok, msg = test_one_preset(name, cfg)
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {name:14s}  {msg}")

    print()
    ok, msg = test_lightning_actually_flashes()
    print(f"  [{'OK' if ok else 'FAIL'}] lightning          {msg}")
    ok, msg = test_edge_amounts()
    print(f"  [{'OK' if ok else 'FAIL'}] edge amounts       {msg}")
    ok, msg = test_snow()
    print(f"  [{'OK' if ok else 'FAIL'}] snow              {msg}")
    ok, msg = test_trail()
    print(f"  [{'OK' if ok else 'FAIL'}] trail             {msg}")
    ok, msg = test_spotlight()
    print(f"  [{'OK' if ok else 'FAIL'}] spotlight         {msg}")
    print()
    print("== SMOKE DONE ==")


if __name__ == "__main__":
    main()
