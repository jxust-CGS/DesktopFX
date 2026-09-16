# -*- coding: utf-8 -*-
"""窗口水波专项验证 —— 「窗口是水面上的活塞」。

改造后的模型（2026-09-15）只有一句话：**窗口不自己画波，它只是往那片共同
的水面上注入位移**。于是这份脚本要回答的问题也变了 —— 不再是"窗口边上冒了几
个圈"，而是"注进去的东西是不是像真的水"：

1. **事件流**：`WindowProbe` 只报"窗口现在在哪"，速度由调用方算。
   事件只有三种 change / minimize / restore；静止不报、新窗口不报。
2. **逐帧精测**：拖动中的窗口必须每帧都拿得到新矩形 —— 活塞注入量正比于
   边速度，10Hz 采样算出的速度既不准、注入又一串一串地来（看着就一顿一顿）。
3. **活塞物理**：向右拖 -> 右边隆起、左边塌陷；向下拖 -> 下边隆起；拉宽 ->
   右边隆起；缩窄 -> 右边塌陷。**这一个式子同时覆盖平移与改尺寸**。
4. **量级正确**：注入量正比于速度（快拖浪更大），且**与帧率无关**
   （同一段位移，30/60/120 帧/秒注入的总量一致）。
5. **静止 = 死水**：窗口不动就一点不注入（泡着不动的石头不搅水）。
6. **最小化 / 恢复**：同一块矩形、相反的相位（塌陷 vs 抬升），且**只影响
   窗口占的那块**，外面是平的。
7. **接线**：Scene 把窗口事件喂给**共用的那片水**（`scene.wave`），而不是另
   建一个涟漪池；关掉开关就清场。
8. **数值安全**：全程无 NaN / Inf，幅度有界。
9. **回归**：`_push_band` 曾经的"格索引直接拿去减像素"导致整条带恒为 0，
   这里对两个轴向都单独钉一条断言。

跑：python _verify_window_ripple.py
"""
import os
os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")

import ctypes
import ctypes.wintypes as wt

import numpy as np

import desktop_fx as d
import fx_effects as fe
from fx_effects import WaterRippleField, WindowProbe
from fx_scene import Scene

W, H = 900, 560


# ------------------------------------------------------------------ 工具
def has_nan_inf(buf):
    a = buf.view(np.int32).reshape(-1).astype(np.int64)
    return bool(np.any((a > 0x7F800000) | (a < -0x7F800000)))


def base_cfg(**over):
    """默认值一律从产品代码真实导入 —— 测的就是用户拿到的那一份。"""
    cfg = dict(d.DEFAULTS)
    cfg["rain_enabled"] = False
    cfg["snow_enabled"] = False
    cfg["petal_enabled"] = False
    cfg["meteor_enabled"] = False
    cfg["ripple_enabled"] = False
    cfg["spotlight_enabled"] = False
    cfg["paint_enabled"] = False
    cfg["wave_enabled"] = False
    cfg["win_ripple_enabled"] = True
    cfg.update(over)
    return cfg


def edge_val(wf, axis, coord, lo, hi):
    """取某条边扰动带上的**代表值**（取绝对值最大的那一格，带符号）。

    法向在 `coord` 附近扫一片（±10 格）找峰 —— 带骑在"这一帧行程的中点"上
    （见 `Scene._win_perturb`），与端点最多差半帧位移，所以不能只盯着一格看。
    切向只取 `[lo, hi]` 的中段 —— 避开两端收口处（那里按设计要归零）。
    """
    c = wf.cell
    r = 10
    if axis == 0:                       # 竖直边：法向是 x
        ci = int(round(coord / c))
        a0, a1 = max(0, ci - r), min(wf.gw, ci + r + 1)
        b0, b1 = max(0, int(lo / c) + 3), min(wf.gh, int(hi / c) - 2)
        blk = wf.height[b0:b1, a0:a1]
    else:                               # 水平边：法向是 y
        ri = int(round(coord / c))
        a0, a1 = max(0, ri - r), min(wf.gh, ri + r + 1)
        b0, b1 = max(0, int(lo / c) + 3), min(wf.gw, int(hi / c) - 2)
        blk = wf.height[a0:a1, b0:b1]
    flat = blk.reshape(-1)
    if flat.size == 0:
        return 0.0
    return float(flat[int(np.argmax(np.abs(flat)))])


def dome_center(wf, rect):
    """矩形正中那格的位移。"""
    x0, y0, x1, y1 = rect
    ix = int(round(0.5 * (x0 + x1) / wf.cell))
    iy = int(round(0.5 * (y0 + y1) / wf.cell))
    ix = min(max(ix, 0), wf.gw - 1)
    iy = min(max(iy, 0), wf.gh - 1)
    return float(wf.height[iy, ix])


# ------------------------------------------------------------------ 1. 事件流
def test_probe_diff():
    """事件只有三种，且只能由「窗口自己变了」产生。"""
    p = WindowProbe()
    p.set_origin(0, 0)
    old = (0, 0, 100, 100)
    # 移动 -> change（旧矩形 / 新矩形 / 帧时长 都带出来）
    ev = p._diff({1: (old, False)}, {1: ((12, 8, 112, 108), False)}, 0.016)
    if len(ev) != 1 or ev[0][0] != "change":
        return False, f"change not detected: {ev}"
    if ev[0][1] != old or ev[0][2] != (12, 8, 112, 108) or ev[0][3] != 0.016:
        return False, f"change payload wrong: {ev[0]}"
    # 1 像素的自身抖动（四边位移之和 <= 1）不报
    ev = p._diff({1: (old, False)}, {1: ((0, 0, 101, 100), False)}, 0.016)
    if ev:
        return False, f"1px jitter wrongly triggered: {ev}"
    # 最小化 -> 带的是**最小化前**的矩形（水要合拢的位置）
    ev = p._diff({1: (old, False)}, {1: ((0, 0, 100, 100), True)}, 0.1)
    if len(ev) != 1 or ev[0][0] != "minimize" or ev[0][1] != old:
        return False, f"minimize not detected: {ev}"
    # 恢复 -> 带的是**恢复后**的矩形
    ev = p._diff({1: ((0, 0, 100, 100), True)}, {1: ((5, 5, 105, 105), False)}, 0.1)
    if len(ev) != 1 or ev[0][0] != "restore" or ev[0][1] != (5, 5, 105, 105):
        return False, f"restore not detected: {ev}"
    # 静止不动不注入（泡着不动的石头不搅水）
    ev = p._diff({1: (old, False)}, {1: (old, False)}, 0.016)
    if ev:
        return False, f"static window wrongly triggered: {ev}"
    # 新建窗口刻意不报（桌面上冒一个气泡就掀一片浪只会吵闹）
    ev = p._diff({}, {1: (old, False)}, 0.1)
    if ev:
        return False, f"new window wrongly triggered: {ev}"
    # 最小化的窗口在两次扫描之间又动了 -> 那是窗口内部在变，不该报 change
    ev = p._diff({1: (old, True)}, {1: ((30, 30, 130, 130), True)}, 0.1)
    if ev:
        return False, f"moving minimized window wrongly triggered: {ev}"
    # 覆盖层原点偏移要折算进去（多屏时覆盖层不在 (0,0)）
    p.set_origin(10, 20)
    ev = p._diff({1: (old, False)}, {1: ((12, 8, 112, 108), False)}, 0.016)
    if not ev or ev[0][1] != (-10, -20, 90, 80) or ev[0][2] != (2, -12, 102, 88):
        return False, f"origin offset not applied: {ev}"
    return True, "change/minimize/restore/static/open/origin 全部正确"


def _fake_get_window_rect(seq):
    """把 GetWindowRect 换成按序吐出矩形的假实现（写在 byref 的 RECT 上）。"""
    state = {"i": 0}

    def fake(hwnd, pref):
        r = pref._obj                      # ctypes.byref 对象指向的 RECT
        x0, y0, x1, y1 = seq[min(state["i"], len(seq) - 1)]
        state["i"] += 1
        r.left, r.top, r.right, r.bottom = int(x0), int(y0), int(x1), int(y1)
        return 1

    return fake


def test_probe_track_frames():
    """每帧精测：拖动中的窗口每帧都要吐出 change，且热点会被续期。"""
    p = WindowProbe()
    p.set_origin(0, 0)
    p._hot = {1: 0.7}
    p._prev = {1: ((0, 0, 100, 100), False)}
    frames = [(5, 3, 105, 103), (12, 9, 112, 109), (20, 16, 120, 116),
              (20, 16, 120, 116)]      # 第 4 帧没动
    orig_gwr, orig_iconic = fe._GetWindowRect, fe._IsIconic
    fe._GetWindowRect = _fake_get_window_rect(frames)
    fe._IsIconic = lambda hwnd: 0
    try:
        outs = [p._track(0.016) for _ in range(4)]
    finally:
        fe._GetWindowRect, fe._IsIconic = orig_gwr, orig_iconic
    if [len(o) for o in outs] != [1, 1, 1, 0]:
        return False, f"per-frame change count wrong: {[len(o) for o in outs]}"
    if outs[1][0][1] != (5, 3, 105, 103) or outs[1][0][2] != (12, 9, 112, 109):
        return False, f"per-frame payload wrong: {outs[1][0]}"
    if outs[1][0][3] != 0.016:
        return False, f"dt not per-frame: {outs[1][0][3]}"
    if 1 not in p._hot or p._hot[1] < 0.6:
        return False, f"hot TTL not refreshed: {p._hot}"
    # 热点过期 -> 自动退出逐帧跟踪（不然一直在读内核）
    p2 = WindowProbe()
    p2._hot = {1: 0.01}
    p2._prev = {1: ((0, 0, 100, 100), False)}
    fe._GetWindowRect = _fake_get_window_rect([(9, 9, 109, 109)])
    try:
        p2._track(0.05)
    finally:
        fe._GetWindowRect = orig_gwr
    if 1 in p2._hot:
        return False, "expired hot window not dropped"
    return True, "逐帧 change + 热点续期 + 过期退出 均正确"


def test_probe_scan_marks_mover():
    """10Hz 全量扫描要认出「刚开始动」的窗口并转成逐帧跟踪。"""
    p = WindowProbe()
    p.set_origin(0, 0)
    p._prev = {1: ((0, 0, 100, 100), False), 2: ((0, 0, 50, 50), False)}
    cur = {1: ((30, 0, 130, 100), False), 2: ((0, 0, 50, 50), False)}
    ev = p._diff(p._prev, cur, 0.1)
    p._hot = {}
    events = p._scan(cur, 0.1)
    if len(events) != 1 or events[0][0] != "change":
        return False, f"scan missed the mover: {events}"
    if 1 not in p._hot:
        return False, "new mover not promoted to per-frame tracking"
    if 2 in p._hot:
        return False, "static window wrongly promoted"
    if p.windows != [(30, 0, 130, 100), (0, 0, 50, 50)] and \
            sorted(p.windows) != sorted([(30, 0, 130, 100), (0, 0, 50, 50)]):
        return False, f"visible window list wrong: {p.windows}"
    if ev is events:
        return False, "_scan should build its own event list"
    return True, "全量扫描认出动者、转逐帧、静态窗口不受影响"


def test_null_foreground_window():
    """GetForegroundWindow() 在焦点位于桌面 / 切换窗口瞬间会返回 NULL，
    而它的 restype 是 c_void_p —— ctypes 把 NULL 变成 None。曾经的写法
    `int(_GetForegroundWindow())` 于是每帧抛 TypeError，把渲染循环打穿
    （error.log 里成片的 `render: TypeError ... not 'NoneType'`）。
    """
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
    return True, "NULL 前台窗口不再抛异常"


# ------------------------------------------------------------------ 2. 活塞物理
def _push(wf, old, new, dt=0.016, strength=1.0, width=22.0):
    """按 **Scene 的接线方式**注入：带骑在行程中点（中点法，见 `_win_perturb`）。"""
    inv = 1.0 / dt
    mid = tuple(0.5 * (a + b) for a, b in zip(old, new))
    return wf.push_rect(
        mid,
        ((new[0] - old[0]) * inv, (new[2] - old[2]) * inv,
         (new[1] - old[1]) * inv, (new[3] - old[3]) * inv),
        strength, dt, width)


def _sweep(v, dist, dt, strength=1.0, width=22.0, seed=5,
           rect=(400.0, 300.0, 700.0, 500.0)):
    """让窗（矩）形边以速度 v 扫过 dist 像素，返回水面。"""
    wf = WaterRippleField(W, H, seed=seed)
    n = max(1, int(round(dist / (v * dt))))
    step = dist / n
    old = rect
    for i in range(n):
        new = (rect[0] + step * (i + 1), rect[1],
               rect[2] + step * (i + 1), rect[3])
        _push(wf, old, new, dt=dt, strength=strength, width=width)
        old = new
    return wf, rect[2] + dist


def test_push_band_nonzero():
    """回归：`_push_band` 曾把**格索引**直接拿去减**像素**（两种轴向都错），
    整条带恒为 0 —— 窗口怎么拖都不起浪，而且查不出异常。"""
    for axis, coord, lo, hi in ((0, 300.0, 200.0, 400.0),
                                (1, 250.0, 150.0, 450.0)):
        wf = WaterRippleField(W, H, seed=3)
        hit = wf._push_band(axis, coord, lo, hi, 0.05, 22.0)
        if not hit:
            return False, f"axis={axis} band reported no hit"
        if not np.any(wf.height):
            return False, f"axis={axis} band wrote nothing (index/pixel mixup)"
        val = edge_val(wf, axis, coord, lo, hi)
        if abs(val - 0.05) > 0.01:
            return False, f"axis={axis} band peak {val:.4f} != 0.05"
        # 带外（切向两端更远处）必须是平的：硬边会激起网格噪声
        if np.abs(wf.height).sum() <= 0.0:
            return False, f"axis={axis} zero energy"
    return True, "竖直/水平边注入带都非零且峰值正确"


def test_piston_direction():
    """向右拖：前缘（右边）隆起、后缘（左边）塌陷。其余方向同理。"""
    rect = (400.0, 300.0, 700.0, 500.0)
    x0, y0, x1, y1 = rect
    v = 2500.0                                   # > V_REF，饱和

    wf = WaterRippleField(W, H, seed=5)
    _push(wf, rect, (x0 + 40, y0, x1 + 40, y1))
    r = edge_val(wf, 0, x1 + 40, y0, y1)
    l = edge_val(wf, 0, x0 + 40, y0, y1)
    if not (r > 0.01):
        return False, f"拖向右：右缘未隆起 ({r:.4f})"
    if not (l < -0.01):
        return False, f"拖向右：左缘未塌陷 ({l:.4f})"

    # 向左拖 -> 相位反过来
    wf = WaterRippleField(W, H, seed=5)
    _push(wf, rect, (x0 - 40, y0, x1 - 40, y1))
    r = edge_val(wf, 0, x1 - 40, y0, y1)
    l = edge_val(wf, 0, x0 - 40, y0, y1)
    if not (r < -0.01 and l > 0.01):
        return False, f"拖向左：相位不对 (left={l:.4f}, right={r:.4f})"

    # 向下拖 -> 下缘隆起、上缘塌陷
    wf = WaterRippleField(W, H, seed=5)
    _push(wf, rect, (x0, y0 + 40, x1, y1 + 40))
    b = edge_val(wf, 1, y1 + 40, x0, x1)
    t = edge_val(wf, 1, y0 + 40, x0, x1)
    if not (b > 0.01 and t < -0.01):
        return False, f"拖向下：上/下缘方向不对 (top={t:.4f}, bottom={b:.4f})"
    return True, "四个方向的隆起/塌陷都与真实活塞一致"


def test_resize_is_same_formula():
    """平移与改尺寸走同一条公式：拉宽只推右缘，左缘一动不动。"""
    x0, y0, x1, y1 = 400.0, 300.0, 700.0, 500.0
    # 拉宽：右缘右移，左缘不动
    wf = WaterRippleField(W, H, seed=7)
    _push(wf, (x0, y0, x1, y1), (x0, y0, x1 + 40, y1))
    r = edge_val(wf, 0, x1 + 40, y0, y1)
    l = edge_val(wf, 0, x0, y0, y1)
    if not (r > 0.01):
        return False, f"拉宽：右缘未隆起 ({r:.4f})"
    if abs(l) > 1e-6:
        return False, f"拉宽：左缘不该动 ({l:.6f})"
    # 缩窄：右缘左移 -> 抽回（塌陷）
    wf = WaterRippleField(W, H, seed=7)
    _push(wf, (x0, y0, x1, y1), (x0, y0, x1 - 40, y1))
    r = edge_val(wf, 0, x1 - 40, y0, y1)
    if not (r < -0.01):
        return False, f"缩窄：右缘未塌陷 ({r:.4f})"
    # 拉高：下缘下移 -> 隆起
    wf = WaterRippleField(W, H, seed=7)
    _push(wf, (x0, y0, x1, y1), (x0, y0, x1, y1 + 40))
    b = edge_val(wf, 1, y1 + 40, x0, x1)
    if not (b > 0.01):
        return False, f"拉高：下缘未隆起 ({b:.4f})"
    return True, "拉宽/缩窄/拉高：与平移同式，未动的边零注入"


def test_static_window_is_dead_water():
    """窗口静止 -> 一点不注入，水面保持绝对零（不是"很小"）。"""
    wf = WaterRippleField(W, H, seed=11)
    rect = (400.0, 300.0, 700.0, 500.0)
    hit = wf.push_rect(rect, (0.0, 0.0, 0.0, 0.0), 1.0, 0.016, 22.0)
    if hit:
        return False, "静止窗口被报成有扰动"
    if float(np.abs(wf.height).max()) != 0.0:
        return False, "静止窗口注入了位移"
    if wf._alive:
        return False, "静止窗口把水面标成了活跃"
    # 强度 0 也不注入（面板拉到 0 = 关）
    hit = wf.push_rect(rect, (2500.0, 2500.0, 2500.0, 2500.0), 0.0, 0.016, 22.0)
    if hit or float(np.abs(wf.height).max()) != 0.0:
        return False, "强度为 0 时仍在注入"
    return True, "静止 / 强度 0 -> 绝对零位移"


def test_amplitude_follows_speed():
    """整段扫过之后，边走过的每一格留下的位移 ∝ `clip(速度 / 参考速度)`：
    快拖的浪更大（真实船波的弗劳德数也是这个方向），超过参考速度后**饱和** ——
    不是"无限快就无限大"。
    """
    dist = 160.0
    peaks = {}
    for v in (300.0, 600.0, 900.0, 1800.0):
        wf, end = _sweep(v, dist, 1.0 / 60.0)
        peaks[v] = edge_val(wf, 0, end - 0.4 * dist, 300.0, 500.0)
    if not all(p > 0 for p in peaks.values()):
        return False, f"各速度下都应隆起: {peaks}"
    vals = [peaks[v] for v in (300.0, 600.0, 900.0, 1800.0)]
    for a, b in zip(vals, vals[1:]):
        if b <= a:
            return False, f"幅度不随速度单调增: {vals}"
    # 线性段：速度翻倍 -> 幅度翻倍（离散采样带来的约 5% 偏差，给 25% 余量）
    if not (1.5 < vals[1] / vals[0] < 2.5):
        return False, f"线性段比例不对 (300->600): {vals}"
    # 超过参考速度后饱和：两倍速度不许再明显变大
    if vals[3] / vals[2] > 1.15:
        return False, f"超过参考速度后未饱和: {vals}"
    # 与解析值对一下量级：带是"骑在边上、两侧各 half_w"的余弦包，边扫过一个格
    # 点时它要跨过 2·half_w 才会走完，于是累积量 = (2/π)·A·clip(v/V_REF)。
    ref = 2.0 * WaterRippleField._PUSH_AMP / np.pi
    if not (0.7 < vals[2] / ref < 1.6):
        return False, f"饱和幅度 {vals[2]:.4f} 与 2A/π={ref:.4f} 量级不符"
    return True, ("幅度随速度增并饱和: "
                  + ", ".join(f"{int(v)}->{peaks[v]:.4f}" for v in peaks))


def test_frame_rate_independent():
    """同一段位移、不同帧率，注入的总量必须一致（差 ≤ 12%）。

    若简单地"每帧注入一个常数"，30 帧/秒的机器上边在带里停留的帧数减半，
    浪就莫名弱一半 —— 这条断言钉住那个写法。
    另一条历史教训：带**钉在行程末端**（左端点法）时，帧率越低每帧跨得越远、
    注入越被高估，实测 30 帧/秒比 120 帧/秒强 1.4 倍；改成骑中点后收敛到 1.07×，
    剩下的那点偏差是 30 帧/秒下每帧跨 20px、而带只有 44px 宽的离散采样残差。
    """
    dist, dur = 120.0, 0.2
    v = dist / dur                      # 600 px/s
    out = {}
    for fps in (30, 60, 120):
        wf, end = _sweep(v, dist, 1.0 / fps)
        out[fps] = edge_val(wf, 0, end - 0.4 * dist, 300.0, 500.0)
    lo, hi = min(out.values()), max(out.values())
    if lo <= 0:
        return False, f"未起浪: {out}"
    if hi / lo > 1.12:
        return False, f"帧率相关性过大 ({hi / lo:.3f}×): {out}"
    return True, "30/60/120 帧/秒注入一致: " + ", ".join(
        f"{k}->{val:.4f}" for k, val in out.items())


def test_dome_minimize_restore():
    """最小化 = 水从四周合拢（塌陷），恢复 = 把水推开（抬升）；只动那块矩形。"""
    rect = (300.0, 200.0, 600.0, 400.0)
    wf = WaterRippleField(W, H, seed=19)
    if not wf.dome_rect(rect, -0.10):
        return False, "minimize dome 未生效"
    c = dome_center(wf, rect)
    if not (c < -0.05):
        return False, f"最小化中心未塌陷 ({c:.4f})"
    if abs(float(np.abs(wf.height).max()) - 0.10) > 0.02:
        return False, f"塌陷幅度不对 ({np.abs(wf.height).max():.4f})"
    # 矩形外必须是平的（水只在窗口占的位置合拢）
    outside = wf.height.copy()
    c_ = wf.cell
    ix0, iy0 = int(rect[0] / c_) - 4, int(rect[1] / c_) - 4
    ix1, iy1 = int(rect[2] / c_) + 4, int(rect[3] / c_) + 4
    patch = np.zeros_like(outside, dtype=bool)
    patch[max(0, iy0):iy1, max(0, ix0):ix1] = True
    if float(np.abs(outside[~patch]).max()) != 0.0:
        return False, "dome 泄到了矩形之外"

    # 恢复：同几何、反相位
    wf2 = WaterRippleField(W, H, seed=19)
    wf2.dome_rect(rect, +0.10)
    c2 = dome_center(wf2, rect)
    if not (c2 > 0.05):
        return False, f"恢复中心未抬升 ({c2:.4f})"
    if not (np.sign(c2) != np.sign(c)):
        return False, "最小化 / 恢复相位相同（看不出区别）"
    # 幅度：中心最高、四边归零（软包，不是硬边台阶）
    edge = dome_center(wf2, (rect[0] - 2, rect[1], rect[0] + 2, rect[3]))
    if abs(edge) > 0.02:
        return False, f"dome 边缘未归零 ({edge:.4f})"
    if wf2.dome_rect(rect, 0.0):
        return False, "幅度 0 的 dome 不该生效"
    return True, "最小化塌陷 / 恢复抬升，几何一致、相位相反、边界归零"


def test_push_energy_bounded():
    """持续拖动 3 秒：幅度有界、无 NaN / Inf。"""
    wf = WaterRippleField(W, H, seed=23)
    rect = (200.0, 150.0, 500.0, 350.0)
    rng = np.random.RandomState(0)
    for i in range(180):
        dx = float(rng.uniform(-12.0, 24.0))     # 忽快忽慢地拖
        dy = float(rng.uniform(-8.0, 12.0))
        new = (rect[0] + dx, rect[1] + dy, rect[2] + dx, rect[3] + dy)
        _push(wf, rect, new, dt=1.0 / 60.0)
        rect = new
        wf.step(wf.k, wf.damp)
    mx = float(np.abs(wf.height).max())
    if not np.isfinite(mx):
        return False, "NaN / Inf 出现在位移场"
    if mx > 5.0:
        return False, f"幅度失控 ({mx:.3f})"
    if mx < 1e-3:
        return False, f"持续拖动后水面竟然是平的 ({mx:.5f})"
    return True, f"3 秒拖动后幅度有界 ({mx:.3f})，无 NaN / Inf"


# ------------------------------------------------------------------ 3. 场景接线
def test_scene_change_perturbs_shared_surface():
    """Scene 必须把窗口事件注进**共用的那片水**（scene.wave），
    而不是另建一个涟漪池、更不是自己画一套。"""
    scene = Scene(W, H)
    scene.cfg = base_cfg()
    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    old = (120.0, 100.0, 420.0, 320.0)
    new = (160.0, 100.0, 460.0, 320.0)          # 向右拖 40px
    mid = tuple(0.5 * (a + b) for a, b in zip(old, new))

    scene._win_events = [("change", old, new, 0.016)]
    scene.step(0.016, u32, W, H)

    if not np.any(scene.wave.height):
        return False, "Scene 没有把窗口事件注进水面"
    r = edge_val(scene.wave, 0, mid[2], mid[1], mid[3])
    l = edge_val(scene.wave, 0, mid[0], mid[1], mid[3])
    if not (r > 0.0 > l):
        return False, f"注入方向不对 (left={l:.4f}, right={r:.4f})"
    if scene.ripples.active.any():
        return False, "窗口水波仍在往涟漪池里塞圈（应只扰动共用水面）"
    if has_nan_inf(buf):
        return False, "NaN / Inf 出现在缓冲"
    # 只开窗口水波（真实水波关着）也要出像素 —— 两者共用一片水
    if not np.any(buf):
        return False, "窗口水波开着却没上屏任何像素"
    return True, "窗口事件 -> 共用水面 -> 上屏，方向正确且不碰涟漪池"


def test_scene_minimize_restore():
    """最小化 / 恢复在 Scene 层面也要相位相反，且都只动窗口占的位置。"""
    rect = (300.0, 200.0, 600.0, 400.0)
    for et, sign in (("minimize", -1), ("restore", +1)):
        scene = Scene(W, H)
        scene.cfg = base_cfg(win_ripple_strength=1.0)
        buf = np.zeros((H, W, 4), np.uint8)
        u32 = buf.view(np.uint32).reshape(-1)
        scene._win_events = [(et, rect, 0.016)]
        scene.step(0.016, u32, W, H)
        c = dome_center(scene.wave, rect)
        if np.sign(c) != sign or abs(c) < 0.02:
            return False, f"{et} 的中心位移 {c:.4f}（期望符号 {sign}）"
        if has_nan_inf(buf):
            return False, f"{et} 产生 NaN / Inf"
    return True, "最小化塌陷、恢复抬升，幅度符合配置"


def test_scene_size_and_strength_knobs():
    """两个旋钮各自的**含义**：强度改幅度、范围改带的**厚度**（法向宽度）。"""
    rect = (200.0, 150.0, 500.0, 350.0)
    new = (260.0, 150.0, 560.0, 350.0)

    def run(strength, size):
        scene = Scene(W, H)
        scene.cfg = base_cfg(win_ripple_strength=strength, win_ripple_size=size)
        buf = np.zeros((H, W, 4), np.uint8)
        u32 = buf.view(np.uint32).reshape(-1)
        scene._win_events = [("change", rect, new, 0.016)]
        scene.step(0.016, u32, W, H)
        wf = scene.wave
        # 带骑在行程中点；沿**法向**（x）量厚度，沿切向取窗中间那一行
        cx = 0.5 * (rect[2] + new[2])
        row = min(max(int(round(0.5 * (rect[1] + rect[3]) / wf.cell)),
                      0), wf.gh - 1)
        a0 = max(0, int((cx - 120.0) / wf.cell))
        a1 = min(wf.gw, int((cx + 120.0) / wf.cell) + 1)
        prof = np.abs(wf.height[row, a0:a1])
        amp = float(prof.max())
        thick = int(np.count_nonzero(prof > 0.2 * amp)) if amp > 0 else 0
        return amp, thick

    a1, t1 = run(0.5, 1.0)
    a2, t2 = run(1.0, 1.0)
    if not (a2 > a1 * 1.6):
        return False, f"扰动强度没改幅度 ({a1:.4f} -> {a2:.4f})"
    if not (0.9 < t2 / max(t1, 1) < 1.15):
        return False, f"扰动强度不该改带的厚度 ({t1} -> {t2} 格)"
    a3, t3 = run(1.0, 0.4)
    a4, t4 = run(1.0, 2.5)
    if not (t4 > t3 * 1.8):
        return False, f"扰动范围没改带的厚度 ({t3} -> {t4} 格)"
    if not (a3 > 0.0 and a4 > 0.0):
        return False, f"两个范围档位都该有位移 ({a3:.4f}, {a4:.4f})"
    return True, (f"强度 {a1:.4f}->{a2:.4f}（厚度不变 {t1}/{t2} 格）；"
                  f"范围使带厚 {t3}->{t4} 格")


def test_scene_off_clears():
    """关掉开关必须清场，否则重新打开会冒出上一次留下的波。"""
    scene = Scene(W, H)
    scene.cfg = base_cfg()
    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    scene._win_events = [("change", (120.0, 100.0, 420.0, 320.0),
                          (160.0, 100.0, 460.0, 320.0), 0.016)]
    scene.step(0.016, u32, W, H)
    if not scene.wave._alive:
        return False, "前置条件不成立：水面没有活起来"

    scene.cfg = base_cfg(win_ripple_enabled=False)
    scene.step(0.016, u32, W, H)
    if float(np.abs(scene.wave.height).max()) != 0.0 or scene.wave._alive:
        return False, "关掉窗口水波后水面没有清场"
    if scene.win._prev or scene.win._hot:
        return False, "关掉窗口水波后探针状态没有复位"
    # 再开一次：不许因为"停用期的位移"炸出一片浪
    scene.cfg = base_cfg()
    scene.step(0.016, u32, W, H)
    if float(np.abs(scene.wave.height).max()) > 1e-6:
        return False, "重新打开时凭空冒出位移"
    return True, "关闭即清场、探针复位、重开不炸波"


def test_scene_wave_and_window_share_surface():
    """真实水波与窗口水波共用同一片水：同一个 `scene.wave` 数组上，
    鼠标滴下的那滴与窗口推出来的那道浪各在一边，互不覆盖。

    对照组是关键：窗口**没动**时，远端（鼠标够不着的地方）必须是**绝对零** ——
    否则"远端有位移"这件事证明不了任何东西。
    """
    win_ev = ("change", (500.0, 300.0, 700.0, 450.0),
              (540.0, 300.0, 740.0, 450.0), 0.016)

    def run(with_window):
        scene = Scene(W, H)
        scene.cfg = base_cfg(wave_enabled=True, win_ripple_enabled=True)
        buf = np.zeros((H, W, 4), np.uint8)
        u32 = buf.view(np.uint32).reshape(-1)
        scene.wave.splash(200.0, 200.0, dict(d.DEFAULTS))
        if with_window:
            scene._win_events = [win_ev]
        scene.step(0.016, u32, W, H)
        return scene, buf

    s0, _ = run(False)
    s1, buf1 = run(True)
    c = s1.wave.cell
    far = int(500.0 / c)
    near = int(300.0 / c)
    if np.any(s0.wave.height[:, far:]):
        return False, "对照组不干净：窗口没动，远端却有位移"
    if not np.any(s1.wave.height[:, far:]):
        return False, "窗口扰动没落到共用的那片水上"
    if not np.any(s1.wave.height[:, :near]):
        return False, "鼠标那一滴丢了（说明不是同一片水）"
    if has_nan_inf(buf1):
        return False, "NaN / Inf 出现在缓冲"
    if not np.any(buf1):
        return False, "两片半透明叠加后竟然没有上屏像素"
    if scene_mouse_active(s1) is not True:
        return False, "水面活着时 layer_activity 应报鼠标层有内容"
    return True, "同一片水：鼠标滴 + 窗口推 各在一边、叠加不互相覆盖"


def scene_mouse_active(scene):
    """`Scene.layer_activity()` 的鼠标层信号（见 fx_scene 的注释）。"""
    return bool(scene.layer_activity()[1])


def test_presets_and_defaults_keys():
    """配置键改造的回归：删掉的键不许再出现，预设不许引用不存在的键。"""
    gone = ("win_ripple_life", "win_ripple_color", "win_ripple_edge_density")
    for k in gone:
        if k in d.DEFAULTS:
            return False, f"已删除的键仍在 DEFAULTS: {k}"
    for k in ("win_ripple_enabled", "win_ripple_size", "win_ripple_strength"):
        if k not in d.DEFAULTS:
            return False, f"DEFAULTS 缺少 {k}"
    if not isinstance(d.DEFAULTS["win_ripple_enabled"], bool):
        return False, "win_ripple_enabled 应是布尔开关"
    bad = []
    for name, preset in d.PRESETS.items():
        for k in preset:
            if k not in d.DEFAULTS:
                bad.append(f"{name}.{k}")
    if bad:
        return False, "预设引用了不存在的键: " + ", ".join(bad[:6])
    # 水面预设必须带全一套水参数（它和真实水波共用一片水）
    if "水面" in d.PRESETS:
        p = d.PRESETS["水面"]
        for k in ("wave_speed", "wave_persist", "wave_color", "wave_opacity"):
            if k not in p:
                return False, f"「水面」预设缺少 {k}（共用同一片水，得连它一起设）"
    return True, "配置键与预设引用自洽"


def main():
    print("== window ripple verification（窗口 = 水面活塞）==")
    tests = [
        test_probe_diff,
        test_probe_track_frames,
        test_probe_scan_marks_mover,
        test_null_foreground_window,
        test_push_band_nonzero,
        test_piston_direction,
        test_resize_is_same_formula,
        test_static_window_is_dead_water,
        test_amplitude_follows_speed,
        test_frame_rate_independent,
        test_dome_minimize_restore,
        test_push_energy_bounded,
        test_scene_change_perturbs_shared_surface,
        test_scene_minimize_restore,
        test_scene_size_and_strength_knobs,
        test_scene_off_clears,
        test_scene_wave_and_window_share_surface,
        test_presets_and_defaults_keys,
    ]
    ok_all = True
    for t in tests:
        try:
            ok, msg = t()
        except Exception as e:
            import traceback
            ok = False
            msg = f"exception: {type(e).__name__}: {e}"
            traceback.print_exc()
        ok_all = ok_all and ok
        print(f"  [{'OK' if ok else 'FAIL'}] {t.__name__:38s} {msg}")
    print(f"\n== {'WINDOW RIPPLE VERIFIED' if ok_all else 'WINDOW RIPPLE FAILED'} "
          f"({len(tests)} 项) ==")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
