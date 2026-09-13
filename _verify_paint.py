# -*- coding: utf-8 -*-
"""颜料盘特效专项验证。

这个效果在 CPU 上是一个"半拉格朗日平流 + 预烘 curl-noise 基流"的低分辨率
流体，观感旋钮全是概率性的，所以断言必须盯住**旋钮的含义**，不能只看
"画面有没有变化"：

  * paint_amount  -> 稳态颜料总量（不放逸）
  * paint_hue_spread -> 屏幕上同时出现的色相范围（0 = 单色，1 = 整条彩虹）
  * paint_glow    -> 核心之外的**淡光晕**面积（不是整体变亮）
  * paint_shading -> 有正有负的逐像素明暗调制（不是整体调亮度）
  * paint_fade    -> 停止加颜料后的衰减速度
  * paint_diffusion -> 峰值下降、面积铺开（同一份质量被摊薄）
  * paint_speed   -> 同一段时间内颜料被搬运的距离
  * 基色          -> 画上去的像素真的偏这个色相（必须先反预乘再比）

另外两条是本效果踩过的真实 bug，各留一个回归：
  * **质量不守恒**：update() 若在 _advect 之前缓存 self.dye 局部引用，衰减/
    扩散会作用在已被丢弃的旧数组上，颜料无限堆积 —— 所以有
    test_no_runaway_accumulation（长跑盯总量上限）。
  * **生成速率算反**：want/fade 会变成 want×寿命，默认参数下 1.9 团/秒变成
    75 团/秒 —— 所以 test_amount_semantics 直接盯质量随 amount 的标度。
"""
import os
os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")

import numpy as np

import desktop_fx as d
from fx_effects import PaintField, RainField, SnowField


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


def _mass(p):
    return float(p.dye.sum())


def _alpha_hist(buf):
    return buf[..., 3]


def _unpremul(buf):
    """反预乘回 0~255 的真彩。缓冲区里 RGB 已乘过 alpha，软边像素会被压暗，
    直接比 R>B 会得到 0%（落樱/流星都在这里吃过亏）。"""
    a = buf[..., 3].astype(np.float32)
    m = a > 24
    if not m.any():
        return None
    inv = 255.0 / a[m]
    return (buf[..., 2].astype(np.float32)[m] * inv,
            buf[..., 1].astype(np.float32)[m] * inv,
            buf[..., 0].astype(np.float32)[m] * inv)


def _hue(r, g, b):
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    dlt = np.maximum(mx - mn, 1e-6)
    h = np.where(mx == r, (g - b) / dlt % 6.0,
                 np.where(mx == g, (b - r) / dlt + 2.0, (r - g) / dlt + 4.0))
    return (h / 6.0) % 1.0


def _settle(p, cfg, W, H, secs, u32=None, draw=True):
    n = int(secs / 0.016)
    for _ in range(n):
        if u32 is not None:
            u32[:] = 0
        p.update(0.016, cfg)
        if draw and u32 is not None:
            p.draw(u32, W, H, cfg)


def _seed_blob(p, cfg):
    """铺一团已知颜料并掐掉自动生成，用来做「搬运 / 扩散」这类单团实验。"""
    p.set_amount(0)
    p._primed = True
    p.dye[:] = 0.0
    p._iu[:] = 0.0
    p._iv[:] = 0.0
    p._splat(p.gw * 0.5, p.gh * 0.5, max(2.0, p.r_base),
             1.6, np.array([1.0, 0.4, 0.2], np.float32))


# ------------------------------------------------------------------ 用例
def test_render_60_frames():
    W, H = 1280, 720
    p = PaintField(W, H, seed=37)
    cfg = paint_cfg()
    p.set_color(cfg["paint_color"])
    p.set_amount(12)
    buf, u32 = _fresh(W, H)
    changed = 0
    prev = None
    for f in range(60):
        u32[:] = 0
        if p.update(0.016, cfg) is not None:
            return False, "PaintField.update 不该有返回值"
        p.draw(u32, W, H, cfg)
        if has_nan_inf(buf):
            return False, f"NaN/Inf at frame {f}"
        if f > 0 and np.abs(buf.astype(int) - prev.astype(int)).max() > 4:
            changed += 1
        prev = buf.copy()
    if changed < 20:
        return False, f"only {changed}/59 animated frames"
    return True, f"{changed}/59 frames animated, no NaN"


def test_visible_on_dark_bg():
    W, H = 1280, 720
    p = PaintField(W, H, seed=37)
    cfg = paint_cfg()
    p.set_color(cfg["paint_color"])
    p.set_amount(12)
    buf, u32 = _fresh(W, H)
    _settle(p, cfg, W, H, 8.0, u32)
    a = _alpha_hist(buf)
    lit = float((a > 8).mean())
    strong = float((a > 160).mean())
    if lit < 0.05:
        return False, f"颜料几乎看不见：{lit*100:.1f}% 像素点亮"
    if strong < 0.002:
        return False, f"没有一处浓颜料：{strong*100:.2f}% 像素 alpha>160"
    if lit > 0.995 and strong > 0.9:
        return False, f"整屏糊死（lit {lit*100:.1f}% / strong {strong*100:.1f}%）"
    return True, (f"点亮 {lit*100:.1f}%，浓颜料 {strong*100:.2f}% "
                  f"(深底上可见且没有糊满)")


def test_amount_semantics():
    """amount 是稳态颜料总量：质量必须随 amount 单调上升，且 0 时趋于清零。"""
    W, H = 1280, 720
    ms = {}
    for n in (0, 6, 12, 40):
        p = PaintField(W, H, seed=37)
        cfg = paint_cfg(paint_amount=n)
        p.set_color(cfg["paint_color"])
        p.set_amount(n)
        _settle(p, cfg, W, H, 10.0)
        ms[n] = _mass(p)
    if not (ms[0] < ms[6] < ms[12] < ms[40]):
        return False, f"质量不随 amount 单调: {({k: round(v) for k, v in ms.items()})}"
    if ms[40] < ms[6] * 2.0:
        return False, f"amount 6->40 质量只涨 {ms[40]/max(ms[6],1e-9):.2f} 倍（应 >2）"
    # amount=0 时只剩 initial prime 的残余，必须明显低于 amount=6
    if ms[0] > ms[6] * 0.6:
        return False, f"amount=0 时质量仍有 {ms[0]:.0f}，接近 amount=6 的 {ms[6]:.0f}"
    return True, (f"质量 {[round(ms[k]) for k in (0,6,12,40)]} 随 amount 单调，"
                  f"40/6 = {ms[40]/max(ms[6],1e-9):.1f}×")


def test_no_runaway_accumulation():
    """长跑回归：固定参数下颜料总量必须收敛，不能无限堆积。"""
    W, H = 1280, 720
    p = PaintField(W, H, seed=37)
    cfg = paint_cfg()
    p.set_color(cfg["paint_color"])
    p.set_amount(12)
    marks = []
    for k in range(4):
        _settle(p, cfg, W, H, 5.0)
        marks.append(_mass(p) / (p.gw * p.gh))
    if max(marks) > 1.0:
        return False, f"颜料堆积到 mean={max(marks):.3f}（>1.0 就是整屏糊死）"
    tail = marks[-2:]
    if abs(tail[1] - tail[0]) > 0.35 * max(tail[0], 1e-6):
        return False, f"仍未收敛：{['%.3f' % v for v in marks]}"
    return True, ("20s 后收敛在 mean=%.3f，四点 " % marks[-1]
                  + "/".join("%.3f" % v for v in marks))


def test_color_meaningful():
    """基色必须真的决定画上去的色相（反预乘后再比）。"""
    W, H = 960, 540
    res = {}
    for name, hexc in (("红", "#FF0000"), ("绿", "#00FF00"), ("蓝", "#0000FF")):
        p = PaintField(W, H, seed=37)
        cfg = paint_cfg(paint_color=hexc, paint_hue_spread=0.0, paint_glow=0.0)
        p.set_color(hexc)
        p.set_amount(14)
        buf, u32 = _fresh(W, H)
        _settle(p, cfg, W, H, 8.0, u32)
        px = _unpremul(buf)
        if px is None:
            return False, f"{name}: 没有任何不透明像素"
        res[name] = px
    r, g, b = res["红"]
    if not (float((r > g).mean()) > 0.9 and float((r > b).mean()) > 0.9):
        return False, "基色 #FF0000 画出来不是偏红"
    r, g, b = res["绿"]
    if not (float((g > r).mean()) > 0.9 and float((g > b).mean()) > 0.9):
        return False, "基色 #00FF00 画出来不是偏绿"
    r, g, b = res["蓝"]
    if not (float((b > r).mean()) > 0.9 and float((b > g).mean()) > 0.9):
        return False, "基色 #0000FF 画出来不是偏蓝"
    return True, "红/绿/蓝三个基色各自画出了对应色调（反预乘后 100% 命中）"


def test_hue_spread():
    """paint_hue_spread 0 -> 单色；1 -> 屏幕上同时出现大半个色相环。"""
    W, H = 960, 540
    span = {}
    for sp in (0.0, 1.0):
        p = PaintField(W, H, seed=37)
        cfg = paint_cfg(paint_hue_spread=sp, paint_glow=0.0, paint_amount=20)
        p.set_color("#FF0000")
        p.set_amount(20)
        buf, u32 = _fresh(W, H)
        _settle(p, cfg, W, H, 14.0, u32)
        px = _unpremul(buf)
        if px is None:
            return False, f"spread={sp}: 没有不透明像素"
        r, g, b = px
        sat = (np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)) \
            / np.maximum(np.maximum(np.maximum(r, g), b), 1e-6)
        m = sat > 0.25                     # 只看有彩度的像素，灰色像素色相没意义
        if int(m.sum()) < 50:
            return False, f"spread={sp}: 有彩像素太少（{int(m.sum())}）"
        hh = _hue(r[m], g[m], b[m])
        span[sp] = _circ_span(hh)
    if span[0.0] > 0.16:
        return False, f"spread=0 时色相跨度仍有 {span[0.0]*100:.0f}%（应 <16%，接近单色）"
    if span[1.0] < 0.45:
        return False, f"spread=1 时色相只铺开 {span[1.0]*100:.0f}%（应 >45%）"
    return True, (f"色相跨度 spread=0 -> {span[0.0]*100:.0f}%，"
                  f"spread=1 -> {span[1.0]*100:.0f}%")


def _circ_span(h):
    """色相是环形的：取最大的"空档"补集作为实际跨度。"""
    s = np.sort(h)
    gaps = np.diff(np.concatenate([s, s[:1] + 1.0]))
    return float(1.0 - gaps.max())


def test_glow_adds_halo_not_brightness():
    """辉光：柔光覆盖随 paint_glow 单调铺开，且不把画面爆成一片白。

    注意不能用 `(a>0)` 当"全亮"桶 —— 高斯核尾巴让 alpha>0 恒为 ~100%，
    曾经因此得到"辉光让光晕变小"的假结论。用中间段 `>32` 才反映柔光面积。
    """
    W, H = 960, 540
    soft, blow, means = [], [], []
    for gl in (0.0, 0.5, 1.0):
        p = PaintField(W, H, seed=37)
        cfg = paint_cfg(paint_glow=gl)
        p.set_color(cfg["paint_color"])
        p.set_amount(12)
        buf, u32 = _fresh(W, H)
        _settle(p, cfg, W, H, 16.0, u32)
        a = _alpha_hist(buf).astype(np.float32)
        soft.append(float((a > 32).mean()))
        blow.append(float((a > 250).mean()))
        means.append(float(a.mean()))
    if not (soft[0] < soft[1] < soft[2]):
        return False, f"柔光覆盖不随 glow 单调：{['%.1f%%' % (v*100) for v in soft]}"
    if soft[2] < soft[0] * 1.25:
        return False, (f"glow 1.0 的柔光覆盖只从 {soft[0]*100:.1f}% 到 "
                       f"{soft[2]*100:.1f}%（应 >1.25×）")
    if max(blow) > 0.02:
        return False, f"辉光把画面爆白了（>250 的像素 {max(blow)*100:.2f}%）"
    if means[2] < means[0]:
        return False, "辉光反而让总光量下降"
    return True, ("柔光覆盖 glow 0/0.5/1.0 = "
                  + "/".join("%.1f%%" % (v*100) for v in soft)
                  + f"，无爆白（>250 最多 {max(blow)*100:.2f}%）")


def test_shading_modulates_both_ways():
    """立体感是逐像素有正有负的调制，不是整体调亮度。"""
    W, H = 960, 540
    imgs = {}
    for sh in (0.0, 1.0):
        p = PaintField(W, H, seed=37)
        cfg = paint_cfg(paint_shading=sh, paint_glow=0.0)
        p.set_color(cfg["paint_color"])
        p.set_amount(12)
        buf, u32 = _fresh(W, H)
        _settle(p, cfg, W, H, 10.0, u32)
        imgs[sh] = buf[..., 3].astype(np.int32)
    m = (imgs[0.0] > 8) & (imgs[1.0] > 8)
    diff = imgs[1.0][m] - imgs[0.0][m]
    if diff.size < 200:
        return False, "有效像素太少，无法判断"
    brighter = float((diff > 2).mean())
    darker = float((diff < -2).mean())
    if brighter < 0.12 or darker < 0.12:
        return False, (f"shading 只有单向变化：亮 {brighter*100:.1f}% / "
                       f"暗 {darker*100:.1f}%（两个方向都应 >12%）")
    return True, (f"逐像素双向调制：亮 {brighter*100:.0f}% / 暗 {darker*100:.0f}%"
                  f"（不是整体调亮度）")


def test_fade_speed():
    """paint_fade 越大，停止加料后衰减越快。"""
    W, H = 960, 540
    res = {}
    for fd in (0.05, 0.9):
        p = PaintField(W, H, seed=37)
        cfg = paint_cfg(paint_fade=fd)
        p.set_color(cfg["paint_color"])
        p.set_amount(20)
        _settle(p, cfg, W, H, 10.0)
        m0 = _mass(p)
        p.set_amount(0)
        _settle(p, cfg, W, H, 4.0)
        res[fd] = _mass(p) / max(m0, 1e-9)
    if not (res[0.9] < res[0.05] * 0.5):
        return False, (f"衰减没拉开：fade 0.05 剩 {res[0.05]*100:.0f}%，"
                       f"fade 0.9 剩 {res[0.9]*100:.0f}%")
    return True, (f"停料 4s 后：fade 0.05 剩 {res[0.05]*100:.0f}%，"
                  f"fade 0.9 剩 {res[0.9]*100:.0f}%")


def test_diffusion_spreads():
    """扩散 = 摊薄（削峰 + 铺开），**不是**消灭。用单团做受控实验：
    同一颗种子、同一段流场，唯一变量是 paint_diffusion。"""
    W, H = 960, 540
    res = {}
    for df in (0.0, 1.0):
        p = PaintField(W, H, seed=37)
        cfg = paint_cfg(paint_diffusion=df)
        p.set_color(cfg["paint_color"])
        _seed_blob(p, cfg)
        _settle(p, cfg, W, H, 2.0)
        w = p.dye.sum(axis=2)
        m = float(w.sum())
        ys, xs = np.mgrid[0:p.gh, 0:p.gw]
        cx = float((w * xs).sum() / m)
        cy = float((w * ys).sum() / m)
        var = float((w * ((xs - cx) ** 2 + (ys - cy) ** 2)).sum() / m)
        res[df] = (float(w.max()), var, m)
    if not (res[1.0][0] < res[0.0][0] * 0.90):
        return False, (f"扩散没削峰：peak {res[0.0][0]:.3f} -> {res[1.0][0]:.3f}"
                       f"（应 <0.90×）")
    if not (res[1.0][1] > res[0.0][1] * 1.04):
        return False, (f"扩散没铺开：二阶矩 {res[0.0][1]:.1f} -> {res[1.0][1]:.1f}"
                       f"（应 >1.04×）")
    # 只比"扩散 0 vs 满"的差：平流本身在边界上会掉质量（把颜料推出屏幕），
    # 那是另一回事；这里要证明的是"扩散只是摊薄，不额外消灭颜料"。
    rel = abs(res[1.0][2] - res[0.0][2]) / max(res[0.0][2], 1e-9)
    if rel > 0.05:
        return False, f"扩散额外吃掉 {rel*100:.1f}% 的质量（应 <5%）"
    return True, (f"单团 2s：peak {res[0.0][0]:.3f} -> {res[1.0][0]:.3f}，"
                  f"二阶矩 {res[0.0][1]:.1f} -> {res[1.0][1]:.1f}，"
                  f"质量只差 {rel*100:.1f}%")


def test_speed_moves_paint():
    """paint_speed 决定颜料被搬运多远（同一颗种子、同一段时间量质心位移）。"""
    W, H = 960, 540
    disp = {}
    for sp in (0.15, 3.0):
        p = PaintField(W, H, seed=37)
        cfg = paint_cfg(paint_speed=sp, paint_diffusion=0.0)
        p.set_color(cfg["paint_color"])
        _seed_blob(p, cfg)
        ys, xs = np.mgrid[0:p.gh, 0:p.gw]
        cen = []
        for _ in range(63):                       # 1.0s
            p.update(0.016, cfg)
            tot = p.dye.sum()
            cen.append(((p.dye.sum(axis=2) * xs).sum() / tot,
                        (p.dye.sum(axis=2) * ys).sum() / tot))
        x0, y0 = cen[0]
        x1, y1 = cen[-1]
        disp[sp] = float(np.hypot(x1 - x0, y1 - y0))
    if disp[3.0] < disp[0.15] * 1.5:
        return False, (f"速度没拉开位移：0.15 -> {disp[0.15]:.2f} 格，"
                       f"3.0 -> {disp[3.0]:.2f} 格")
    if disp[3.0] < 0.5:
        return False, f"最高速 1s 只搬了 {disp[3.0]:.2f} 格，几乎没动"
    return True, (f"1s 质心位移：speed 0.15 -> {disp[0.15]:.2f} 格，"
                  f"3.0 -> {disp[3.0]:.2f} 格")


def test_size_scales_blob():
    """paint_size 必须真的改单团半径 —— 必须走**真实的** _spawn 链路，
    直接自己调 _splat(r_base*size) 是绕过了配置读取，抓不出断链
    （snow_size 当年就是这么断的：只在 DEFAULTS/UI 里存在，渲染层从没读过）。"""
    W, H = 960, 540
    res = {}
    for sz in (0.4, 2.5):
        p = PaintField(W, H, seed=37)
        cfg = paint_cfg(paint_size=sz, paint_diffusion=0.0)
        p.set_color(cfg["paint_color"])
        p.set_amount(0)
        p._primed = True
        p.dye[:] = 0.0
        p._iu[:] = 0.0
        p._iv[:] = 0.0
        p._spawn(1, cfg, 0.0)          # 同一颗种子 -> 抖动相同，只差 paint_size
        d_ = p.dye.max(axis=2)
        res[sz] = float((d_ > 0.08).mean())
    if not (res[2.5] > res[0.4] * 4.0):
        return False, (f"paint_size 没生效：0.4 -> {res[0.4]*100:.2f}% 覆盖，"
                       f"2.5 -> {res[2.5]*100:.2f}%")
    return True, (f"单团覆盖 0.4x -> {res[0.4]*100:.2f}%，"
                  f"2.5x -> {res[2.5]*100:.2f}%（面积比 "
                  f"{res[2.5]/max(res[0.4],1e-9):.1f}×）")


def test_edge_amounts():
    W, H = 640, 360
    for n in (0, 1, 60):
        try:
            p = PaintField(W, H, seed=37)
            cfg = paint_cfg(paint_amount=n)
            p.set_color(cfg["paint_color"])
            p.set_amount(n)
            buf, u32 = _fresh(W, H)
            _settle(p, cfg, W, H, 4.0, u32)
            if has_nan_inf(buf):
                return False, f"amount={n} 出现 NaN/Inf"
        except Exception as e:
            return False, f"amount={n} 抛异常: {e}"
    return True, "amount 0 / 1 / 60 都不炸"


def test_resize():
    p = PaintField(1280, 720, seed=37)
    g1 = (p.gw, p.gh)
    cfg = paint_cfg()
    p.set_color(cfg["paint_color"])
    p.set_amount(12)
    buf, u32 = _fresh(1280, 720)
    _settle(p, cfg, 1280, 720, 3.0, u32)
    p.resize(3840, 1200)
    cfg["spotlight_enabled"] = False
    buf, u32 = _fresh(3840, 1200)
    p.set_amount(12)
    for _ in range(120):
        u32[:] = 0
        p.update(0.016, cfg)
        p.draw(u32, 3840, 1200, cfg)
    if has_nan_inf(buf):
        return False, "resize 后出现 NaN/Inf"
    if float((buf[..., 3] > 8).mean()) < 0.02:
        return False, "resize 后画不出东西"
    if (p.gw, p.gh) == g1:
        return False, "grid 没随分辨率重建"
    # 还要求网格真的按宽高比走（双屏 3.2:1 要明显更宽）
    if p.gw / p.gh < 2.5:
        return False, f"3840×1200 下 gw/gh={p.gw/p.gh:.2f}，网格没跟着宽高比变形"
    return True, f"{g1} -> {(p.gw, p.gh)} 重建成功，宽高比跟着走"


def test_grid_budget():
    """网格总量必须始终在预算内（否则 sim 成本失控）。"""
    worst = 0
    for (w, h) in ((1280, 720), (1920, 1080), (3840, 1200), (2560, 1440),
                   (1080, 1920), (3840, 2160), (800, 600)):
        p = PaintField(w, h, seed=1)
        n = p.gw * p.gh
        worst = max(worst, n)
        if n > PaintField._CELLS_MAX * 1.15:
            return False, f"{w}×{h} 网格 {p.gw}×{p.gh}={n} 超预算"
        if p.gw < 8 or p.gh < 8:
            return False, f"{w}×{h} 网格退化到 {p.gw}×{p.gh}"
    return True, f"7 种分辨率下网格最大 {worst} 格（预算 {PaintField._CELLS_MAX}）"


def test_spotlight_composite():
    """开启聚光灯时颜料盘必须"只覆盖有颜料的像素"，不能整屏抹掉聚光灯。"""
    W, H = 960, 540
    p = PaintField(W, H, seed=37)
    dim = np.uint32(0x99505050)                 # 一个明显的"聚光灯压暗"
    # A) 不开聚光灯：没颜料的地方必须被清成 0（全屏覆盖写）
    cfg = paint_cfg(spotlight_enabled=False, paint_amount=0)
    p.set_color(cfg["paint_color"])
    p.set_amount(0)
    p._primed = True
    p.dye[:] = 0.0
    p._splat(p.gw * 0.5, p.gh * 0.5, p.r_base, 1.6,
             np.array([1.0, 1.0, 1.0], np.float32))
    buf, u32 = _fresh(W, H)
    u32[:] = dim
    p.draw(u32, W, H, cfg)
    zeroed = float((buf.view(np.uint32).reshape(-1) == 0).mean())
    if zeroed < 0.5:
        return False, f"不开聚光灯时只清了 {zeroed*100:.0f}% 的像素（应≈全屏覆盖）"
    # B) 开聚光灯：压暗色必须原样留在没有颜料的地方
    cfg2 = paint_cfg(spotlight_enabled=True, paint_amount=0)
    buf2, u32b = _fresh(W, H)
    u32b[:] = dim
    p.draw(u32b, W, H, cfg2)
    kept = float((buf2.view(np.uint32).reshape(-1) == dim).mean())
    if kept < 0.5:
        return False, f"开了聚光灯后只保留 {kept*100:.0f}% 的压暗像素（颜料盘把它抹了）"
    return True, (f"不开聚光灯全屏覆盖（清空 {zeroed*100:.0f}%）；"
                  f"开了之后保留 {kept*100:.0f}% 压暗像素")


def test_fit_into_scene():
    """整场景合成：颜料盘层必须真的画上去，且颜色偏基色。"""
    from fx_scene import Scene
    W, H = 1280, 720
    scene = Scene(W, H)
    cfg = dict(d.DEFAULTS)
    cfg.update(rain_enabled=False, snow_enabled=False, petal_enabled=False,
               meteor_enabled=False, spotlight_enabled=False,
               paint_enabled=True, paint_amount=16, paint_glow=0.0,
               paint_hue_spread=0.0, paint_color="#FF0000")
    scene.cfg = cfg
    buf, u32 = _fresh(W, H)
    for _ in range(500):
        u32[:] = 0
        scene.step(0.016, u32, W, H)
    if has_nan_inf(buf):
        return False, "Scene 里跑出 NaN/Inf"
    lit = float((buf[..., 3] > 8).mean())
    if lit < 0.03:
        return False, f"Scene 里颜料盘几乎没画出来（{lit*100:.1f}%）"
    px = _unpremul(buf)
    if px is None:
        return False, "Scene 里没有不透明像素"
    warm = float((px[0] > px[2]).mean())
    if warm < 0.9:
        return False, f"Scene 里只有 {warm*100:.0f}% 像素偏红（基色没传进去）"
    return True, f"Scene 合成正常，{lit*100:.1f}% 点亮，{warm*100:.0f}% 偏红"


def test_combo_with_rain_snow():
    """与雨 / 雪同屏共存，三层都要有贡献。"""
    from fx_scene import Scene
    W, H = 1280, 720
    scene = Scene(W, H)

    def render(paint, rain, snow):
        cfg = dict(d.DEFAULTS)
        cfg.update(rain_enabled=rain, snow_enabled=snow,
                   petal_enabled=False, meteor_enabled=False,
                   spotlight_enabled=False, paint_enabled=paint,
                   rain_amount=400, snow_amount=300,
                   paint_amount=16, paint_glow=0.0)
        scene.cfg = cfg
        buf, u32 = _fresh(W, H)
        for _ in range(300):
            u32[:] = 0
            scene.step(0.016, u32, W, H)
        return buf

    full = render(True, True, True).astype(int)

    def delta(other):
        dd = np.abs(full - other.astype(int)).sum(axis=2)
        return int((dd > 4).sum())

    d_paint = delta(render(False, True, True))
    d_rain = delta(render(True, False, True))
    d_snow = delta(render(True, True, False))
    if d_paint < 500:
        return False, f"颜料盘层没画上去（抽层差异 {d_paint}px）"
    if d_rain < 100:
        return False, f"雨层没画上去（抽层差异 {d_rain}px）"
    if d_snow < 100:
        return False, f"雪层没画上去（抽层差异 {d_snow}px）"
    return True, f"三层共存（抽层差异 颜料 {d_paint} / 雨 {d_rain} / 雪 {d_snow} px）"


def test_presets_wired():
    names = d.PRESET_GROUPS.get("颜料盘", [])
    if names != ["颜料盘", "霓虹流体", "水墨"]:
        return False, f"PRESET_GROUPS['颜料盘'] = {names}"
    for n in names:
        if n not in d.PRESETS:
            return False, f"预设 {n} 不在 PRESETS 里"
        bad = [k for k in d.PRESETS[n] if k not in d.DEFAULTS]
        if bad:
            return False, f"预设 {n} 含 DEFAULTS 里没有的键 {bad}"
        if not d.PRESETS[n].get("paint_enabled"):
            return False, f"预设 {n} 没打开 paint_enabled"
    return True, f"3 个预设键全部合法（{', '.join(names)}）"


def test_defaults_complete():
    need = ["paint_enabled", "paint_amount", "paint_size", "paint_speed",
            "paint_diffusion", "paint_fade", "paint_glow", "paint_shading",
            "paint_hue_spread", "paint_color", "paint_opacity"]
    miss = [k for k in need if k not in d.DEFAULTS]
    if miss:
        return False, f"DEFAULTS 缺 {miss}"
    if d.DEFAULTS["paint_enabled"] is not False:
        return False, "paint_enabled 默认必须是关（不该一启动就吃 CPU）"
    return True, f"{len(need)} 个配置键齐全，默认关闭"


def main():
    print("== paint (颜料盘) verification ==")
    tests = [
        test_render_60_frames,
        test_visible_on_dark_bg,
        test_amount_semantics,
        test_no_runaway_accumulation,
        test_color_meaningful,
        test_hue_spread,
        test_glow_adds_halo_not_brightness,
        test_shading_modulates_both_ways,
        test_fade_speed,
        test_diffusion_spreads,
        test_speed_moves_paint,
        test_size_scales_blob,
        test_edge_amounts,
        test_resize,
        test_grid_budget,
        test_spotlight_composite,
        test_fit_into_scene,
        test_combo_with_rain_snow,
        test_presets_wired,
        test_defaults_complete,
    ]
    ok_all = True
    for fn in tests:
        try:
            ok, msg = fn()
        except Exception as e:
            import traceback
            ok, msg = False, f"exception: {e} | {traceback.format_exc().splitlines()[-1]}"
        ok_all = ok_all and ok
        print(f"  [{'OK' if ok else 'FAIL'}] {fn.__name__:34s} {msg}")
    print()
    print("== PAINT", "VERIFIED ==" if ok_all else "FAILED ==")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
