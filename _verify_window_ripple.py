# -*- coding: utf-8 -*-
"""窗口水波验证：
- WindowProbe._diff 正确识别 move / minimize / restore / 静止 / 新建窗口
- Scene.step 注入合成事件：移动时沿窗缘发波、停止后触发松手大爆发、
  最小化/恢复各触发一次爆发
- 全程无 NaN / Inf
"""
import numpy as np

import desktop_fx as d
from fx_effects import WindowProbe, hex_to_bgr
from fx_scene import Scene


def has_nan_inf(buf):
    a = buf.view(np.int32).reshape(-1)
    return bool(np.any((a == -2147483648) | (a == 0x7F800000) | (a > 0x7F800000)))


def base_cfg():
    cfg = dict(d.DEFAULTS)
    cfg["rain_enabled"] = False
    cfg["snow_enabled"] = False
    cfg["win_ripple_enabled"] = True
    cfg["win_ripple_edge_density"] = 12
    cfg["win_ripple_strength"] = 0.8
    return cfg


def test_probe_diff():
    p = WindowProbe()
    p.set_origin(0, 0)
    # 移动
    ev = p._diff({1: ((0, 0, 100, 100), False)},
                 {1: ((12, 8, 112, 108), False)})
    if not ev or ev[0][0] != "move":
        return False, f"move not detected: {ev}"
    # 极小位移不应触发
    ev = p._diff({1: ((0, 0, 100, 100), False)},
                 {1: ((1, 1, 101, 101), False)})
    if ev:
        return False, f"tiny move wrongly triggered: {ev}"
    # 最小化
    ev = p._diff({1: ((0, 0, 100, 100), False)},
                 {1: ((0, 0, 100, 100), True)})
    if not ev or ev[0][0] != "minimize":
        return False, f"minimize not detected: {ev}"
    # 恢复
    ev = p._diff({1: ((0, 0, 100, 100), True)},
                 {1: ((0, 0, 100, 100), False)})
    if not ev or ev[0][0] != "restore":
        return False, f"restore not detected: {ev}"
    # 静止不动不触发
    ev = p._diff({1: ((0, 0, 100, 100), False)},
                 {1: ((0, 0, 100, 100), False)})
    if ev:
        return False, f"static window wrongly triggered: {ev}"
    # 新窗口（open）不触发
    ev = p._diff({}, {1: ((0, 0, 100, 100), False)})
    if ev:
        return False, f"new window wrongly triggered: {ev}"
    return True, "probe diff move/minimize/restore/static/open all correct"


def test_scene_move_release(W=900, H=560):
    scene = Scene(W, H)
    scene.cfg = base_cfg()
    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    rect = (120, 100, 420, 320)

    before = int(scene.ripples.active.sum())
    for _ in range(5):
        scene._win_events = [("move", rect)]
        scene.step(0.016, u32, W, H)
    mid = int(scene.ripples.active.sum())
    if mid <= before:
        return False, f"move did not spawn ripples (before={before}, mid={mid})"
    if has_nan_inf(buf):
        return False, "NaN/Inf during move"

    # 停止移动 -> 应触发松手大爆发（累计 > 0.2s 超过释放阈值）
    scene._win_events = []
    for _ in range(20):
        scene.step(0.05, u32, W, H)
    if has_nan_inf(buf):
        return False, "NaN/Inf during release"
    if scene._win_moving:
        return False, "still in moving state after stop"
    return True, f"move spawned {mid - before} ripples, release cleared state, no NaN"


def test_scene_minimize_restore(W=900, H=560):
    for et in ("minimize", "restore"):
        scene = Scene(W, H)
        scene.cfg = base_cfg()
        buf = np.zeros((H, W, 4), np.uint8)
        u32 = buf.view(np.uint32).reshape(-1)
        before = int(scene.ripples.active.sum())
        scene._win_events = [(et, (120, 100, 420, 320))]
        scene.step(0.016, u32, W, H)
        after = int(scene.ripples.active.sum())
        if after <= before:
            return False, f"{et} did not spawn ripple (before={before}, after={after})"
        if has_nan_inf(buf):
            return False, f"{et} produced NaN"
    return True, "minimize & restore both spawn splash ripples"


def test_null_foreground_window():
    """GetForegroundWindow() 在焦点位于桌面 / 切换窗口瞬间会返回 NULL，
    而它的 restype 是 c_void_p —— ctypes 把 NULL 变成 None。曾经的写法
    `int(_GetForegroundWindow())` 于是每帧抛 TypeError，把渲染循环打穿
    （error.log 里成片的 `render: TypeError ... not 'NoneType'`）。
    """
    import fx_effects as fe

    p = fe.WindowProbe()
    p.set_origin(0, 0)
    fake = {1: ((10, 10, 110, 110), False)}
    p._prev = dict(fake)
    orig_enum = p._enumerate
    orig_fg = fe._GetForegroundWindow
    p._enumerate = lambda: dict(fake)
    try:
        fe._GetForegroundWindow = lambda: None      # 模拟 NULL 前台窗口
        p._acc = 1.0
        p.update(0.2)
    finally:
        fe._GetForegroundWindow = orig_fg
        p._enumerate = orig_enum
    return True, "NULL foreground window handled without raising"


def main():
    print("== window ripple verification ==")
    tests = [test_probe_diff, test_scene_move_release, test_scene_minimize_restore,
             test_null_foreground_window]
    ok_all = True
    for t in tests:
        try:
            ok, msg = t()
        except Exception as e:
            ok, msg = False, f"exception: {type(e).__name__}: {e}"
        ok_all = ok_all and ok
        print(f"  [{'OK' if ok else 'FAIL'}] {t.__name__:30s} {msg}")
    if ok_all:
        print("\n== WINDOW RIPPLE VERIFIED ==")
    else:
        print("\n== WINDOW RIPPLE FAILED ==")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
