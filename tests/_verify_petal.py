# -*- coding: utf-8 -*-
"""落樱特效专项验证：
- 60 帧有动画、无 NaN / Inf、update 不返回 splash
- 深底可见度
- 边界：petal_amount = 0 / 1 / 800，petal_size 0.4 / 2.5 都不炸
- resize 后继续正常
- 主体色 / 边缘色两个色键都真的生效
- **形状**：花瓣不是圆（长轴 > 横轴），且顶端真的有凹口
- **alpha 不溢出**：近档花瓣中心必须饱和（overflow 会让最亮处反而变暗）
- 近大远小 + petal_size 倍率
- 翻面 / 自旋 / 风速三个旋钮「关掉 vs 打开」的差分断言
- 与雪、雨同屏

为什么非要测形状：「画面有变化」抓不出形状类 bug —— 雪曾经每个粒子只画出一条
45° 对角线（dx 与 dy 用了同一个索引），在 1080p 下细斜线看着就像个小点，藏了
很久。花瓣同理：椭圆和一个被"转错 15°"的花瓣在缩略图里几乎一样。
"""
import os
os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")

import numpy as np
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
from fx_effects import PetalField, RainField, SnowField


def has_nan_inf(buf):
    a = buf.view(np.int32).reshape(-1)
    return int(np.any((a == -2147483648) | (a == 0x7F800000) | (a > 0x7F800000)))


def petal_cfg(**over):
    c = dict(d.DEFAULTS)
    c["petal_enabled"] = True
    c.update(over)
    return c


def _fresh(W, H):
    buf = np.zeros((H, W, 4), np.uint8)
    return buf, buf.view(np.uint32).reshape(-1)


def test_render_60_frames():
    W, H = 1920, 1080
    p = PetalField(W, H, seed=5)
    cfg = petal_cfg(petal_amount=200, petal_opacity=0.9)
    p.set_color(cfg["petal_color"])
    p.set_edge_color(cfg["petal_edge_color"])
    p.set_amount(int(cfg["petal_amount"]), W, H)
    buf, u32 = _fresh(W, H)
    changed = 0
    prev = None
    for f in range(60):
        u32[:] = 0
        sp = p.update(0.016, cfg)
        if sp.size != 0:
            return False, "PetalField.update 返回了 splash（落樱不该有）"
        p.draw(u32, W, H, cfg)
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
    p = PetalField(W, H, seed=3)
    cfg = petal_cfg(petal_amount=260, petal_opacity=1.0)
    p.set_color(cfg["petal_color"])
    p.set_edge_color(cfg["petal_edge_color"])
    p.set_amount(int(cfg["petal_amount"]), W, H)
    buf, u32 = _fresh(W, H)
    for _ in range(40):
        p.update(0.016, cfg)
    u32[:] = 0
    p.draw(u32, W, H, cfg)
    frac = int((buf[..., 3] > 8).sum()) / (W * H)
    if frac < 0.003:
        return False, f"petal barely visible on dark bg: {frac*100:.2f}% lit"
    return True, f"{frac*100:.2f}% pixels lit on black bg (visible)"


def test_edge_amounts():
    W, H = 800, 600
    for n in (0, 1, 800):
        try:
            p = PetalField(W, H, seed=2)
            p.set_amount(n, W, H)
            cfg = petal_cfg(petal_amount=n)
            buf, u32 = _fresh(W, H)
            for _ in range(10):
                p.update(0.016, cfg)
                u32[:] = 0
                p.draw(u32, W, H, cfg)
            if has_nan_inf(buf):
                return False, f"petal_amount={n} produced NaN"
        except Exception as e:
            return False, f"petal_amount={n} crashed: {e}"
    for size in (0.4, 2.5):
        try:
            p = PetalField(W, H, seed=2)
            p.set_amount(30, W, H)
            cfg = petal_cfg(petal_amount=30, petal_size=size)
            buf, u32 = _fresh(W, H)
            for _ in range(5):
                p.update(0.016, cfg)
                u32[:] = 0
                p.draw(u32, W, H, cfg)
            if has_nan_inf(buf):
                return False, f"petal_size={size} produced NaN"
        except Exception as e:
            return False, f"petal_size={size} crashed: {e}"
    return True, "petal_amount 0/1/800 + petal_size 0.4/2.5 all clean"


def test_resize():
    p = PetalField(800, 600, seed=4)
    p.set_amount(120, 800, 600)
    cfg = petal_cfg(petal_amount=120)
    buf, u32 = _fresh(800, 600)
    for _ in range(10):
        p.update(0.016, cfg)
        u32[:] = 0
        p.draw(u32, 800, 600, cfg)
    p.resize(1920, 1080)
    buf2, u32b = _fresh(1920, 1080)
    try:
        for _ in range(10):
            p.update(0.016, cfg)
            u32b[:] = 0
            p.draw(u32b, 1920, 1080, cfg)
        if has_nan_inf(buf2):
            return False, "NaN after resize"
    except Exception as e:
        return False, f"resize crashed: {e}"
    return True, "resize 800x600 -> 1920x1080 clean"


def test_color_change():
    """主体色与边缘色各自都必须能单独改变画面。"""
    W, H = 800, 600

    def render(body, edge):
        p = PetalField(W, H, seed=1)
        p.set_amount(120, W, H)
        cfg = petal_cfg(petal_amount=120, petal_color=body, petal_edge_color=edge)
        p.set_color(body)
        p.set_edge_color(edge)
        for _ in range(20):
            p.update(0.016, cfg)
        buf, u32 = _fresh(W, H)
        p.draw(u32, W, H, cfg)
        return buf

    base = render("#FF9DB6", "#FFF0F4")
    body = render("#00A0FF", "#FFF0F4")
    edge = render("#FF9DB6", "#00A0FF")
    d_body = np.abs(base.astype(int) - body.astype(int)).sum()
    d_edge = np.abs(base.astype(int) - edge.astype(int)).sum()
    if d_body < 1e5:
        return False, f"petal_color 无效 (diff={int(d_body)})"
    if d_edge < 1e5:
        return False, f"petal_edge_color 无效 (diff={int(d_edge)})"
    return True, f"both color keys took effect (body={int(d_body)}, edge={int(d_edge)})"


def test_shape_and_tier():
    """花瓣必须「不是圆」+「顶端有凹口」+ 近大远小 + petal_size 生效。

    量形状时把 flutter 关掉（squash 恒为 1）并把 rot 固定为 0，这样花瓣长轴
    必然竖直，横向半宽固定 —— 才能用 bbox 直接验形状。
    """
    W, H = 400, 400

    def render(depth, size=1.0, rot=0.0, tumble=0.0, **over):
        p = PetalField(W, H, seed=1)
        p.set_amount(1, W, H)
        p.x[:] = 200.0
        p.y[:] = 200.0
        p.depth[:] = depth
        p.vary[:] = 1.0
        p.rot[:] = rot
        p.tumble[:] = tumble
        cfg = petal_cfg(petal_amount=1, petal_opacity=1.0, petal_size=size,
                        petal_flutter=0.0, **over)
        p.set_color(cfg["petal_color"])
        p.set_edge_color(cfg["petal_edge_color"])
        buf, u32 = _fresh(W, H)
        p.draw(u32, W, H, cfg)
        return buf, cfg

    def bbox(buf):
        ys, xs = np.nonzero(buf[..., 3] > 0)
        if xs.size == 0:
            return None
        return (xs.min(), xs.max(), ys.min(), ys.max())

    # ---- 形状：不是圆 ----
    # 判据取"长轴竖直时 bbox 竖着、转过 90° 后 bbox 横着且两轴恰好互换"：
    # 圆（或任何各向同性形状）转 90° 后 bbox 完全不变，这个断言必挂。
    # 只看"高 > 宽"不够 —— 花瓣长宽比实测只有 1.27（15° 错切会把 bbox 撑宽），
    # 阈值卡在 1.3 就会误伤。
    buf, _ = render(1.3)
    bx = bbox(buf)
    if bx is None:
        return False, "近档花瓣一个像素都没画出来"
    x0, x1, y0, y1 = bx
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    if bh <= bw:
        return False, f"rot=0 时花瓣长轴不竖直（bbox {bw}x{bh}）"
    buf90, _ = render(1.3, rot=np.pi / 2)
    b90 = bbox(buf90)
    w90, h90 = b90[1] - b90[0] + 1, b90[3] - b90[2] + 1
    if abs(w90 - bh) > 1 or abs(h90 - bw) > 1:
        return False, (f"转过 90° 后 bbox 没有互换（0°={bw}x{bh} -> "
                       f"90°={w90}x{h90}），形状各向同性/旋转没生效")
    # ---- 顶端凹口：沿长轴看，某一端的中心列比两侧列"缺一块" ----
    # 注意凹口在哪一端由形状常数决定（实测在 v>0 那端，即 rot=0 时的下端），
    # 所以判据写成"两端任一端有凹口"，再单独验它落在苍白端 —— 否则只是把
    # 实现细节抄进断言，改一两行代码测试就假挂。
    cx = 200
    off = max(2, int(bw * 0.28))

    def span(col):
        col_a = buf[y0:y1 + 1, col, 3]
        nz = np.nonzero(col_a > 0)[0]
        if not nz.size:
            return None, None
        return y0 + int(nz[0]), y0 + int(nz[-1])

    c_top, c_bot = span(cx)
    l_top, l_bot = span(cx - off)
    r_top, r_bot = span(cx + off)
    notch_top = c_top > min(l_top, r_top) + 1
    notch_bot = c_bot < max(l_bot, r_bot) - 1
    if not (notch_top or notch_bot):
        return False, (f"花瓣两端都饱满，没有凹口（中心 {c_top}~{c_bot} vs "
                       f"两侧 {l_top}~{r_top}）—— 形状退化成椭圆了")

    # 凹口所在端必须是**苍白**端：参考 shader 用 gradY 让凹口那端接近白。
    # 主体色 r-g≈98、边缘色 r-g≈15，所以越苍白 r-g 越小。
    band = max(2, (y1 - y0 + 1) // 4)

    def paleness(row_lo, row_hi):
        """该行带内花瓣的苍白度（用 r-g 表示）。

        缓冲里是**预乘**色，所以先按 alpha 还原成原色再平均。阈值取 alpha>=60
        而不是 200：花瓣边缘是渐隐的，alpha>=200 只剩核心十来个像素，两端的
        行带里一个都剩不下（第一版就这样报过"核心太小"）。
        """
        sub_a = buf[row_lo:row_hi + 1, x0:x1 + 1, 3].astype(np.float32)
        m = sub_a >= 60
        if not m.any():
            return None
        w = sub_a[m]
        rr = buf[row_lo:row_hi + 1, x0:x1 + 1, 2].astype(np.float32)[m] * 255.0 / w
        gg = buf[row_lo:row_hi + 1, x0:x1 + 1, 1].astype(np.float32)[m] * 255.0 / w
        return float((rr - gg).mean())

    p_top = paleness(y0, y0 + band - 1)
    p_bot = paleness(y1 - band + 1, y1)
    if p_top is None or p_bot is None:
        return False, "花瓣核心太小，量不出两端的苍白度差"
    if notch_bot and not (p_bot < p_top - 3):
        return False, (f"凹口端不够苍白（下端 r-g={p_bot:.0f} vs 上端 {p_top:.0f}），"
                       f"渐变与凹口没对应上")
    if notch_top and not (p_top < p_bot - 3):
        return False, (f"凹口端不够苍白（上端 r-g={p_top:.0f} vs 下端 {p_bot:.0f}），"
                       f"渐变与凹口没对应上")
    # ---- alpha 不溢出：近档中心必须饱和 ----
    ca = int(buf[200, 200, 3])
    if ca < 225:
        return False, f"近档花瓣中心没饱和 (alpha={ca})，疑似 alpha 溢出回绕"
    # ---- 近大远小 ----
    def height(depth):
        b, _ = render(depth)
        bb = bbox(b)
        return 0 if bb is None else bb[3] - bb[2] + 1
    hf, hm, hn = height(0.5), height(1.0), height(1.3)
    if not (hf < hm < hn):
        return False, f"没有近大远小: far={hf} mid={hm} near={hn}"
    # ---- petal_size 倍率 ----
    b2, _ = render(1.0, size=1.8)
    bb2 = bbox(b2)
    w2 = 0 if bb2 is None else bb2[3] - bb2[2] + 1
    if w2 <= hm:
        return False, f"petal_size 没生效 ({hm}px -> {w2}px)"
    notch = "下端" if notch_bot else "上端"
    return True, (f"bbox {bw}x{bh} -> 转90°={w90}x{h90} 两轴互换(非圆), "
                  f"凹口在{notch}(苍白端 r-g {p_bot if notch_bot else p_top:.0f}), "
                  f"中心 a={ca}, 远/中/近={hf}/{hm}/{hn}px, x1.8->{w2}px")


def test_flutter_and_spin():
    """翻面 / 自旋两个旋钮必须真的接到渲染层（关 vs 开的差分断言）。"""
    W, H = 800, 600

    def shot(flutter, tumble, rot=0.0, frames=1):
        p = PetalField(W, H, seed=1)
        p.set_amount(1, W, H)
        p.x[:] = 400.0
        p.y[:] = 300.0
        p.depth[:] = 1.2
        p.vary[:] = 1.0
        p.rot[:] = rot
        p.tumble[:] = tumble
        cfg = petal_cfg(petal_amount=1, petal_opacity=1.0, petal_size=1.0,
                        petal_flutter=flutter, petal_spin=0.0, petal_speed=0.0,
                        petal_drift=0.0, petal_wind=0.0)
        p.set_color(cfg["petal_color"])
        p.set_edge_color(cfg["petal_edge_color"])
        buf, u32 = _fresh(W, H)
        for _ in range(frames):
            p.update(0.0, cfg)
            u32[:] = 0
            p.draw(u32, W, H, cfg)
        return buf

    def lit(flutter, tumble, rot=0.0, frames=1):
        return int((shot(flutter, tumble, rot, frames)[..., 3] > 0).sum())

    # 正面对比：flutter=0 时 tumble 相位无关（宽度恒为 1）
    flat_a = lit(0.0, 0.0)
    flat_b = lit(0.0, np.pi / 2)
    if flat_a != flat_b:
        return False, f"flutter=0 时 tumble 仍在改变形状 ({flat_a} vs {flat_b})"
    # 侧面：flutter=1 且 |cos|=0 -> 压到最扁
    thin = lit(1.0, np.pi / 2)
    if not thin < flat_a * 0.55:
        return False, f"翻面没生效：正面 {flat_a}px 侧面 {thin}px（应明显变扁）"
    if thin < 8:
        return False, f"翻面压得太狠，侧面只剩 {thin}px（会闪没）"
    # 自旋：rot 不同 -> 花瓣朝向不同。这里必须比**缓冲内容**而不是点亮面积 ——
    # 旋转不改变面积，用面积比会恒等（我第一版就栽在这，白报一次"自旋没接上"）。
    b0 = shot(0.0, 0.0, rot=0.0)
    b90 = shot(0.0, 0.0, rot=np.pi / 2)
    diff = int(np.abs(b0.astype(int) - b90.astype(int)).sum())
    if diff < 1000:
        return False, f"rot 转 90° 后缓冲几乎没变 (diff={diff})，自旋没接到绘制"

    # 自旋旋钮：spin=0 时角度档不动，spin>0 时角度档必须真的变化
    def buckets(spin, frames=90):
        p = PetalField(W, H, seed=7)
        p.set_amount(1, W, H)
        cfg = petal_cfg(petal_amount=1, petal_spin=spin, petal_flutter=0.0)
        seen = set()
        for _ in range(frames):
            p.update(1.0 / 60.0, cfg)
            seen.add(int(np.floor(p.rot[0] * PetalField.ANGLE_N / (2 * np.pi)))
                     % PetalField.ANGLE_N)
        return seen
    if len(buckets(0.0)) != 1:
        return False, "petal_spin=0 时花瓣仍在自旋"
    if len(buckets(4.0)) < 3:
        return False, f"petal_spin 未生效，1.5s 只走了 {len(buckets(4.0))} 个角度档"
    return True, (f"翻面 正面{flat_a}->侧面{thin}px, 自旋 rot 差 {diff}, "
                  f"自旋旋钮 0/1.5s 档位 {len(buckets(0.0))}/{len(buckets(4.0))}")


def test_wind():
    """风速：wind=0 不横移、wind>0 横移且带阵风起伏。"""
    H = 600
    X0 = 50000.0
    WIDE = 100000        # 排除环绕折返造成的假阳性（雪那边踩过：σ/μ 算出 76）
    TAU = 2.0 * np.pi

    def x_series(wind, frames):
        p = PetalField(WIDE, H, seed=8)
        p.set_amount(1, WIDE, H)
        p.x[:] = X0
        p.y[:] = 300.0
        p.sway_amp[:] = 0.0
        p.spin[:] = 0.0
        cfg = petal_cfg(petal_amount=1, petal_speed=0.0, petal_drift=0.0,
                        petal_wind=wind, petal_spin=0.0)
        xs = []
        for _ in range(frames):
            p.update(1.0 / 60.0, cfg)
            xs.append(float(p.x[0]))
        return np.array(xs)

    still = x_series(0.0, 60)
    if abs(still[-1] - X0) > 1.0:
        return False, f"wind=0 却在横移 ({still[-1] - X0:.1f}px)"
    moved = x_series(240.0, 60)
    dx = moved[-1] - X0
    if dx < 30.0:
        return False, f"风速未生效：1s 仅横移 {dx:.1f}px"
    xs = x_series(240.0, 900)
    if xs.max() - xs.min() > WIDE * 0.5:
        return False, "风速测试中粒子绕回了（测法有误）"
    v = np.diff(xs) * 60.0
    v = v[60:]
    gust = float(v.std() / max(1e-6, abs(v.mean())))
    if gust < 0.10:
        return False, f"风速是匀速的，没有阵风 (mean={v.mean():.1f} std={v.std():.1f})"
    return True, f"wind 1s {dx:.0f}px, 阵风 σ/μ={gust:.2f}"


def test_combo_with_snow():
    """落樱 + 雪 + 雨 同屏：三层都要真的画进同一帧。

    判据用"抽掉一层看差异"，而不是"数某种色相" —— 花瓣是"主体粉 -> 边缘近白"
    的渐变，大面积像素其实接近白，拿色相阈值去数会漏（第一版就这样误报过一次
    "落樱缺失"）。抽层差分对"到底画没画上去"是直接证据。
    """
    W, H = 1280, 720

    def render(use_rain, use_snow, use_petal):
        # 每层都用固定 seed 重建，四次渲染的随机场完全一致，差分才干净
        rain = RainField(W, H, seed=7)
        snow = SnowField(W, H, seed=9)
        p = PetalField(W, H, seed=23)
        cfg = petal_cfg(rain_enabled=use_rain, snow_enabled=use_snow,
                        rain_amount=200, snow_amount=160, petal_amount=200,
                        rain_color="#5FA8D8", snow_color="#FFFFFF",
                        petal_color="#FF9DB6", petal_edge_color="#FFF0F4",
                        splash=False, petal_opacity=1.0)
        rain.set_color(cfg["rain_color"])
        rain.set_amount(200, W, H)
        snow.set_color(cfg["snow_color"])
        snow.set_amount(160, W, H)
        p.set_color(cfg["petal_color"])
        p.set_edge_color(cfg["petal_edge_color"])
        p.set_amount(200, W, H)
        buf, u32 = _fresh(W, H)
        for _ in range(20):
            if use_rain:
                rain.update(0.016, cfg)
            if use_snow:
                snow.update(0.016, cfg)
            if use_petal:
                p.update(0.016, cfg)
        if use_rain:
            rain.draw(u32, W, H, cfg)
        if use_snow:
            snow.draw(u32, W, H, cfg)
        if use_petal:
            p.draw(u32, W, H, cfg)
        return buf

    full = render(True, True, True)

    def delta(other):
        d = np.abs(full.astype(int) - other.astype(int)).sum(axis=2)
        return int((d > 4).sum()), (d > 4)

    d_rain, _ = delta(render(False, True, True))
    d_snow, _ = delta(render(True, False, True))
    d_petal, m_petal = delta(render(True, True, False))
    if d_rain < 100:
        return False, f"雨层没画上去 (抽层差异 {d_rain}px)"
    if d_snow < 100:
        return False, f"雪层没画上去 (抽层差异 {d_snow}px)"
    if d_petal < 300:
        return False, f"落樱层没画上去 (抽层差异 {d_petal}px)"
    # 花瓣贡献的像素必须偏暖（R > B），否则说明这块画上去的是别的东西
    warm = float((full[..., 2].astype(int)[m_petal]
                  > full[..., 0].astype(int)[m_petal]).mean())
    if warm < 0.8:
        return False, f"落樱贡献的像素只有 {warm*100:.0f}% 偏暖（应 >80%）"
    return True, (f"rain/snow/petal 三层共存 "
                  f"(抽层差异 {d_rain}/{d_snow}/{d_petal}px, 花瓣暖色 {warm*100:.0f}%)")


def main():
    print("== petal (落樱) verification ==")
    tests = [
        test_render_60_frames,
        test_visible_on_dark_bg,
        test_edge_amounts,
        test_resize,
        test_color_change,
        test_shape_and_tier,
        test_flutter_and_spin,
        test_wind,
        test_combo_with_snow,
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
    print("== PETAL", "VERIFIED ==" if ok_all else "FAILED ==")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
