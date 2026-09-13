# -*- coding: utf-8 -*-
"""流星雨特效专项验证：
- 60 帧有动画、无 NaN / Inf、update 不返回 splash
- 深底可见度
- 边界：meteor_amount = 0 / 1 / 400，meteor_size 0.3 / 4.0 都不炸
- resize 后继续正常
- **方向**：meteor_angle 正/负号真的改变飞行方向（不是"画了点什么"就放行）
- **拖尾是连续的**：沿轨迹投影后每个区间都必须有像素 —— 采样间距一旦大于
  直径，尾巴就断成一串虚线，而"点亮了多少像素"这种断言完全抓不到
- **头部亮、尾部暗**：亮度沿轨迹单调递减（rope trail 的本质）
- **颜色是有意义的**：头尾都设成纯红时，画上去的像素必须 R > B
  （落樱就是在这里被抓住红蓝互换的 —— 颜色错了不等于没画上去）
- 辉光 / 尾长 / 速度 / 数量 四个旋钮「关掉 vs 打开」的差分断言
- 星空开关的差分断言 + 只开星空也能单独出图
- 与雨、雪、落樱同屏

为什么非要测"连续性"和"颜色语义"：这两类 bug 在缩略图里几乎看不出来，
却能毁掉整个效果的观感。雪曾经每个粒子只画一条 45° 细斜线藏了很久，
落樱把红蓝画反也只体现在"颜色不对"，而所有"有变化 / 有像素"的断言照样通过。
"""
import os
os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")

import numpy as np
import desktop_fx as d
from fx_effects import MeteorField, PetalField, RainField, SnowField


def has_nan_inf(buf):
    a = buf.view(np.int32).reshape(-1)
    return int(np.any((a == -2147483648) | (a == 0x7F800000) | (a > 0x7F800000)))


def met_cfg(**over):
    c = dict(d.DEFAULTS)
    c["meteor_enabled"] = True
    c.update(over)
    return c


def _fresh(W, H):
    buf = np.zeros((H, W, 4), np.uint8)
    return buf, buf.view(np.uint32).reshape(-1)


def _run(m, cfg, W, H, frames, dt=1 / 60.0, reset=True):
    """跑若干帧，返回最后一次的 (buf, u32)。"""
    buf, u32 = _fresh(W, H)
    for _ in range(frames):
        m.update(dt, cfg)
        if reset:
            u32[:] = 0
        m.draw(u32, W, H, cfg)
    return buf, u32


def _lit(buf):
    return buf[..., 3] > 0


def _solo(W, H, cfg, frames=900, seed=11):
    """只放一颗流星，返回**点亮像素最多**的那一帧。

    两个坑：
      * 不能取"最后一帧"—— 流星刚出生时尾巴还没长出来，末尾几帧反而可能是空的；
        取点亮最多的一帧才是"最完整"的那一张。
      * 帧数要给够：amount=1 时生成速率 = 1/寿命，长寿流星（长尾 + 慢速）
        可能 70 帧都还没出生（早期版本就是在这里拿到空图）。
      * 只采 **n == 1** 的帧：生成速率恰好等于 1/平均寿命，偶尔会同时存在两颗，
        此时 bbox / 投影会把两颗的轨迹连成一片，"尾长"量出来是两者间距。
    """
    m = MeteorField(W, H, seed=seed)
    m.set_amount(1, W, H)
    best, best_lit, best_dir = None, -1, (0.0, 1.0)
    seen = 0
    for _ in range(frames):
        m.update(1 / 60.0, cfg)
        b = np.zeros((H, W, 4), np.uint8)
        m.draw(b.view(np.uint32).reshape(-1), W, H, cfg)
        if m.n == 1:
            seen += 1
            n = int((b[..., 3] > 0).sum())
            if n > best_lit:
                sp = float(np.hypot(m.vx[0], m.vy[0])) + 1e-6
                best_lit, best = n, b
                best_dir = (float(m.vx[0]) / sp, float(m.vy[0]) / sp)
            if seen >= 130:
                break
    return m, best, best_dir


# ------------------------------------------------------------------ 基础
def test_render_60_frames():
    W, H = 1920, 1080
    m = MeteorField(W, H, seed=5)
    cfg = met_cfg(meteor_amount=18, meteor_opacity=0.9)
    m.set_amount(18, W, H)
    buf, u32 = _fresh(W, H)
    changed = 0
    prev = None
    for f in range(60):
        u32[:] = 0
        sp = m.update(1 / 60.0, cfg)
        if sp.size != 0:
            return False, "MeteorField.update 返回了 splash（流星不该有）"
        m.draw(u32, W, H, cfg)
        if has_nan_inf(buf):
            return False, f"NaN/Inf at frame {f}"
        if f > 0 and np.abs(buf.astype(int) - prev.astype(int)).max() > 4:
            changed += 1
        prev = buf.copy()
    if changed < 30:
        return False, f"only {changed}/59 animated frames"
    return True, f"{changed}/59 frames animated, no NaN"


def test_visible_on_dark_bg():
    W, H = 1280, 720
    m = MeteorField(W, H, seed=3)
    cfg = met_cfg(meteor_amount=20, meteor_opacity=1.0)
    m.set_amount(20, W, H)
    buf, _ = _run(m, cfg, W, H, 90)
    lit = _lit(buf).sum() / (W * H) * 100
    if lit < 0.15:
        return False, f"only {lit:.2f}% pixels lit on black bg"
    return True, f"{lit:.2f}% pixels lit on black bg (visible)"


def test_edge_amounts():
    W, H = 1280, 720
    for amt in (0, 1, 400):
        m = MeteorField(W, H, seed=2)
        cfg = met_cfg(meteor_amount=amt)
        m.set_amount(amt, W, H)
        for _ in range(40):
            m.update(1 / 60.0, cfg)
        buf, u32 = _fresh(W, H)
        m.draw(u32, W, H, cfg)
        if has_nan_inf(buf):
            return False, f"NaN at amount={amt}"
        if not np.isfinite(m.x).all():
            return False, f"non-finite state at amount={amt}"
    for sz in (0.3, 4.0):
        m = MeteorField(W, H, seed=2)
        cfg = met_cfg(meteor_amount=12, meteor_size=sz)
        m.set_amount(12, W, H)
        buf, _ = _run(m, cfg, W, H, 60)
        if has_nan_inf(buf):
            return False, f"NaN at size={sz}"
    return True, "meteor_amount 0/1/400 + meteor_size 0.3/4.0 all clean"


def test_resize():
    W, H = 800, 600
    m = MeteorField(W, H, seed=4)
    cfg = met_cfg(meteor_amount=14)
    m.set_amount(14, W, H)
    _run(m, cfg, W, H, 30)
    W2, H2 = 1920, 1080
    m.resize(W2, H2)
    buf, _ = _run(m, cfg, W2, H2, 90)
    if has_nan_inf(buf):
        return False, "NaN after resize"
    if not _lit(buf).any():
        return False, "nothing drawn after resize"
    return True, "resize 800x600 -> 1920x1080 clean"


def test_amount_semantics():
    """meteor_amount 必须真的是"稳态在屏颗数"——寿命换算漏系数会虚高。"""
    W, H = 1920, 1080
    m = MeteorField(W, H, seed=8)
    for want in (6, 24):
        m.set_amount(want, W, H)
        cfg = met_cfg(meteor_amount=want)
        tot = 0
        for _ in range(600):
            m.update(1 / 60.0, cfg)
            tot += m.n
        got = tot / 600
        if not (want * 0.72 <= got <= want * 1.30):
            return False, f"meteor_amount={want} -> 稳态 {got:.1f} 颗（偏差过大）"
    return True, "meteor_amount 6/24 -> 稳态颗数符合预期"


# ------------------------------------------------------------------ 形状 / 语义
def test_angle_direction():
    """角度正负必须真的改变飞行方向（否则"画了东西"照样通过）。

    注意只量**同一颗流星**一生内的位移：跨流星取首末帧会得到随机差值。
    """
    W, H = 1280, 720
    res = {}
    for ang in (40.0, -40.0):
        m = MeteorField(W, H, seed=6)
        cfg = met_cfg(meteor_amount=1, meteor_angle=ang, meteor_stars=0.0)
        m.set_amount(1, W, H)
        best = 0.0
        run = []
        for _ in range(300):
            m.update(1 / 60.0, cfg)
            if m.n:
                run.append(float(m.x[0]))
            elif run:
                if abs(run[-1] - run[0]) > abs(best):
                    best = run[-1] - run[0]
                run = []
        if run and abs(run[-1] - run[0]) > abs(best):
            best = run[-1] - run[0]
        res[ang] = best
    if not (res[40.0] > 150.0 and res[-40.0] < -150.0):
        return False, f"angle=+40 一生位移 {res[40.0]:+.0f}px, angle=-40 -> {res[-40.0]:+.0f}px"
    return True, f"angle +40/-40 单颗一生水平位移 {res[40.0]:+.0f}/{res[-40.0]:+.0f}px（方向正确）"


def test_tail_continuous():
    """沿轨迹投影后每个区间都要有像素：采样间距 > 直径就会断成虚线。"""
    W, H = 1280, 720
    worst = None
    for cfg_over in (dict(), dict(meteor_size=0.3, meteor_length=900),
                     dict(meteor_size=4.0, meteor_length=60)):
        cfg = met_cfg(meteor_amount=1, meteor_stars=0.0, **cfg_over)
        m, buf, (ux, uy) = _solo(W, H, cfg)
        if buf is None or not _lit(buf).any():
            return False, f"{cfg_over} 什么都没画出来"
        ys, xs = np.nonzero(_lit(buf))
        proj = xs * ux + ys * uy
        lo, hi = float(proj.min()), float(proj.max())
        if hi - lo < 8.0:
            return False, f"{cfg_over} 轨迹只有 {hi-lo:.0f}px（太短，测不出连续性）"
        nb = max(10, int((hi - lo) / 6.0))
        hist, _ = np.histogram(proj, bins=nb, range=(lo, hi))
        holes = int((hist == 0).sum())
        if holes:
            return False, (f"{cfg_over} 轨迹有 {holes}/{nb} 个空档 —— 拖尾断成虚线")
        worst = (len(xs), hi - lo)
    return True, f"三档（默认/极细极长/极粗极短）轨迹均连续，最大 {worst[0]}px/{worst[1]:.0f}px"


def test_trail_smooth():
    """拖尾不光要"没有空档"，还得**亮度均匀**。

    盖章是覆盖写（画家算法）而不是求和，相邻圆盘重叠处只会取"较晚画的"
    那一个的权重。核若是尖顶（纯衰减），两圆心连线的中点只能取到 w(间距/2)，
    结果是 ~2 倍的亮度波纹 —— 一条彗尾看着就是一串珠子。空档断言查不出这个
    （每格都有像素），只有量"沿轨迹的亮度起伏"才抓得到。

    注意必须把"设计上的头亮尾暗"和"串珠振荡"分开看：
      * 不能量整条轨迹的 max-min —— 头亮尾暗是设计意图，第一版就是这么误判成
        87% 的；
      * 也不能只看窄窗口的 max-min —— 窗口稍宽就会把梯度算进去。
      正确做法：窄分段（比采样间距还窄）取每段最大 alpha，再用**移动平均**当
      趋势线，看每段掉到趋势线以下多少。串珠表现为规律性下沉。
    """
    W, H = 1920, 1080
    worst = 0.0
    for cfg_over in (dict(), dict(meteor_size=0.4), dict(meteor_size=3.0)):
        cfg = met_cfg(meteor_amount=1, meteor_stars=0.0, **cfg_over)
        m, buf, (ux, uy) = _solo(W, H, cfg, seed=21)
        if buf is None or not _lit(buf).any():
            return False, f"{cfg_over} 什么都没画出来"
        ys, xs = np.nonzero(_lit(buf))
        a = buf[ys, xs, 3].astype(np.float32)
        proj = xs * ux + ys * uy
        lo, hi = float(proj.min()), float(proj.max())
        span = hi - lo
        if span < 60.0:
            return False, f"{cfg_over} 轨迹只有 {span:.0f}px（太短，测不出起伏）"
        # 靠头的一半（避开亮头辉光与极暗尾尖），分 64 段，逐段取最大 alpha。
        # 段宽 ~2px < 采样间距，这样"落在圆心 vs 落在两心之间"才区分得开。
        b0, b1 = hi - span * 0.50, hi - span * 0.12
        nb = 64
        edges = np.linspace(b0, b1, nb + 1)
        v = np.array([float(a[(proj >= edges[i]) & (proj < edges[i + 1])].max())
                      if ((proj >= edges[i]) & (proj < edges[i + 1])).any() else 0.0
                      for i in range(nb)])
        if (v <= 0).sum() > nb * 0.1:
            return False, f"{cfg_over} 靠头段有 {(v<=0).sum()}/{nb} 段全黑"
        # 判据用"锯齿个数"而不是起伏幅度：设计上的头亮尾暗本身就是一条平滑
        # 上升的曲线，起伏幅度会被它淹掉（试过 max-min、滑动窗口 max-min、
        # 移动平均残差，对串珠都只有 ~20% 的响应，和阈值分不开）。
        # 而"局部极小点"是尺度无关的：平滑曲线单调上升 -> 0 个；
        # 串珠 -> 每隔一个采样间距就出现一个。实测 0 个 vs 十几、二十个。
        tol = 3.0
        minima = 0
        for i in range(1, nb - 1):
            if v[i] <= v[i - 1] - tol and v[i] <= v[i + 1] - tol:
                minima += 1
        worst = max(worst, minima)
        if minima > 4:
            return False, (f"{cfg_over} 沿轨迹出现 {minima} 个亮度凹点 —— 拖尾"
                           f"串珠化（核的平坦区没盖住采样间距）")
    return True, f"三档粗细均平滑，最多 {int(worst)} 个亮度凹点（平滑应为 0-2 个）"


def test_head_bright_tail_dim():
    """rope trail 的本质：亮度沿轨迹从头部单调衰减。"""
    W, H = 1280, 720
    cfg = met_cfg(meteor_amount=1, meteor_stars=0.0)
    m, buf, (ux, uy) = _solo(W, H, cfg, seed=17)
    if buf is None or not _lit(buf).any():
        return False, "没画出来"
    ys, xs = np.nonzero(_lit(buf))
    a = buf[ys, xs, 3].astype(np.float32)
    proj = xs * ux + ys * uy
    lo, hi = float(proj.min()), float(proj.max())
    span = hi - lo
    # 取分位数而不是均值：圆盘核的边缘很软，头端一圈像素 alpha 本来就近 0，
    # 用均值会把"亮头"平均成暗的。分位数量的才是"这一端最亮能到多少"。
    head = float(np.percentile(a[proj > hi - span * 0.15], 95))
    tail = float(np.percentile(a[proj < lo + span * 0.25], 95))
    if not (head > tail * 1.8 and head > 110.0):
        return False, f"头/尾 95 分位 alpha {head:.0f}/{tail:.0f}（头部不够亮或梯度不足）"
    return True, f"头/尾 95 分位 alpha {head:.0f}/{tail:.0f}（亮度沿轨迹衰减）"


def test_color_meaningful():
    """头尾都设纯红时，画上去的像素必须真的偏红 —— 抓红蓝互换。"""
    W, H = 1280, 720
    m = MeteorField(W, H, seed=9)
    cfg = met_cfg(meteor_amount=20, meteor_stars=0.0, meteor_opacity=1.0)
    m.set_amount(20, W, H)
    m.set_color("#FF0000")
    m.set_tail_color("#FF0000")
    buf, _ = _run(m, cfg, W, H, 90)
    msk = _lit(buf)
    if msk.sum() < 200:
        return False, f"几乎没画上去 ({int(msk.sum())}px)"
    # 缓冲区是**预乘**的：软边像素的 R 本身就被 alpha 压小了，
    # 直接比 R>B 会把"边缘像素"误判成"颜色不对"。必须先反预乘回 0~255。
    a = buf[..., 3].astype(np.float32)
    r = buf[..., 2].astype(np.float32) * 255.0 / np.maximum(a, 1.0)
    g = buf[..., 1].astype(np.float32) * 255.0 / np.maximum(a, 1.0)
    b = buf[..., 0].astype(np.float32) * 255.0 / np.maximum(a, 1.0)
    warm = float((r[msk] > b[msk] + 40).mean())
    if warm < 0.90:
        return False, f"纯红流星只有 {warm*100:.0f}% 的像素 R>B（红蓝通道可能反了）"
    return True, (f"纯红流星 {warm*100:.0f}% 像素 R>B "
                  f"(反预乘均值 R{r[msk].mean():.0f}/G{g[msk].mean():.0f}/B{b[msk].mean():.0f})")


def test_tail_color_gradient():
    """头尾给反差色时，沿轨迹必须真的出现双色渐变（不是单一色块）。"""
    W, H = 1280, 720
    m = MeteorField(W, H, seed=9)
    cfg = met_cfg(meteor_amount=1, meteor_stars=0.0, meteor_opacity=1.0)
    m.set_amount(1, W, H)
    m.set_color("#FFFFFF")
    m.set_tail_color("#0000FF")
    buf, best_lit, (ux, uy) = None, -1, (0.0, 1.0)
    seen = 0
    for _ in range(900):
        m.update(1 / 60.0, cfg)
        b = np.zeros((H, W, 4), np.uint8)
        m.draw(b.view(np.uint32).reshape(-1), W, H, cfg)
        if m.n == 1:
            seen += 1
            n = int((b[..., 3] > 0).sum())
            if n > best_lit:
                best_lit, buf = n, b
                sp = float(np.hypot(m.vx[0], m.vy[0])) + 1e-6
                ux, uy = float(m.vx[0]) / sp, float(m.vy[0]) / sp
            if seen >= 130:
                break
    if buf is None or not _lit(buf).any():
        return False, "没画出来"
    ys, xs = np.nonzero(_lit(buf))
    a = buf[ys, xs, 3].astype(np.float32)
    proj = xs * ux + ys * uy
    lo, hi = float(proj.min()), float(proj.max())
    span = max(1.0, hi - lo)
    # 反预乘回 0~255 再比色相：缓冲是预乘的，直接比会把"软边"当成"颜色不同"
    rr = buf[ys, xs, 2].astype(np.float32) * 255.0 / np.maximum(a, 1.0)
    bb = buf[ys, xs, 0].astype(np.float32) * 255.0 / np.maximum(a, 1.0)
    # 尾部很暗（prof 收到 0），阈值必须放宽，否则一个像素都采不到
    near_head = (proj > hi - span * 0.12) & (a > 90)
    near_tail = (proj < lo + span * 0.35) & (a > 12)
    if near_head.sum() < 5 or near_tail.sum() < 5:
        return False, f"采样不足（头 {int(near_head.sum())} / 尾 {int(near_tail.sum())}）"
    hb = (bb[near_head] - rr[near_head]).mean()      # 头：白 -> 应 ~0
    tb = (bb[near_tail] - rr[near_tail]).mean()      # 尾：蓝 -> 应明显 > 0
    if not (tb > 60.0 and tb > hb + 45.0):
        return False, f"尾端偏蓝 {tb:+.0f} 未显著强于头端 {hb:+.0f}（双色渐变没生效）"
    return True, f"尾端偏蓝 {tb:+.0f} vs 头端 {hb:+.0f}（头白尾蓝渐变生效）"


def test_opacity():
    W, H = 1280, 720
    lows = []
    for op in (0.15, 1.0):
        m = MeteorField(W, H, seed=7)
        cfg = met_cfg(meteor_amount=24, meteor_stars=0.0, meteor_opacity=op)
        m.set_amount(24, W, H)
        buf, _ = _run(m, cfg, W, H, 90)
        msk = _lit(buf)
        lows.append(float(buf[..., 3][msk].mean()) if msk.any() else 0.0)
    if not (lows[0] < lows[1] * 0.55):
        return False, f"浓度 0.15/1.0 平均 alpha {lows[0]:.0f}/{lows[1]:.0f}（没区分度）"
    return True, f"浓度 0.15/1.0 平均 alpha {lows[0]:.0f}/{lows[1]:.0f}"


# ------------------------------------------------------------------ 旋钮差分
def test_knob_differentials():
    """辉光 / 尾长 / 速度 / 数量：关 vs 开必须有可测差异。"""
    W, H = 1280, 720

    def lit_of(**over):
        m = MeteorField(W, H, seed=12)
        cfg = met_cfg(meteor_amount=24, meteor_stars=0.0)
        cfg.update(over)
        m.set_amount(int(cfg["meteor_amount"]), W, H)
        buf, _ = _run(m, cfg, W, H, 100)
        return int(_lit(buf).sum()), m, buf

    g0, _, _ = lit_of(meteor_glow=0.0)
    g1, _, b1 = lit_of(meteor_glow=1.0)
    if not (g1 > g0 * 1.10):
        return False, f"辉光 0 -> {g0}px，1 -> {g1}px（辉光没生效）"

    n_low, _, _ = lit_of(meteor_amount=4)
    n_high, _, _ = lit_of(meteor_amount=60)
    if not (n_high > n_low * 2.0):
        return False, f"数量 4 -> {n_low}px，60 -> {n_high}px（数量没生效）"

    # 尾长：单颗流星量轨迹长度
    # 尾长：单颗流星在**它这一生内**能画出的最长轨迹。
    # 必须用 1920x1080 —— 720p 下 34° 的斜尾在竖直方向最多只能放下 ~870px，
    # 900 的尾长会被屏幕裁掉，量出来和 380 差不多（屏幕裁剪伪装成"旋钮失效"）。
    def trail_len(length):
        m = MeteorField(1920, 1080, seed=13)
        cfg = met_cfg(meteor_amount=1, meteor_stars=0.0, meteor_length=length)
        m.set_amount(1, 1920, 1080)
        best, run = 0.0, 0.0
        for _ in range(1000):
            m.update(1 / 60.0, cfg)
            b = np.zeros((1080, 1920, 4), np.uint8)
            m.draw(b.view(np.uint32).reshape(-1), 1920, 1080, cfg)
            if m.n == 1:
                ys, xs = np.nonzero(_lit(b))
                if xs.size:
                    run = max(run, float(np.hypot(xs.max() - xs.min(),
                                                  ys.max() - ys.min())))
            elif run:
                best, run = max(best, run), 0.0
        return max(best, run)

    l_short = trail_len(90)
    l_long = trail_len(900)
    if not (l_long > l_short * 1.8 and l_short > 20):
        return False, f"尾长 90 -> {l_short:.0f}px，900 -> {l_long:.0f}px（尾长没生效）"

    # 速度：同一时间内的位移。必须先等到有流星存在 —— 生成是"渐入"的，
    # 帧 5 时场上还是空的（早期版本这里恒返回 0，看着像"速度旋钮失效"）。
    def travel(speed):
        m = MeteorField(W, H, seed=14)
        cfg = met_cfg(meteor_amount=1, meteor_stars=0.0, meteor_speed=speed)
        m.set_amount(1, W, H)
        for _ in range(2000):
            m.update(1 / 60.0, cfg)
            if m.n:
                break
        if not m.n:
            return 0.0
        x0, y0 = float(m.x[0]), float(m.y[0])
        for _ in range(6):
            m.update(1 / 60.0, cfg)
        if not m.n:
            return 0.0
        return float(np.hypot(m.x[0] - x0, m.y[0] - y0))

    v_slow, v_fast = travel(250), travel(1800)
    if not (v_fast > v_slow * 4.0 and v_slow > 5.0):
        return False, f"速度 250 -> {v_slow:.0f}px，1800 -> {v_fast:.0f}px（速度没生效）"

    return True, (f"辉光 {g0}->{g1}px, 数量 4/60 -> {n_low}/{n_high}px, "
                  f"尾长 90/900 -> {l_short:.0f}/{l_long:.0f}px, "
                  f"速度 250/1800 -> {v_slow:.0f}/{v_fast:.0f}px")


def test_stars():
    """星空：开关必须有差分，且只开星空（amount=0）也能单独出图。"""
    W, H = 1280, 720
    star_col = []

    def lit_of(amount, stars):
        cfg = met_cfg(meteor_amount=amount, meteor_stars=stars)
        m = MeteorField(W, H, seed=15)
        m.set_amount(amount, W, H)
        buf, _ = _run(m, cfg, W, H, 90)
        return int(_lit(buf).sum()), buf

    s0, _ = lit_of(0, 0.0)
    s1, b1 = lit_of(0, 1.0)
    if s0 != 0:
        return False, f"amount=0 且 stars=0 时仍画了 {s0}px"
    if s1 < 300:
        return False, f"只开星空只有 {s1}px（星空没画出来）"
    m0 = _twinkle_spread(W, H, 1.0)
    if m0 < 0.03:
        return False, f"星空闪烁幅度只有 {m0*100:.1f}%（固定不动像脏点）"
    all_lit, _ = lit_of(20, 0.0)
    both, _ = lit_of(20, 1.0)
    if not (both > all_lit):
        return False, f"流星+星空 {both}px 不多于只有流星 {all_lit}px"
    return True, (f"仅星空 {s1}px（逐帧亮度起伏 {m0*100:.1f}%），"
                  f"流星 {all_lit}->带星空 {both}px")


def _twinkle_spread(W, H, den):
    """量星空闪烁：整屏星点总亮度的逐帧相对起伏。"""
    cfg = met_cfg(meteor_amount=0, meteor_stars=den)
    m = MeteorField(W, H, seed=16)
    m.set_amount(0, W, H)
    vals = []
    for _ in range(150):
        m.update(1 / 60.0, cfg)
        buf, u32 = _fresh(W, H)
        m.draw(u32, W, H, cfg)
        vals.append(float(buf[..., 3].sum()))
    vals = np.array(vals)
    if vals.max() <= 0:
        return 0.0
    return float((vals.max() - vals.min()) / vals.max())


# ------------------------------------------------------------------ 同屏
def test_combo_with_rain_snow_petal():
    W, H = 1280, 720
    rain = RainField(W, H, seed=1)
    snow = SnowField(W, H, seed=2)
    petal = PetalField(W, H, seed=3)
    met = MeteorField(W, H, seed=4)
    cfg = dict(d.DEFAULTS)
    cfg.update(rain_enabled=True, snow_enabled=True, petal_enabled=True,
               meteor_enabled=True, meteor_amount=24, meteor_stars=0.0)
    rain.set_color(cfg["rain_color"])
    rain.set_amount(int(cfg["rain_amount"]), W, H)
    snow.set_color(cfg["snow_color"])
    snow.set_amount(int(cfg["snow_amount"]), W, H)
    petal.set_color(cfg["petal_color"])
    petal.set_edge_color(cfg["petal_edge_color"])
    petal.set_amount(int(cfg["petal_amount"]), W, H)
    met.set_amount(int(cfg["meteor_amount"]), W, H)

    def render(u_rain, u_snow, u_petal, u_met):
        buf, u32 = _fresh(W, H)
        for _ in range(40):
            if u_rain:
                rain.update(1 / 60.0, cfg)
            if u_snow:
                snow.update(1 / 60.0, cfg)
            if u_petal:
                petal.update(1 / 60.0, cfg)
            if u_met:
                met.update(1 / 60.0, cfg)
        if u_rain:
            rain.draw(u32, W, H, cfg)
        if u_snow:
            snow.draw(u32, W, H, cfg)
        if u_petal:
            petal.draw(u32, W, H, cfg)
        if u_met:
            met.draw(u32, W, H, cfg)
        return buf

    full = render(True, True, True, True)

    def delta(other):
        dd = np.abs(full.astype(int) - other.astype(int)).sum(axis=2)
        return int((dd > 4).sum()), (dd > 4)

    d_rain, _ = delta(render(False, True, True, True))
    d_snow, _ = delta(render(True, False, True, True))
    d_met, m_met = delta(render(True, True, True, False))
    if d_rain < 100:
        return False, f"雨层没画上去 (抽层差异 {d_rain}px)"
    if d_snow < 100:
        return False, f"雪层没画上去 (抽层差异 {d_snow}px)"
    if d_met < 200:
        return False, f"流星层没画上去 (抽层差异 {d_met}px)"
    # 抽掉的流星层像素里，原图应偏冷（默认头白尾蓝：B >= R）
    cool = float((full[..., 0].astype(int)[m_met]
                  >= full[..., 2].astype(int)[m_met] - 12).mean())
    if cool < 0.80:
        return False, f"流星贡献的像素只有 {cool*100:.0f}% 偏冷（头白尾蓝，应 >80%）"
    return True, (f"rain/snow/petal/meteor 四层共存 "
                  f"(抽层差异 {d_rain}/{d_snow}/{d_met}px, 流星冷色 {cool*100:.0f}%)")


def test_scene_step():
    """走一遍 Scene.step 真实接线（含全部预设），确认没接线错误。"""
    import fx_scene
    W, H = 1280, 720
    sc = fx_scene.Scene(W, H)
    for name, preset in [("默认", {}), ("流星雨", d.PRESETS["流星雨"]),
                         ("流星夜", d.PRESETS["流星夜"]),
                         ("双子座流星雨", d.PRESETS["双子座流星雨"])]:
        cfg = dict(d.DEFAULTS)
        cfg.update(preset)
        sc.cfg = cfg
        buf, u32 = _fresh(W, H)
        try:
            for _ in range(30):
                u32[:] = 0
                sc.step(1 / 60.0, u32, W, H)
        except Exception as e:
            return False, f"预设 {name} 走 Scene.step 抛异常: {e}"
        if has_nan_inf(buf):
            return False, f"预设 {name} 出现 NaN/Inf"
    return True, "默认 + 流星雨/流星夜/双子座流星雨 预设均通过 Scene.step"


def main():
    print("== meteor (流星雨) verification ==")
    tests = [
        test_render_60_frames,
        test_visible_on_dark_bg,
        test_edge_amounts,
        test_resize,
        test_amount_semantics,
        test_angle_direction,
        test_tail_continuous,
        test_trail_smooth,
        test_head_bright_tail_dim,
        test_color_meaningful,
        test_tail_color_gradient,
        test_opacity,
        test_knob_differentials,
        test_stars,
        test_combo_with_rain_snow_petal,
        test_scene_step,
    ]
    ok_all = True
    for fn in tests:
        try:
            ok, msg = fn()
        except Exception as e:
            import traceback
            ok, msg = False, f"exception: {e}\n{traceback.format_exc()[-400:]}"
        ok_all = ok_all and ok
        print(f"  [{'OK' if ok else 'FAIL'}] {fn.__name__:28s} {msg}")
    print()
    print("== METEOR", "VERIFIED ==" if ok_all else "FAILED ==")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
