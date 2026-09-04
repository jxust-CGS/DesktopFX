# -*- coding: utf-8 -*-
"""光标拖尾 + 聚光灯 专项验证：
- 拖尾：移动光标后渲染有动画、无 NaN、缓冲里确实出现沿轨迹的亮点
- 拖尾边界：trail_length 1 / 60 不炸，静止后缓冲逐渐变空
- 聚光灯：全屏被压暗（多数像素有暗色 alpha），光标处挖出透明圆洞
- 聚光灯包围盒优化：远离光标的像素是"均匀暗色"，只有洞口附近逐像素变化
- 组合：雨 + 聚光灯 + 拖尾 同帧出现，三层都在缓冲里
- resize 后两类特效继续正常
"""
import os
os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")

import numpy as np

import desktop_fx as d
from fx_effects import (RainField, CursorTrailField, SpotlightField,
                        hex_to_bgr)
from fx_scene import Scene


def has_nan_inf(buf):
    a = buf.view(np.int32).reshape(-1)
    return int(np.any((a == -2147483648) | (a == 0x7F800000) | (a > 0x7F800000)))


def layer_cfg(**over):
    c = dict(d.DEFAULTS)
    c.update(over)
    return c


def _fresh(W, H):
    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    return buf, u32


# ------------------------------------------------------------------ 拖尾
def test_trail_render():
    W, H = 1280, 720
    trail = CursorTrailField(W, H, seed=13)
    cfg = layer_cfg(trail_enabled=True, trail_opacity=0.85, trail_color="#5FD0FF")
    trail.set_color(cfg["trail_color"])
    trail.set_length(24)
    trail.set_size(1.6)
    buf, u32 = _fresh(W, H)
    cx, cy = 100.0, 100.0
    changed = 0
    prev = None
    for f in range(60):
        # 沿对角线匀速移动光标，速度恒定
        cx = min(W - 30, cx + 11.0)
        cy = min(H - 30, cy + 6.0)
        trail.feed(cx, cy, speed=620.0)
        u32[:] = 0
        trail.update(0.016, cfg)
        trail.draw(u32, W, H, cfg)
        if has_nan_inf(buf):
            return False, f"NaN/Inf at frame {f}"
        if f > 0 and np.abs(buf.astype(int) - prev.astype(int)).max() > 4:
            changed += 1
        prev = buf.copy()
    lit = int((buf[..., 3] > 8).sum())
    if lit < 50:
        return False, f"trail barely visible (lit={lit})"
    if changed < 30:
        return False, f"trail too few animated frames {changed}/59"
    return True, f"{changed}/59 frames animated, {lit} lit px, no NaN"


def test_trail_edge():
    W, H = 640, 480
    for n in (1, 60):
        try:
            trail = CursorTrailField(W, H, seed=2)
            trail.set_color("#FFFFFF")
            trail.set_length(n)
            cfg = layer_cfg(trail_enabled=True, trail_opacity=0.8)
            buf, u32 = _fresh(W, H)
            for f in range(20):
                trail.feed(100 + f * 3, 200 + f * 2, speed=400.0)
                u32[:] = 0
                trail.update(0.016, cfg)
                trail.draw(u32, W, H, cfg)
            if has_nan_inf(buf):
                return False, f"trail_length={n} produced NaN"
        except Exception as e:
            return False, f"trail_length={n} crashed: {e}"
    # 静止后不应无限堆积亮点：先喂满再长时间不动
    trail = CursorTrailField(W, H, seed=2)
    trail.set_length(24)
    trail.set_color("#FFFFFF")
    cfg = layer_cfg(trail_opacity=0.8)
    buf, u32 = _fresh(W, H)
    for _ in range(24):
        trail.feed(300, 240, speed=0.0)
    u32[:] = 0
    trail.update(0.016, cfg)
    trail.draw(u32, W, H, cfg)
    lit_busy = int((buf[..., 3] > 8).sum())
    for _ in range(60):   # 长时间不动，但 feed 仍在同一点 -> 拖尾应缩成一个点
        trail.feed(300, 240, speed=0.0)
        u32[:] = 0
        trail.update(0.016, cfg)
        trail.draw(u32, W, H, cfg)
    lit_idle = int((buf[..., 3] > 8).sum())
    if lit_idle > lit_busy * 2 + 50:
        return False, f"idle trail grew unexpectedly (busy={lit_busy}, idle={lit_idle})"
    return True, f"trail_length 1/60 clean; idle collapses (busy={lit_busy}->idle={lit_idle})"


def test_trail_resize():
    trail = CursorTrailField(800, 600, seed=4)
    trail.set_length(20)
    trail.set_color("#5FD0FF")
    cfg = layer_cfg(trail_opacity=0.8)
    buf, u32 = _fresh(800, 600)
    for _ in range(10):
        trail.feed(120, 90, speed=400.0)
        u32[:] = 0
        trail.update(0.016, cfg)
        trail.draw(u32, 800, 600, cfg)
    trail.resize(1920, 1080)
    buf2, u32b = _fresh(1920, 1080)
    try:
        for _ in range(10):
            trail.feed(900, 540, speed=400.0)
            u32b[:] = 0
            trail.update(0.016, cfg)
            trail.draw(u32b, 1920, 1080, cfg)
        if has_nan_inf(buf2):
            return False, "trail NaN after resize"
    except Exception as e:
        return False, f"trail resize crashed: {e}"
    return True, "trail resize 800x600 -> 1920x1080 clean"


# ------------------------------------------------------------------ 聚光灯
def test_spotlight_render():
    W, H = 1200, 800
    spot = SpotlightField(W, H, seed=17)
    cfg = layer_cfg(spotlight_enabled=True, spotlight_radius=200,
                    spotlight_dim=0.6, spotlight_color="#05070A")
    spot.set_color(cfg["spotlight_color"])
    spot.set_radius(cfg["spotlight_radius"])
    spot.set_dim(cfg["spotlight_dim"])
    spot.set_cursor(600.0, 400.0)
    buf, u32 = _fresh(W, H)
    spot.draw(u32, W, H, cfg)

    alpha = buf[..., 3].astype(int)
    dim_a = int(round(0.6 * 255))          # 期望的均匀暗色 alpha
    # 中心洞（透明）
    center_a = int(alpha[400, 600])
    # 远角（均匀暗色）
    corner_a = int(alpha[10, 10])
    # 洞口附近（d≈140，处在柔化区间内）
    near_a = int(alpha[400, 740])          # d=140 < 200

    if center_a > 12:
        return False, f"spotlight hole not transparent at cursor (a={center_a})"
    if abs(corner_a - dim_a) > 6:
        return False, f"corner not uniform dim (corner={corner_a}, expect~{dim_a})"
    if not (center_a < near_a < corner_a):
        return False, f"hole falloff wrong (center={center_a}, near={near_a}, corner={corner_a})"
    # 多数像素应被压暗
    dimmed = int((alpha > 50).sum())
    if dimmed < W * H * 0.5:
        return False, f"too few dimmed pixels ({dimmed/(W*H)*100:.1f}%)"
    if has_nan_inf(buf):
        return False, "spotlight produced NaN"
    return True, (f"hole transparent(a={center_a}), "
                  f"falloff center<near<corner ({center_a}<{near_a}<{corner_a}), "
                  f"dimmed {dimmed/(W*H)*100:.0f}% screen")


def test_spotlight_box_only():
    """远离光标的像素应是均匀暗色（包围盒外只铺一层常量），
    确认逐像素计算只发生在洞口附近的包围盒内。"""
    W, H = 1000, 700
    spot = SpotlightField(W, H, seed=17)
    spot.set_color("#04060A")
    spot.set_radius(150)
    spot.set_dim(0.5)
    spot.set_cursor(500.0, 350.0)
    buf, u32 = _fresh(W, H)
    spot.draw(u32, W, H, dict(d.DEFAULTS))
    alpha = buf[..., 3].astype(int)
    # 远离光标（左上角、右下角）两像素应当相等（均匀暗色）
    a1 = int(alpha[5, 5])
    a2 = int(alpha[H - 5, W - 5])
    if a1 != a2:
        return False, f"far corners differ ({a1} vs {a2}); box optimization broken?"
    dim_a = int(round(0.5 * 255))
    if abs(a1 - dim_a) > 6:
        return False, f"far pixel not uniform dim (a={a1}, expect~{dim_a})"
    return True, f"far corners uniform dim (a={a1}); per-pixel only inside box"


def test_spotlight_resize():
    spot = SpotlightField(800, 600, seed=5)
    spot.set_color("#05070A")
    spot.set_radius(180)
    spot.set_dim(0.6)
    spot.set_cursor(400, 300)
    buf, u32 = _fresh(800, 600)
    spot.draw(u32, 800, 600, dict(d.DEFAULTS))
    if has_nan_inf(buf):
        return False, "spotlight NaN before resize"
    spot.resize(1920, 1080)
    spot.set_cursor(960, 540)
    buf2, u32b = _fresh(1920, 1080)
    try:
        spot.draw(u32b, 1920, 1080, dict(d.DEFAULTS))
        if has_nan_inf(buf2):
            return False, "spotlight NaN after resize"
    except Exception as e:
        return False, f"spotlight resize crashed: {e}"
    if int(buf2[540, 960, 3]) > 12:
        return False, "spotlight hole lost after resize"
    return True, "spotlight resize 800x600 -> 1920x1080 clean"


# ------------------------------------------------------------------ 组合
def test_combo_rain_spot_trail():
    W, H = 1280, 720
    rain = RainField(W, H, seed=7)
    trail = CursorTrailField(W, H, seed=13)
    spot = SpotlightField(W, H, seed=17)
    cfg = layer_cfg(rain_enabled=True, rain_amount=200, rain_color="#5FA8D8",
                    rain_opacity=0.85,
                    spotlight_enabled=True, spotlight_radius=220,
                    spotlight_dim=0.6, spotlight_color="#05070A",
                    trail_enabled=True, trail_opacity=0.85, trail_color="#5FD0FF")
    rain.set_color(cfg["rain_color"])
    rain.set_amount(int(cfg["rain_amount"]), W, H)
    trail.set_color(cfg["trail_color"])
    trail.set_length(24)
    trail.set_size(1.6)
    spot.set_color(cfg["spotlight_color"])
    spot.set_radius(cfg["spotlight_radius"])
    spot.set_dim(cfg["spotlight_dim"])

    buf, u32 = _fresh(W, H)
    for _ in range(20):
        rain.update(0.016, cfg)
    for _ in range(20):
        trail.feed(400 + _ * 10, 300, speed=500.0)
    spot.set_cursor(640.0, 360.0)
    u32[:] = 0
    # 顺序与 Scene.step 一致：聚光灯 -> 雨 -> 拖尾
    spot.draw(u32, W, H, cfg)
    rain.draw(u32, W, H, cfg)
    trail.feed(640, 360, speed=500.0)
    trail.update(0.016, cfg)
    trail.draw(u32, W, H, cfg)

    alpha = buf[..., 3].astype(int)
    # 聚光灯：大部分屏幕被压暗
    dimmed = int((alpha > 60).sum())
    # 雨：蓝色雨丝出现
    b = buf[..., 0].astype(int)
    r = buf[..., 2].astype(int)
    blueish = int(((b - r) > 30).sum())
    # 拖尾：青蓝亮点（在 (640,360) 附近应有亮像素）
    g = buf[..., 1].astype(int)
    trailish = int(((g - r) > 30).sum())
    if dimmed < W * H * 0.4:
        return False, f"combo: spotlight dim missing (dimmed={dimmed/(W*H)*100:.1f}%)"
    if blueish < 40:
        return False, f"combo: rain missing (blue={blueish})"
    if trailish < 10:
        return False, f"combo: trail missing (trailish={trailish})"
    if has_nan_inf(buf):
        return False, "combo produced NaN"
    return True, (f"rain+spotlight+trail coexist "
                  f"(dimmed {dimmed/(W*H)*100:.0f}%, blue={blueish}, trail={trailish})")


def test_scene_step_all_presets():
    """端到端：用 App 真实的 Scene.step 管线（与渲染循环同一份代码）逐帧跑
    全部预设，确认拖尾/聚光灯接入后没有任何 KeyError / 崩溃 / NaN。
    无需真实窗口，因此可在无桌面的环境里验证整条链路。"""
    W, H = 1280, 720
    scene = Scene(W, H)
    scene.set_origin(0, 0)
    buf, u32 = _fresh(W, H)
    # 先让鼠标"动起来"，否则拖尾/涟漪毫无输入
    scene.mouse.x = scene.mouse.prev_x = 100
    scene.mouse.y = scene.mouse.prev_y = 100
    for name, p in d.PRESETS.items():
        cfg = dict(d.DEFAULTS)
        cfg.update(p)
        scene.cfg = cfg
        try:
            for f in range(20):
                # 模拟光标每帧移动一点
                scene.mouse.x = scene.mouse.prev_x + 9
                scene.mouse.y = scene.mouse.prev_y + 4
                u32[:] = 0
                scene.step(0.016, u32, W, H)
                if has_nan_inf(buf):
                    return False, f"{name}: NaN/Inf during Scene.step"
        except Exception as e:
            return False, f"{name}: Scene.step crashed: {e}"
    return True, f"all {len(d.PRESETS)} presets stepped via Scene.step, no error/NaN"


def main():
    print("== cursor trail + spotlight verification ==")
    tests = [
        test_trail_render,
        test_trail_edge,
        test_trail_resize,
        test_spotlight_render,
        test_spotlight_box_only,
        test_spotlight_resize,
        test_combo_rain_spot_trail,
        test_scene_step_all_presets,
    ]
    ok_all = True
    for fn in tests:
        try:
            ok, msg = fn()
        except Exception as e:
            ok, msg = False, f"exception: {e}"
        ok_all = ok_all and ok
        print(f"  [{'OK' if ok else 'FAIL'}] {fn.__name__:26s} {msg}")
    print()
    print("== LAYERS", "VERIFIED ==" if ok_all else "FAILED ==")


if __name__ == "__main__":
    main()
