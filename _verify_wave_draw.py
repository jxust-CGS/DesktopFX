# -*- coding: utf-8 -*-
"""水面「上屏」这一步的专项验证（快，约 1 秒）—— 查表着色 + 直写上屏。

为窗口水波做优化时（2026-09-15）把上屏从 33.6 ms 压到 18.7 ms（4K、满屏波）：
  * **查表着色**：原来那条二十多趟数组运算的流水线只取决于 `dep` 一个标量
    （最贵的打包一项 11.5 ms @52.7 万格），于是预计算成 8192 级表，运行时
    只剩"量化 + 查表"，约 2 ms；
  * **solo 直写**：鼠标层这帧只有水波时缓冲区本来就是空的，"逐像素比掩码"
    （3.3M 像素 7.0 ms）纯属白付，改成整片直写只要 1.0 ms。

优化最怕"数值上等价、画面上不等价"，所以这份脚本钉四件事：

1. **画出来跟原式一样**：按 `_shade_lut` 说明里的公式逐字独立重算一遍再比字节。
2. **`solo=True` 只是换写法，不是换画法**：干净缓冲上必须与掩码版**逐位相同**。
3. **两者的语义边界不许串门**：干净缓冲上 solo == 掩码版；**脏缓冲**上 solo
   整框覆盖（= 干净渲染），而掩码版必须保住别人的像素。
4. **查表缓存必须真的命中**（否则"优化"退化成每帧重算一张 8192 点的表）。

跑：python _verify_wave_draw.py
"""
import os
os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")

import numpy as np

import desktop_fx as d
import fx_effects as fe
from fx_effects import WaterRippleField

W, H = 1280, 720


def wav_cfg(**over):
    """默认值一律从产品代码真实导入，保证测的就是用户会拿到的那一份。"""
    c = {k: v for k, v in d.DEFAULTS.items() if k.startswith("wave_")}
    c["wave_enabled"] = True
    c.update(over)
    return c


def _buf(w=W, h=H):
    b = np.zeros((h, w, 4), np.uint8)
    return b, b.view(np.uint32).reshape(-1)


def _aged(cfg, seed=5, hits=((640.0, 360.0, True), (300.0, 200.0, False)),
          frames=30):
    """造一片"有亮有暗"、够大够密的波场（每滴都要算进包围盒）。"""
    wf = WaterRippleField(W, H, seed=seed)
    wf.set_color(cfg.get("wave_color", "#FFFFFF"))
    for cx, cy, big in hits:
        wf.splash(cx, cy, cfg, big=big)
    for _ in range(frames):
        wf.update(1.0 / 60.0, cfg)
    return wf


# ------------------------------------------------------------------ 1. 原式
def _formula_lut(n, ks, kd, ksp, op, tint):
    """`_shade_lut` 说明里那条链的**独立重算**（写清楚点，方便对读）。

        pos    = clip(dep, 0, 1)        neg = clip(-dep, 0, 1)
        bright = clip(pos^1.5·ks + pos^4·ksp, 0, 1)
        alpha  = clip(bright + neg·kd, 0, 1) · 255·op
        rgb    = bright · 255·op · 染色(b,g,r)

    `tint` 是 **0~1 归一化**的 (b, g, r) —— 和 `WaterRippleField._tint` 一样
    （`hex_to_bgr` 给的是 0~255，实测时忘了除 255 会让打包溢出到隔壁通道）。
    """
    dep = np.linspace(-1.0, 1.0, n, dtype=np.float64)
    pos = np.clip(dep, 0.0, 1.0)
    neg = np.clip(-dep, 0.0, 1.0)
    bright = np.clip(pos ** 1.5 * ks + pos ** 4 * ksp, 0.0, 1.0)
    # bright 与 dark 互斥（pos、neg 必有一个是 0），所以括号里必然 ≤ 1
    alpha = np.clip((bright + neg * kd) * 255.0 * op, 0.0, 255.0)
    lit = bright * 255.0 * op
    cb, cg, cr = tint
    return fe._premul_pack(
        np.floor(lit * cb + 0.5).astype(np.uint32),
        np.floor(lit * cg + 0.5).astype(np.uint32),
        np.floor(lit * cr + 0.5).astype(np.uint32),
        np.floor(alpha + 0.5).astype(np.uint32))


def test_lut_matches_formula():
    """查表 = 原式：每条通道的差不超过 1 个字节（只剩量化误差）。"""
    cases = [
        ("默认白", dict(), "#FFFFFF"),
        ("高对比 + 满浓度", dict(wave_contrast=1.2, wave_shading=1.0,
                            wave_highlight=1.2, wave_opacity=0.20), "#FFFFFF"),
        ("只剩压暗", dict(wave_contrast=0.0, wave_highlight=0.0), "#FFFFFF"),
        ("红染色", dict(wave_opacity=0.20), "#FF0000"),
        ("青染色", dict(wave_opacity=0.12), "#00FFFF"),
        ("极低浓度", dict(wave_opacity=0.003), "#FFFFFF"),
    ]
    worst = 0
    for label, over, tint_hex in cases:
        cfg = wav_cfg(**over)
        wf = WaterRippleField(W, H, seed=5)
        wf.set_color(tint_hex)
        ks = float(np.clip(cfg["wave_contrast"], 0.0, 1.5))
        kd = float(np.clip(cfg["wave_shading"], 0.0, 1.0))
        ksp = float(np.clip(cfg["wave_highlight"], 0.0, 1.5))
        op = float(cfg["wave_opacity"])
        got = wf._shade_lut(ks, kd, ksp, op)
        exp = _formula_lut(WaterRippleField._LUT_N, ks, kd, ksp, op, wf._tint)
        if got.shape != exp.shape or got.dtype != exp.dtype:
            return False, f"{label}：表形状/类型不对 {got.dtype}{got.shape}"
        diff = np.abs(got.view(np.uint8).reshape(-1, 4).astype(np.int16)
                      - exp.view(np.uint8).reshape(-1, 4).astype(np.int16))
        md = int(diff.max())
        worst = max(worst, md)
        if md > 1:
            bad = np.where(diff.max(axis=1) > 1)[0]
            i = int(bad[0])
            return False, (f"{label}：最大通道差 {md}（> 1 个量化台阶），"
                           f"{bad.size} 个点不符，首个 idx={i} "
                           f"查表={got.view(np.uint8).reshape(-1, 4)[i]} "
                           f"原式={exp.view(np.uint8).reshape(-1, 4)[i]}")
    # 染色的通道顺序曾经错过一次（存成 RGB 时设 #FF0000 画出来是蓝的），
    # 这里顺带钉住：红色染色只允许红通道有值。
    wf = WaterRippleField(W, H, seed=5)
    wf.set_color("#FF0000")
    tbl = wf._shade_lut(1.0, 0.0, 0.0, 0.20).view(np.uint8).reshape(-1, 4)
    peak = int(round(255.0 * 0.20))              # bright=1 -> 提亮量 255·浓度
    if not (int(tbl[:, 2].max()) == peak and tbl[:, 0].max() == 0
            and tbl[:, 1].max() == 0):
        return False, ("染色通道顺序不对（R 应在 BGR 的第三个字节）："
                       f"峰 B={tbl[:, 0].max()} G={tbl[:, 1].max()} "
                       f"R={tbl[:, 2].max()}（期望 R={peak}）")
    return True, f"6 组参数下最大通道差 {worst} 字节，染色通道顺序正确"


def test_lut_cache():
    """参数/染色不变时必须复用同一张表（否则每帧白建 8192 点的表）。"""
    wf = WaterRippleField(W, H, seed=5)
    a = wf._shade_lut(0.4, 0.3, 0.4, 0.1)
    if wf._shade_lut(0.4, 0.3, 0.4, 0.1) is not a:
        return False, "同样的参数重建了表（缓存没命中）"
    if wf._shade_lut(0.5, 0.3, 0.4, 0.1) is a:
        return False, "对比度变了却复用了旧表"
    b = wf._shade_lut(0.5, 0.3, 0.4, 0.1)
    wf.set_color("#FF0000")
    if wf._shade_lut(0.5, 0.3, 0.4, 0.1) is b:
        return False, "换染色后复用了旧表（红蓝会互换）"
    if wf._shade_lut(0.5, 0.3, 0.4, 0.2) is b:
        return False, "浓度变了却复用了旧表"
    return True, "参数/浓度/染色任一变化都会重建，重复调用命中缓存"


# ------------------------------------------------------------------ 2. 上屏写法
def test_solo_equals_masked_when_clean():
    """干净缓冲上，`solo=True` 必须与掩码版**逐位相同** —— 它只省掉"比掩码"，
    不许改变任何一个像素。"""
    cfg = wav_cfg()
    wf = _aged(cfg)
    if not wf._alive:
        return False, "前置条件不成立：水面没活起来"
    _, a = _buf()
    _, b = _buf()
    wf.draw(a, W, H, cfg, solo=False)
    wf.draw(b, W, H, cfg, solo=True)
    lit = int((a.view(np.uint8).reshape(-1, 4)[:, 3] > 0).sum())
    if lit < 500:
        return False, f"画出来的像素太少（{lit}），这条断言会变得空洞"
    if not np.array_equal(a, b):
        d = int((a != b).sum())
        return False, f"solo 与掩码版差了 {d} 个像素"
    return True, f"两版逐位相同（{lit} 个非透明像素）"


def test_solo_semantics_on_dirty_buffer():
    """脏缓冲（= 同一层上还有雨雪 / 颜料盘 / 涟漪）下两者的分工：

    * `solo=True` = 整片直写，**该覆盖的地方与干净渲染逐位相同**；
    * `solo=False` = 只覆盖非零像素，**必须原封不动地保住别人的像素**
      （同层还有别的特效时走的就是这条路，抹掉了就是"别人的特效被吃掉一块"）。

    注意直写只作用于**包围盒内部**，盒子外面当然还是脏值 —— 所以断言只比
    "水波真正写了的地方"，不能拿整张缓冲去比。
    """
    cfg = wav_cfg()
    wf = _aged(cfg)
    _, clean = _buf()
    wf.draw(clean, W, H, cfg, solo=False)
    cv = clean.view(np.uint8).reshape(-1, 4)
    written = cv[:, 3] > 0                       # 水波真正改过的像素
    if int(written.sum()) < 500:
        return False, "水波没画出东西，断言会变空洞"

    dirty0 = np.full(W * H, 0xDEADBEEF, np.uint32)
    solo_dst = dirty0.copy()
    wf.draw(solo_dst, W, H, cfg, solo=True)
    if not np.array_equal(solo_dst[written], clean[written]):
        n = int((solo_dst[written] != clean[written]).sum())
        return False, f"solo 直写的内容与干净渲染差了 {n} 个像素"

    masked_dst = dirty0.copy()
    wf.draw(masked_dst, W, H, cfg, solo=False)
    keep = cv[:, 3] == 0
    # 整像素比（0xDEADBEEF 是小端存的，逐通道比会被自己坑到）
    leaked = int((masked_dst[keep] != np.uint32(0xDEADBEEF)).sum())
    if leaked:
        return False, f"掩码版抹掉了 {leaked} 个别人的像素"
    if not np.array_equal(masked_dst[written], clean[written]):
        return False, "掩码版没把该覆盖的像素覆盖上"
    # 直写多覆盖了包围盒里的空像素，掩码版没有 —— 这正是省下来的那 6ms
    n_solo = int((solo_dst != dirty0).sum())
    n_mask = int((masked_dst != dirty0).sum())
    if n_solo <= n_mask:
        return False, f"直写覆盖量（{n_solo}）竟然不多于掩码版（{n_mask}）"
    return True, (f"内容一致（{int(written.sum())} 个像素）；掩码版保住他人像素，"
                  f"直写多覆盖 {n_solo - n_mask} 个空像素")


def test_draw_edges_do_not_raise():
    """贴边 / 包围盒超出缓冲时不许越界，也不许把可见的那部分丢掉。

    绘制包围盒最后一步是 `py1 = min(h, ...)` / `px1 = min(w, ...)`：缓冲比
    屏幕小的时候（切换分辨率的那一帧、或者测试里的小缓冲）要靠它把窗口收回来。
    """
    cfg = wav_cfg()
    for cx, cy in ((0.0, 0.0), (float(W - 1), float(H - 1)),
                   (0.0, float(H - 1)), (float(W - 1), 0.0)):
        wf = WaterRippleField(W, H, seed=5)
        wf.splash(cx, cy, cfg, big=True)
        for _ in range(5):
            wf.update(1.0 / 60.0, cfg)
        _, u32 = _buf()
        wf.draw(u32, W, H, cfg)
        if not np.any(u32):
            return False, f"落点 ({cx},{cy}) 贴边时一个像素都没画"
    # 缓冲比屏幕小：包围盒被右下边界切掉，可见部分仍要画出来
    wf = WaterRippleField(W, H, seed=5)
    wf.splash(585.0, 385.0, cfg, big=True)
    wf.update(1.0 / 60.0, cfg)
    _, small = _buf(600, 400)
    wf.draw(small, 600, 400, cfg)
    if not np.any(small):
        return False, "包围盒被缓冲边界切到时什么都没输出"
    # 水波完全在缓冲之外：安静返回，不许越界
    wf = WaterRippleField(W, H, seed=5)
    wf.splash(1200.0, 660.0, cfg, big=True)
    wf.update(1.0 / 60.0, cfg)
    _, corner = _buf(400, 300)
    wf.draw(corner, 400, 300, cfg)
    if np.any(corner):
        return False, "屏幕外的波画进了左上角"
    return True, "四角贴边不越界；包围盒被切时画可见部分、完全在外时安静返回"


def test_draw_skips_when_dead():
    """水面死了就一个字节都不许写（层判空靠它，写错了会把上一帧冻住）。"""
    cfg = wav_cfg()
    wf = WaterRippleField(W, H, seed=5)
    dirty = np.full(W * H, 0xDEADBEEF, np.uint32)
    wf.draw(dirty, W, H, cfg, solo=True)
    wf.draw(dirty, W, H, cfg, solo=False)
    if not np.array_equal(dirty, np.full(W * H, 0xDEADBEEF, np.uint32)):
        return False, "水面没活的时候仍然写了像素"
    return True, "零波场 -> 两种写法都不写任何字节"


def main():
    print("== wave draw verification（查表着色 + 直写上屏）==")
    tests = [test_lut_matches_formula, test_lut_cache,
             test_solo_equals_masked_when_clean,
             test_solo_semantics_on_dirty_buffer,
             test_draw_edges_do_not_raise, test_draw_skips_when_dead]
    ok_all = True
    for t in tests:
        try:
            ok, msg = t()
        except Exception as e:
            import traceback
            traceback.print_exc()
            ok, msg = False, f"exception: {type(e).__name__}: {e}"
        ok_all = ok_all and ok
        print(f"  [{'OK' if ok else 'FAIL'}] {t.__name__:36s} {msg}")
    print(f"\n== {'WAVE DRAW VERIFIED' if ok_all else 'WAVE DRAW FAILED'} "
          f"({len(tests)} 项) ==")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
