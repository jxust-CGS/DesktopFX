# -*- coding: utf-8 -*-
"""真实水波（WaterRippleField）专项验证 —— 第一层：**波动方程内核**。

这个特效和 `RippleField`（解析彩色圆环）是两套独立实现，所以这份脚本要能回答
一个核心问题：**它到底是不是"真的波"**，而不是"画得好看一点的圈"。

真波有四个假环做不到的签名，每一条都单独立了断言：
1. **匀速推进**：波前半径随时间线性增长（假环是 `1-(1-p)²` 先快后慢，还会到顶）。
2. **一直在跑**：3 秒时波前比 1 秒时大得多（假环的半径有硬上限 rmax，到顶即停）。
3. **线性叠加**：两滴的场 = 两滴单独的场逐格相加（波动方程是线性的；
   这是"干涉"的严格表述，而圆环只会 alpha 叠加）。
4. **边界反射**：贴墙落点，探针会**先后收到两个脉冲**（直达 + 反射回来）。
   对照组放在远离墙的位置，同一时间窗里必须收不到第二个脉冲 ——
   否则"收到脉冲"这件事说明不了任何东西。

其余是每个旋钮的**含义**（不是"有变化"）：波速真的改推进速度、阻尼真的改存活时长、
落点半径真的改初始波前、强度与能量严格成平方关系（线性方程的精确性质，可以卡得很紧）。

跑：python _verify_wave.py
"""
import os
os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")

import numpy as np

import desktop_fx as d
from fx_effects import WaterRippleField
from fx_scene import Scene

W, H = 1920, 1080


def wav_cfg(**over):
    """默认值一律从产品代码真实导入，保证测的就是用户会拿到的那一份。"""
    c = {k: v for k, v in d.DEFAULTS.items() if k.startswith("wave_")}
    c.update(over)
    return c


def _front(wf, px, py, thr):
    """波前半径：离开落点最远的、还有波的格子（px）。"""
    m = (np.abs(wf.height) > thr) | (np.abs(wf.vel) > thr)
    if not m.any():
        return 0.0
    ys, xs = np.nonzero(m)
    return float(np.sqrt((ys * wf.cell - py) ** 2 + (xs * wf.cell - px) ** 2).max())


def _run(secs=3.0, fps=60.0, w=W, h=H, pts=None, cfg=None, seed=5):
    """开一块场、落点、跑 secs 秒。返回 (field, thr)。thr 取初始峰值的 3%。"""
    cfg = cfg or wav_cfg()
    wf = WaterRippleField(w, h, seed=seed)
    for (x, y, big) in (pts or [(w * 0.5, h * 0.5, True)]):
        wf.splash(x, y, cfg, big=big)
    thr = 0.03 * float(np.abs(wf.height).max())
    for _ in range(int(secs * fps)):
        wf.update(1.0 / fps, cfg)
    return wf, thr


# ------------------------------------------------------------------ 真波的签名
def test_wave_front_travels():
    """波前必须**匀速**向外推进 —— 这是"波"和"半径随时间插值的圈"的分水岭。"""
    wf = WaterRippleField(W, H, seed=5)
    cfg = wav_cfg()
    wf.splash(960.0, 540.0, cfg, big=True)
    thr = 0.03 * float(np.abs(wf.height).max())
    marks = {}
    for i in range(1, 181):
        wf.update(1.0 / 60.0, cfg)
        t = i / 60.0
        for k in (0.5, 1.0, 2.0, 3.0):
            if k not in marks and t >= k:
                marks[k] = _front(wf, 960.0, 540.0, thr)
    ks = sorted(marks)
    if marks[ks[-1]] < 250.0:
        return False, f"3 秒才推到 {marks[ks[-1]]:.0f}px（应 > 250）"
    speeds = []
    for a, b in zip(ks, ks[1:]):
        speeds.append((marks[b] - marks[a]) / (b - a))
    if min(speeds) <= 0.0:
        return False, f"波前不是单调推进：{[round(v) for v in speeds]}"
    spread = (max(speeds) - min(speeds)) / max(sum(speeds) / len(speeds), 1e-9)
    if spread > 0.35:
        return False, (f"波前速度不稳（段间波动 {spread * 100:.0f}%）："
                       f"{[round(v) for v in speeds]}")
    return True, ("波前 " + " / ".join(f"{k}s {marks[k]:.0f}px" for k in ks)
                  + f"；分段速度 {[round(v) for v in speeds]} px/s")


def test_wave_keeps_going():
    """**判别性断言**：3 秒时波前必须远大于 1 秒时。

    `RippleField` 的环半径有硬上限（rmax），到顶就不动了、并按寿命归零 ——
    这条断言在它身上必然失败。所以它同时是"是不是真波"的判据。
    """
    wf = WaterRippleField(W, H, seed=5)
    cfg = wav_cfg()
    wf.splash(960.0, 540.0, cfg, big=True)
    thr = 0.03 * float(np.abs(wf.height).max())
    r = {}
    for i in range(1, 181):
        wf.update(1.0 / 60.0, cfg)
        t = i / 60.0
        for k in (1.0, 3.0):
            if k not in r and t >= k:
                r[k] = _front(wf, 960.0, 540.0, thr)
    if r[3.0] < r[1.0] * 2.0:
        return False, (f"波前没继续推进：1s {r[1.0]:.0f}px -> 3s {r[3.0]:.0f}px"
                       f"（应 >2×；假环在这里就到顶了）")
    return True, f"波前 1s {r[1.0]:.0f}px -> 3s {r[3.0]:.0f}px（{r[3.0] / r[1.0]:.2f}×）"


def test_framerate_independent():
    """仿真频率与帧率解耦：30 / 60 / 120fps 跑同样 3 秒，波前必须几乎一致。"""
    def at(fps):
        wf = WaterRippleField(W, H, seed=5)
        cfg = wav_cfg()
        wf.splash(960.0, 540.0, cfg, big=True)
        thr = 0.03 * float(np.abs(wf.height).max())
        for _ in range(int(3.0 * fps)):
            wf.update(1.0 / fps, cfg)
        return _front(wf, 960.0, 540.0, thr)

    base = at(30.0)
    res = {f: at(f) for f in (30.0, 60.0, 120.0)}
    worst = max(abs(v / base - 1.0) for v in res.values())
    if worst > 0.02:
        return False, ("帧率一换波前就变："
                       + "、".join(f"{f:.0f}fps {v:.0f}px" for f, v in res.items()))
    return True, ("、".join(f"{f:.0f}fps {v:.0f}px" for f, v in res.items())
                  + f"（最大偏差 {worst * 100:.2f}%）")


def test_interference_superposes():
    """线性叠加：两滴的场 = 两滴单独的场**逐格相加**。这就是"波互相穿过"。"""
    cfg = wav_cfg()
    pa, pb = (720.0, 540.0), (1200.0, 540.0)

    def fld(pts):
        wf = WaterRippleField(W, H, seed=5)
        for (x, y) in pts:
            wf.splash(x, y, cfg, big=True)
        for _ in range(168):                     # 2.8s
            wf.update(1.0 / 60.0, cfg)
        return wf

    wa, wb, wab = fld([pa]), fld([pb]), fld([pa, pb])
    err = np.abs(wab.height - (wa.height + wb.height))
    scale = float(np.abs(wab.height).max())
    rel = float(err.max()) / max(scale, 1e-12)
    if rel > 1e-4:
        return False, f"两滴的场不等于各自之和（相对偏差 {rel:.2e}）—— 不是线性的波"
    return True, f"max|两滴 − (A+B)| = {float(err.max()):.2e}，场量级 {scale:.3g}（相对 {rel:.1e}）"


def test_interference_amplifies():
    """相长干涉：两滴在中点叠加出的振幅必须明显大于单滴在中点的振幅。"""
    cfg = wav_cfg()
    mid = (960.0, 540.0)

    def peak(pts):
        wf = WaterRippleField(W, H, seed=5)
        for (x, y) in pts:
            wf.splash(x, y, cfg, big=True)
        mg, mh = int(round(mid[0] / wf.cell)), int(round(mid[1] / wf.cell))
        best = 0.0
        for _ in range(300):                     # 5s
            wf.update(1.0 / 60.0, cfg)
            best = max(best, abs(float(wf.height[mh, mg])))
        return best

    one = peak([(720.0, 540.0)])
    two = peak([(720.0, 540.0), (1200.0, 540.0)])
    if one <= 0.0:
        return False, "单滴波根本没走到中点，前置条件不成立"
    if two < one * 1.7:
        return False, f"两滴在中点没有增强：单滴 {one:.4g} -> 两滴 {two:.4g}（应 >1.7×）"
    return True, f"中点振幅 单滴 {one:.4g} -> 两滴 {two:.4g}（{two / one:.2f}× 相长干涉）"


def test_boundary_reflects():
    """贴墙落点：探针必须**先收到直达波、过一会儿再收到反射回来的波**。

    关键是带对照组 —— 把落点挪到远离墙的地方（波在 5 秒内回不来），
    同一个时间窗里就不该有第二个脉冲。没有对照的话，"探针有信号"什么都证明不了。
    """
    cfg = wav_cfg()

    def trace(drop_x, probe_x, secs=4.0):
        wf = WaterRippleField(W, H, seed=5)
        wf.splash(drop_x, 540.0, cfg, big=True)
        gx = int(round(probe_x / wf.cell))
        gy = int(round(540.0 / wf.cell))
        out = []
        for _ in range(int(secs * 60)):
            wf.update(1.0 / 60.0, cfg)
            out.append(abs(float(wf.height[gy, gx])))
        return np.asarray(out)

    # 落点与探针间距都是 180px，只有"探针离墙多远"不同
    near = trace(240.0, 60.0)      # 墙在 x=0，直达 180px / 反射 360px
    far = trace(1140.0, 960.0)     # 墙远在 2 秒以外
    pk_n, pk_f = near.max(), far.max()
    # 直达脉冲大约 1.7s 到达，反射大约 3.3s —— 看 2.9s 之后还有没有新的脉冲
    tail_n = float(near[int(2.9 * 60):].max())
    tail_f = float(far[int(2.9 * 60):].max())
    if tail_f > 0.08 * pk_f:
        return False, (f"对照组本身就有回波（尾段 {tail_f:.3g} vs 峰值 {pk_f:.3g}）"
                       f"—— 判据不成立，得换个几何")
    if tail_n < 0.08 * pk_n:
        return False, (f"贴墙的探针没收到反射波：尾段 {tail_n:.3g} vs 峰值 {pk_n:.3g}"
                       f"（墙没把波挡回来）")
    return True, (f"贴墙：峰值 {pk_n:.3g}，2.9s 后回波 {tail_n:.3g}"
                  f"（{tail_n / pk_n * 100:.0f}%）；对照组尾段 {tail_f:.3g}（无回波）")


# ------------------------------------------------------------------ 旋钮的含义
def test_speed_knob():
    """wave_speed 必须真的改**推进速度**，而且是单调的。"""
    res = {}
    for sp in (90.0, 180.0, 270.0):
        wf = WaterRippleField(W, H, seed=5)
        cfg = wav_cfg(wave_speed=sp)
        wf.splash(960.0, 540.0, cfg, big=True)
        thr = 0.03 * float(np.abs(wf.height).max())
        for _ in range(120):                     # 2s
            wf.update(1.0 / 60.0, cfg)
        res[sp] = _front(wf, 960.0, 540.0, thr) / 2.0
    ks = sorted(res)
    if not (res[ks[0]] < res[ks[1]] < res[ks[2]]):
        return False, f"波速不单调：{[round(res[k]) for k in ks]} px/s"
    if res[ks[2]] < res[ks[0]] * 2.0:
        return False, f"波速档位拉不开：{[round(res[k]) for k in ks]} px/s（应 ≥2×）"
    return True, "表观波速 " + "、".join(f"{k:.0f}->{res[k]:.0f}px/s" for k in ks)


def test_persist_knob():
    """wave_persist 必须真的改**存活时长**：同样跑 6 秒，留下的能量要有明显差别。"""
    res = {}
    for p in (0.0, 0.45, 1.0):
        wf = WaterRippleField(W, H, seed=5)
        cfg = wav_cfg(wave_persist=p)
        wf.splash(960.0, 540.0, cfg, big=True)
        for _ in range(360):                     # 6s
            wf.update(1.0 / 60.0, cfg)
        res[p] = wf.energy()
    ks = sorted(res)
    if not (res[ks[0]] < res[ks[1]] * 0.75 and res[ks[1]] < res[ks[2]] * 0.75):
        return False, ("阻尼档位拉不开："
                       + "、".join(f"persist={k} E={res[k]:.3g}" for k in ks))
    return True, "6 秒后剩余能量 " + "、".join(f"persist={k} {res[k]:.3g}" for k in ks)


def test_strength_quadratic():
    """能量必须严格 ∝ 强度² —— 线性波动方程的精确性质，所以阈值可以卡很紧。

    这条同时覆盖"强度旋钮真的接到了落点上"和"方程没被写成非线性"。
    """
    vals = {}
    for s in (0.04, 0.08, 0.16):
        wf = WaterRippleField(W, H, seed=5)
        cfg = wav_cfg(wave_strength=s)
        wf.splash(960.0, 540.0, cfg, big=True)
        for _ in range(60):                      # 1s
            wf.update(1.0 / 60.0, cfg)
        vals[s] = wf.energy() / (s * s)
    lo, hi = min(vals.values()), max(vals.values())
    if (hi - lo) / max(hi, 1e-12) > 0.02:
        return False, ("能量与强度²不成正比（方程非线性或强度没接上）："
                       + "、".join(f"s={k} E/s²={v:.2f}" for k, v in vals.items()))
    return True, ("E/strength² " + "、".join(f"{k}: {v:.2f}" for k, v in vals.items())
                  + f"（离散度 {(hi - lo) / hi * 100:.2f}%）")


def test_size_knob():
    """wave_size 必须真的改**落点半径**（初始扰动范围），不是断链配置。"""
    res = {}
    for sz in (10.0, 22.0, 44.0):
        wf = WaterRippleField(W, H, seed=5)
        cfg = wav_cfg(wave_size=sz)
        wf.splash(960.0, 540.0, cfg, big=True)
        thr = 0.03 * float(np.abs(wf.height).max())
        for _ in range(30):                      # 0.5s
            wf.update(1.0 / 60.0, cfg)
        res[sz] = _front(wf, 960.0, 540.0, thr)
    ks = sorted(res)
    if not (res[ks[0]] < res[ks[1]] < res[ks[2]]):
        return False, f"落点半径没生效：{[round(res[k]) for k in ks]} px"
    return True, "0.5s 波前 " + "、".join(f"size={k:.0f}->{res[k]:.0f}px" for k in ks)


# ------------------------------------------------------------------ 边界与稳定性
def test_idle_is_dead():
    """不落点就必须彻底静止：能量 0、active_box None、update 返回 0（一帧都不推进）。"""
    wf = WaterRippleField(W, H, seed=5)
    cfg = wav_cfg()
    steps = 0
    for _ in range(300):
        steps += wf.update(1.0 / 60.0, cfg)
    if wf.energy() != 0.0:
        return False, f"空场竟然有能量 {wf.energy():.3g}"
    if wf.active_box() is not None:
        return False, f"空场 active_box 不是 None：{wf.active_box()}"
    if steps != 0:
        return False, f"空场还在推进（300 帧跑了 {steps} 步）"
    return True, "300 帧：能量 0、active_box None、推进 0 步"


def test_dies_out_by_itself():
    """落点后不再喂输入：能量**包络**必须单调衰减，最终自己归零并把场清空。

    **不能逐帧断言"能量在降"** —— 这是 leapfrog 格式，总能量在 h（势）与 g（动）
    之间来回换，逐帧本来就会小幅振荡。实测最大一次是 ×1.038，而且集中在最初
    17 帧：落点只往 h 里注入，随后才转成动能。所以按 0.5 秒采样看包络。
    """
    wf = WaterRippleField(W, H, seed=5)
    cfg = wav_cfg()
    wf.splash(960.0, 540.0, cfg, big=True)
    seq = []
    prev = wf.energy()
    max_rise = 1.0
    frames = 0
    while wf._alive and frames < 8000:
        wf.update(1.0 / 60.0, cfg)
        e = wf.energy()
        if prev > 1e-6:                      # 快归零时的浮点噪声不算
            max_rise = max(max_rise, e / prev)
        prev = e
        frames += 1
        if frames % 30 == 0:
            seq.append(e)
    if frames >= 8000:
        return False, "跑 130 秒还没自然散尽（阻尼断链了？）"
    bad = [i for i in range(1, len(seq)) if seq[i] >= seq[i - 1]]
    if bad:
        return False, (f"0.5 秒采样的能量包络不是单调下降（第 {bad[0]} 个采样点上涨）"
                       f"：{[f'{v:.3g}' for v in seq[:8]]}...")
    if max_rise > 1.06:
        return False, f"没有新输入却出现 ×{max_rise:.3f} 的能量反弹（应只是格式振荡 ≤1.06）"
    if wf.energy() != 0.0:
        return False, f"归零后场里还有 {wf.energy():.3g}"
    return True, (f"{frames / 60.0:.1f} 秒自然散尽；{len(seq)} 个 0.5s 采样点全部下降，"
                  f"瞬态最大反弹 ×{max_rise:.3f}")


def test_never_blows_up():
    """狂点 + 连续拖动 + 极限参数：必须始终有限、有界、不出 NaN。"""
    cfg = wav_cfg(wave_speed=1000.0, wave_persist=1.0, wave_strength=4.0)
    wf = WaterRippleField(W, H, seed=11)
    rng = np.random.RandomState(0)
    for _ in range(300):
        wf.drop(rng.uniform(0, W), rng.uniform(0, H), 33.0, 4.0)
    for i in range(1800):
        wf.drop(120.0 + (i % 1700), 540.0 + 120 * np.sin(i / 40.0), 22.0, 4.0)
        wf.update(1.0 / 60.0, cfg)
    hmax = float(np.abs(wf.height).max())
    gmax = float(np.abs(wf.vel).max())
    finite = bool(np.isfinite(wf.height).all() and np.isfinite(wf.vel).all())
    if not finite:
        return False, "场里出现了 NaN / Inf"
    if hmax > WaterRippleField._H_MAX or gmax > WaterRippleField._H_MAX:
        return False, f"越过了钳位：|h|max={hmax:.3g} |g|max={gmax:.3g}"
    return True, f"30 秒狂点+拖动：|h|max={hmax:.3f} |g|max={gmax:.3f}（钳位 8.0）"


def test_steady_state_is_bounded():
    """连续拖动（每帧一个落点）必须收敛到稳态，不能越积越高把波峰削平。"""
    wf = WaterRippleField(W, H, seed=5)
    cfg = wav_cfg()
    peaks = []
    for i in range(1800):
        wf.drop(120.0 + (i % 1700), 540.0 + 120 * np.sin(i / 40.0), 22.0,
                cfg["wave_strength"])
        wf.update(1.0 / 60.0, cfg)
        if i in (600, 1200, 1799):
            peaks.append(float(np.abs(wf.height).max()))
    if max(peaks) >= WaterRippleField._H_MAX * 0.5:
        return False, (f"连续拖动把场推到了 {max(peaks):.3f}，逼近钳位 "
                       f"{WaterRippleField._H_MAX}（波峰会被削平）")
    if peaks[-1] > peaks[0] * 2.0:
        return False, f"还在累积：第 600 帧 {peaks[0]:.3f} -> 第 1800 帧 {peaks[-1]:.3f}"
    return True, ("第 600/1200/1800 帧峰值 "
                  + " / ".join(f"{v:.3f}" for v in peaks) + "（稳态，未触钳位）")


def test_resize_rebuilds():
    """换分辨率必须重建网格、清空场、并且波速不变（波速是物理量，不该跟分辨率走）。"""
    wf = WaterRippleField(W, H, seed=5)
    cfg = wav_cfg()
    wf.splash(960.0, 540.0, cfg, big=True)
    for _ in range(120):
        wf.update(1.0 / 60.0, cfg)
    thr_small = 0.03 * float(np.abs(wf.height).max())
    r_small = _front(wf, 960.0, 540.0, thr_small)
    if wf.energy() <= 0.0:
        return False, "前置条件不成立：resize 前场里就没有波"

    wf.resize(3840, 1200)
    if wf.energy() != 0.0 or wf.active_box() is not None:
        return False, "resize 之后没有清场（旧网格的波会以错误的比例冒出来）"

    w2 = WaterRippleField(3840, 1200, seed=5)
    w2.splash(1920.0, 600.0, cfg, big=True)
    thr_big = 0.03 * float(np.abs(w2.height).max())
    for _ in range(120):
        w2.update(1.0 / 60.0, cfg)
    r_big = _front(w2, 1920.0, 600.0, thr_big)
    if abs(r_big / max(r_small, 1e-9) - 1.0) > 0.10:
        return False, (f"换分辨率后波前推进对不上：1920 宽 {r_small:.0f}px vs "
                       f"3840 宽 {r_big:.0f}px（2 秒，应基本一致）")
    return True, (f"resize 清场 ✓；2 秒波前 1920x1080 {r_small:.0f}px / "
                  f"3840x1200 {r_big:.0f}px（{r_big / max(r_small, 1e-9):.2f}×）")


# ------------------------------------------------------------------ 上屏渲染
def _buf(w=W, h=H):
    b = np.zeros((h, w, 4), np.uint8)
    return b, b.view(np.uint32).reshape(-1)


def _render(wf, cfg, w=W, h=H):
    b, u32 = _buf(w, h)
    wf.draw(u32, w, h, cfg)
    return b


def _lit_stats(buf):
    """反预乘后的统计。**颜色通道必须反预乘回 0~255 再看** —— 缓冲区是预乘的，
    软边像素的 RGB 会被 alpha 压小（落樱、颜料盘都在这里栽过）。"""
    a = buf[..., 3].astype(np.float32)
    rgb = buf[..., :3].astype(np.float32)[..., ::-1]
    lit = a > 0
    bright = lit & (rgb.max(axis=2) > 0)
    return dict(cov=float(lit.mean()), amax=float(a.max()),
                amean=float(a[lit].mean()) if lit.any() else 0.0,
                bright=int(bright.sum()), dark=int((lit & ~bright).sum()))


def test_render_writes_pixels():
    """点击之后必须真的画到屏幕上。"""
    wf = WaterRippleField(W, H, seed=5)
    cfg = wav_cfg()
    wf.splash(960.0, 540.0, cfg, big=True)
    for _ in range(30):
        wf.update(1.0 / 60.0, cfg)
    s = _lit_stats(_render(wf, cfg))
    if s["cov"] < 0.003:
        return False, f"点击后几乎没有像素：覆盖 {s['cov'] * 100:.3f}%"
    return True, (f"点击后 0.5s：覆盖 {s['cov'] * 100:.2f}%、"
                  f"alpha 均 {s['amean']:.1f}、峰 {s['amax']:.0f}")


def test_render_idle_blank():
    """空闲时必须**一步不推进、一个像素不写**（这是它敢常开的前提）。"""
    wf = WaterRippleField(W, H, seed=5)
    cfg = wav_cfg()
    b, u32 = _buf()
    steps = 0
    for _ in range(300):
        u32[:] = 0
        steps += wf.update(1.0 / 60.0, cfg)
        wf.draw(u32, W, H, cfg)
    n = int((b[..., 3] > 0).sum())
    if steps != 0 or n != 0:
        return False, f"空闲还在动：推进 {steps} 步、写了 {n} 个像素"
    return True, "空闲 300 帧：0 步推进、0 像素"


def test_bright_and_dark_pixels():
    """提亮与压暗必须**同时**出现 —— 只有亮环就是发光圈，只有暗环就是墨渍。"""
    wf = WaterRippleField(W, H, seed=5)
    cfg = wav_cfg(wave_opacity=1.0, wave_highlight=0.0)
    wf.splash(960.0, 540.0, cfg, big=True)
    for _ in range(30):
        wf.update(1.0 / 60.0, cfg)
    s = _lit_stats(_render(wf, cfg))
    if s["bright"] < 500 or s["dark"] < 500:
        return False, (f"提亮 {s['bright']} 个 / 压暗 {s['dark']} 个 —— 缺一侧，"
                       f"看起来就不是水面")
    return True, f"同一圈的两侧：提亮 {s['bright']} 像素、压暗 {s['dark']} 像素"


def test_light_direction_orientates_shading():
    """`wave_light` 必须真的改**明暗方位**：亮部方位角要跟滑条值对上。

    **这条抓到过一个真 bug**：`dep = ∇h·L` 一开始直接用了 `(cos, sin)`，
    结果把光设成"从右边来"、亮部却出现在**反**方向 180° 处，滑条完全名不副实。
    """
    got, bad = {}, []
    for deg in (0.0, 90.0, 180.0, 270.0):
        wf = WaterRippleField(W, H, seed=5)
        cfg = wav_cfg(wave_light=deg)
        wf.splash(960.0, 540.0, cfg, big=True)
        for _ in range(30):
            wf.update(1.0 / 60.0, cfg)
        dep = wf.shade(cfg)
        ys, xs = np.mgrid[0:wf.gh, 0:wf.gw]
        dx = (xs - 960.0 / wf.cell) * wf.cell
        dy = (ys - 540.0 / wf.cell) * wf.cell
        m = (np.abs(dep) > 0.1) & (np.hypot(dx, dy) > 30.0)
        if int(m.sum()) < 200:
            return False, "前置条件不成立：着色的格太少"
        pos = dep > 0
        a = float(np.degrees(np.arctan2(dy[m & pos].mean(), dx[m & pos].mean())) % 360.0)
        got[deg] = a
        err = abs((a - deg + 180.0) % 360.0 - 180.0)
        if err > 12.0:
            bad.append((deg, a, err))
    if bad:
        return False, ("亮部方位没跟着光照走：" +
                       "、".join(f"light={x:.0f}->{y:.0f}(差{e:.0f}°)" for x, y, e in bad))
    return True, "亮部方位 " + "、".join(f"{k:.0f}->{got[k]:.0f}" for k in sorted(got))


def test_color_tints_highlight():
    """颜色语义：提亮像素反预乘后必须真的是那个色（红、蓝各验一次）。"""
    res = {}
    for name, hexs in (("红", "#FF0000"), ("蓝", "#0000FF")):
        wf = WaterRippleField(W, H, seed=5)
        cfg = wav_cfg(wave_color=hexs, wave_opacity=1.0)
        wf.set_color(hexs)
        wf.splash(960.0, 540.0, cfg, big=True)
        for _ in range(30):
            wf.update(1.0 / 60.0, cfg)
        b = _render(wf, cfg)
        a = b[..., 3].astype(np.float32)
        rgb = b[..., :3].astype(np.float32)[..., ::-1]
        m = (a > 60) & (rgb.max(axis=2) > 0)
        if int(m.sum()) < 200:
            return False, f"{name}：提亮像素太少（{int(m.sum())}）"
        res[name] = (rgb[m] / np.maximum(a[m], 1.0)[..., None] * 255.0).mean(axis=0)
    if not (res["红"][0] > res["红"][1] * 1.8 and res["红"][0] > res["红"][2] * 1.8):
        return False, f"红色没生效：反预乘均值(R,G,B) {np.round(res['红'], 1)}"
    if not (res["蓝"][2] > res["蓝"][0] * 1.8 and res["蓝"][2] > res["蓝"][1] * 1.8):
        return False, f"蓝色没生效：反预乘均值(R,G,B) {np.round(res['蓝'], 1)}"
    return True, ("反预乘均值(R,G,B) 红 " + str(np.round(res["红"], 0))
                  + " / 蓝 " + str(np.round(res["蓝"], 0)))


def test_render_knob_differentials():
    """四个着色旋钮的「关掉 vs 打开」差分（每条都测**含义**，不是"有变化"）。"""
    def stat(**over):
        wf = WaterRippleField(W, H, seed=5)
        cfg = wav_cfg(**over)
        wf.splash(960.0, 540.0, cfg, big=True)
        for _ in range(30):
            wf.update(1.0 / 60.0, cfg)
        return _lit_stats(_render(wf, cfg))

    base = stat()
    off = stat(wave_opacity=0.0)
    if off["cov"] != 0.0:
        return False, f"浓度=0 还在画：覆盖 {off['cov'] * 100:.2f}%"
    c0 = stat(wave_contrast=0.0, wave_highlight=0.0)
    if c0["bright"] > base["bright"] * 0.02:
        return False, (f"迎光提亮=0（连高光也关）还剩 {c0['bright']} 个亮像素"
                       f"（原本 {base['bright']}）")
    s0 = stat(wave_shading=0.0)
    if s0["dark"] > base["dark"] * 0.2:
        return False, f"背光压暗=0 没削掉暗部：{base['dark']} -> {s0['dark']}"
    h0 = stat(wave_highlight=0.0)
    if h0["amax"] > base["amax"] * 0.95:
        return False, f"浪尖高光=0 没压低峰值：{base['amax']:.0f} -> {h0['amax']:.0f}"
    return True, (f"浓度=0 -> 0 像素；提亮关 {base['bright']}->{c0['bright']}、"
                  f"压暗关 {base['dark']}->{s0['dark']}、"
                  f"高光关峰值 {base['amax']:.0f}->{h0['amax']:.0f}")


def test_render_crop_is_lossless():
    """包围盒裁剪必须无损：把 box 强行撑到整块网格重画一遍，两者要**逐像素一致**。

    这里有个容易漏的坑：包围盒按"还有没有波"（高度阈值）划，可上屏画的是**坡度**，
    两个都低于阈值的相邻格之间照样能有可观的坡度差。绘制阈值因此用的是
    `_render_h()` 而不是 `_EPS`（见那里的推导）。
    """
    for age in (30, 120, 400):
        wf = WaterRippleField(W, H, seed=5)
        cfg = wav_cfg()
        wf.splash(960.0, 540.0, cfg, big=True)
        for _ in range(age):
            wf.update(1.0 / 60.0, cfg)
        a = _render(wf, cfg)
        orig = WaterRippleField.active_box

        def full(self, pad=3, thr=None):
            return (0, 0, self.gh, self.gw)

        WaterRippleField.active_box = full
        try:
            b2 = _render(wf, cfg)
        finally:
            WaterRippleField.active_box = orig
        diff = int((a.view(np.uint32) != b2.view(np.uint32)).sum())
        if diff:
            return False, f"波龄 {age / 60.0:.1f}s：裁剪版差 {diff} 个像素（切掉了可见的波）"
    if WaterRippleField(W, H)._render_h() >= WaterRippleField._EPS:
        return False, "绘制阈值没有比存活阈值更严 —— 盒子外会有可见的波被裁掉"
    return True, ("波龄 0.5 / 2.0 / 6.7s 三档，裁剪版与整块网格版逐像素完全一致"
                  f"（绘制阈值 {WaterRippleField(W, H)._render_h():.1e} "
                  f"< 存活阈值 {WaterRippleField._EPS:.1e}）")


# ------------------------------------------------------------------ 观感
def _cell_variation(buf, wf):
    """**同一个模拟格内部的像素标准差**。

    上屏是最近邻，所以每个"显示格"内部恒定；而一个模拟格被切成 up² 个显示格：
    最近邻时它们同值（σ = 0，整格就是一块硬方块），双线性之后它们各不相同
    （σ > 0，格内就有渐变）。这个判据简单、无周期依赖、物理意义直接。

    **分组相位必须跟 draw 里上屏的映射一致**（`round((p+0.5)/cell − 0.5)`）——
    差半个像素就会把相邻两格混进一组、读出一个假的非零值（第一版就栽在这）。

    性能说明：不用"格子边界处的梯度集中度"。双线性出来的曲面本身是分段线性的，
    梯度同样逐格跳跃，那个指标对两种做法都报警、根本分不开（颜料盘试过）。
    """
    a = buf[..., 3].astype(np.float32)
    ys, xs = np.nonzero(a > 4)
    if ys.size < 2000:
        return 0.0
    gy = np.rint((ys + 0.5) / wf.cell - 0.5).astype(np.int64)
    gx = np.rint((xs + 0.5) / wf.cell - 0.5).astype(np.int64)
    k = np.unique(gy * np.int64(1 << 20) + gx, return_inverse=True)[1]
    key = (k // 64) * np.int64(64) + (k % 64)
    order = np.argsort(key, kind="stable")
    key, v = key[order], a[ys, xs][order]
    edges = np.flatnonzero(np.diff(key)) + 1
    starts = np.concatenate(([0], edges))
    ends = np.concatenate((edges, [key.size]))
    keep = (ends - starts) >= 3
    if keep.sum() < 30:
        return 0.0
    return float(np.mean([v[s:e].std() for s, e in zip(starts[keep], ends[keep])]))


def _drag(wf, cfg, secs=2.2, click_at=1.2, w=W, h=H):
    """拖一段 + 中途点一下，返回最后一帧的缓冲区（每帧清空，只看"此刻的波"）。"""
    buf, u32 = _buf(w, h)
    n = int(secs * 60)
    for i in range(1, n + 1):
        u32[:] = 0
        x = 200.0 + (i * 14.4) % (w - 400.0)
        y = h * 0.5 + h * 0.16 * np.sin(i / 17.0)
        if i % 3 == 0:
            wf.splash(x, y, cfg)
        if abs(i - click_at * 60) < 0.5:
            wf.splash(x, y, cfg, big=True)
        wf.update(1.0 / 60.0, cfg)
        wf.draw(u32, w, h, cfg)
    return buf


def test_no_grid_artifact():
    """不许有"方格纸"：同一模拟格内部的像素必须有渐变（σ > 0）。

    **带对照组**：把 `_BLOCK_PX` 顶到天上逼出 up=1（走最近邻老路径），确认这个
    判据在那条路上读到 0 —— 否则判据是聋的，修没修好都读不出来。
    """
    saved = WaterRippleField._BLOCK_PX
    try:
        WaterRippleField._BLOCK_PX = 1e9
        wf, cfg = WaterRippleField(W, H, seed=5), wav_cfg()
        ctrl = _cell_variation(_drag(wf, cfg), wf)
        ctrl_up = wf._up
        WaterRippleField._BLOCK_PX = saved
        wf2, cfg2 = WaterRippleField(W, H, seed=5), wav_cfg()
        got = _cell_variation(_drag(wf2, cfg2), wf2)
        new_up = wf2._up
    finally:
        WaterRippleField._BLOCK_PX = saved
    if ctrl_up != 1:
        return False, f"对照组没退到最近邻（up={ctrl_up}），这条测不出来"
    if ctrl > 0.5:
        return False, f"对照组（最近邻）本该读到 ~0，实测 {ctrl:.3f} —— 判据不灵敏"
    if got < 2.0:
        return False, (f"放大后格内仍然几乎没变化（σ={got:.3f}）—— 方格纸还在；"
                       f"对照组 {ctrl:.3f}")
    return True, (f"格内 σ：最近邻 up={ctrl_up} -> {ctrl:.3f}（整格一块方块），"
                  f"双线性 up={new_up} -> {got:.3f}（显示格 "
                  f"{wf2.cell / new_up:.2f}px）")


def test_intensity_is_restrained():
    """默认档对桌面的**干扰程度**必须被压住。

    这是一条防回退的断言：它叠在桌面上是一层半透明明暗条纹，峰值就是"桌面被改动了
    多少"。旧默认 0.85/0.55/0.90/0.85 让大量像素顶到 0.85 的不透明度
    （峰 217、p95 也是 217 —— 曲线都被削平了），用户反馈"对整个桌面观看影响比较大"。
    """
    wf, cfg = WaterRippleField(W, H, seed=5), wav_cfg()
    buf = _drag(wf, cfg)
    s = _lit_stats(buf)
    a = buf[..., 3].astype(np.float32)
    lit = a > 0
    p95 = float(np.percentile(a[lit], 95)) if lit.any() else 0.0
    means = float(a[lit].mean()) if lit.any() else 0.0
    if s["amax"] > 150 or p95 > 110 or means > 32:
        return False, (f"默认档太重：峰 {s['amax']:.0f} / p95 {p95:.0f} / 均 {means:.1f}"
                       f"（上限 150 / 110 / 32）")
    if s["amax"] < 40:
        return False, f"默认档轻到看不见了：峰 {s['amax']:.0f}"
    return True, (f"默认档 alpha 峰 {s['amax']:.0f} / p95 {p95:.0f} / 均 {means:.1f} / "
                  f"覆盖 {s['cov'] * 100:.1f}%")


def test_preset_ladder_is_ordered():
    """五档预设的强度必须**单调递增**（用户就是拿它们对比的）。

    量的是"屏幕像素被改动的总量" `Σ alpha` —— 比只看峰值稳，因为它同时反映
    强度与覆盖。相邻两档必须差 25% 以上，否则用户根本分不出哪档是哪个。
    """
    names = [n for n in d.PRESET_GROUPS.get("真实水波", []) if n in d.PRESETS]
    if len(names) < 5:
        return False, f"「真实水波」分组只有 {len(names)} 个预设（要 5 档）"
    tot = {}
    for n in names:
        cfg = dict(d.DEFAULTS)
        cfg.update(d.PRESETS[n])
        wf = WaterRippleField(W, H, seed=5)
        buf = _drag(wf, cfg)
        a = buf[..., 3].astype(np.float64)
        tot[n] = float(a.sum())
    seq = [tot[n] for n in names]
    bad = [i for i in range(1, len(seq)) if seq[i] <= seq[i - 1] * 1.25]
    if bad:
        return False, ("档位强度没拉开："
                       + "、".join(f"{names[i-1]} {seq[i-1]:.3g} >= {names[i]} {seq[i]:.3g}"
                                   for i in bad))
    return True, "、".join(f"{n} {tot[n] / tot[names[-1]] * 100:.0f}%" for n in names) +         "（最强档 = 100%）"


# ------------------------------------------------------------------ 接线
def _scene(cfg, w=W, h=H):
    sc = Scene(w, h)
    sc.cfg = cfg
    # Scene.step 会真的去读鼠标；测试里换成固定行程的桩，才能自己控制轨迹
    sc.mouse.update = lambda *a, **k: 8.0
    return sc


def test_scene_wiring():
    """Scene 接线：开水波要真的画出像素；关掉要立刻清场（不留残波）。"""
    cfg = dict(d.DEFAULTS)
    cfg["wave_enabled"] = True
    sc = _scene(cfg)
    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    peak = 0
    for i in range(120):
        u32[:] = 0
        sc.mouse.mx = 300.0 + i * 9.0
        sc.mouse.my = 540.0 + 90.0 * np.sin(i / 15.0)
        sc.mouse.clicked = 1 if i == 80 else 0
        sc.step(1.0 / 60.0, u32, W, H)
        peak = max(peak, int((buf[..., 3] > 0).sum()))
    if peak < 500:
        return False, f"跑了 120 帧，单帧最多只点亮 {peak} 个像素（接线断了）"
    if not sc.wave._alive:
        return False, "Scene 里水波根本没活起来"
    sc.cfg = dict(cfg, wave_enabled=False)
    u32[:] = 0
    sc.step(1.0 / 60.0, u32, W, H)
    if sc.wave._alive or sc.wave.energy() != 0.0:
        return False, "关闭水波后没清场（残留的波会在重新打开时冒出来）"
    return True, f"120 帧拖动+点击，单帧最多 {peak} 像素；关闭后立即清场"


def test_scene_both_ripples_at_once():
    """两套涟漪必须能**同时开**并各自出图 —— 它们本来就是并存供用户挑的。"""
    cfg = dict(d.DEFAULTS, wave_enabled=True, ripple_move=True, ripple_click=True)
    sc = _scene(cfg)
    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)
    hit = 0
    for i in range(90):
        u32[:] = 0
        sc.mouse.mx = 300.0 + i * 9.0
        sc.mouse.my = 540.0
        sc.step(1.0 / 60.0, u32, W, H)
        hit = max(hit, int((buf[..., 3] > 0).sum()))
    rings = int(sc.ripples.active.sum())
    wenv = sc.wave.energy()
    if rings <= 0:
        return False, "彩色涟漪没被触发（接线断了）"
    if wenv <= 0.0:
        return False, "真实水波没被触发（接线断了）"
    if hit <= 0:
        return False, "两套同开时一个像素都没画出来"
    # 关掉水波之后，涟漪必须不受影响（否则就是两套互相踩了）
    sc.cfg = dict(cfg, wave_enabled=False)
    sc.wave.reset()
    for i in range(20):
        u32[:] = 0
        sc.mouse.mx = 800.0 + i * 9.0
        sc.mouse.my = 540.0
        sc.step(1.0 / 60.0, u32, W, H)
    if int(sc.ripples.active.sum()) <= 0 or sc.wave.energy() != 0.0:
        return False, "关掉水波后涟漪跟着没了（两套互相踩）"
    return True, f"同开：涟漪活跃 {rings} 环、水波能量 {wenv:.3g}；关水波后涟漪照常"


def test_presets_valid():
    """「真实水波」预设：键都在 DEFAULTS 里、都真的开了水波、且能被 Scene 跑通。"""
    names = [n for n in d.PRESET_GROUPS.get("真实水波", []) if n in d.PRESETS]
    if len(names) < 3:
        return False, f"「真实水波」分组只有 {len(names)} 个预设"
    for n in names:
        bad = [k for k in d.PRESETS[n] if k not in d.DEFAULTS]
        if bad:
            return False, f"预设 {n} 有非法键 {bad}"
        if not d.PRESETS[n].get("wave_enabled"):
            return False, f"预设 {n} 没打开 wave_enabled"
    if d.DEFAULTS.get("wave_enabled") is not False:
        return False, "真实水波必须默认关闭"
    hit = {}
    for n in names:
        cfg = dict(d.DEFAULTS)
        cfg.update(d.PRESETS[n])
        w, h = 960, 540
        sc = _scene(cfg, w, h)
        bb = np.zeros((h, w, 4), np.uint8)
        uu = bb.view(np.uint32).reshape(-1)
        for i in range(90):
            uu[:] = 0
            sc.mouse.mx = 200.0 + i * 7.0
            sc.mouse.my = 270.0 + 60.0 * np.sin(i / 12.0)
            sc.step(1.0 / 60.0, uu, w, h)
        hit[n] = int((bb[..., 3] > 0).sum())
    dead = [n for n, v in hit.items() if v < 200]
    if dead:
        return False, f"预设跑 90 帧没出图：{dead}"
    return True, "、".join(f"{n} {hit[n]}px" for n in names)


def main():
    tests = [
        test_wave_front_travels,
        test_wave_keeps_going,
        test_framerate_independent,
        test_interference_superposes,
        test_interference_amplifies,
        test_boundary_reflects,
        test_speed_knob,
        test_persist_knob,
        test_strength_quadratic,
        test_size_knob,
        test_idle_is_dead,
        test_dies_out_by_itself,
        test_never_blows_up,
        test_steady_state_is_bounded,
        test_resize_rebuilds,
        test_render_writes_pixels,
        test_render_idle_blank,
        test_bright_and_dark_pixels,
        test_light_direction_orientates_shading,
        test_color_tints_highlight,
        test_render_knob_differentials,
        test_render_crop_is_lossless,
        test_no_grid_artifact,
        test_intensity_is_restrained,
        test_preset_ladder_is_ordered,
        test_scene_wiring,
        test_scene_both_ripples_at_once,
        test_presets_valid,
    ]
    ok_all = True
    for fn in tests:
        try:
            ok, msg = fn()
        except Exception as e:
            import traceback
            ok, msg = False, f"exception: {e}\n{traceback.format_exc()[-400:]}"
        ok_all = ok_all and ok
        print(f"  [{'OK' if ok else 'FAIL'}] {fn.__name__:30s} {msg}")
    print()
    print("== WAVE", "VERIFIED ==" if ok_all else "FAILED ==")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
