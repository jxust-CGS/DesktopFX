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
from fx_effects import WaterRippleField

W, H = 1920, 1080


def wav_cfg(**over):
    """默认就是类的兜底值 —— 等接进 desktop_fx 后改成读 d.DEFAULTS。"""
    c = {"wave_speed": WaterRippleField._SPEED_REF,
         "wave_persist": WaterRippleField._PERSIST_DEF,
         "wave_size": 22.0,
         "wave_strength": WaterRippleField._STRENGTH_REF}
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
