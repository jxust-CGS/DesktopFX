# -*- coding: utf-8 -*-
"""雪特效专项验证：
- 60 帧有动画、无 NaN / Inf、不返回 splash
- 白雪落在纯黑底上应当可见（亮像素占比 > 阈值）
- 边界：snow_amount = 0 / 1 / 1000 都不炸
- resize 后继续正常
- 换色后缓冲确实不同（颜色键真的生效）
- 与雨同屏：雪 + 雨都出现在同一帧缓冲（组合能力）
- 形状/近大远小/snow_size 倍率（bbox 长宽相等 + 中心饱和）
- 闪烁 / 辉光 / 风速三个旋钮「关掉 vs 打开」的差分断言
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


def test_size_and_shape():
    """雪花必须是「圆」且「近大远小」，snow_size 倍率必须生效。

    历史回归：足迹的 dx / dy 曾由同一个索引产生（dx == dy），每个粒子实际
    只画出一条 45° 对角线；同时 alpha 可超 1.0 导致左移 24 位溢出，最亮的
    雪花中心反而变暗。这两点靠「有像素变化」是测不出来的，所以这里直接
    检查 bbox 长宽相等 + 中心 alpha 饱和 + 尺寸随深度单调。
    """
    W, H = 400, 400
    cfg = snow_cfg(snow_opacity=1.0, snow_size=1.0)

    def foot(depth, size=1.0):
        s = SnowField(W, H, seed=1)
        s.set_amount(1, W, H)
        s.x[:] = 200.0
        s.y[:] = 200.0
        s.depth[:] = depth
        s.vary[:] = 1.0
        buf = np.zeros((H, W, 4), np.uint8)
        u32 = buf.view(np.uint32).reshape(-1)
        # 量形状时必须关掉脉动与辉光：前者会压低中心 alpha（"中心饱和"
        # 判据失效），后者会把 bbox 撑大一圈（"圆"与"近大远小"都会失真）。
        s.draw(u32, W, H, snow_cfg(snow_opacity=1.0, snow_size=size,
                                   snow_twinkle=0.0, snow_glow=0.0))
        ys, xs = np.nonzero(buf[..., 3] > 0)
        if xs.size == 0:
            return 0, 0, 0
        return (xs.max() - xs.min() + 1, ys.max() - ys.min() + 1,
                int(buf[200, 200, 3]))

    dw, dh, _ = foot(0.5)
    mw, mh, _ = foot(1.0)
    nw, nh, na = foot(1.3)
    if not (dw < mw < nw):
        return False, f"not near-big-far-small: far={dw} mid={mw} near={nw}"
    if abs(nw - nh) > 1:
        return False, f"flake not round: bbox {nw}x{nh}"
    if na < 250:
        return False, f"flake center not solid (alpha={na}, expect 255)"
    bw, _, _ = foot(1.0, size=1.8)
    if bw <= mw:
        return False, f"snow_size had no effect ({mw}px -> {bw}px)"
    return True, f"far/mid/near={dw}/{mw}/{nw}px round, center a=255, x1.8->{bw}px"


def test_twinkle_glow_wind():
    """B 闪烁 / D 辉光 / E 风速 —— 三个新旋钮必须真的接到渲染层。

    历史教训：snow_size 曾经只在 DEFAULTS 与 UI 里存在、渲染层从未读取，
    滑条拖到底画面也毫无变化。只测「画面有变化」抓不出这种断链，所以这里
    对每个旋钮都做「关掉 vs 打开」的差分断言。
    """
    W, H = 800, 600

    def freeze(seed=6, amount=120):
        """静止场：关掉下落/漂移/摆动，只留待测的那一个变量。"""
        s = SnowField(W, H, seed=seed)
        s.set_amount(amount, W, H)
        s.sway_amp[:] = 0.0
        return s

    # ---------- B 闪烁 ----------
    def center_series(amp):
        s = freeze(amount=1)
        s.x[:] = 400.0
        s.y[:] = 300.0
        s.depth[:] = 1.3            # 近档：alpha 接近饱和，最容易看出"裁平"
        s.vary[:] = 1.0
        s.twk_freq[:] = 1.5
        s.twk_phase[:] = 0.0
        cfg = snow_cfg(snow_opacity=1.0, snow_size=1.0, snow_speed=0.0,
                       snow_drift=0.0, snow_twinkle=amp, snow_glow=0.0,
                       snow_wind=0.0)
        buf = np.zeros((H, W, 4), np.uint8)
        u32 = buf.view(np.uint32).reshape(-1)
        out = []
        for _ in range(30):
            s.update(1.0 / 60.0, cfg)
            u32[:] = 0
            s.draw(u32, W, H, cfg)
            out.append(int(buf[300, 400, 3]))
        return out

    flat = center_series(0.0)
    pulse = center_series(0.6)
    if max(flat) - min(flat) > 2:
        return False, f"twinkle=0 中心 alpha 仍在抖 ({min(flat)}~{max(flat)})"
    if max(pulse) - min(pulse) < 30:
        return False, f"twinkle 未生效，中心 alpha 几乎不动 ({min(pulse)}~{max(pulse)})"
    if min(pulse) < 40:
        return False, f"twinkle 幅度过大，粒子会闪没 ({min(pulse)}~{max(pulse)})"

    # ---------- D 辉光 ----------
    def lit_area(glow):
        s = freeze()
        cfg = snow_cfg(snow_amount=120, snow_opacity=0.95, snow_speed=0.0,
                       snow_drift=0.0, snow_glow=glow, snow_twinkle=0.0,
                       snow_wind=0.0)
        buf = np.zeros((H, W, 4), np.uint8)
        u32 = buf.view(np.uint32).reshape(-1)
        for _ in range(6):
            s.update(1.0 / 60.0, cfg)
        u32[:] = 0
        s.draw(u32, W, H, cfg)
        return int((buf[..., 3] > 0).sum()), int(buf[..., 3].max())

    a_off, m_off = lit_area(0.0)
    a_on, m_on = lit_area(1.0)
    if a_on < a_off * 1.15:
        return False, f"辉光未生效：点亮面积 {a_off} -> {a_on}"
    if m_on < m_off:
        return False, f"辉光把雪花核心压暗了（峰值 {m_off} -> {m_on}）"

    # ---------- E 风速 ----------
    X0 = 50000.0
    WIDE = 100000          # 画布拉到 10 万像素宽：风速测试期间绝不会碰环绕边界
    # （曾经用 800px 宽跑 15s，粒子横移出界被 wrap 折返，逐帧速度里混进一个
    #   -900px 的跳变，把 σ/μ 算成 76 —— 假阳性。测风速必须排除环绕。）

    def x_series(wind, frames):
        s = SnowField(WIDE, H, seed=8)
        s.set_amount(1, WIDE, H)
        s.x[:] = X0
        s.y[:] = 300.0
        s.sway_amp[:] = 0.0
        cfg = snow_cfg(snow_amount=1, snow_speed=0.0, snow_drift=0.0,
                       snow_wind=wind, snow_twinkle=0.0, snow_glow=0.0)
        xs = []
        for _ in range(frames):
            s.update(1.0 / 60.0, cfg)
            xs.append(float(s.x[0]))
        return np.array(xs)

    still = x_series(0.0, 60)
    if abs(still[-1] - X0) > 1.0:
        return False, f"wind=0 却在横移 ({still[-1] - X0:.1f}px)"
    moved = x_series(240.0, 60)
    dx = moved[-1] - X0
    if dx < 30.0:
        return False, f"风速未生效：1s 仅横移 {dx:.1f}px"

    xs = x_series(240.0, 900)                     # 15s，够 0.07Hz 阵风走完一圈
    if xs.max() - xs.min() > WIDE * 0.5:
        return False, "风速测试中粒子绕回了（测法有误）"
    v = np.diff(xs) * 60.0
    v = v[60:]
    gustiness = float(v.std() / max(1e-6, abs(v.mean())))
    if gustiness < 0.12:
        return False, f"风速是匀速的，没有阵风起伏 (mean={v.mean():.1f} std={v.std():.1f})"

    return True, (f"twinkle={min(pulse)}~{max(pulse)}/255, "
                  f"glow lit {a_off}->{a_on}px 峰{m_on}, "
                  f"wind 1s {dx:.0f}px 阵风σ/μ={gustiness:.2f}")


def main():
    print("== snow effect verification ==")
    tests = [
        test_render_60_frames,
        test_visible_on_dark_bg,
        test_edge_amounts,
        test_resize,
        test_color_change,
        test_combo_with_rain,
        test_size_and_shape,
        test_twinkle_glow_wind,
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
