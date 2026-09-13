# -*- coding: utf-8 -*-
"""颜料盘特效专项验证（**鼠标搅动版**）。

这一版和上一版的语义完全不同，测试也必须重写。它不再是"自己铺满整屏的色雾"，
而是**交互驱动**的：

  * 指针滑过 -> 沿轨迹落色 + 沿运动方向推流场（"搅动"）
  * 点击     -> 落点炸开一圈**径向向外**的冲量（"扩散"）
  * 空闲     -> 不注入任何颜料，已有颜料涡旋、拉丝、消散，最终整屏透明

所以断言盯的是**旋钮的含义**，不是"画面有没有变化"：

  * 静止不画 -> 不喂指针跑 300 帧，场上必须一个像素都没有（本效果的第一诉求：
                用户明确说不要"整个屏幕上的颜色流转"）
  * 指针方向 -> 左拖与右拖，停手后颜料的**漂移方向**必须相反（差分掉基流的贡献）
  * paint_swirl  -> 停手后的漂移距离（0 = 颜料钉在原地）
  * paint_click  -> 点击后二阶矩的膨胀倍数（只点击不动鼠标也要扩散）
  * paint_brush  -> 笔触宽度
  * paint_flow   -> 同一笔的浓度
  * paint_fade   -> 静置后的残留
  * paint_diffusion -> 峰值下降 / 二阶矩上升
  * 基色 -> 画上去的像素真的偏这个色相（必须先反预乘再比）

三条是本效果踩过的真实 bug，各留一个回归：
  * **染料引用失效**：update() 在 _advect 之前缓存 self.dye，而 _advect 会换掉
    self.dye（双缓冲换位）-> 衰减/扩散作用在被丢弃的旧数组上，颜料无限堆积。
  * **包围盒裁剪**：draw 只画"有颜料的格子"，裁错了会丢可见像素
    -> test_bbox_crop_is_lossless 拿关掉裁剪的结果逐像素比。
  * **指针位置过期**：特效关着时不喂指针，重新启用后第一帧会拉出一条横贯屏幕的
    长笔触 -> test_teleport_no_stroke。
"""
import os
os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")

import colorsys
import re
import time

import numpy as np

import desktop_fx as d
from fx_effects import PaintField, RainField, SnowField
from fx_scene import Scene


# ------------------------------------------------------------------ 工具
def has_nan_inf(buf):
    a = buf.view(np.int32).reshape(-1)
    return int(np.any((a == -2147483648) | (a == 0x7F800000) | (a > 0x7F800000)))


def paint_cfg(**over):
    c = dict(d.DEFAULTS)
    c["paint_enabled"] = True
    c.update(over)
    return c


def _fresh(W, H):
    buf = np.zeros((H, W, 4), np.uint8)
    return buf, buf.view(np.uint32).reshape(-1)


def _new(W, H, cfg):
    p = PaintField(W, H, seed=37)
    p.set_color(cfg["paint_color"])
    return p


def _drive(p, cfg, n, pos=None, click_at=(), dt=1.0 / 60.0):
    """跑 n 帧。pos(i) -> (x, y)；pos=None 表示这一轮完全不给指针。"""
    for i in range(n):
        if pos is not None:
            x, y = pos(i)
            p.set_pointer(x, y, 1 if i in click_at else 0)
        p.update(dt, cfg)


def _line(x0, y0, x1, y1, n):
    def pos(i):
        t = (i + 1) / float(n)
        return (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
    return pos


def _const(x, y):
    return lambda i: (x, y)


def _lum(p):
    g = p.dye
    return np.maximum(np.maximum(g[..., 0], g[..., 1]), g[..., 2])


def _shape(p):
    """归一化到总质量 1 的分布 —— 用来比较"形状"（平流 / 涡旋的痕迹）。

    直接比重心不行：基流无散度，净平流≈0，但它会把颜料揉开，形状变化很显著。
    """
    g = _lum(p)
    m = float(g.sum())
    return (g / m) if m > 0.0 else g


def _cover(p, thr=0.02):
    """有颜料的格子占整个网格的比例。"""
    return float((_lum(p) > thr).mean())


def _blob(p):
    """(峰值, 质心x, 质心y, 二阶矩半径)，网格坐标。"""
    g = _lum(p)
    m = float(g.sum())
    if m <= 0.0:
        return 0.0, 0.0, 0.0, 0.0
    ys, xs = np.mgrid[0:p.gh, 0:p.gw]
    cx = float((g * xs).sum() / m)
    cy = float((g * ys).sum() / m)
    var = float((g * ((xs - cx) ** 2 + (ys - cy) ** 2)).sum() / m)
    return float(g.max()), cx, cy, var ** 0.5


def _draw(p, cfg, W, H):
    buf, u32 = _fresh(W, H)
    p.draw(u32, W, H, cfg)
    return buf, u32


def _unpremul(buf, amin=48):
    """反预乘回 0~255 的真彩。缓冲区里 RGB 已经乘过 alpha，软边像素会被压暗，
    直接比 R>B 会得出"颜色没生效"的错误结论（落樱踩过这个坑）。"""
    a = buf[..., 3].astype(np.float32)
    m = a >= amin
    if not m.any():
        return None, None, None, m
    aa = a[m]
    r = np.clip(buf[..., 2].astype(np.float32)[m] * 255.0 / aa, 0.0, 255.0)
    g = np.clip(buf[..., 1].astype(np.float32)[m] * 255.0 / aa, 0.0, 255.0)
    b = np.clip(buf[..., 0].astype(np.float32)[m] * 255.0 / aa, 0.0, 255.0)
    return r, g, b, m


def _hues(r, g, b):
    return np.array([colorsys.rgb_to_hsv(r[i] / 255.0, g[i] / 255.0,
                                         b[i] / 255.0)[0] for i in range(r.size)])


def _cell_variation(buf, p):
    """一个**模拟格**内部的像素标准差（全部格子取平均）。

    这是"方格纸"最直接的判据：最近邻上屏时，同一模拟格映射出的所有屏幕像素
    必然是同一个值（标准差恒为 0）；做过双线性放大之后，格内就有了渐变。

    不要用"格子边界处的梯度集中度"来判 —— 双线性插值出来的曲面本身就是分段
    线性的，梯度同样是逐格跳跃，那个指标对两种做法都"报警"，分不开。
    """
    a = buf[..., 3].astype(np.float32)
    gh, gw, f = p.gh, p.gw, p.up
    ry = (p._iy // f).astype(np.int64)
    rx = (p._ix // f).astype(np.int64)
    cid = (ry[:, None] * gw + rx[None, :]).reshape(-1)
    v = a.reshape(-1)
    n = np.bincount(cid, minlength=gh * gw).astype(np.float64)
    s1 = np.bincount(cid, weights=v, minlength=gh * gw)
    s2 = np.bincount(cid, weights=v * v, minlength=gh * gw)
    ok = n > 4
    if not ok.any():
        return float("nan")
    var = np.maximum(s2[ok] / n[ok] - (s1[ok] / n[ok]) ** 2, 0.0)
    return float(np.sqrt(var).mean())


def _circ_span(h):
    """色相的环形跨度（0~1）。"""
    if h.size < 2:
        return 0.0
    s = np.sort(np.mod(h, 1.0))
    gaps = np.diff(np.concatenate([s, [s[0] + 1.0]]))
    return float(1.0 - gaps.max())


# ------------------------------------------------------------------ 基础渲染
def test_render_60_frames():
    W, H = 1280, 720
    cfg = paint_cfg()
    p = _new(W, H, cfg)
    _drive(p, cfg, 60, _line(200, 200, 1000, 500, 60))
    buf, _ = _draw(p, cfg, W, H)
    if has_nan_inf(buf):
        return False, "离屏缓冲里出现 NaN/Inf"
    lit = int((buf[..., 3] > 8).sum())
    if lit < 500:
        return False, f"拖了 60 帧只点亮 {lit} 像素"
    return True, f"60 帧拖动点亮 {lit} 像素（{lit / (W * H) * 100:.2f}% 屏）"


def test_idle_is_transparent():
    """**本效果的核心诉求**：没人动鼠标时不能自己铺满屏幕。"""
    W, H = 1280, 720
    cfg = paint_cfg()
    p = _new(W, H, cfg)
    _drive(p, cfg, 300)                      # 完全不给指针
    buf, _ = _draw(p, cfg, W, H)
    lit = int((buf[..., 3] > 0).sum())
    if lit != 0 or p.dye.max() != 0.0:
        return False, f"静止 300 帧后仍有 {lit} 像素 / dye 峰值 {p.dye.max():.4f}"
    if p._dirty:
        return False, "静止且空场时 _dirty 仍为真（draw 会白白跑一遍辉光）"
    return True, "静止 300 帧：0 像素、dye=0、_dirty=False（完全透明）"


def test_drag_leaves_local_stroke():
    """笔触必须是局部的，不能糊满全屏。"""
    W, H = 1280, 720
    cfg = paint_cfg(paint_glow=0.0)
    p = _new(W, H, cfg)
    # 只在上半屏横着划一条
    _drive(p, cfg, 40, _line(200, 200, 1100, 200, 40))
    lum = _lum(p)
    rows = np.flatnonzero(lum.max(axis=1) > 0.02)
    cov = _cover(p)
    if cov > 0.55:
        return False, f"一条笔触就覆盖了 {cov * 100:.0f}% 网格（应为局部）"
    span = (rows[-1] - rows[0] + 1) / float(p.gh)
    if span > 0.55:
        return False, f"笔触竖直跨度占全高 {span * 100:.0f}%（应贴着鼠标那一行）"
    return True, (f"笔触覆盖 {cov * 100:.1f}% 网格，竖直跨度 "
                  f"{rows[0]}~{rows[-1]} = 全高 {span * 100:.0f}%")


def test_pointer_pushes_paint_forward():
    """拖拽必须把颜料**推着一起走** —— 量质心越过路径中点的距离。

    旧版 `_splat` 把推流乘了一次 `amp`（单团浓度 0.18），实际推力只有设计值的
    1/5.5，流场几乎推不动颜料：`paint_swirl=1` 时质心只比 `swirl=0` 的基线多走
    8px（+40 vs +32）。修掉之后是 +134 vs +32（4.2×）。

    必须在**拖动过程中**量，不能等停手后看残余漂移 —— 停手后是基流的旋度在
    主导，方向已经不代表"手推的方向"了（实测会反向）。
    """
    W, H = 1280, 720
    cfg = paint_cfg()

    def fwd(xa, xb, swirl, follow=None):
        saved = PaintField._FOLLOW
        if follow is not None:
            PaintField._FOLLOW = follow
        try:
            c = dict(cfg, paint_swirl=swirl)
            p = _new(W, H, c)
            for i in range(20):
                t_ = (i + 1) / 20.0
                p.set_pointer(xa + (xb - xa) * t_, 360.0, 0)
                p.update(1.0 / 60.0, c)
            g = _lum(p)
            m = float(g.sum())
            ys, xs = np.mgrid[0:p.gh, 0:p.gw]
            cx = float((g * xs).sum() / m) * p.cell
            sign = 1.0 if xb > xa else -1.0
            return (cx - (xa + xb) / 2.0) * sign
        finally:
            PaintField._FOLLOW = saved

    base = fwd(300.0, 900.0, 0.0)
    right = fwd(300.0, 900.0, 1.0)
    left = fwd(900.0, 300.0, 1.0)
    ctrl = fwd(300.0, 900.0, 1.0, 0.55)
    if right < base * 2.0 or left < base * 2.0:
        return False, (f"颜料没被手推着走：基线 +{base:.0f}px、"
                       f"右拖 +{right:.0f}px / 左拖 +{left:.0f}px（应 > 基线 2 倍）")
    if ctrl > right * 0.6:
        return False, (f"对照组（_FOLLOW=0.55，旧的弱推力）本该明显更弱，实测 "
                       f"+{ctrl:.0f}px vs +{right:.0f}px —— 判据不灵敏")
    return True, (f"质心越过路径中点：基线 +{base:.0f}px、右拖 +{right:.0f}px、"
                  f"左拖 +{left:.0f}px（对称）；弱推力对照组只有 +{ctrl:.0f}px")


def test_swirl_knob_moves_paint():
    """paint_swirl = 0 时颜料必须钉在原地（基流 + 推流都关掉）。

    判据是**形状变化**而不是质心位移：基流是 curl-noise（无散度旋度场），
    它把颜料卷成涡、揉开，但净平流≈0，质心几乎不动 —— 拿质心当判据会误判
    "旋钮没生效"。归一化后逐格比 L1 距离就能把"旋钮真的在搅"量出来。
    """
    W, H = 1280, 720
    cfg = paint_cfg(paint_glow=0.0, paint_diffusion=0.0)
    res = {}
    for sw in (0.0, 2.0):
        c = dict(cfg, paint_swirl=sw)
        p = _new(W, H, c)
        _drive(p, c, 12, _line(400.0, 360.0, 800.0, 360.0, 12))
        s0 = _shape(p)
        _drive(p, c, 30, _const(800.0, 360.0))
        s1 = _shape(p)
        res[sw] = float(np.abs(s1 - s0).sum())
    if res[0.0] > 0.01:
        return False, f"paint_swirl=0 时颜料形状仍在变（L1={res[0.0]:.4f}）"
    if res[2.0] < 0.15:
        return False, (f"paint_swirl 没生效：0 -> L1={res[0.0]:.4f}，"
                       f"2 -> L1={res[2.0]:.4f}")
    return True, (f"停手 30 帧的形状变化 L1：swirl=0 -> {res[0.0]:.4f}（钉死），"
                  f"swirl=2 -> {res[2.0]:.4f}（被搅开）")


def test_click_expands():
    """点击必须产生"扩散"：二阶矩要显著变大、且是持续长大的，
    而且只点不动鼠标也要扩散。"""
    W, H = 1280, 720
    cfg = paint_cfg(paint_glow=0.0, paint_diffusion=0.0)
    p = _new(W, H, cfg)
    _drive(p, cfg, 1, _const(640.0, 360.0), click_at=(0,))
    peak0, _, _, r0 = _blob(p)
    if r0 <= 0.0:
        return False, "点击没有落下任何颜料"
    _drive(p, cfg, 15, _const(640.0, 360.0))
    r_mid = _blob(p)[3]
    _drive(p, cfg, 15, _const(640.0, 360.0))
    peak1, _, _, r1 = _blob(p)
    if r1 < r0 * 1.35:
        return False, f"点击后没有扩散：半径 {r0:.1f} -> {r1:.1f} 格（应 >1.35×）"
    if r_mid < r0 * 1.15 or r1 < r_mid * 1.05:
        return False, (f"扩散不是持续长大：{r0:.1f} -> {r_mid:.1f} -> {r1:.1f} 格")
    if peak1 > peak0:
        return False, f"扩散后峰值反而升高（{peak0:.3f} -> {peak1:.3f}）"
    return True, (f"原地点击：半径 {r0:.1f} -> {r_mid:.1f} -> {r1:.1f} 格"
                  f"（{r1 / r0:.2f}×），峰值 {peak0:.3f} -> {peak1:.3f}（摊薄）")


def test_click_knob():
    """paint_click = 0 时只允许有一团静止的颜料，不允许扩散。"""
    W, H = 1280, 720
    cfg = paint_cfg(paint_glow=0.0, paint_diffusion=0.0, paint_swirl=0.0)
    res = {}
    for ck in (0.0, 2.5):
        c = dict(cfg, paint_click=ck)
        p = _new(W, H, c)
        _drive(p, c, 1, _const(640.0, 360.0), click_at=(0,))
        r0 = _blob(p)[3]
        _drive(p, c, 24, _const(640.0, 360.0))
        res[ck] = _blob(p)[3] / max(r0, 1e-6)
    if res[0.0] > 1.15:
        return False, f"paint_click=0 仍在扩散（{res[0.0]:.2f}×）"
    if res[2.5] < res[0.0] * 1.4:
        return False, (f"paint_click 没生效：0 -> {res[0.0]:.2f}×，"
                       f"2.5 -> {res[2.5]:.2f}×")
    return True, (f"点击后半径倍数：click=0 -> {res[0.0]:.2f}×，"
                  f"click=2.5 -> {res[2.5]:.2f}×")


def test_no_burst_without_click():
    """不点击、不移动 —— 什么都不该发生（点击是唯一的一次性注入）。"""
    W, H = 1280, 720
    cfg = paint_cfg()
    p = _new(W, H, cfg)
    _drive(p, cfg, 200, _const(640.0, 360.0))       # 一直停在原地，从不点击
    if p.dye.max() != 0.0:
        return False, f"没点击却在原地留下了颜料（峰值 {p.dye.max():.4f}）"
    return True, "指针静止 200 帧且从不点击：场里干干净净"


def test_core_follows_cursor():
    """鼠标必须落在颜料**最亮处**的正下方 —— 亮心不能拖在鼠标后面。

    判据用**峰值**而不是质心：一条拖尾本来就该留在身后（那是"搅动"的余韵），
    质心必然偏后，拿它当判据等于要求这个特效不许有拖尾。真正决定"颜色在不在
    鼠标底下"的是最亮的那一块。

    历史：`_FOLLOW` 只有 0.55（"流场只以指针一半的速度跟随"），稳态拖动时亮心
    落在鼠标后 129px，而刷子半径才 45px —— 肉眼就是"颜色跑在鼠标左上"。
    改成 2.5 后各速度都压进 ±30px。
    """
    W, H = 1920, 1080
    cfg = paint_cfg()

    def bright_off(speed, follow=None):
        """亮部（亮度 > 峰值一半）的质心相对鼠标的偏移。

        不用"最亮那一格"：跟随不够时亮部是一整条几百像素的平台，最亮格的位置
        本身不稳定、也不代表人眼看到的"颜色在哪"。
        """
        saved = PaintField._FOLLOW
        if follow is not None:
            PaintField._FOLLOW = follow
        try:
            p = _new(W, H, cfg)
            # 直线要一直留在屏内，否则高速那档会"跑出屏幕"导致不再落色、
            # 读数停在边界上（实测 1200px/s 跑 2 秒就出屏了，读出来 -1082px）。
            step = speed / 60.0
            n = int(min(140, (W - 300.0) / step))
            x = 150.0
            for _ in range(n):
                x += step
                p.set_pointer(x, 540.0, 0)
                p.update(1.0 / 60.0, cfg)
            col = _lum(p).max(axis=0)
            w = np.maximum(col - 0.5 * col.max(), 0.0)
            if w.sum() <= 0.0:
                return float("nan")
            return float((w * np.arange(col.size)).sum() / w.sum()) * p.cell - x
        finally:
            PaintField._FOLLOW = saved

    brush = PaintField._R_NORM * min(W, H)
    offs = {sp: bright_off(sp) for sp in (300.0, 600.0, 1200.0)}
    ctrl = bright_off(600.0, 0.55)
    if abs(ctrl) < brush * 2.0:
        return False, (f"对照组（_FOLLOW=0.55）本该把亮部甩在身后，实测只偏 "
                       f"{ctrl:+.0f}px —— 判据不灵敏，不能用")
    bad = {k: v for k, v in offs.items() if abs(v) > brush * (2.0 if k > 900 else 1.0)}
    if bad:
        return False, ("亮部拖在鼠标后面：" +
                       "、".join(f"{int(k)}px/s {v:+.0f}px" for k, v in bad.items()) +
                       f"（刷子半径 {brush:.0f}px）")
    return True, ("亮部质心相对鼠标 " +
                  "、".join(f"{int(k)}px/s {v:+.0f}px" for k, v in offs.items()) +
                  f"（刷子半径 {brush:.0f}px）；对照组 _FOLLOW=0.55 是 {ctrl:+.0f}px")


def test_brush_scales_stroke():
    W, H = 1280, 720
    res = {}
    for b in (0.4, 2.5):
        cfg = paint_cfg(paint_glow=0.0, paint_diffusion=0.0, paint_brush=b)
        p = _new(W, H, cfg)
        _drive(p, cfg, 30, _line(300.0, 360.0, 900.0, 360.0, 30))
        res[b] = _cover(p)
    if not (res[2.5] > res[0.4] * 3.0):
        return False, (f"paint_brush 没生效：0.4 -> {res[0.4] * 100:.2f}%，"
                       f"2.5 -> {res[2.5] * 100:.2f}%")
    return True, (f"同一笔的覆盖：0.4 -> {res[0.4] * 100:.2f}%，"
                  f"2.5 -> {res[2.5] * 100:.2f}%（{res[2.5] / res[0.4]:.1f}×）")


def test_flow_scales_density():
    W, H = 1280, 720
    res = {}
    for f in (0.25, 2.0):
        cfg = paint_cfg(paint_glow=0.0, paint_diffusion=0.0, paint_flow=f)
        p = _new(W, H, cfg)
        _drive(p, cfg, 20, _line(300.0, 360.0, 900.0, 360.0, 20))
        res[f] = _blob(p)[0]
    if not (res[2.0] > res[0.25] * 2.0):
        return False, (f"paint_flow 没生效：0.25 -> {res[0.25]:.3f}，"
                       f"2.0 -> {res[2.0]:.3f}")
    return True, (f"同一笔的峰值：0.25 -> {res[0.25]:.3f}，"
                  f"2.0 -> {res[2.0]:.3f}（{res[2.0] / res[0.25]:.1f}×）")


def test_fade_clears_screen():
    """停手后屏幕必须重新变干净。"""
    W, H = 1280, 720
    res = {}
    for fd in (0.1, 1.0):
        cfg = paint_cfg(paint_glow=0.0, paint_fade=fd, paint_swirl=0.0,
                        paint_diffusion=0.0)
        p = _new(W, H, cfg)
        _drive(p, cfg, 30, _line(300.0, 360.0, 1000.0, 360.0, 30))
        m0 = float(p.dye.sum())
        _drive(p, cfg, 180, _const(1000.0, 360.0))      # 静置 3 秒
        res[fd] = (float(p.dye.sum()) / max(m0, 1e-9), _cover(p, 0.02))
    if res[1.0][0] > 0.05:
        return False, f"fade=1.0 静置 3s 还剩 {res[1.0][0] * 100:.1f}% 质量"
    if not (res[1.0][0] < res[0.1][0] * 0.6):
        return False, (f"paint_fade 没生效：0.1 剩 {res[0.1][0] * 100:.1f}%，"
                       f"1.0 剩 {res[1.0][0] * 100:.1f}%")
    return True, (f"静置 3s 残留：fade=0.1 -> {res[0.1][0] * 100:.1f}%，"
                  f"fade=1.0 -> {res[1.0][0] * 100:.1f}%")


def test_diffusion_spreads():
    """扩散 = 同一份质量被摊薄：峰值降、二阶矩升、质量基本守恒。"""
    W, H = 1280, 720
    res = {}
    for df in (0.0, 1.0):
        cfg = paint_cfg(paint_glow=0.0, paint_diffusion=df, paint_swirl=0.0,
                        paint_fade=0.0)
        p = _new(W, H, cfg)
        p._splat(p.gw * 0.5, p.gh * 0.5, max(2.0, p.r_base), 1.5,
                 np.array([1.0, 0.4, 0.2], np.float32))
        p._dirty = True
        m0 = float(p.dye.sum())
        r0 = _blob(p)[3]
        _drive(p, cfg, 60, _const(W * 0.5, H * 0.5))
        res[df] = (_blob(p)[0], _blob(p)[3] / max(r0, 1e-6),
                   float(p.dye.sum()) / m0)
    if res[1.0][0] > res[0.0][0] * 0.95:
        return False, f"扩散没削峰：{res[0.0][0]:.3f} -> {res[1.0][0]:.3f}"
    if res[1.0][1] < 1.03:
        return False, f"扩散没铺开：二阶矩 {res[0.0][1]:.3f} -> {res[1.0][1]:.3f}"
    if abs(res[1.0][2] - res[0.0][2]) > 0.05:
        return False, (f"扩散顺带吃掉了质量：无扩散剩 {res[0.0][2] * 100:.1f}%、"
                       f"强扩散剩 {res[1.0][2] * 100:.1f}%")
    return True, (f"单团 1s：峰值 {res[0.0][0]:.3f} -> {res[1.0][0]:.3f}，"
                  f"二阶矩 {res[1.0][1]:.2f}×，质量 {res[0.0][2] * 100:.0f}% "
                  f"vs {res[1.0][2] * 100:.0f}%（扩散不吃质量）")


def test_no_runaway_accumulation():
    """长跑回归：衰减不能失效（_advect 换缓冲后必须重取 self.dye）。"""
    W, H = 1280, 720
    cfg = paint_cfg()
    p = _new(W, H, cfg)
    _drive(p, cfg, 120, _line(200.0, 300.0, 1100.0, 420.0, 120))
    m1 = float(p.dye.sum())
    _drive(p, cfg, 600, _const(1100.0, 420.0))      # 再静置 10 秒
    m2 = float(p.dye.sum())
    if m2 > m1 * 0.35:
        return False, (f"停手 10s 后质量几乎没降：{m1:.0f} -> {m2:.0f}"
                       f"（衰减可能作用在了被丢弃的旧缓冲上）")
    if p.dye.max() > 3.01:
        return False, f"浓度越界（{p.dye.max():.2f} > clamp 3.0）"
    return True, f"停手静置 10s：质量 {m1:.0f} -> {m2:.0f}（{m2 / m1 * 100:.1f}%）"


def test_teleport_no_stroke():
    """指针瞬移（切显示器 / 刚启用）不能拉一条横贯屏幕的长线。"""
    W, H = 1280, 720
    cfg = paint_cfg(paint_glow=0.0, paint_swirl=0.0, paint_diffusion=0.0)
    p = _new(W, H, cfg)
    _drive(p, cfg, 10, _const(150.0, 150.0))        # 先落一小团
    c0 = _cover(p)
    p.set_pointer(1200.0, 650.0, 0)                 # 瞬移一帧
    p.update(1.0 / 60.0, cfg)
    c1 = _cover(p)
    if c1 > c0 * 2.5 + 0.02:
        return False, f"瞬移画出了一条长线：覆盖 {c0 * 100:.2f}% -> {c1 * 100:.2f}%"
    return True, f"瞬移一帧：覆盖 {c0 * 100:.2f}% -> {c1 * 100:.2f}%（未连线）"


def test_disabled_resets():
    """关闭再打开不能看见上一次的残迹（Scene 每帧在 else 分支调 reset）。"""
    W, H = 960, 540
    cfg = paint_cfg()
    p = _new(W, H, cfg)
    _drive(p, cfg, 30, _line(200.0, 200.0, 800.0, 300.0, 30))
    if p.dye.max() <= 0.0:
        return False, "前置条件不成立：没有画出任何颜料"
    p.reset()
    if p.dye.max() != 0.0 or p._iu.max() != 0.0 or p._iv.max() != 0.0:
        return False, "reset() 之后染料 / 冲量场还有残留"
    p.reset()                                       # 幂等
    return True, "reset() 清空染料与冲量场，重复调用安全"


def test_bbox_crop_is_lossless():
    """draw 只画"有颜料的包围盒"，裁错就会丢可见像素。

    把阈值降到 0（= 不裁剪，几乎整网格都算"有颜料"）再画一遍，两者必须
    逐像素相同 —— 差异只可能来自被裁掉的那些"打包后本来就是 0"的格子。
    """
    W, H = 960, 540
    cfg = paint_cfg()
    p = _new(W, H, cfg)
    _drive(p, cfg, 30, _line(150.0, 200.0, 800.0, 380.0, 30))
    saved = PaintField._BBOX_EPS
    try:
        b_crop, _ = _draw(p, cfg, W, H)
        PaintField._BBOX_EPS = 0.0
        b_full, _ = _draw(p, cfg, W, H)
    finally:
        PaintField._BBOX_EPS = saved
    d_ = int((b_crop != b_full).sum())
    if d_ != 0:
        return False, f"包围盒裁剪丢了 {d_} 个子像素（应完全一致）"
    lit = int((b_full[..., 3] > 0).sum())
    return True, f"裁剪版与全画版逐像素一致（{lit} 像素）"


def test_color_meaningful():
    """基色要真的画上去 —— 必须反预乘回 0~255 再比。"""
    W, H = 960, 540
    cfg = paint_cfg(paint_color="#FF3040", paint_hue_spread=0.0, paint_glow=0.0)
    p = _new(W, H, cfg)
    _drive(p, cfg, 40, _line(200.0, 270.0, 800.0, 270.0, 40))
    buf, _ = _draw(p, cfg, W, H)
    r, g, b, _m = _unpremul(buf)
    if r is None:
        return False, "没有画出足够不透明的像素（反预乘样本为空）"
    warm = float((r > b).mean())
    if warm < 0.9:
        return False, f"只有 {warm * 100:.0f}% 的像素 R>B（基色 #FF3040 是暖色）"
    return True, (f"反预乘后 R>B 占 {warm * 100:.0f}%，均值 "
                  f"R={r.mean():.0f} G={g.mean():.0f} B={b.mean():.0f}")


def test_hue_spread_knob():
    W, H = 960, 540
    res = {}
    for sp in (0.0, 1.0):
        cfg = paint_cfg(paint_hue_spread=sp, paint_glow=0.0)
        p = _new(W, H, cfg)
        _drive(p, cfg, 90, lambda i: (250.0 + (i % 30) * 20.0,
                                      200.0 + (i // 30) * 60.0))
        buf, _ = _draw(p, cfg, W, H)
        r, g, b, _m = _unpremul(buf)
        res[sp] = _circ_span(_hues(r, g, b)) if r is not None and r.size > 8 else 0.0
    if not (res[1.0] > res[0.0] + 0.1):
        return False, f"色相铺开没生效：0 -> {res[0.0]:.3f}，1.0 -> {res[1.0]:.3f}"
    return True, f"像素色相跨度：0 -> {res[0.0]:.3f}，1.0 -> {res[1.0]:.3f} 圈"


def test_glow_widens_halo():
    """辉光必须真的"发光"：淡光晕面积变大、最亮处不能被压暗（不是断链配置）。"""
    W, H = 960, 540
    out = {}
    for gl in (0.0, 1.0):
        cfg = paint_cfg(paint_glow=gl, paint_swirl=0.0, paint_diffusion=0.0)
        p = _new(W, H, cfg)
        _drive(p, cfg, 20, _line(300.0, 270.0, 700.0, 270.0, 20))
        buf, _ = _draw(p, cfg, W, H)
        a = buf[..., 3].astype(np.float32)
        out[gl] = (float((a > 16).mean()), float((a > 1).mean()),
                   float((a > 190).mean()))
    if out[1.0][0] < out[0.0][0] * 1.10:
        return False, (f"辉光没铺开淡光晕（>16 档）：{out[0.0][0] * 100:.2f}% -> "
                       f"{out[1.0][0] * 100:.2f}%")
    if out[1.0][1] < out[0.0][1]:
        return False, (f"辉光让最外圈变窄了：>1 档 {out[0.0][1] * 100:.2f}% -> "
                       f"{out[1.0][1] * 100:.2f}%")
    if out[1.0][2] < out[0.0][2] * 0.95:
        return False, (f"辉光把核心压暗了：>190 档 {out[0.0][2] * 100:.2f}% -> "
                       f"{out[1.0][2] * 100:.2f}%")
    return True, (f"淡光晕(>16) {out[0.0][0] * 100:.2f}% -> {out[1.0][0] * 100:.2f}%，"
                  f"外圈(>1) {out[0.0][1] * 100:.2f}% -> {out[1.0][1] * 100:.2f}%，"
                  f"核心(>190) {out[0.0][2] * 100:.2f}% -> {out[1.0][2] * 100:.2f}%")


def test_shading_modulates_brightness():
    """立体感 = 逐像素的明暗调制（有亮有暗），不是均匀调亮度。

    两个坑：
      * **不能量色相** —— 着色同时乘在 d 与 lum 上，`rgb = d/lum` 会把它约掉，
        立体感只体现在 alpha（亮度）上；
      * **不能量粗糙度** —— 调制量本身是亮度的梯度，平滑得很，二阶差分看不出。
    正确判据是"同像素的亮度被改了多少"：两次跑用同一 seed、同一指令，染料场
    逐像素相同，所以可以直接比 alpha。
    """
    W, H = 960, 540
    res = {}

    def run(sh):
        cfg = paint_cfg(paint_shading=sh, paint_glow=0.0, paint_swirl=0.0,
                        paint_diffusion=0.15)
        p = _new(W, H, cfg)
        p._splat(p.gw * 0.5, p.gh * 0.5, max(2.0, p.r_base * 1.6), 1.4,
                 np.array([0.4, 0.8, 1.0], np.float32))
        p._dirty = True
        _drive(p, cfg, 40, _const(W * 0.5, H * 0.5))
        return _draw(p, cfg, W, H)[0][..., 3].astype(np.float32)

    a0 = run(0.0)
    a1 = run(1.0)
    m = (a0 > 40) & (a1 > 40)
    if int(m.sum()) < 200:
        return False, f"前置条件不成立：两次都亮的像素只有 {int(m.sum())} 个"
    rel = float(np.abs(a1 - a0)[m].mean() / max(a0[m].mean(), 1e-6))
    if rel < 0.10:
        return False, f"立体感没改变逐像素亮度：平均改动量只有 {rel * 100:.1f}%"
    return True, (f"同像素平均亮度改动量 {rel * 100:.0f}%"
                  f"（{int(m.sum())} 像素，均值 {a0[m].mean():.0f} -> {a1[m].mean():.0f}）")


def test_no_grid_artifact():
    """上屏不能出现"方格纸"。

    模拟网格短边只有 ~110（格子 ~10px @1080p），直接最近邻铺到屏幕上就是满屏
    10px 硬方块。现在先在模拟网格上做**双线性**放大到"显示网格"再铺屏。

    判据：同一模拟格内部的像素标准差。最近邻必然恒为 0（整格同色 = 方块），
    双线性则会有明显渐变。测试必须带**对照组**（_BLOCK_PX 顶到天上逼出 up=1
    走旧的最近邻路径），确认判据在那条路径上确实读到 0 —— 否则判据是聋的。
    """
    W, H = 1920, 1080
    # 用默认的浓度/落色量：把浓度压低反而会让整片颜料都很平，格内差异被抹掉，
    # 判据变得没信号（实测 0.08 vs 0.58）。
    base = paint_cfg(paint_glow=0.0, paint_shading=0.0)
    saved = PaintField._BLOCK_PX
    out = {}
    try:
        for tag, bp in (("nearest", 1.0e9), ("smooth", saved)):
            PaintField._BLOCK_PX = bp
            p = _new(W, H, base)
            _drive(p, base, 80, _line(200.0, 400.0, 1700.0, 640.0, 80))
            _drive(p, base, 4, _const(1700.0, 640.0))
            buf, _ = _draw(p, base, W, H)
            out[tag] = (_cell_variation(buf, p), p.up, H / float(p._dh))
    finally:
        PaintField._BLOCK_PX = saved
    if not (out["nearest"][0] < 0.05):
        return False, (f"对照组（最近邻）的格内方差不是 0：{out['nearest'][0]:.3f}"
                       f" —— 判据不成立，不能用来宣称「已修好」")
    if out["smooth"][0] < 0.30:
        return False, (f"格内仍然近乎恒定（{out['smooth'][0]:.3f}），方格症状还在："
                       f"最近邻对照组 {out['nearest'][0]:.3f}")
    return True, (f"一个模拟格内的像素标准差：最近邻 up=1 {out['nearest'][0]:.3f}"
                  f"（整格同色 = 方块）-> 双线性 up={out['smooth'][1]} "
                  f"{out['smooth'][0]:.3f}（格边长 "
                  f"{out['nearest'][2]:.1f}px -> {out['smooth'][2]:.1f}px）")


def test_cost_scales_with_area():
    """draw 的成本必须随颜料面积走 —— 这是包围盒裁剪存在的理由。"""
    W, H = 1920, 1080
    cfg = paint_cfg()
    tiny = _new(W, H, cfg)
    _drive(tiny, cfg, 20, _line(300.0, 540.0, 520.0, 560.0, 20))
    full = _new(W, H, cfg)
    for i in range(90):
        full.set_pointer(120.0 + i * 20.0, 540.0 + 300.0 * np.sin(i / 9.0), 0)
        full.update(1.0 / 60.0, cfg)
    for _ in range(6):
        full.set_pointer(960.0, 540.0, 0)
        full.update(1.0 / 60.0, cfg)
    u32 = _fresh(W, H)[1]

    def bench(p, n=25):
        for _ in range(5):
            p.draw(u32, W, H, cfg)
        t0 = time.perf_counter()
        for _ in range(n):
            p.draw(u32, W, H, cfg)
        return (time.perf_counter() - t0) / n * 1000.0

    a = bench(tiny)
    b = bench(full)
    if a > b * 0.5:
        return False, (f"小笔触的 draw({a:.2f}ms) 相对满屏({b:.2f}ms) 没有明显便宜，"
                       f"包围盒可能失效")
    return True, (f"一小笔 {a:.2f} ms vs 满屏 {b:.2f} ms（便宜 {b / max(a, 1e-6):.1f}×）")


def test_resize():
    cfg = paint_cfg()
    p = _new(1280, 720, cfg)
    _drive(p, cfg, 30, _line(300.0, 200.0, 900.0, 500.0, 30))
    for (w, h) in ((1920, 1080), (800, 600), (3840, 1200), (1280, 720)):
        p.resize(w, h)
        _drive(p, cfg, 10, _line(200.0, 200.0, min(w - 200.0, 900.0), 400.0, 10))
        buf, _ = _draw(p, cfg, w, h)
        if has_nan_inf(buf):
            return False, f"{w}x{h} 出现 NaN/Inf"
    return True, "1280x720 / 1920x1080 / 800x600 / 3840x1200 切换干净"


def test_grid_budget():
    """网格大小必须跟着屏幕比例走，且不超预算（上屏放大是 O(格子数) 的）。"""
    rows = []
    for (w, h) in ((1920, 1080), (2560, 1080), (3840, 1200), (1280, 1024)):
        p = PaintField(w, h, seed=1)
        cells = p.gw * p.gh
        ratio = (p.gw / float(p.gh)) / (w / float(h))
        rows.append((w, h, p.gw, p.gh, cells))
        if cells > PaintField._CELLS_MAX * 1.05:
            return False, f"{w}x{h} 网格 {p.gw}x{p.gh} = {cells} 格，超预算"
        if not (0.97 < ratio < 1.03):
            return False, f"{w}x{h} 网格宽高比偏离屏幕 {ratio:.3f}（格必须近似正方）"
        if p.gw % 2 or p.gh % 2:
            return False, f"{w}x{h} 网格 {p.gw}x{p.gh} 不是偶数（辉光要做 2×2 池化）"
    return True, " · ".join(f"{w}x{h}->{gw}x{gh}({c}格)" for w, h, gw, gh, c in rows)


def test_spotlight_composite():
    """聚光灯在的时候，颜料只能写真有颜料的像素（否则会把压暗层整片抹掉）。"""
    W, H = 960, 540
    cfg = paint_cfg(spotlight_enabled=True)
    p = _new(W, H, cfg)
    _drive(p, cfg, 20, _line(300.0, 270.0, 700.0, 270.0, 20))
    buf, u32 = _fresh(W, H)
    u32[:] = 0x80202020                      # 模拟聚光灯画过的压暗底（A=0x80）
    p.draw(u32, W, H, cfg)
    far = buf[5:40, 5:40]
    if int(far[..., 3].max()) != 0x80:
        return False, f"远处像素的 alpha 被改成了 {int(far[..., 3].max())}（应为 128）"
    lit = int((buf[..., 3] > 0x90).sum())
    if lit < 200:
        return False, f"颜料只有 {lit} 像素叠上了压暗层"
    return True, f"压暗层保留（a=128），颜料叠加 {lit} 像素"


def test_scene_step():
    """Scene 接线：每帧喂指针；关闭时 reset。"""
    W, H = 960, 540
    sc = Scene(W, H)
    sc.cfg = paint_cfg()
    # Scene.step 开头会调 mouse.update() 去读真实光标，把手动设的 mx/my 冲掉；
    # 这里把它换成空实现，测试才能自己控制指针轨迹。
    sc.mouse.update = lambda dt, ox, oy: 0.0
    buf, u32 = _fresh(W, H)
    for i in range(40):
        u32[:] = 0
        sc.mouse.mx = 200.0 + i * 15.0
        sc.mouse.my = 200.0 + i * 5.0
        sc.mouse.clicked = 1 if i == 25 else 0
        sc.step(1.0 / 60.0, u32, W, H)
    if sc.paint.dye.max() <= 0.0:
        return False, "Scene.step 跑 40 帧后颜料场依然是空的（接线断了）"
    lit = int((buf[..., 3] > 8).sum())
    if lit < 100:
        return False, f"Scene 合成后只点亮 {lit} 像素"
    sc.cfg = dict(sc.cfg, paint_enabled=False)
    u32[:] = 0
    sc.step(1.0 / 60.0, u32, W, H)
    if sc.paint.dye.max() != 0.0:
        return False, "关闭颜料盘后没有调 reset（残留颜料会重新冒出来）"
    return True, f"Scene 拖动+点击 {lit} 像素；关闭后立即清场"


def test_combo_with_rain_snow():
    W, H = 960, 540
    sc = Scene(W, H)
    sc.cfg = paint_cfg(rain_enabled=True, snow_enabled=True)
    buf, u32 = _fresh(W, H)
    for i in range(60):
        u32[:] = 0
        sc.mouse.mx = 200.0 + i * 10.0
        sc.mouse.my = 250.0
        sc.step(1.0 / 60.0, u32, W, H)
    if has_nan_inf(buf):
        return False, "雨 + 雪 + 颜料同屏出现 NaN/Inf"
    blue = int(((buf[..., 0] > 90) & (buf[..., 2] < 200) & (buf[..., 3] > 40)).sum())
    warm = int(((buf[..., 2] > 150) & (buf[..., 3] > 40)).sum())
    if blue < 50 or warm < 50:
        return False, f"三层没有同时出现（冷色 {blue} / 暖色 {warm}）"
    return True, f"雨/雪/颜料三层共存（冷色 {blue} px、颜料暖色 {warm} px）"


def test_presets_wired():
    keys = set(d.DEFAULTS)
    names = d.PRESET_GROUPS.get("颜料盘", [])
    if not names:
        return False, "PRESET_GROUPS 里没有「颜料盘」分组"
    for n in names:
        if n not in d.PRESETS:
            return False, f"分组里的预设 {n} 不在 PRESETS 中"
        bad = [k for k in d.PRESETS[n] if k not in keys]
        if bad:
            return False, f"预设 {n} 含未知配置键 {bad}"
        if not d.PRESETS[n].get("paint_enabled"):
            return False, f"预设 {n} 没打开 paint_enabled"
    return True, f"{len(names)} 个预设键全部合法（{', '.join(names)}）"


def test_defaults_complete():
    keys = [k for k in d.DEFAULTS if k.startswith("paint_")]
    need = {"paint_enabled", "paint_brush", "paint_flow", "paint_swirl",
            "paint_diffusion", "paint_fade", "paint_click", "paint_glow",
            "paint_shading", "paint_hue_spread", "paint_color", "paint_opacity"}
    miss = need - set(keys)
    if miss:
        return False, f"DEFAULTS 缺 {sorted(miss)}"
    if d.DEFAULTS["paint_enabled"]:
        return False, "颜料盘默认应为关闭（它是交互特效，不是常驻氛围）"
    # 界面页写的键必须在 DEFAULTS 里存在，否则滑条是断链
    with open("fx_ui.py", encoding="utf-8") as fh:
        ui = fh.read()
    used = set(re.findall(r'"(paint_[a-z_]+)"', ui))
    ghost = sorted(used - set(keys))
    if ghost:
        return False, f"fx_ui 引用了 DEFAULTS 里没有的键：{ghost}"
    if len(used) < 11:
        return False, f"fx_ui 只用到 {len(used)} 个 paint 键，疑似漏接"
    return True, f"{len(keys)} 个配置键齐全，默认关闭；界面引用的 {len(used)} 个键全部存在"


def test_perf_budget():
    """单层成本：拖动中 update+draw 要留在预算里。"""
    W, H = 1920, 1080
    cfg = paint_cfg()
    p = _new(W, H, cfg)
    buf, u32 = _fresh(W, H)
    for i in range(60):
        p.set_pointer(200.0 + i * 25.0, 300.0 + i * 8.0, 0)
        p.update(1.0 / 60.0, cfg)
    for _ in range(6):
        u32[:] = 0
        p.draw(u32, W, H, cfg)

    def bench(fn, n=40):
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        return (time.perf_counter() - t0) / n * 1000.0

    def drag():
        p.set_pointer(300.0, 540.0, 0)
        p.update(1.0 / 60.0, cfg)
        u32[:] = 0
        p.draw(u32, W, H, cfg)

    busy = bench(drag)
    empty = PaintField(W, H, seed=37)
    empty.set_color(cfg["paint_color"])
    idle = bench(lambda: empty.draw(u32, W, H, cfg))
    # 硬门槛：空场必须是免费档（"颜料稀疏"是整个改动的前提）
    if idle > 1.0:
        return False, f"空场 draw 仍要 {idle:.2f} ms（应该≈0，说明裁剪没生效）"
    # 满屏颜料是最坏情况（双线性放大把上屏成本抬了约 3 倍，换来的是没有方格纸）。
    # 这个数字随机器当前负载波动极大（本机 8MB memcpy 在 0.5~5ms 之间跳），
    # 所以只设一个"明显病态"的硬上限 + 常规档位给警告；真正的硬断言是上面
    # 的"空场 draw ≈ 0"和 test_cost_scales_with_area 的"随面积缩放"。
    if busy > 110.0:
        return False, f"拖动中 {busy:.1f} ms/帧（病态，超过 110ms 硬上限）"
    note = "" if busy <= 40.0 else "  [WARN] 高于 40ms 常规预算（当前机器负载偏高）"
    return True, (f"拖动中 update+draw {busy:.1f} ms/帧、空场 draw {idle:.3f} ms"
                  f"（1080p 单层）{note}")


def main():
    tests = [
        test_render_60_frames,
        test_idle_is_transparent,
        test_drag_leaves_local_stroke,
        test_pointer_pushes_paint_forward,
        test_swirl_knob_moves_paint,
        test_click_expands,
        test_click_knob,
        test_no_burst_without_click,
        test_core_follows_cursor,
        test_brush_scales_stroke,
        test_flow_scales_density,
        test_fade_clears_screen,
        test_diffusion_spreads,
        test_no_runaway_accumulation,
        test_teleport_no_stroke,
        test_disabled_resets,
        test_bbox_crop_is_lossless,
        test_color_meaningful,
        test_hue_spread_knob,
        test_glow_widens_halo,
        test_shading_modulates_brightness,
        test_resize,
        test_no_grid_artifact,
        test_grid_budget,
        test_cost_scales_with_area,
        test_spotlight_composite,
        test_scene_step,
        test_combo_with_rain_snow,
        test_presets_wired,
        test_defaults_complete,
        test_perf_budget,
    ]
    ok_all = True
    for fn in tests:
        try:
            ok, msg = fn()
        except Exception as e:
            import traceback
            ok = False
            msg = f"exception: {e} | {traceback.format_exc().splitlines()[-1]}"
        ok_all = ok_all and ok
        print(f"  [{'OK' if ok else 'FAIL'}] {fn.__name__:36s} {msg}")
    print()
    print("== PAINT", "VERIFIED ==" if ok_all else "FAILED ==")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
