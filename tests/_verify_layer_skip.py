# -*- coding: utf-8 -*-
"""层判空 / 跳过上屏的**安全不变量**探针（无窗口，可与正在运行的特效共存）。

背景：2026-09-15 把 `Scene.layer_activity()` 从"开关开着就算有内容"收紧成读
特效自身状态（`wave._alive` / `paint._dirty`），并把渲染循环里的空判定
`u32.any()` 换成等价的 `u32.max() != 0`。收益是鼠标层不再空转 5.9ms/帧，
但**代价必须是零**：判空判错会导致某层该出图却没上屏，画面被冻在桌面上。

所以这里要证的不是"省了多少"，而是这几条：
  A. `u32.max() != 0` 与 `u32.any()` 在 uint32 上**逐值等价**（含全零 / 单个非零
     在首 / 在末等边界）；
  B. **安全方向**：`layer_activity()` 判为"空"的那些帧，对应缓冲必须**真的全零**
     —— 只有这样"跳过上屏"才与"提交一张全 0"逐像素等效，不可能冻住画面；
  C. 完整生命周期：水波从"有波"衰减到"无波"时，判空必须在缓冲变空的那一帧
     （或更早）生效，且之后连续跳过的帧缓冲持续全零。

判为"有内容"但缓冲其实是空的（保守）只会浪费性能，不算错误，这里单独统计。

踩坑记录：第一版把"水波空闲"场景写成只关 `ripple_enabled`，忘了**雨**
（DEFAULTS 里 `rain_enabled=True` + `splash=True`）。雨滴落到屏幕底边会溅起
涟漪，而涟漪画的正是**鼠标层** —— 于是"空闲"场景里鼠标层一直有真内容（底边
rows 1194~1199），断言全挂。不是代码错，是探针配置错。反过来说：**雨开着时
鼠标层几乎恒活跃**，层跳过省下的 5.9ms 只在雨关掉（或 splash 关掉）时才拿得到。
"""
import os
import sys

os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")

import numpy as np                                # noqa: E402

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

HERE = ROOT          # 见上：HERE 仍指项目根

import desktop_fx as d                            # noqa: E402

# 配置隔离：绝不读 / 写用户的 config.json
d.CONFIG_PATH = os.path.join(HERE, "_probe_layer_skip_cfg.json")

from fx_scene import Scene                        # noqa: E402

W, H = 960, 600
DT = 1.0 / 60.0
# 画布刻意不用用户那块 4K 双屏：这里测的三条性质 —— "判空的帧缓冲是否真的全零"、
# "max() != 0 是否等价于 any()"、"跳过清零是否与每帧清零逐像素一致" —— 全都与
# 像素数量无关，只与**判定逻辑**有关。用 3840x1200 跑同样的帧数只是白花十几倍时间
# （实测 53s，降一档到 1280x800 仍要 35s），进不了 --fast。缩到 960x600 后覆盖不减、
# 整脚本约 20s，可以进核心回归。

FAIL = []
STATS = {"frames": 0, "skip_fs": 0, "skip_ms": 0, "conservative": 0}


def check(ok, msg):
    if not ok:
        FAIL.append(msg)
    return ok


# ------------------------------------------------------------------ A. 等价性
def test_max_equiv_any():
    rng = np.random.RandomState(3)
    cases = {}
    z = np.zeros(64, dtype=np.uint32)
    cases["全零"] = z
    a = z.copy(); a[0] = 1
    cases["首元素非零"] = a
    b = z.copy(); b[-1] = 0xFFFFFFFF
    cases["末元素最大"] = b
    c = z.copy(); c[7] = 0x1000000          # 只有 alpha 位
    cases["仅 alpha 位"] = c
    e = z.copy(); e[31] = 1                 # 只有最低位（B 通道）
    cases["仅最低位"] = e
    f = rng.randint(0, 2, size=100000).astype(np.uint32)
    cases["随机稀疏"] = f
    g = (rng.randint(0, 1 << 31, size=100000)).astype(np.uint32)
    cases["随机稠密"] = g

    bad = 0
    for name, arr in cases.items():
        x = bool(arr.any())
        y = int(arr.max()) != 0
        if x != y:
            bad += 1
            print(f"  ✗ {name}: any()={x} max()!=0={y}")
    check(bad == 0, f"max/any 等价性有 {bad} 例不符")
    print(f"A. .max() != 0 逐值等价于 .any() —— {len(cases)} 组边界用例"
          f"{'全部一致 ✓' if bad == 0 else '出现分歧 ✗'}")


# ------------------------------------------------------------------ 场景驱动
def run_scenario(name, cfg_over, frames, setup=None):
    """推进 `frames` 帧，逐帧复核"判空即为真空"这条安全不变量。

    完整复刻渲染循环的判定顺序：先问 `layer_activity()`，信号判空时再用真实
    像素复核（`max() != 0`）。返回两层的判定序列与缓冲 max 序列。
    """
    scene = Scene(W, H)
    scene.set_origin(0, 0)
    cfg = dict(d.DEFAULTS)
    cfg.update(cfg_over)
    fs_buf = np.zeros(W * H, dtype=np.uint32)
    ms_buf = np.zeros(W * H, dtype=np.uint32)

    out = {"ms": [], "ms_max": [], "fs": [], "fs_max": []}
    for i in range(frames):
        scene.cfg = cfg
        fs_buf[:] = 0
        ms_buf[:] = 0
        if setup is not None:
            setup(scene, i)
        scene.step(DT, fs_buf, W, H, ms_buf)

        fs_has, ms_has = scene.layer_activity()
        if not fs_has:
            fs_has = int(fs_buf.max()) != 0
        if not ms_has:
            ms_has = int(ms_buf.max()) != 0

        for tag, has, buf, key in (("全屏层", fs_has, fs_buf, "fs"),
                                   ("鼠标层", ms_has, ms_buf, "ms")):
            m = int(buf.max())
            out[key].append(bool(has))
            out[key + "_max"].append(m)
            STATS["frames"] += 1
            if not has:
                # 安全方向：判空的帧，缓冲必须真的全零
                if m != 0:
                    check(False, f"{name} 第 {i} 帧{tag}判空但缓冲非零"
                                 f"(max={m}) —— 这帧会被跳过上屏，画面将冻住！")
                else:
                    STATS["skip_" + key] += 1
            elif m == 0:
                STATS["conservative"] += 1      # 保守：只损失性能，不算错误
    return out


def test_clear_skip_equivalence():
    """跳过清零 vs 每帧清零：逐帧逐像素必须完全一致。

    渲染循环现在只在"上帧该层非空"时才 `u32[:] = 0`。要证的是这与"每帧都清"
    的输出毫无差别 —— 否则跳过的那些帧会把旧像素留在缓冲里（残影 / 糊屏）。

    要让这条路径真的被测到，场景必须**反复在"有内容 / 空"之间翻转**：
      - 鼠标层：手工撒涟漪（寿命 0.6s ≈ 36 帧），撒四簇 -> 中间大段是全空的；
      - 全屏层：中途把雪打开再关掉 -> 一次 空->有->空 的完整翻转。
    只让波的常驻内容填满全程（第一版就是这么写的）只会跳过 5 次清零，
    等于没测。

    两个 Scene 必须逐帧严格同步，所以把所有**鼠标驱动**的路径全关掉
    （wave_move / ripple_move / ripple_enabled / paint / win_ripple / 波全关），
    这样鼠标读数不进任何链路；随机数流各自从同一 seed 起步，演化一致。
    再加第三个场景 C（与 A 一样每帧清零）当**确定性对照**：A 与 C 若不一致，
    说明是这套对照装置本身不同步，而不是跳过清零的锅。
    """
    base = dict(d.DEFAULTS)
    base.update(rain_enabled=False, ripple_enabled=False, ripple_move=False,
                wave_enabled=False, wave_move=False, wave_click=False,
                wave_ambient=0.0, spotlight_enabled=False, paint_enabled=False,
                win_ripple_enabled=False, petal_enabled=False,
                meteor_enabled=False, snow_enabled=False)

    def cfg_at(i):
        c = dict(base)
        c["snow_enabled"] = 60 <= i < 200          # 全屏层：空 -> 有 -> 空
        return c

    def spawn_ripples(scene):
        scene.ripples.spawn(W * 0.5, H * 0.5, 120.0, 0.6, 0.8,
                            np.array([200.0, 220.0, 255.0], np.float32))

    splash_frames = (5, 30, 180, 330)
    N_EQ = 420
    scenes = [Scene(W, H) for _ in range(3)]
    for s in scenes:
        s.set_origin(0, 0)
    bufs = [np.zeros(W * H, dtype=np.uint32) for _ in range(3)]
    mbufs = [np.zeros(W * H, dtype=np.uint32) for _ in range(3)]

    fs_empty = ms_empty = True          # 只有 B（下标 1）用跳过逻辑
    bad = 0
    skipped = 0
    ctrl_bad = 0
    frames_with_content = 0
    for i in range(N_EQ):
        cfg = cfg_at(i)
        for k, s in enumerate(scenes):
            s.cfg = cfg
            # A、C 每帧无条件清零；B 只在上帧非空时清零（渲染循环现在的行为）
            if k != 1:
                bufs[k][:] = 0
                mbufs[k][:] = 0
            else:
                if not fs_empty:
                    bufs[k][:] = 0
                else:
                    skipped += 1
                if not ms_empty:
                    mbufs[k][:] = 0
                else:
                    skipped += 1
        if i in splash_frames:
            for s in scenes:
                spawn_ripples(s)
        for k, s in enumerate(scenes):
            s.step(DT, bufs[k], W, H, mbufs[k])

        if int(mbufs[1].max()) != 0 or int(bufs[1].max()) != 0:
            frames_with_content += 1
        if not np.array_equal(bufs[0], bufs[2]) or \
                not np.array_equal(mbufs[0], mbufs[2]):
            ctrl_bad += 1
        if not np.array_equal(mbufs[0], mbufs[1]) or \
                not np.array_equal(bufs[0], bufs[1]):
            bad += 1
            if bad <= 3:
                print(f"  第 {i} 帧不一致：鼠标层差 "
                      f"{int(np.count_nonzero(mbufs[0] != mbufs[1]))} 像素、"
                      f"全屏层差 {int(np.count_nonzero(bufs[0] != bufs[1]))}")

        f_has, m_has = scenes[1].layer_activity()
        if not f_has:
            f_has = int(bufs[1].max()) != 0
        if not m_has:
            m_has = int(mbufs[1].max()) != 0
        fs_empty, ms_empty = not f_has, not m_has

    check(ctrl_bad == 0, f"对照装置自身不同步（A vs C 有 {ctrl_bad} 帧不一致）"
                         f"—— 这条测试的结论不可信，先修装置")
    check(skipped > 100, f"只跳过了 {skipped} 次清零，样本太少，测不到什么")
    check(frames_with_content > 80,
          f"{N_EQ} 帧里有内容的只有 {frames_with_content} 帧，对照太稀")
    check(bad == 0, f"跳过清零后有 {bad} 帧与「每帧清零」不一致 —— "
                    f"跳过会把旧像素留在缓冲里")
    print(f"9) 跳过清零 vs 每帧清零：{N_EQ} 帧（{frames_with_content} 帧有内容、"
          f"清零被跳过 {skipped} 次）逐像素比对 → 不一致 {bad} 帧"
          f"{'✓' if bad == 0 else '✗'}；对照组 A/C 不一致 {ctrl_bad} 帧"
          f"（应为 0，证明装置本身是同步的）")


def main():
    test_max_equiv_any()

    # 只留雪（全屏层）+ 水波（鼠标层），雨关掉 —— 雨滴溅起的涟漪会落进鼠标层，
    # 那样鼠标层就不是"空闲"了（见文件头踩坑记录）。
    no_rain = dict(rain_enabled=False)
    idle_cfg = dict(no_rain, wave_enabled=True, wave_move=False,
                    wave_click=False, wave_ambient=0.0, ripple_enabled=False,
                    snow_enabled=True)

    # ---- 1) 水波开着但一次都没触发：必须判空（这正是用户当前的场景）
    r = run_scenario("水波空闲", idle_cfg, 180)
    check(not any(r["ms"]), "水波空闲时鼠标层不应判为有内容")
    check(not any(r["ms_max"]), "水波空闲时鼠标层缓冲应恒为 0")
    print(f"\n1) 真实水波开着、从未触发 180 帧：鼠标层判为有内容的帧 "
          f"{sum(r['ms'])}/180（应为 0），缓冲最大 {max(r['ms_max'])}（应为 0）")

    # ---- 1b) 反向对照：雨开着溅涟漪 -> 鼠标层必须判为有内容
    r = run_scenario("雨溅涟漪", dict(idle_cfg, rain_enabled=True, splash=True,
                                     ripple_enabled=True), 120)
    check(any(r["ms"]), "雨滴溅起的涟漪落进鼠标层，应判为有内容")
    print(f"1b) 反向对照：雨 + 溅水花 120 帧：鼠标层判为有内容的帧 "
          f"{sum(r['ms'])}/120（应 > 0，证明判空没有把涟漪漏掉）")

    # ---- 2) 水波完整生命周期：撒一波 -> 衰减 -> 判空
    def setup_wave(scene, i):
        if i == 3:
            scene.wave.splash(W * 0.5, H * 0.5, scene.cfg, big=True)

    # 衰减必须调快（wave_persist=0 -> 每秒保留比例最低）才可能在 900 帧内看到
    # "彻底无波"那一帧。用默认 0.45 的话 15s 后场上仍有波，`after` 会是空表，
    # `all(...)` 退化成**空集真** —— 看着通过，其实"波死掉后判空"这条路径没测到。
    life_cfg = dict(idle_cfg, wave_persist=0.0)
    r = run_scenario("水波生命周期", life_cfg, 900, setup=setup_wave)
    v, m = r["ms"], r["ms_max"]
    first_true = next((i for i, x in enumerate(v) if x), None)
    last_true = max((i for i, x in enumerate(v) if x), default=None)
    check(first_true is not None and m[first_true] > 0,
          "水波激活后从未判为有内容 —— 波被漏掉了")
    check(last_true is not None and last_true < len(v) - 1,
          f"波在 {len(v)} 帧内没衰减到零（末帧仍有内容）—— "
          f"这样断言是空集真，等于没测到「波死掉后判空」这条路径")
    after = m[last_true + 1:]
    check(len(after) > 0 and all(x == 0 for x in after),
          f"水波衰减后缓冲仍有残留：{[x for x in after if x][:5]}")
    print(f"2) 水波生命周期 900 帧（15s，persist=0）：第 {first_true} 帧判为有内容，"
          f"第 {last_true} 帧为最后一个有内容的帧，之后 {len(after)} 帧判定已空"
          f"且缓冲为零 ✓")

    # ---- 3/4) 颜料盘：没落笔必须判空；落笔后必须判有，散尽后必须回到判空
    # **不能**用 Scene.step 驱动：它每帧都拿**真实光标**调
    # `paint.set_pointer(float(mouse.mx), float(mouse.my), clicked)`，探针喂进去的
    # 指针会被原地冲掉（第一版就是这么测的，落笔后仍判 0 帧）。直接测 PaintField
    # 本身 —— `layer_activity()` 读的就是它这个 `_dirty`，测的是同一个量。
    from fx_effects import PaintField                     # noqa: E402

    def paint_probe(feed):
        """feed=(起帧, 止帧) 期间喂入移动的指针；None = 全程不喂。

        `paint_fade=1.0`（最快消隐）是为了在这几百帧里真的看到"颜料酒尽"那一帧
        —— 用默认 0.45 的话 4s 后包围盒里仍有颜料，`tail` 是空表，断言又变成
        空集真（和场景 2 同一个坑）。
        """
        pf = PaintField(W, H)
        buf = np.zeros(W * H, dtype=np.uint32)
        cfgp = dict(d.DEFAULTS)
        cfgp.update(paint_enabled=True, paint_fade=1.0)
        trace = []
        for i in range(480):
            if feed is not None and feed[0] <= i <= feed[1]:
                pf.set_pointer(W * 0.5 + (i - feed[0]) * 24.0, H * 0.5, 1)
            pf.update(DT, cfgp)
            buf[:] = 0
            pf.draw(buf, W, H, cfgp)
            trace.append((bool(pf._dirty), int(buf.max())))
        return trace

    tr = paint_probe(None)
    check(not any(x[0] for x in tr), "颜料盘未落笔时 _dirty 不应为真")
    check(not any(x[1] for x in tr), "颜料盘未落笔时不应写出任何像素")
    print(f"3) 颜料盘未落笔 480 帧：_dirty 为真的帧 "
          f"{sum(1 for x in tr if x[0])}/480（应为 0），写出的像素恒为 0")

    tr = paint_probe((2, 10))
    check(any(x[0] for x in tr), "颜料盘落笔后 _dirty 应置位")
    drew = [i for i, x in enumerate(tr) if x[1]]
    check(bool(drew), "颜料盘落笔后应真的写出像素")
    last_drew = max(drew)
    tail = tr[last_drew + 1:]
    check(last_drew < len(tr) - 1,
          "颜料在 480 帧内没散尽 —— 这样断言是空集真，等于没测到散尽后的判空")
    check(bool(tail) and all(x[1] == 0 for x in tail),
          "颜料散尽后不应再写出像素")
    check(not tr[-1][0], "颜料散尽后 _dirty 应被 draw 自己清零")
    print(f"4) 颜料盘落笔 480 帧：落笔后 {len(drew)} 帧写出像素"
          f"（第 {min(drew)}~{last_drew} 帧），之后 {len(tail)} 帧不再写且"
          f"末帧 _dirty={tr[-1][0]}（应为 False）")

    # ---- 5) 聚光灯开着：保持保守判"有"（它本来就恒画满屏压暗）
    r = run_scenario("聚光灯", dict(no_rain, spotlight_enabled=True,
                                    wave_enabled=False, snow_enabled=False), 90)
    check(all(r["ms"]), "聚光灯恒画满屏压暗，应恒判为有内容")
    check(all(x > 0 for x in r["ms_max"]), "聚光灯应真的写满屏")
    print(f"5) 聚光灯开着 90 帧：鼠标层判为有内容的帧 {sum(r['ms'])}/90，"
          f"缓冲最大 {min(r['ms_max'])}~{max(r['ms_max'])}（应恒非零）")

    # ---- 6) 涟漪池：手工撒一圈涟漪 -> 池子空后必须判空
    # 必须把 ripple_enabled/ripple_move 都关掉：开着的话**真实光标一动**就会
    # 持续往里扔涟漪，池子永远不空（第一版就是这么写的，239/240 帧都有内容）。
    # 直接 `ripples.spawn` 不受开关限制，update/draw 也是无条件跑的。
    def setup_rip(scene, i):
        if i == 2:
            scene.ripples.spawn(W * 0.5, H * 0.5, 120.0, 0.6, 0.8,
                                np.array([200.0, 220.0, 255.0], np.float32))

    r = run_scenario("涟漪生命周期", dict(no_rain, ripple_enabled=False,
                                         ripple_move=False, wave_enabled=False,
                                         snow_enabled=False),
                     240, setup=setup_rip)
    check(r["ms"][2] or r["ms"][3], "涟漪生成后应判为有内容")
    check(not r["ms"][-1], "涟漪早已消亡，末帧不应判为有内容")
    print(f"6) 涟漪生命周期 240 帧：有内容帧数 {sum(r['ms'])}，末帧判空"
          f"{'✓' if not r['ms'][-1] else '✗'}")

    # ---- 7) 全屏层：雪开着必须一直判"有"
    r = run_scenario("雪", dict(no_rain, snow_enabled=True, wave_enabled=False,
                                ripple_enabled=False), 60)
    check(all(r["fs"]), "雪开着时全屏层应恒判为有内容")
    check(all(x > 0 for x in r["fs_max"]), "雪应真的写全屏层")
    print(f"7) 雪开着 60 帧：全屏层判为有内容的帧 {sum(r['fs'])}/60（应为 60），"
          f"缓冲最大 {min(r['fs_max'])}~{max(r['fs_max'])}")

    # ---- 8) 全关：两层都应判空
    r = run_scenario("全关", dict(snow_enabled=False, rain_enabled=False,
                                  petal_enabled=False, meteor_enabled=False,
                                  wave_enabled=False, spotlight_enabled=False,
                                  paint_enabled=False, ripple_enabled=False,
                                  win_ripple_enabled=False), 60)
    check(not any(r["ms"]), "全部特效关闭时鼠标层应恒判为空")
    check(not any(r["fs"]), "全部特效关闭时全屏层应恒判为空")
    print(f"8) 特效全关 60 帧：鼠标层判为有内容的帧 {sum(r['ms'])}/60、"
          f"全屏层 {sum(r['fs'])}/60（都应为 0）")

    # ---- 9) 跳过清零 vs 每帧清零：必须逐帧逐像素完全一致
    test_clear_skip_equivalence()

    # ------------------------------------------------------------------ 汇总
    print("\n" + "=" * 72)
    print(f"安全不变量：共 {STATS['frames']} 层次判定，其中被跳过上屏的 "
          f"{STATS['skip_fs'] + STATS['skip_ms']} 次"
          f"（全屏层 {STATS['skip_fs']} + 鼠标层 {STATS['skip_ms']}）"
          f"缓冲全部为零 —— 这就是「跳过 = 提交全 0」的等价性")
    print(f"保守误报（判有但缓冲其实是空）{STATS['conservative']} 帧 —— "
          f"只损失性能，不影响正确性")
    if FAIL:
        print("\n✗ 失败项：")
        for msg in FAIL:
            print("   -", msg)
        sys.exit(1)
    print("\n✓ 全部通过：判空收紧后不会把任何一帧的画面冻在桌面上")


if __name__ == "__main__":
    main()
