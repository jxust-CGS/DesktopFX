# -*- coding: utf-8 -*-
"""点击特效（点击表情 / 点击文字）与分组重构的专项验证。

覆盖五块：

  1. **图案精灵** —— 8 种图案都画得出东西、预乘合法、旋转档位互不相同
     （同档位说明旋转没生效）、缓存不无界增长。
  2. **爆发物理** —— 粒子数正确、扩散（速度）与重力生效、到时自动清空、
     乱序输入无 NaN、池满时不越界。
  3. **文字** —— 多行解析（空行 / 空白 / CRLF）、多条目轮换、字号档位、
     空文字不崩、文字画得出来且**带描边**（浅底上也读得清）。
  4. **分组重构** —— 4 个分组、拖动/点击各自成组、`multi_key_of` 落到鼠标层、
     点击分组内两个特效**默认互斥**。
  5. **跨文件一致性** —— fx_ui 里的图案/字体/装饰表必须与 fx_effects 的实现
     一一对应（少了会画出空白、多了会静默退回默认，都不报错）。

结尾带负向对照：把 `_over_blend` 换成覆盖写法，混合类断言必须变红。

不建窗口、不构造 App，只碰内存缓冲。
"""
import math
import os
import shutil
import sys
import tempfile
import time
import wave

import numpy as np

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

import _srclib  # noqa: E402  测试助手：按源码打补丁做负向对照（见 fx_effects 三层拆分）

HERE = ROOT          # 见上：HERE 仍指项目根

import desktop_fx as d                              # noqa: E402
import fx_effects as F                              # noqa: E402
import fx_kinds                                     # noqa: E402
from fx_effects import ClickEmojiField, ClickTextField   # noqa: E402
import _win32                                       # noqa: E402  面板点击过滤用

# ⚠️ 打桩 `_mci_send` 必须打在 **fx_click** 上，不能打 `fx_effects`。
# `_mci_send` 拆到了 fx_click.py，`fx_effects._mci_send` 只是转出（同一个函数
# 对象），而 `VoicePlayer` 是在**自己模块的全局**里找这个名字的 ——
# 改 `fx_effects._mci_send` 不会影响它，症状是"打桩后一条播放命令都没发出"。
import fx_click                                     # noqa: E402

W, H = 640, 480


def _ecfg(**over):
    cfg = dict(click_emoji_enabled=True, click_emoji_shape="爱心",
               click_emoji_count=12.0, click_emoji_size=22.0,
               click_emoji_spread=90.0, click_emoji_life=1.1,
               click_emoji_color="#FF6B8E", click_emoji_gravity=1.0,
               click_emoji_rotate=True, click_emoji_opacity=1.0,
               click_emoji_source=F.CLICK_SOURCE_BUILTIN,
               click_emoji_files="", click_emoji_custom_dir="",
               click_emoji_pick=F.CLICK_PICK_MULTI)
    cfg.update(over)
    return cfg


def _tcfg(**over):
    cfg = dict(click_text_enabled=True, click_text_list="好耶\n太棒了",
               click_text_color="#FFFFFF", click_text_size=34.0,
               click_text_font="微软雅黑", click_text_deco="彩带",
               click_text_rise=90.0, click_text_life=1.4,
               click_text_opacity=1.0, click_text_spin=0.75)
    cfg.update(over)
    return cfg


def _run(field, cfg, fps, secs, target, click=0, first_click=True):
    for k in range(max(1, int(fps * secs))):
        field.set_pointer(target[0], target[1],
                          click if (k == 0 and first_click) else 0)
        field.update(1.0 / fps, cfg)


# ---------------------------------------------------------- 1. 图案精灵
def test_all_shapes_draw():
    """8 种图案都要画得出东西，且预乘合法（rgb ≤ alpha）。"""
    for name in F._SHAPE_NAMES:
        spr = F._shape_sprite(name, "#FF6B8E", 32, 0, 12)
        if spr.shape != (32, 32, 4):
            return False, f"{name}: 形状是 {spr.shape}"
        a = spr[:, :, 3]
        nz = int((a > 0.05).sum())
        if nz < 40:
            return False, f"{name}: 非透明像素只有 {nz}（图案画空了？）"
        if a.max() < 0.9:
            return False, f"{name}: 没有接近不透明的主体像素"
        over = float((spr[:, :, :3].max(axis=2) - a).max())
        if over > 1e-3:
            return False, f"{name}: 预乘不合法，rgb 比 alpha 大 {over:.4f}"
    return True, f"{len(F._SHAPE_NAMES)} 种图案：非空、不透明、预乘合法"


def test_shape_names_and_ui_lists_match():
    """fx_ui 的三张选择表必须与 fx_effects 的实现一一对应。

    这是**跨文件一致性**，不锁住就会出两种静默故障：UI 里多一项会悄悄退回
    默认图案（用户以为选了雪花其实是爱心），少一项则新图案永远选不到。
    """
    import fx_ui
    if list(fx_ui.CLICK_SHAPES) != list(F._SHAPE_NAMES):
        return False, (f"图案表不一致：UI={fx_ui.CLICK_SHAPES} / "
                       f"实现={list(F._SHAPE_NAMES)}")
    if sorted(fx_ui.CLICK_FONTS) != sorted(F._FONT_FILES):
        return False, (f"字体表不一致：UI={sorted(fx_ui.CLICK_FONTS)} / "
                       f"实现={sorted(F._FONT_FILES)}")
    # 装饰表对应 ClickTextField.update() 的三个分支
    if set(fx_ui.CLICK_DECOS) != {"无", "彩带", "星光"}:
        return False, f"装饰表与实现分支不符：{fx_ui.CLICK_DECOS}"
    for name in fx_ui.CLICK_FONTS:
        if name not in F._FONT_FILES:
            return False, f"UI 提供了实现里没有的字体 {name}"
    return True, (f"图案 {len(fx_ui.CLICK_SHAPES)} / 字体 {len(fx_ui.CLICK_FONTS)}"
                  f" / 装饰 {len(fx_ui.CLICK_DECOS)} 三张表与实现一致")


def test_rotation_steps_differ():
    """12 个旋转档必须互不相同 —— 全一样说明旋转根本没生效。"""
    base = F._shape_sprite("星星", "#FFD166", 32, 0, 12)
    diffs = []
    for k in range(1, 12):
        other = F._shape_sprite("星星", "#FFD166", 32, k, 12)
        diffs.append(float(np.abs(other - base).max()))
    if min(diffs) < 0.3:
        return False, f"有旋转档与第 0 档几乎相同（最小差 {min(diffs):.3f}）"
    # 转半圈（6 档）应该跟原图差得最远 —— 星星是中心对称的，这条只做粗校验
    if max(diffs) < 0.5:
        return False, f"所有旋转档差异都太小（最大 {max(diffs):.3f}）"
    return True, f"11 个非零档与第 0 档的差异落在 {min(diffs):.2f}~{max(diffs):.2f}"


def test_shape_cache_bounded():
    """精灵缓存必须封顶：配置驱动重建很便宜，但无界累积会吃内存。"""
    F._SHAPE_CACHE.clear()
    for k in range(3):
        F._shape_sprite("爱心", "#FF6B8E", 20 + k, 0, 12)
    if len(F._SHAPE_CACHE) < 3:
        return False, "缓存没生效（同样的键重复构建）"
    for k in range(F._SHAPE_CACHE_MAX + 40):
        F._shape_sprite("星星", "#%06X" % (k * 7 % 0xFFFFFF), 24, 0, 12)
    if len(F._SHAPE_CACHE) > F._SHAPE_CACHE_MAX + 12:
        return False, f"缓存涨到 {len(F._SHAPE_CACHE)} 条，封顶没生效"
    return True, f"缓存有封顶（上限 {F._SHAPE_CACHE_MAX}，实测 {len(F._SHAPE_CACHE)}）"


# ---------------------------------------------------------- 2. 爆发物理
def test_burst_spawn_and_expire():
    f = ClickEmojiField(W, H)
    cfg = _ecfg(click_emoji_count=16.0)
    f.set_pointer(300.0, 240.0, 1)
    f.update(1.0 / 60.0, cfg)
    if f.engine.count != 16:
        return False, f"点一次应炸开 16 个，实际 {f.engine.count}"
    if not f.active():
        return False, "有粒子却报 active=False（该层会被判空、粒子冻住）"
    # 过了寿命就该清空
    _run(f, cfg, 60, 1.1 * 1.25 + 0.5, (300.0, 240.0), first_click=False)
    if f.engine.count != 0 or f.active():
        return False, f"过了寿命仍有 {f.engine.count} 个粒子"
    return True, "一次点击 16 个粒子，寿命到点自动清空"


def test_spread_controls_travel():
    """扩散（初速）调大，同样时间后粒子应该飞得更远。"""
    def extent(spread):
        f = ClickEmojiField(W, H)
        cfg = _ecfg(click_emoji_count=20.0, click_emoji_spread=spread,
                    click_emoji_gravity=0.0, click_emoji_life=3.0)
        f.set_pointer(320.0, 240.0, 1)
        f.update(1.0 / 60.0, cfg)
        _run(f, cfg, 60, 0.5, (320.0, 240.0), first_click=False)
        dx = np.abs(f.engine.px[:f.engine.count] - 320.0)
        dy = np.abs(f.engine.py[:f.engine.count] - 240.0)
        return float(np.sqrt(dx * dx + dy * dy).mean())

    near = extent(40.0)
    far = extent(200.0)
    if far <= near * 1.8:
        return False, f"扩散 40 → {near:.1f} 像素、200 → {far:.1f} 像素，没拉开"
    return True, f"扩散生效：40 → 平均 {near:.1f} 像素，200 → {far:.1f} 像素"


def test_gravity_pulls_down():
    """重力为正时下半部分的粒子应明显多于无重力（且明显多于上半）。"""
    def below_ratio(grav):
        f = ClickEmojiField(W, H)
        cfg = _ecfg(click_emoji_count=40.0, click_emoji_spread=60.0,
                    click_emoji_gravity=grav, click_emoji_life=3.0)
        f.set_pointer(320.0, 240.0, 1)
        f.update(1.0 / 60.0, cfg)
        _run(f, cfg, 60, 0.8, (320.0, 240.0), first_click=False)
        ys = f.engine.py[:f.engine.count]
        return float((ys > 240.0).mean())

    flat = below_ratio(0.0)
    heavy = below_ratio(1.0)
    if heavy < 0.62:
        return False, f"有重力时只有 {heavy:.0%} 的粒子在下方，重力没生效"
    if heavy <= flat + 0.15:
        return False, f"重力 0 → {flat:.0%} 在下方、重力 1 → {heavy:.0%}，没拉开"
    return True, f"重力生效：0 → {flat:.0%} 在下半，1 → {heavy:.0%}"


def test_rotate_spins():
    f = ClickEmojiField(W, H)
    cfg = _ecfg(click_emoji_count=10.0, click_emoji_rotate=True,
                click_emoji_life=3.0)
    f.set_pointer(320.0, 240.0, 1)
    f.update(1.0 / 60.0, cfg)
    r0 = f.engine.rot[:f.engine.count].copy()
    _run(f, cfg, 60, 0.4, (320.0, 240.0), first_click=False)
    r1 = f.engine.rot[:f.engine.count]
    if float(np.abs(r1 - r0).max()) < 0.5:
        return False, "开了自旋但角度没变"
    # 关掉自旋角度就该纹丝不动
    f2 = ClickEmojiField(W, H)
    c2 = _ecfg(click_emoji_count=10.0, click_emoji_rotate=False,
               click_emoji_life=3.0)
    f2.set_pointer(320.0, 240.0, 1)
    f2.update(1.0 / 60.0, c2)
    a0 = f2.engine.rot[:f2.engine.count].copy()
    _run(f2, c2, 60, 0.4, (320.0, 240.0), first_click=False)
    if float(np.abs(f2.engine.rot[:f2.engine.count] - a0).max()) > 1e-5:
        return False, "关了自旋角度仍在变"
    return True, "开自旋会转、关自旋不动"


def test_pool_overflow_is_safe():
    """连点把池子打满时不能越界 / 不能 NaN。"""
    f = ClickEmojiField(W, H)
    cfg = _ecfg(click_emoji_count=40.0, click_emoji_life=3.0)
    for k in range(30):
        f.set_pointer(100.0 + k * 12, 240.0, 1)
        f.update(1.0 / 60.0, cfg)
        f.update(1.0 / 60.0, cfg)
    n = f.engine.count
    if n > F._BurstEngine.MAX:
        return False, f"粒子数 {n} 超过池上限 {F._BurstEngine.MAX}"
    vals = np.concatenate([f.engine.px[:n], f.engine.py[:n],
                           f.engine.vx[:n], f.engine.vy[:n],
                           f.engine.rot[:n]])
    if not np.isfinite(vals).all():
        return False, "连点后出现 NaN / Inf"
    if n <= 0:
        return False, "连点之后一个粒子都没有"
    return True, f"连点 30 次后池内 {n} 个（上限 {F._BurstEngine.MAX}），无 NaN"


def test_wild_input_no_nan():
    f = ClickEmojiField(W, H)
    rng = np.random.default_rng(5)
    for _ in range(300):
        cfg = _ecfg(click_emoji_count=float(rng.integers(1, 40)),
                    click_emoji_size=float(rng.uniform(8, 64)),
                    click_emoji_spread=float(rng.uniform(20, 300)),
                    click_emoji_gravity=float(rng.uniform(-0.5, 1.5)),
                    click_emoji_life=float(rng.uniform(0.4, 3.0)))
        f.set_pointer(float(rng.uniform(-5e3, 5e3)),
                      float(rng.uniform(-5e3, 5e3)),
                      1 if rng.random() < 0.4 else 0)
        f.update(float(rng.uniform(0.0, 0.5)), cfg)
    n = f.engine.count
    vals = np.concatenate([f.engine.px[:n], f.engine.py[:n],
                           f.engine.vx[:n], f.engine.vy[:n]])
    if not np.isfinite(vals).all():
        return False, "乱序输入后出现 NaN / Inf"
    return True, "300 次随机参数（含越大越界的坐标与 dt=0~0.5）无 NaN"


def test_confetti_uses_palette():
    """彩带的**默认**色是「彩色」（逐粒子取整套调色板）。

    新语义下颜色由用户选：任何图案选「彩色」都多彩，选单色就单色 ——
    所以这里测的是"彩带的默认色"，而不是"彩带强制多彩"（旧语义下它
    会忽略主色，现在不会了）。
    """
    f = ClickEmojiField(W, H)
    cfg = _ecfg(click_emoji_shape="彩带", click_emoji_count=30.0)
    cfg.pop("click_emoji_color")            # 不给颜色 → 走图案的惯用色
    f.set_pointer(320.0, 240.0, 1)
    f.update(1.0 / 60.0, cfg)
    n = f.engine.count
    uniq = {tuple(int(v) for v in c) for c in f.engine.rgb[:n]}
    if len(uniq) < 3:
        return False, (f"彩带默认色只有 {len(uniq)} 种颜色"
                       f"（惯用色应当是「彩色」）")
    # 反过来：显式给单色就应该是单色（不再被忽略）
    f3 = ClickEmojiField(W, H)
    f3.set_pointer(320.0, 240.0, 1)
    f3.update(1.0 / 60.0, _ecfg(click_emoji_shape="彩带",
                               click_emoji_count=30.0,
                               click_emoji_color="#000000"))
    u3 = {tuple(int(v) for v in c) for c in f3.engine.rgb[:f3.engine.count]}
    if len(u3) != 1:
        return False, f"给彩带指定单色却出现 {len(u3)} 种颜色（主色被忽略了？）"
    # 非彩带图案的默认色是单色
    f2 = ClickEmojiField(W, H)
    f2.set_pointer(320.0, 240.0, 1)
    cfg2 = _ecfg(click_emoji_shape="爱心", click_emoji_count=30.0)
    cfg2.pop("click_emoji_color")
    f2.update(1.0 / 60.0, cfg2)
    u2 = {tuple(int(v) for v in c) for c in f2.engine.rgb[:f2.engine.count]}
    if len(u2) != 1:
        return False, f"爱心的默认色有 {len(u2)} 种颜色（应当是单色红）"
    return True, (f"彩带默认 {len(uniq)} 种颜色；指定单色则 1 种；"
                  f"爱心默认 1 种")


def test_draws_into_buffer():
    """真的画得上屏，而且不该把整屏涂满。"""
    f = ClickEmojiField(W, H)
    cfg = _ecfg(click_emoji_count=20.0)
    f.set_pointer(W * 0.5, H * 0.5, 1)
    f.update(1.0 / 60.0, cfg)
    buf = np.zeros(W * H, np.uint32)
    for _ in range(4):
        buf[:] = 0
        f.update(1.0 / 60.0, cfg)
        f.draw(buf, W, H, cfg)
    n = int((buf > 0).sum())
    if n <= 0:
        return False, "画完之后一个像素都没有"
    if n > W * H * 0.6:
        return False, f"改动 {n} 像素（超过 60% 画面），范围失控"
    # 不透明度 0 时一个像素都不该落
    f2 = ClickEmojiField(W, H)
    c0 = _ecfg(click_emoji_count=20.0, click_emoji_opacity=0.0)
    f2.set_pointer(W * 0.5, H * 0.5, 1)
    f2.update(1.0 / 60.0, c0)
    b2 = np.zeros(W * H, np.uint32)
    f2.draw(b2, W, H, c0)
    if (b2 > 0).any():
        return False, "不透明度 0 时仍然落了像素"
    return True, f"落屏 {n} 像素（占 {100.0 * n / (W * H):.1f}%），不透明度 0 不落"


# ---------------------------------------------------------- 3. 文字
def test_text_list_parsing():
    t = ClickTextField(W, H)
    cases = [
        ("好耶\n太棒了", ["好耶", "太棒了"]),
        ("  只有一个  ", ["只有一个"]),
        ("a\n\n\nb", ["a", "b"]),            # 空行要去掉
        ("a\r\nb", ["a", "b"]),              # CRLF
        ("", []),
        ("   \n  \t ", []),
        ("1\n2\n3\n4", ["1", "2", "3", "4"]),
    ]
    for raw, want in cases:
        got = t.texts_of(dict(click_text_list=raw))
        if got != want:
            return False, f"{raw!r} 解析成 {got}，期望 {want}"
    return True, f"{len(cases)} 组写法（含空行 / CRLF / 全空白）解析正确"


def test_text_rotation():
    """多条文字按点击**轮换**，不是随机 —— 随机无法确认每条都生效。"""
    t = ClickTextField(W, H)
    cfg = _tcfg(click_text_list="甲\n乙\n丙", click_text_deco="无")
    seq = []
    for _ in range(7):
        t.set_pointer(320.0, 240.0, 1)
        t.update(1.0 / 60.0, cfg)
        t.pops = [p for p in t.pops if p["t"] < 0.05]   # 只留最新一条
        seq.append(t.pops[-1]["idx"])
    if seq != [0, 1, 2, 0, 1, 2, 0]:
        return False, f"轮换顺序是 {seq}，期望 [0,1,2,0,1,2,0]"
    # 换成两条，游标要按新长度取模而不是越界
    t.reset()
    c2 = _tcfg(click_text_list="甲\n乙", click_text_deco="无")
    for _ in range(3):
        t.set_pointer(320.0, 240.0, 1)
        t.update(1.0 / 60.0, c2)
    if t.pops[-1]["idx"] >= 2:
        return False, f"改成两条后序号越界：{t.pops[-1]['idx']}"
    return True, "多条目按 0→1→2→0 轮换；条目数变化后不越界"


def test_text_pop_scales():
    """弹字用多个字号档（直接重渲染），开头小、中段过冲、之后稳定。"""
    steps = F._TEXT_POP_STEPS
    if len(steps) < 3:
        return False, f"缩放档只有 {len(steps)} 个"
    if max(steps) <= steps[-1] or max(steps) <= steps[0]:
        return False, f"没有过冲：{steps}"
    sizes = [F._text_sprite("好", "微软雅黑", 40, "#FFFFFF", s).shape[0]
             for s in steps]
    if sizes[-1] <= sizes[0]:
        return False, f"末档不该比首档小：{sizes}"
    return True, f"5 档字号 {'/'.join(str(s) for s in sizes)}，末档回到稳定尺寸"


def test_text_has_outline():
    """文字必须带描边：白字落在浅色桌面上没有描边就会糊掉。

    判据是"最外圈存在半透明的深色像素" —— 直接比对具体颜色会依赖字体渲染细节。
    """
    spr = F._text_sprite("好耶", "微软雅黑", 40, "#FFFFFF", 1.0)
    a = spr[:, :, 3]
    ys, xs = np.nonzero(a > 0.02)
    if ys.size == 0:
        return False, "文字渲染成了空白"
    # 取描边区域：alpha 较低（描边是半透明的）且颜色偏暗
    rgb = np.where(a[..., None] > 1e-6,
                   spr[:, :, :3] / np.maximum(a[..., None], 1e-6), 0.0)
    lum = rgb[:, :, 0] * 0.299 + rgb[:, :, 1] * 0.587 + rgb[:, :, 2] * 0.114
    edge = (a > 0.25) & (a < 0.85) & (lum < 0.35)
    if int(edge.sum()) < 12:
        return False, (f"找不到描边像素（{int(edge.sum())} 个）—— "
                       f"白字在浅色桌面上会看不清")
    return True, f"文字带描边（{int(edge.sum())} 个半透明暗像素）"


def test_text_empty_is_safe():
    t = ClickTextField(W, H)
    cfg = _tcfg(click_text_list="", click_text_deco="无")
    t.set_pointer(320.0, 240.0, 1)
    t.update(1.0 / 60.0, cfg)
    buf = np.zeros(W * H, np.uint32)
    t.draw(buf, W, H, cfg)
    if (buf > 0).any():
        return False, "文字清空后仍然画了东西"
    if t.active():
        return False, "文字清空后仍报 active（该层会被一直提交）"
    return True, "文字留空时不崩、不画、不占层"


def test_text_deco():
    """装饰按设置生效：选了彩带就有粒子，选「无」就没有。"""
    for deco, want in (("彩带", True), ("星光", True), ("无", False)):
        t = ClickTextField(W, H)
        cfg = _tcfg(click_text_deco=deco)
        t.set_pointer(320.0, 240.0, 1)
        t.update(1.0 / 60.0, cfg)
        got = t.deco.count > 0
        if got != want:
            return False, f"装饰「{deco}」粒子数 {t.deco.count}，期望有={want}"
        if want and t.deco._shape not in F._SHAPE_NAMES:
            return False, f"装饰「{deco}」用了未知图案 {t.deco._shape}"
    return True, "彩带 / 星光会撒粒子，选「无」则只有文字"


def test_text_draws_and_rises():
    """文字要真的画出来，且随时间向上移动。"""
    t = ClickTextField(W, H)
    cfg = _tcfg(click_text_deco="无", click_text_rise=120.0)
    t.set_pointer(320.0, 300.0, 1)
    t.update(1.0 / 60.0, cfg)
    buf = np.zeros(W * H, np.uint32)
    t.draw(buf, W, H, cfg)
    n0 = int((buf > 0).sum())
    if n0 <= 0:
        return False, "弹字一帧都没画出来"
    # 行号 = 扁平下标 // 宽。跑一段后平均行号应更小（更靠上）
    y0 = float((np.nonzero(buf > 0)[0] // W).mean())
    _run(t, cfg, 60, 0.6, (320.0, 300.0), first_click=False)
    buf2 = np.zeros(W * H, np.uint32)
    t.draw(buf2, W, H, cfg)
    if not (buf2 > 0).any():
        return False, "上飘途中文字消失了"
    y1 = float((np.nonzero(buf2 > 0)[0] // W).mean())
    if y1 >= y0 - 3.0:
        return False, f"平均行号 {y0:.0f} → {y1:.0f}，没有上飘"
    return True, f"弹出 {n0} 像素，0.6 秒后平均行号 {y0:.0f} → {y1:.0f}（在上飘）"


# ---------------------------------------------------------- 4. 分组重构
def test_categories():
    want = ["全屏特效", "鼠标拖动特效", "鼠标点击特效", "鼠标点击音效",
            "其他特效"]
    if list(fx_kinds.CATEGORY_ORDER) != want:
        return False, f"分组是 {fx_kinds.CATEGORY_ORDER}，期望 {want}"
    if fx_kinds.effects_of("鼠标点击特效") != ["点击表情", "点击文字"]:
        return False, ("鼠标点击特效组的内容是 "
                       f"{fx_kinds.effects_of('鼠标点击特效')}")
    # 「点击音效」独立成类：它跟画什么无关，跟点击特效挤在一起会让用户
    # 一开声音就得关掉图案。
    if fx_kinds.effects_of("鼠标点击音效") != ["点击音效"]:
        return False, ("鼠标点击音效组的内容是 "
                       f"{fx_kinds.effects_of('鼠标点击音效')}")
    drag = fx_kinds.effects_of("鼠标拖动特效")
    for name in ("聚光灯", "颜料盘", "宠物跟随", "涟漪", "真实水波"):
        if name not in drag:
            return False, f"{name} 不在鼠标拖动特效组里"
    for cat in fx_kinds.CATEGORY_ORDER:
        if not fx_kinds.effects_of(cat):
            return False, f"{cat} 分组是空的"
    # 侧栏顺序 = 分组顺序拼起来
    flat = [p for _, ps in fx_kinds.NAV_GROUPS for p in ps]
    if flat != fx_kinds.nav_order()[:len(flat)]:
        return False, "侧栏顺序与分组顺序对不上"
    return True, (f"{len(fx_kinds.CATEGORY_ORDER)} 个分组，点击组含 "
                  f"{fx_kinds.effects_of('鼠标点击特效')}，"
                  f"音效单独一组")


def test_click_effects_are_exclusive():
    """两个点击特效默认互斥（用户选的"只生效一个"），且都在鼠标层。"""
    for name in ("点击表情", "点击文字"):
        if fx_kinds.layer_of(name) != "mouse":
            return False, f"{name} 不在鼠标层，而画在 {fx_kinds.layer_of(name)}"
        if fx_kinds.multi_key_of(name) != "multi_mouse":
            return False, (f"{name} 的多开键是 {fx_kinds.multi_key_of(name)}，"
                           f"期望 multi_mouse")
    # 默认 multi_mouse=False -> 开一个会关掉另一个
    app = d.App.__new__(d.App)
    app.cfg = dict(d.DEFAULTS)
    app.snapshot = dict(app.cfg)
    app.user_presets = {}
    app._autosave_dirty = False
    app.set("click_emoji_enabled", True)
    if app.cfg["click_text_enabled"]:
        return False, "前提不成立：一开始文字就是开着的"
    app.set("click_text_enabled", True)
    if app.cfg["click_emoji_enabled"]:
        return False, "开了「点击文字」却没有关掉「点击表情」—— 互斥没生效"
    if not app.cfg["click_text_enabled"]:
        return False, "「点击文字」没能打开"
    # 打开多开就都留着
    app.set("multi_mouse", True)
    app.set("click_emoji_enabled", True)
    if not (app.cfg["click_emoji_enabled"] and app.cfg["click_text_enabled"]):
        return False, "允许多开之后两个仍不能共存"
    return True, "默认互斥（开文字会关掉表情）；允许多开后两个共存"


def test_registry_and_defaults():
    for k in ("click_emoji_enabled", "click_emoji_shape", "click_emoji_count",
              "click_emoji_size", "click_emoji_spread", "click_emoji_life",
              "click_emoji_color", "click_emoji_gravity",
              "click_emoji_rotate", "click_emoji_opacity",
              "click_text_enabled", "click_text_list", "click_text_color",
              "click_text_size", "click_text_font", "click_text_deco",
              "click_text_rise", "click_text_life", "click_text_opacity",
              "click_text_voice_match", "click_sfx_dir",
              "click_emoji_align"):
        if k not in d.DEFAULTS:
            return False, f"DEFAULTS 缺 {k}"
    if d.DEFAULTS["click_emoji_shape"] not in F._SHAPE_NAMES:
        return False, f"默认图案 {d.DEFAULTS['click_emoji_shape']!r} 没有实现"
    if d.DEFAULTS["click_text_font"] not in F._FONT_FILES:
        return False, f"默认字体 {d.DEFAULTS['click_text_font']!r} 没有实现"
    if d.DEFAULTS["click_text_deco"] not in ("无", "彩带", "星光"):
        return False, f"默认装饰 {d.DEFAULTS['click_text_deco']!r} 非法"
    for page in ("点击表情", "点击文字"):
        if page not in d.PRESET_GROUPS:
            return False, f"PRESET_GROUPS 缺 {page}"
        for n in d.PRESET_GROUPS[page]:
            if n not in d.PRESETS:
                return False, f"预设 {n} 没定义"
            bad = [k for k in d.PRESETS[n] if k not in d.DEFAULTS]
            if bad:
                return False, f"预设 {n} 含未登记的键：{bad}"
    return True, "19 个配置键齐全，默认值都指向已实现的图案/字体，预设全部已登记"


def test_scene_wiring():
    """接进 Scene：只落鼠标层；关掉后清干净；layer_activity 认它们。

    `MouseProbe` 读的是**真实光标**，测试环境里位置不定、也点不到 —— 直接用
    桩替换 `update()`，第一帧给一次点击，之后不再点。同时把 mx/my 钉在画面
    中央，免得宠物/特效被整只裁到屏幕外（那样"没画出像素"与功能坏掉就分不清）。
    """
    from _verify_kinds import _base_cfg
    from fx_scene import Scene

    box = {"fired": True}

    def fake_update(dt, ox, oy, keys=False):
        sc.mouse.mx, sc.mouse.my = W * 0.5, H * 0.5
        sc.mouse.clicked = 0 if box["fired"] else 1
        return 0.0

    for key, page in (("click_emoji_enabled", "点击表情"),
                      ("click_text_enabled", "点击文字")):
        sc = Scene(W, H)
        sc.mouse.update = fake_update
        sc.cfg = _base_cfg(**{key: True})
        fs = np.zeros(W * H, np.uint32)
        ms = np.zeros(W * H, np.uint32)
        for k in range(20):
            box["fired"] = k >= 1          # 只在第一帧点击一次
            fs[:] = 0
            ms[:] = 0
            sc.step(1.0 / 60.0, fs, W, H, ms)
        if not ms.any():
            return False, f"{page}：开着却一个像素都没落到鼠标层"
        if fs.any():
            return False, f"{page}：像素跑进了全屏层"
        if not sc.layer_activity()[1]:
            return False, f"{page}：有内容却报鼠标层为空 -> 会被冻住"

        # 关掉之后必须清干净
        sc2 = Scene(W, H)
        sc2.mouse.update = fake_update
        sc2.cfg = _base_cfg()
        blank = np.zeros(W * H, np.uint32)
        for _ in range(30):
            sc2.step(1.0 / 60.0, blank, W, H, blank)
        if sc2.click_emoji.active() or sc2.click_text.active():
            return False, f"{page}：关掉之后仍报 active（残留未清）"
        if sc2.layer_activity()[1]:
            return False, f"{page}：全关之后鼠标层仍报有内容"
    return True, "两个点击特效只落鼠标层、参与判空、关掉后清干净"


# ------------------------------------------------- 5. 负向对照
def test_negative_copy_instead_of_over():
    """把共用的 over 混合换成覆盖写法，混合类断言必须变红。

    点击特效和宠物共用同一份 `_over_blend`，所以这条同时守着两边。
    """
    marker = "    dst[...] = out\n"
    # ⚠️ 唯一性要**跨 fx_image / fx_click / fx_effects 一起数**：拆分后混合实现
    # 住在 fx_image.py，若有人又在别处抄一份，这条对照就该变红。
    total = _srclib.count_marker(marker)
    if total != 1:
        return False, f"定位不到共用的混合写回（三个产品文件合计找到 {total} 处）"
    orig = _srclib.read_src("fx_image.py")
    stripped = orig.replace(marker, "    dst[...] = np.zeros(dst.shape, np.uint32)\n")
    if stripped == orig:
        return False, "负向对照没改到源码"

    try:
        # `_over_blend` 住在 fx_image，而 ClickEmojiField 在 import 时把它绑进了
        # 自己的命名空间 —— 必须连着 fx_image 一起重新 exec（详见 _srclib）。
        with _srclib.patched("fx_image", stripped):
            mod = _srclib.exec_module("fx_click.py", "fxclick_click_copy")
    except Exception as e:
        return False, f"负向对照模块编译失败：{type(e).__name__}: {e}"

    # 覆盖写法下，**被写过的区域内**两种底色应逐像素完全相同（根本没读底色）。
    #
    # ⚠️ 必须先把范围收进"真的被改动的像素"：直接比整块缓冲是没意义的 ——
    # 没被写到的地方本来就各自保持着底色，两种底色当然处处不同，那会给出
    # 一个看着很漂亮的失败（或成功）数字，却什么都没测到。
    cfg = _ecfg(click_emoji_count=24.0, click_emoji_life=3.0)
    bg_bright = np.uint32(0xFFF0F0F0)
    bg_dark = np.uint32(0xFF202020)

    def draw_on(pack):
        f = mod.ClickEmojiField(W, H)
        f.set_pointer(W * 0.5, H * 0.5, 1)
        f.update(1.0 / 60.0, cfg)
        for _ in range(6):
            f.update(1.0 / 60.0, cfg)
        buf = np.full(W * H, np.uint32(pack), np.uint32)
        f.draw(buf, W, H, cfg)
        return buf

    bright = draw_on(bg_bright)
    dark = draw_on(bg_dark)
    painted = (bright != bg_bright) | (dark != bg_dark)
    n_painted = int(painted.sum())
    if n_painted < 100:
        return False, f"负向对照没画上东西（只改动 {n_painted} 像素），无法判断"
    edge = painted & (bright != dark)
    if int(edge.sum()) != 0:
        return False, (f"负向对照失效：被写过的 {n_painted} 个像素里仍有 "
                       f"{int(edge.sum())} 个随底色变化")
    return True, (f"覆盖写法下被写过的 {n_painted} 个像素两种底完全一致"
                  f"（判据确实在测混合方式，而非底色本身）")


# ------------------------------------------------- 文字不能被横向切掉
def _text_footprint(field, cfg, text, font, size):
    """返回 (精灵墨迹宽, 落屏墨迹宽, 落屏墨迹高, 精灵高)。"""
    spr = F._text_sprites(text, font, size, cfg["click_text_color"])[-1]
    a = spr[..., 3]
    ys, xs = np.nonzero(a > 0.02)
    spr_w = xs.max() - xs.min() + 1
    spr_h = ys.max() - ys.min() + 1
    buf = np.zeros(W * H, np.uint32)
    field.draw(buf, W, H, cfg)
    img = buf.reshape(H, W)
    ys2, xs2 = np.nonzero(img > 0)
    if xs2.size == 0:
        return spr_w, 0, 0, spr_h
    return spr_w, xs2.max() - xs2.min() + 1, ys2.max() - ys2.min() + 1, spr_h


def test_text_not_clipped_horizontally():
    """文字必须**整幅**落到屏上，不能被横向裁掉。

    这条守着 `ClickTextField._blit` 曾经的一个真 bug：它拿 `img.shape[0]`
    （高度）当正方形边长用，而文字精灵是**非正方形**的（"太好了" 是
    116×46）—— 于是横向只取到 46px，墨迹 104px 只落屏 41px（切掉 61%），
    用户看到的就是"文字显示不完整"。图案精灵恰好是正方形，所以在那边
    一直没暴露。
    """
    cases = (("太好了", "微软雅黑", 34.0), ("好耶", "行楷", 40.0),
             ("绝绝子", "琥珀", 30.0))
    for text, font, size in cases:
        cfg = _tcfg(click_text_list=text, click_text_font=font,
                    click_text_size=size, click_text_deco="无",
                    click_text_spin=0.0)
        f = ClickTextField(W, H)
        f.set_pointer(W * 0.5, H * 0.5, 1)
        for _ in range(20):            # 推进到弹出完成（最后一档缩放）
            f.update(1.0 / 60.0, cfg)
        spr_w, scr_w, scr_h, spr_h = _text_footprint(f, cfg, text, font, size)
        if scr_w < spr_w * 0.95:
            return False, (f"{text}（{font}）：精灵墨迹宽 {spr_w}px，"
                           f"落屏只有 {scr_w}px（切掉了 "
                           f"{100 * (1 - scr_w / spr_w):.0f}%）")
        if scr_h < spr_h * 0.95:
            return False, (f"{text}（{font}）：纵向也被切了 "
                           f"{spr_h}px -> {scr_h}px")
    return True, "3 组文字都整幅落屏（宽高均 ≥ 精灵的 95%）"


def test_text_clip_check_is_sensitive():
    """负向对照：把 `_blit` 改回"拿 shape[0] 当边长"，上面那条必须变红。

    没有它，`test_text_not_clipped_horizontally` 可能只是在测"我新写的
    东西自洽"，而不是真的在拦这个 bug。
    """
    src = _srclib.read_src("fx_click.py")
    good = ("        sh, sw = img.shape[:2]\n"
            "        x0 = int(round(cx - sw * 0.5))\n"
            "        y0 = int(round(cy - sh * 0.5))\n"
            "        gx0, gy0 = max(0, x0), max(0, y0)\n"
            "        gx1, gy1 = min(int(w), x0 + sw), min(int(h), y0 + sh)\n")
    bad = ("        sw = img.shape[0]\n"
           "        sh = sw\n"
           "        x0 = int(round(cx - sw * 0.5))\n"
           "        y0 = int(round(cy - sh * 0.5))\n"
           "        gx0, gy0 = max(0, x0), max(0, y0)\n"
           "        gx1, gy1 = min(int(w), x0 + sw), min(int(h), y0 + sh)\n")
    if src.count(good) != 1:
        return False, f"定位不到 _blit 的宽高取值（找到 {src.count(good)} 处）"

    try:
        # `_blit` 已随点击管线搬进 fx_click.py（见 test_text_clip_check_is_sensitive
        # 上面的 `src = _srclib.read_src("fx_click.py")`）。
        mod = _srclib.exec_module("fx_click.py", "fxclick_clip_copy",
                                  src=src.replace(good, bad))
    except Exception as e:
        return False, f"负向对照模块编译失败：{type(e).__name__}: {e}"

    cfg = _tcfg(click_text_list="太好了", click_text_deco="无",
                click_text_spin=0.0)
    f = mod.ClickTextField(W, H)
    f.set_pointer(W * 0.5, H * 0.5, 1)
    for _ in range(20):
        f.update(1.0 / 60.0, cfg)
    spr_w, scr_w, _, _ = _text_footprint(f, cfg, "太好了", "微软雅黑", 34.0)
    if scr_w >= spr_w * 0.95:
        return False, (f"负向对照失效：改回 shape[0] 当边长后落屏仍有 "
                       f"{scr_w}px（精灵 {spr_w}px）")
    return True, (f"退回「拿高度当边长」后落屏只剩 {scr_w}px / {spr_w}px"
                  f"（判据确实在拦横向裁剪）")


# ------------------------------------------------- 图案默认色与「彩色」
def test_shape_default_colors():
    """每个图案都有惯用色，且**选图案会自动套上**。"""
    import fx_ui
    if dict(F._SHAPE_DEFAULT_COLORS) != dict(fx_ui.SHAPE_DEFAULT_COLORS):
        return False, ("默认色表不一致："
                       f"UI={fx_ui.SHAPE_DEFAULT_COLORS} / "
                       f"实现={F._SHAPE_DEFAULT_COLORS}")
    missing = [n for n in F._SHAPE_NAMES if n not in F._SHAPE_DEFAULT_COLORS]
    if missing:
        return False, f"这些图案没有惯用色：{missing}"

    # 缺键时按图案兜底（预设只写图案、不写色时走这条路）
    for shape, want in (("爱心", "#FF5D73"), ("星星", "#FFC145"),
                        ("雪花", "#FFFFFF")):
        f = ClickEmojiField(W, H)
        cfg = _ecfg(click_emoji_shape=shape, click_emoji_count=8.0)
        cfg.pop("click_emoji_color")
        f.set_pointer(W * 0.5, H * 0.5, 1)
        f.update(1.0 / 60.0, cfg)
        got = f.engine.rgb[0]
        exp = F._hex_rgb(want)
        if not np.allclose(got, exp, atol=1.5):
            return False, (f"{shape} 缺键时没兜到惯用色：拿到 "
                           f"{tuple(int(v) for v in got)}，期望 {exp}")

    # UI 侧：选图案会把颜色键一起改掉
    class _Stub:
        def __init__(self):
            self.cfg = dict(d.DEFAULTS)
            self.set_calls = []

        def set(self, k, v):
            self.cfg[k] = v
            self.set_calls.append((k, v))

    stub = _Stub()
    panel = fx_ui.ControlPanel.__new__(fx_ui.ControlPanel)
    panel.app = stub
    fx_ui.ControlPanel._on_shape_change(panel, "星星")
    if stub.cfg["click_emoji_color"] != "#FFC145":
        return False, (f"选「星星」后颜色没跟着变："
                       f"{stub.cfg['click_emoji_color']}")
    return True, ("8 个图案的惯用色齐全且与 UI 一致；缺键按图案兜底；"
                  "选图案会自动套色")


def test_rainbow_is_per_particle():
    """「彩色」= 逐粒子从调色板取色；单色时整簇同色。"""
    import fx_ui
    if fx_ui.CLICK_RAINBOW != F.COLOR_RAINBOW:
        return False, (f"哨兵值不一致：UI={fx_ui.CLICK_RAINBOW!r} / "
                       f"实现={F.COLOR_RAINBOW!r}")
    if F.COLOR_RAINBOW not in fx_ui.CLICK_EMOJI_COLORS:
        return False, "颜色表里没有「彩色」这一项，面板上选不到"

    # 任何图案都能用彩色（不只是彩带）
    for shape in ("爱心", "雪花", "彩带"):
        f = ClickEmojiField(W, H)
        cfg = _ecfg(click_emoji_shape=shape, click_emoji_count=24.0,
                    click_emoji_color=F.COLOR_RAINBOW)
        f.set_pointer(W * 0.5, H * 0.5, 1)
        f.update(1.0 / 60.0, cfg)
        uniq = len({tuple(int(v) for v in c)
                    for c in f.engine.rgb[:f.engine.count]})
        if uniq < 3:
            return False, (f"{shape} 选「彩色」后只出现 {uniq} 种颜色 "
                           f"（哨兵没被识别成彩色？）")
    # 单色时反过来必须只有 1 种
    f = ClickEmojiField(W, H)
    f.set_pointer(W * 0.5, H * 0.5, 1)
    f.update(1.0 / 60.0, _ecfg(click_emoji_count=24.0,
                               click_emoji_color="#FF5D73"))
    uniq = len({tuple(int(v) for v in c)
                for c in f.engine.rgb[:f.engine.count]})
    if uniq != 1:
        return False, f"单色却出现 {uniq} 种颜色"
    return True, "「彩色」逐粒子取色（3 种图案都验过），单色仍只有 1 种"


def test_confetti_is_curved():
    """彩带要是**弯的**飘带，不是直条。

    判据取"各列墨迹中心的 y 极差"：直线段这个值是 0，弯曲才会拉开。
    之前是两段直线矩形拼的，看着像硬纸片。
    """
    spr = F._shape_sprite("彩带", "#FFC145", 64, 0, 12)
    a = spr[..., 3]
    ys, xs = np.nonzero(a > 0.5)
    if xs.size == 0:
        return False, "彩带画不出东西"
    centers = []
    for x in range(int(xs.min()), int(xs.max()) + 1, 4):
        col = np.nonzero(a[:, x] > 0.5)[0]
        if col.size:
            centers.append(float(col.mean()))
    if len(centers) < 5:
        return False, f"可采样的列太少（{len(centers)}）"
    spread = max(centers) - min(centers)
    if spread < 8.0:
        return False, (f"各列墨迹中心的 y 极差只有 {spread:.1f}px（<8）"
                       f"—— 彩带还是直的")
    # 宽度也要沿程收放（"在翻卷"），否则只是一条弯棍
    wid = []
    for x in range(int(xs.min()), int(xs.max()) + 1, 4):
        col = np.nonzero(a[:, x] > 0.5)[0]
        if col.size:
            wid.append(col.size)
    ratio = (max(wid) / max(1, min(wid))) if wid else 0.0
    if ratio < 1.4:
        return False, f"彩带各处宽度几乎不变（{ratio:.2f} 倍），没有翻卷感"
    return True, (f"彩带弯曲（各列中心 y 极差 {spread:.0f}px），"
                  f"宽度沿程变化 {ratio:.1f} 倍")


def test_text_tilt_random():
    """文字每次点击随机倾斜；幅度为 0 时全部水平。"""
    f = ClickTextField(W, H)
    cfg = _tcfg(click_text_spin=1.0, click_text_deco="无")
    angles, drifts = [], []
    for _ in range(40):
        f.set_pointer(W * 0.5, H * 0.5, 1)
        f.update(1.0 / 60.0, cfg)
        pp = f.pops[-1]
        angles.append(F._TEXT_TILT_STEPS[pp["tilt"]])
        drifts.append(pp["drift"])
    if len(set(angles)) < 3:
        return False, f"倾斜角只有 {sorted(set(angles))} 种，几乎不随机"
    if min(angles) >= 0 or max(angles) <= 0:
        return False, f"倾斜只朝一个方向：{sorted(set(angles))}"
    if max(abs(a) for a in angles) > 30.0 + 1e-6:
        return False, f"倾斜超过 ±30°：{max(angles)}"
    if max(drifts) - min(drifts) < 0.5:
        return False, "横向漂移几乎不变，几行文字会叠在同一条竖线上"
    # 幅度 0 → 只有水平
    f2 = ClickTextField(W, H)
    cfg0 = _tcfg(click_text_spin=0.0, click_text_deco="无")
    got = set()
    for _ in range(20):
        f2.set_pointer(W * 0.5, H * 0.5, 1)
        f2.update(1.0 / 60.0, cfg0)
        got.add(F._TEXT_TILT_STEPS[f2.pops[-1]["tilt"]])
    if got != {0.0}:
        return False, f"倾斜幅度 0 却出现了 {got}"
    # 角度是**点击时定下来的**，同一次点击内不能每帧变
    f3 = ClickTextField(W, H)
    f3.set_pointer(W * 0.5, H * 0.5, 1)
    f3.update(1.0 / 60.0, cfg)
    t0 = f3.pops[-1]["tilt"]
    for _ in range(30):
        f3.update(1.0 / 60.0, cfg)
        if f3.pops and f3.pops[-1]["tilt"] != t0:
            return False, "同一次点击内倾斜角在变（每帧重随机会疯狂抖动）"
    return True, (f"40 次点击出现 {len(set(angles))} 种倾斜角"
                  f"（{min(angles):+.0f}°~{max(angles):+.0f}°），"
                  f"幅度 0 时全水平，且同一次点击内角度固定")


# ------------------------------------------------- 6. 自定义表情包（图片文件夹）
def _mk_img_folder(wide=(300, 30), tall=(30, 300)):
    """造一个小图库：一张宽图、一张高图、外加一个坏文件。

    坏文件是刻意的 —— 真实的表情包文件夹里就有 0 字节文件、改了后缀的
    网页图，池子必须跳过它们而不是整体作废。
    """
    from PIL import Image
    folder = tempfile.mkdtemp(prefix="fx_click_img_")
    if wide:
        Image.new("RGBA", wide, (255, 0, 0, 255)).save(
            os.path.join(folder, "wide.png"))
    if tall:
        Image.new("RGB", tall, (0, 0, 255)).save(
            os.path.join(folder, "tall.jpg"))
    with open(os.path.join(folder, "broken.png"), "wb") as fh:
        fh.write(b"not an image at all")
    return folder


def _wait_pool_at(src, px, kind="folder", timeout=10.0):
    """等后台把这一批素材解完（判据用**完整状态**，不是"有个数就行"）。

    ⚠️ `px` 必须与 `configure` 时传的**完全一致**：状态是按整个 job（种类 +
    来源 + 尺寸）比对的，尺寸写错就永远停在"读取中…"、白等到超时 —— 而这种
    错**不会让任何断言失败**，只是让脚本悄悄慢上二十秒。默认 10 秒超时就是为了
    让这类错误不至于太贵。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = F.CLICK_IMAGE_POOL.state(kind, src, px)
        if st and st != "读取中…":
            return st
        time.sleep(0.05)
    return F.CLICK_IMAGE_POOL.state(kind, src, px)


def _wait_pool(folder, timeout=10.0):
    return _wait_pool_at(folder, 40, "folder", timeout)


def test_custom_image_pool():
    """扫描 / 解码 / 两个尺寸档 / 坏文件按**文件**计数。"""
    folder = _mk_img_folder()
    pool = F.CLICK_IMAGE_POOL
    pool.clear()
    pool.configure("folder", folder, 40)
    st = _wait_pool(folder)
    n, files, failed = pool.stats()
    pool.clear()
    shutil.rmtree(folder, ignore_errors=True)
    if files != 3:
        return False, f"扫到的文件数 {files}（期望 3：两张图 + 一个坏文件）"
    if failed != 1:
        return False, (f"解码失败计数 {failed}（期望 1 —— 按文件算，"
                       f"同一个坏文件在 2 个尺寸档上各失败一次只算 1）")
    if n != 4:
        return False, f"精灵条目 {n}（期望 4 = 2 张图 × 2 档尺寸）"
    if "已载入 4 张" not in st or "1 张读不了" not in st:
        return False, f"状态文案不对：{st!r}"
    return True, f"3 个文件 -> 4 个精灵条目，坏文件单独计数，状态 {st!r}"


def test_custom_image_sprite_format():
    """精灵格式必须与内置图案那条路径一致（预乘 float32 RGBA）。"""
    folder = _mk_img_folder()
    pool = F.CLICK_IMAGE_POOL
    pool.clear()
    pool.configure("folder", folder, 40)
    _wait_pool(folder)
    sprites = pool.sprites()
    shapes = sorted({s.shape for s in sprites.frames})
    bad = []
    for s in sprites.frames:
        if s.dtype != np.float32 or s.ndim != 3 or s.shape[2] != 4:
            bad.append(s.shape)
        elif not bool((s[..., :3] <= s[..., 3:4] + 1e-6).all()):
            bad.append("not premultiplied")
        elif float(s.max()) > 1.0 + 1e-6:
            bad.append("range > 1")
    pool.clear()
    shutil.rmtree(folder, ignore_errors=True)
    if bad:
        return False, f"精灵格式不对：{bad[:3]}"
    # 长边必须落在「目标尺寸 × 尺寸档」上，且矩形保持原比例
    longs = sorted({max(s.shape[0], s.shape[1]) for s in sprites.frames})
    ratios = sorted({round(max(s.shape[0], s.shape[1])
                           / max(1, min(s.shape[0], s.shape[1])), 1)
                     for s in sprites.frames})
    if longs != [30, 40]:
        return False, f"长边档位 {longs}（期望 [30, 40] = 40×0.74 / 40×1.0）"
    if max(ratios) < 5.0:
        return False, f"矩形没保住比例：长宽比 {ratios}"
    return True, (f"预乘 float32 RGBA；形状 {shapes}；长边 {longs}；"
                  f"长宽比 {ratios}（矩形保持原比例）")


def test_custom_image_rect_not_clipped():
    """矩形图片落屏时**必须按原比例**铺开。

    这是 `ClickTextField._blit` 那个坑的图片版：拿 `shape[0]`（高）当正方形
    边长，会让 300×30 的图只落进 30×30 的方块 —— 左右各切掉 90%。
    内置图案恰好是正方形，所以这条路径一直没暴露过。

    判据用"落屏跨度更接近图宽还是图高"：正确实现的 x 跨度 ≈ 图宽，
    错误实现 ≈ 图高（两者相差 10 倍，不存在误判空间）。
    """
    folder = _mk_img_folder(wide=(300, 30), tall=None)
    pool = F.CLICK_IMAGE_POOL
    pool.clear()
    pool.configure("folder", folder, 60)
    _wait_pool_at(folder, 60)          # 尺寸必须与上面 configure 的一致
    sprites = pool.sprites()
    if not sprites:
        pool.clear()
        shutil.rmtree(folder, ignore_errors=True)
        return False, "图库没载进来，无法判断"
    # 每张图有两个尺寸档，粒子取哪一档是随机的 —— 参照系取**整张表**，
    # 别拿 `sprites[0]`：那是洗过牌的顺序，与这一簇实际用到的那张未必同一档。
    sizes = sorted({(int(s.shape[1]), int(s.shape[0]))
                     for s in sprites.frames})

    cfg = _ecfg(click_emoji_source=F.CLICK_SOURCE_FOLDER,
                click_emoji_custom_dir=folder, click_emoji_count=8.0,
                click_emoji_size=60.0, click_emoji_spread=0.0,
                click_emoji_gravity=0.0, click_emoji_life=2.0)
    f = ClickEmojiField(W, H)
    f.set_pointer(W * 0.5, H * 0.5, 1)
    buf = np.zeros(W * H, np.uint32)
    for _ in range(4):
        f.update(1.0 / 60.0, cfg)
    f.draw(buf, W, H, cfg)
    used = f._imgs is not None
    alpha = ((buf >> np.uint32(24)) & np.uint32(0xFF)).reshape(H, W)
    ys, xs = np.nonzero(alpha)
    pool.clear()
    shutil.rmtree(folder, ignore_errors=True)
    if not used or xs.size == 0:
        return False, f"自定义图片模式没上屏（用了图片={used}）"
    span_x = int(xs.max() - xs.min()) + 1
    span_y = int(ys.max() - ys.min()) + 1
    if not sizes or min(w for w, _ in sizes) <= max(h for _, h in sizes) * 3:
        return False, f"测试材料本身不是宽图（{sizes}），断言会空转"
    # 一簇粒子都在同一点，墨迹 = 其中最大那张；它必须与表里某个尺寸对得上
    best = min(sizes, key=lambda e: abs(e[0] - span_x) + abs(e[1] - span_y))
    if abs(best[0] - span_x) > 2 or abs(best[1] - span_y) > 2:
        return False, (f"墨迹 {span_x}×{span_y} 与图库里的 {sizes} 对不上 —— "
                       f"落屏时被当正方形裁了")
    return True, (f"墨迹 {span_y}×{span_x} 与图库 {sizes} 相符（宽扁比例保住）；"
                  f"若拿高当边长，x 跨度只会有 {best[1]}")


def test_custom_image_fallback():
    """素材没就绪 / 文件夹空 -> 回退内置图案，点击**始终有反馈**。"""
    empty = tempfile.mkdtemp(prefix="fx_click_empty_")
    pool = F.CLICK_IMAGE_POOL
    pool.clear()
    pool.configure("folder", empty, 40)
    st = _wait_pool(empty)
    cfg = _ecfg(click_emoji_source=F.CLICK_SOURCE_FOLDER,
                click_emoji_custom_dir=empty, click_emoji_count=12.0,
                click_emoji_spread=200.0)
    f = ClickEmojiField(W, H)
    f.set_pointer(W * 0.5, H * 0.5, 1)
    buf = np.zeros(W * H, np.uint32)
    f.update(1.0 / 60.0, cfg)
    for _ in range(4):
        f.update(1.0 / 60.0, cfg)
    f.draw(buf, W, H, cfg)
    # 未就绪的那一刻（还没扫完）也必须能画
    f2 = ClickEmojiField(W, H)
    f2.set_pointer(W * 0.5, H * 0.5, 1)
    buf2 = np.zeros(W * H, np.uint32)
    f2.update(1.0 / 60.0, _ecfg(click_emoji_source=F.CLICK_SOURCE_FOLDER,
                                click_emoji_custom_dir="/no/such/dir",
                                click_emoji_spread=200.0))
    for _ in range(4):
        f2.update(1.0 / 60.0, _ecfg(click_emoji_source=F.CLICK_SOURCE_FOLDER,
                                    click_emoji_custom_dir="/no/such/dir",
                                    click_emoji_spread=200.0))
    f2.draw(buf2, W, H, _ecfg(click_emoji_spread=200.0))
    pool.clear()
    shutil.rmtree(empty, ignore_errors=True)
    if f._imgs is not None:
        return False, "空文件夹却用了自定义精灵"
    if not buf.any() or not buf2.any():
        return False, (f"回退后没画出东西（空文件夹 {bool(buf.any())} / "
                       f"路径不存在 {bool(buf2.any())}）")
    if "没找到图片" not in st:
        return False, f"空文件夹的状态文案不对：{st!r}"
    return True, f"空文件夹（{st!r}）与不存在的路径都回退内置图案且画得出"


# ------------------------------------------------- 7. 点击语音
def _mk_audio_folder():
    folder = tempfile.mkdtemp(prefix="fx_click_voice_")
    for nm in ("a.wav", "b.wav"):
        with wave.open(os.path.join(folder, nm), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\x00\x00" * 800)
    with open(os.path.join(folder, "readme.txt"), "w", encoding="utf-8") as fh:
        fh.write("这不是音频，不该被扫进来")
    return folder


def _wait_busy(vp, want, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if vp.playing() == want:
            return True
        time.sleep(0.02)
    return False


def test_voice_no_overlap():
    """上一段没播完时，新的点击必须**被丢弃**（返回 False，不排队）。"""
    folder = _mk_audio_folder()
    playing = [True]
    cmds = []
    orig = fx_click._mci_send

    def fake(cmd):
        cmds.append(cmd)
        if cmd.startswith("status"):
            return 0, "playing" if playing[0] else "stopped"
        return 0, ""

    fx_click._mci_send = fake
    try:
        vp = F.VoicePlayer()
        first = vp.play_random(folder)
        second = vp.play_random(folder)      # 还在"播放中"
        third = vp.play_random(folder)
        still_busy = vp.playing()
        playing[0] = False
        released = _wait_busy(vp, False)
        fourth = vp.play_random(folder)
        # 目录扫描已经收进模块级 `list_audio`（面板与点击两处共用它的缓存），
        # 所以这里查的是它，而不是播放器自身的字段。
        scanned = F.list_audio(folder)
    finally:
        fx_click._mci_send = orig
        shutil.rmtree(folder, ignore_errors=True)

    if not first or not fourth:
        return False, "空闲时没能起播（first=%s fourth=%s）" % (first, fourth)
    if second or third:
        return False, "播放中又接受了一次点击 —— 两段语音会叠在一起"
    if not still_busy or not released:
        return False, f"busy 标志不对（播放中 {still_busy} / 播完释放 {released}）"
    if len(scanned) != 2:
        return False, f"目录扫描把非音频也算进去了：{scanned}"
    if not any(c.startswith("play ") for c in cmds):
        return False, f"没发出播放命令：{cmds}"
    if not any(c.startswith("open ") and "type waveaudio" in c for c in cmds):
        return False, f"wav 没用 waveaudio 设备打开：{cmds}"
    return True, (f"播放中连点 2 次都被丢弃；播完立即放行；只扫到 2 个音频"
                  f"（readme.txt 被排除）")


def test_voice_releases_on_failure():
    """播放线程**出错也必须放行** —— 否则语音这辈子只响一次（永远"忙"）。"""
    folder = _mk_audio_folder()

    def boom(cmd):
        raise RuntimeError("device gone")

    orig = fx_click._mci_send
    fx_click._mci_send = boom
    try:
        vp = F.VoicePlayer()
        started = vp.play_random(folder)
        released = _wait_busy(vp, False)
        again = vp.play_random(folder)
    finally:
        fx_click._mci_send = orig
        shutil.rmtree(folder, ignore_errors=True)
    if not started:
        return False, "起播就失败了（应当先占坑、之后才去碰设备）"
    if not released:
        return False, "设备异常后 busy 没释放 -> 语音只响一次"
    if not again:
        return False, "异常之后再也起不来了"
    return True, "设备抛异常时 busy 仍在 finally 里释放，后续点击照常"


def test_click_asset_defaults():
    """来源/素材类配置键存在、类型正确，坏值被钳制。"""
    want = (("click_emoji_source", str), ("click_emoji_custom_dir", str),
            ("click_sfx_enabled", bool), ("click_sfx_dir", str))
    for k, t in want:
        if k not in d.DEFAULTS:
            return False, f"DEFAULTS 缺 {k}"
        if not isinstance(d.DEFAULTS[k], t):
            return False, f"{k} 的默认值类型是 {type(d.DEFAULTS[k])}，期望 {t}"
    bad = []
    for k, v in (("click_sfx_dir", 123), ("click_sfx_dir", ["C:/x"]),
                 ("click_emoji_custom_dir", 3.5),
                 ("click_emoji_source", True)):
        ok, _ = d._coerce_cfg_value(k, v)
        if ok:
            bad.append((k, v))
    if bad:
        return False, f"坏值被收下了：{bad}"
    for k, v, exp in (("click_sfx_enabled", "是", True),
                      ("click_sfx_enabled", "关", False)):
        ok, got = d._coerce_cfg_value(k, v)
        if not ok or got is not exp:
            return False, f"{k}={v!r} 应转成 {exp}，实得 {got!r}"
    src = F.CLICK_SOURCES
    if d.DEFAULTS["click_emoji_source"] not in src:
        return False, f"默认来源 {d.DEFAULTS['click_emoji_source']!r} 不在 {src}"
    return True, f"4 个键齐备、类型正确；坏路径/坏来源被拒；默认来源在 {src} 内"


def test_scene_voice_wiring():
    """语音挂在**点击事件**上，与两个点击特效的开关无关；只在点击那一帧触发。"""
    from _verify_kinds import _base_cfg
    from fx_scene import Scene

    folder = _mk_audio_folder()
    calls = []

    class _Spy:
        def play_path(self, p, overlap=False, volume=None):
            calls.append(p)
            return True

        def play_random(self, f):
            calls.append(f)
            return True

    sc = Scene(W, H)
    sc.voice = _Spy()
    # 必须**两个**都换：click_sfx 在 __init__ 里就拿到了当时的播放器，
    # 换 scene.voice 换不掉它 —— 忘了这行，测试会"什么都没发生"，
    # 而产品代码本身是好的（这类替身失配最难查）。
    sc.click_sfx.player = sc.voice
    box = {"fired": True}

    def fake_update(dt, ox, oy, keys=False):
        sc.mouse.mx, sc.mouse.my = W * 0.5, H * 0.5
        sc.mouse.clicked = 0 if box["fired"] else 1
        return 0.0

    sc.mouse.update = fake_update
    buf = np.zeros(W * H, np.uint32)
    # 语音开着、两个点击特效都关着
    sc.cfg = _base_cfg(**_sfx_pack(folder), click_sfx_enabled=True)
    for k in range(6):
        box["fired"] = k >= 1
        sc.step(1.0 / 60.0, buf, W, H, buf)
    on_count = len(calls)
    off_calls = len(calls)
    sc.cfg = _base_cfg(**_sfx_pack(folder), click_sfx_enabled=False)
    for k in range(6):
        box["fired"] = k >= 1
        sc.step(1.0 / 60.0, buf, W, H, buf)
    shutil.rmtree(folder, ignore_errors=True)
    if on_count != 1:
        return False, f"开着时触发了 {on_count} 次（期望 1：只在点击那一帧）"
    if len(calls) != off_calls:
        return False, "关掉之后仍在触发"
    if calls and not str(calls[0]).startswith(folder):
        return False, f"播的不是那个文件夹里的音频：{calls[0]!r}"
    return True, ("只在点击那一帧触发一次、播的来自那个文件夹，"
                  "与两个点击特效的开关无关")


# ------------------------------------------------- 8. 点击文字的语音包
def _sfx_pack(folder):
    """把一个目录表达成「点击音效」的"音效文件夹"来源（统一模式认这个）。

    语音目录已经并入音效来源 —— 「语音与文字统一」要在**多条**音频里各取一行
    字，所以它只在「音效文件夹」那一档下成立。
    """
    return {"click_sfx_source": F.SFX_SOURCE_FOLDER, "click_sfx_dir": folder}


def _mk_pack(folder=None, names=("01-你好.wav", "02_再见.mp3", "3. 走起.wma")):
    """造一个语音包文件夹（真音频文件，名字带序号）。"""
    folder = folder or tempfile.mkdtemp(prefix="fx_textpack_")
    for i, nm in enumerate(names):
        p = os.path.join(folder, nm)
        if nm.lower().endswith(".wav"):
            with wave.open(p, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(8000)
                w.writeframes(b"\x00\x00" * 80)
        else:
            with open(p, "wb") as fh:      # 内容不重要，只要后缀与存在
                fh.write(b"\x00" * 64)
    with open(os.path.join(folder, "说明.txt"), "w", encoding="utf-8") as fh:
        fh.write("不是音频")
    return folder


def test_audio_label_cleaning():
    """文件名 -> 显示文字：去扩展名、去音轨号，且不把纯数字名剥成空。"""
    cases = [("01-你好.wav", "你好"), ("12_再见.mp3", "再见"),
             ("3. 走起.wma", "走起"), ("07、来吧.mp3", "来吧"),
             ("  太棒了  .m4a", "太棒了"), ("a/b/纯文件名.wav", "纯文件名"),
             ("1.mp3", "1"),          # 本身就是数字 -> 保留，不能剥成空
             ("2024.wav", "2024"),
             ("无后缀", "无后缀")]
    for src, want in cases:
        got = F.audio_label(src)
        if got != want:
            return False, f"{src!r} -> {got!r}，期望 {want!r}"
    if F.audio_label("") != "":
        return False, "空路径不该返回内容"
    return True, f"{len(cases)} 个文件名（含纯数字名 / 无后缀 / 带路径）转换正确"


def test_audio_labels_dedup_sorted():
    """文字栏里要显示的名字：去重、保持排序（重名不重复占一行）。"""
    folder = tempfile.mkdtemp(prefix="fx_labels_")
    for nm in ("01-你好.wav", "02-你好.mp3", "05-再见.wav", "note.txt"):
        with open(os.path.join(folder, nm), "wb") as fh:
            fh.write(b"\x00")
    got = F.audio_labels(folder)
    shutil.rmtree(folder, ignore_errors=True)
    if got != ["你好", "再见"]:
        return False, f"得到 {got}，期望 ['你好', '再见']（重名只留一行、非音频排除）"
    return True, "两个文件同叫「你好」只占一行，非音频文件被排除"


def test_text_voice_match_pairs_text_and_audio():
    """**核心不变量**：统一模式下弹出的文字必须就是所播那条音频的名字。

    判据取"文字 == audio_label(要播的路径)"，而不是"文字落在候选集合里" ——
    后者随机挑也能通过，测不出"两边各自随机"这个真正的失败模式。
    """
    folder = _mk_pack()
    cfg = _tcfg(**_sfx_pack(folder), click_text_voice_match=True,
                click_text_list="自定义甲\n自定义乙", click_text_deco="无")
    t = ClickTextField(W, H)
    pairs, used = [], []
    for _ in range(12):
        t.set_pointer(W * 0.5, H * 0.5, 1)
        t.update(1.0 / 60.0, cfg)
        t.pops = [p for p in t.pops if p["t"] < 0.05]
        path = t.take_voice()
        pairs.append((t.pops[-1]["text"], path))
        used.append(t.pops[-1]["text"])
    shutil.rmtree(folder, ignore_errors=True)

    bad = [(txt, p) for txt, p in pairs if not p or F.audio_label(p) != txt]
    if bad:
        return False, f"{len(bad)}/{len(pairs)} 次的文字与音频对不上：{bad[:2]}"
    if set(used) & {"自定义甲", "自定义乙"}:
        return False, "统一模式下仍弹了配置里的自定义文字"
    if len(set(used)) < 2:
        return False, f"12 次点击只出现 {set(used)} —— 语音包的随机没生效"
    if t.take_voice() is not None:
        return False, "take_voice 取走后没清空（会重复播上一条）"
    return True, (f"12 次点击全部对上（出现 {len(set(used))} 种："
                  f"{sorted(set(used))}），且不用配置里的自定义文字")


def test_text_voice_match_falls_back():
    """没选语音包 / 空目录 / 目录不存在 -> 退回自定义文字，点击不落空。"""
    empty = tempfile.mkdtemp(prefix="fx_pack_empty_")
    cases = [("", "没选语音包"), (empty, "空目录"),
             (os.path.join(empty, "不存在"), "目录不存在")]
    for folder, name in cases:
        cfg = _tcfg(**_sfx_pack(folder), click_text_voice_match=True,
                    click_text_list="甲\n乙", click_text_deco="无")
        t = ClickTextField(W, H)
        t.set_pointer(W * 0.5, H * 0.5, 1)
        t.update(1.0 / 60.0, cfg)
        t.pops = [p for p in t.pops if p["t"] < 0.05]
        if not t.pops:
            shutil.rmtree(empty, ignore_errors=True)
            return False, f"{name}：统一模式下点击什么都没弹"
        if t.pops[-1]["text"] not in ("甲", "乙"):
            shutil.rmtree(empty, ignore_errors=True)
            return False, f"{name}：退回了 {t.pops[-1]['text']!r}，不是自定义文字"
        if t.take_voice() is not None:
            shutil.rmtree(empty, ignore_errors=True)
            return False, f"{name}：没有音频却排了一条待播路径"
    shutil.rmtree(empty, ignore_errors=True)
    return True, "三种「没有音频」的情形都退回自定义文字且不排音频"


def test_text_voice_off_is_independent():
    """关闭统一：文字可自定义（轮换），这一次点击不指定音频。"""
    folder = _mk_pack()
    cfg = _tcfg(**_sfx_pack(folder), click_text_voice_match=False,
                click_text_list="甲\n乙\n丙", click_text_deco="无")
    t = ClickTextField(W, H)
    seq = []
    for _ in range(4):
        t.set_pointer(W * 0.5, H * 0.5, 1)
        t.update(1.0 / 60.0, cfg)
        t.pops = [p for p in t.pops if p["t"] < 0.05]
        seq.append((t.pops[-1]["text"], t.take_voice()))
    shutil.rmtree(folder, ignore_errors=True)
    if [s[0] for s in seq] != ["甲", "乙", "丙", "甲"]:
        return False, f"关闭统一后文字不是按自定义轮换：{seq}"
    if any(s[1] is not None for s in seq):
        return False, "关闭统一后仍在指定音频（会与随机语音打架）"
    return True, "关闭统一时文字按 甲→乙→丙→甲 轮换，且不指定音频"


# ------------------------------------------------- 9. 动图素材（GIF 要动起来）
def _center_color(spr):
    """精灵正中那个像素的不透明颜色（用来判断"现在是哪一帧"）。"""
    h, w = spr.shape[0], spr.shape[1]
    px = spr[h // 2, w // 2]
    a = float(px[3])
    if a <= 0.01:
        return None
    return tuple(int(round(float(v) * 255.0 / a)) for v in px[:3])


def _mk_gif(path, n=24, px=80, dur=100,
            colors=((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))):
    """造一张多帧 GIF（每帧一个纯色，方便按颜色判断"现在是第几帧"）。"""
    from PIL import Image
    frames = [Image.new("RGB", (px, px), colors[i % len(colors)])
              for i in range(n)]
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=dur, loop=0)
    return path


def test_frame_indexes_spread():
    """抽帧下标：等间隔、**含首尾**、够用时不抽。

    判据写成**性质**（长度 / 首尾 / 严格递增 / 间隔均匀）而不是某个写死的
    序列 —— 写死序列等于把实现抄进测试，实现改了测试跟着改，什么都守不住。
    """
    for n, cap in ((24, 12), (100, 10), (13, 7), (3, 12), (1, 12), (12, 12)):
        want = min(n, cap)
        got = F._frame_indexes(n, cap)
        if len(got) != want:
            return False, f"{n} 帧抽 {cap} 档得到 {len(got)} 个（期望 {want}）"
        if got != sorted(set(got)):
            return False, f"{n}->{cap} 有重复或乱序：{got}"
        if got[0] != 0:
            return False, f"{n}->{cap} 没从第 0 帧开始：{got}"
        if got[-1] != n - 1:
            return False, (f"{n}->{cap} 的尾帧是 {got[-1]}，应当是 {n - 1}"
                           f"（尾帧常是「回到起始姿势」那帧，丢了循环会缺一拍）")
        if len(got) > 2:
            gaps = [got[i + 1] - got[i] for i in range(len(got) - 1)]
            if max(gaps) > 2 * min(gaps):
                return False, f"{n}->{cap} 的间隔不均匀：{gaps}"
    # 上限为 1 的特例：首尾不可能兼得，取首帧，且不能除零
    if F._frame_indexes(8, 1) != [0]:
        return False, "只留一帧时应当取首帧"
    return True, "6 组帧数都是等间隔、含首尾、无重复；不超上限时原样返回"


def test_gif_decodes_all_frames():
    """GIF 解出多帧，静态图仍是单帧。"""
    folder = tempfile.mkdtemp(prefix="fx_gif_")
    try:
        gif = _mk_gif(os.path.join(folder, "a.gif"), n=6, dur=100)
        png = os.path.join(folder, "b.png")
        from PIL import Image
        Image.new("RGBA", (60, 60), (255, 0, 0, 255)).save(png)
        got_gif = F._load_click_frames(gif, 40, 12)
        got_png = F._load_click_frames(png, 40, 12)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if got_gif is None or got_png is None:
        return False, "解码失败"
    frames, _dur = got_gif
    if len(frames) != 6:
        return False, f"6 帧的 GIF 只解出 {len(frames)} 帧"
    if len(got_png[0]) != 1:
        return False, f"静态图解出 {len(got_png[0])} 帧"
    # 每帧都得是合法的预乘 RGBA
    for k, fr in enumerate(frames):
        if fr.dtype != np.float32 or fr.shape[2] != 4:
            return False, f"第 {k} 帧格式不对：{fr.dtype} {fr.shape}"
        if not bool((fr[..., :3] <= fr[..., 3:4] + 1e-6).all()):
            return False, f"第 {k} 帧不是预乘"
    return True, f"6 帧的 GIF -> 6 帧精灵；静态图 -> 1 帧；每帧都是预乘 float32 RGBA"


def test_gif_frame_duration_compensated():
    """**抽帧必须补偿帧时长**：整段动画的总时长要跟原 GIF 一致。

    漏掉这一步的表现是"大动图播起来快得看不清"，而小动图（没抽帧）完全正常
    —— 很容易被当成"那张图本身就快"。判据用总时长而不是单帧时长：单帧时长
    在没抽帧时也等于原值，测不出补偿有没有做。
    """
    folder = tempfile.mkdtemp(prefix="fx_gifdur_")
    try:
        big = _mk_gif(os.path.join(folder, "big.gif"), n=24, dur=100)   # 2.4s
        small = _mk_gif(os.path.join(folder, "small.gif"), n=3, dur=100)  # 0.3s
        fr_big, dur_big = F._load_click_frames(big, 40, 12)
        fr_small, dur_small = F._load_click_frames(small, 40, 12)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    total_big = len(fr_big) * dur_big
    total_small = len(fr_small) * dur_small
    if len(fr_big) != 12:
        return False, f"24 帧的应当抽成 12 帧，实得 {len(fr_big)}"
    if abs(total_big - 2.4) > 0.35:
        return False, (f"抽帧后总时长 {total_big:.2f}s（原 2.40s）—— "
                       f"每帧 {dur_big:.3f}s 没按抽帧比例放大")
    if abs(total_small - 0.3) > 0.06:
        return False, (f"没超上限的动图不该改时长：{total_small:.2f}s vs 0.30s")
    if not (0.15 < dur_big < 0.25):
        return False, f"抽一半之后每帧应当是 ~0.2s，实得 {dur_big:.3f}s"
    return True, (f"24 帧抽成 12 帧后每帧 {dur_big:.2f}s、总时长 {total_big:.2f}s"
                  f"（原 2.40s）；3 帧的 {total_small:.2f}s 未被改动")


def test_sprite_set_frames_cycle():
    """`ClickSpriteSet` 按时间取帧：动图循环、静态恒定、长度是"图数"。"""
    folder = tempfile.mkdtemp(prefix="fx_set_")
    try:
        gif = _mk_gif(os.path.join(folder, "a.gif"), n=4, dur=100)
        png = os.path.join(folder, "b.png")
        from PIL import Image
        Image.new("RGBA", (50, 50), (0, 0, 0, 255)).save(png)
        fr, dur = F._load_click_frames(gif, 40, 12)
        p1, d1 = F._load_click_frames(png, 40, 12)
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    ss = F.ClickSpriteSet([(fr, dur), (p1, d1)])
    if len(ss) != 2:
        return False, f"len 应当是图数 2，实得 {len(ss)}"
    if len(ss.frames) != len(fr) + 1:
        return False, f"帧总数 {len(ss.frames)} != {len(fr)} + 1"
    if ss.animated != 1:
        return False, f"动图计数应当是 1，实得 {ss.animated}"
    if not ss:
        return False, "非空集合判成假"
    if F.ClickSpriteSet([]):
        return False, "空集合判成真（回退逻辑会失效）"

    # 动图：一个周期内应当把每一帧都走到。
    # 判据用**颜色**而不是 `id()`：id 会被临时对象复用（实测同一时刻两次
    # 调用可能给出同一个地址），而"画面上换没换"本来就是颜色的事。
    got = {_center_color(ss.frame_at(0, k * dur)) for k in range(len(fr))}
    if len(got) != len(fr):
        return False, f"一个周期内只走到 {len(got)} 种画面（共 {len(fr)} 帧）"
    # 循环：走过一整圈之后回到第一帧
    if _center_color(ss.frame_at(0, 0.0)) != \
            _center_color(ss.frame_at(0, dur * len(fr))):
        return False, "动图没有循环回到第一帧"
    # 静态资源：任意时刻都是同一张
    if len({_center_color(ss.frame_at(1, t / 10.0)) for t in range(40)}) != 1:
        return False, "静态素材的帧在变"
    return True, (f"{len(ss)} 张图 / {len(ss.frames)} 帧；动图一个周期走到 "
                  f"{len(fr)} 帧后循环回起点，静态素材恒定")


def test_animated_sprite_moves_on_screen():
    """**核心**：动图上屏后画面要逐帧变 —— 这是"GIF 会动"唯一的判据。

    素材只放一个 GIF、粒子不动（扩散与重力都是 0），于是画面里唯一会变的就
    只剩动画帧本身。取正中那个像素按时间采样：只出现一种颜色就说明"没动"
    （`_anim_t` 没推进，或 `frame_at` 永远返回第 0 帧）。
    """
    folder = tempfile.mkdtemp(prefix="fx_animscreen_")
    try:
        _mk_gif(os.path.join(folder, "spin.gif"), n=8, px=80, dur=100)
        pool = F.CLICK_IMAGE_POOL
        pool.clear()
        pool.configure("folder", folder, 48)
        st = _wait_pool_at(folder, 48)
        if not st.startswith("已载入"):
            pool.clear()
            return False, f"素材没载入：{st!r}"
        cfg = _ecfg(click_emoji_source=F.CLICK_SOURCE_FOLDER,
                    click_emoji_custom_dir=folder, click_emoji_count=1.0,
                    click_emoji_size=48.0, click_emoji_spread=0.0,
                    click_emoji_gravity=0.0,                     click_emoji_life=10.0,
                    click_emoji_pick=F.CLICK_PICK_ONE)
        f = ClickEmojiField(W, H)
        f.set_pointer(W * 0.5, H * 0.5, 1)
        f.update(1.0 / 60.0, cfg)
        seen = []
        for _ in range(40):
            f.update(0.05, cfg)
            buf = np.zeros(W * H, np.uint32)
            f.draw(buf, W, H, cfg)
            shot = buf.reshape(H, W)[H // 2, W // 2]
            if int(shot >> np.uint32(24)) & 0xFF:
                seen.append(int(shot & 0xFFFFFF))
        moved = len(set(seen))
        pool.clear()
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if not seen:
        return False, "动图素材一个像素都没上屏"
    if moved < 2:
        return False, (f"40 次采样只出现 {moved} 种颜色 —— 画面上没动，"
                       f"GIF 被当成静图了")
    return True, (f"帧时长 0.1s / 采样间隔 0.05s：40 次采样出现 {moved} 种颜色"
                  f"（隔帧切换，判据成立）")


def test_animated_flag_in_state():
    """素材状态要报告动图张数，否则用户不知道 GIF 有没有被认出来。"""
    folder = tempfile.mkdtemp(prefix="fx_animstate_")
    try:
        _mk_gif(os.path.join(folder, "a.gif"), n=6, px=40, dur=100)
        from PIL import Image
        Image.new("RGBA", (40, 40), (255, 0, 0, 255)).save(
            os.path.join(folder, "b.png"))
        pool = F.CLICK_IMAGE_POOL
        pool.clear()
        pool.configure("folder", folder, 24)
        st = _wait_pool_at(folder, 24)
        n, files, _ = pool.stats()
        pool.clear()
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if "2 张动图" not in st:
        return False, f"状态里没报告动图数：{st!r}"
    if files != 2:
        return False, f"可用图片数 {files}（期望 2）"
    if n != 4:
        return False, f"素材条目 {n}（2 张图 × 2 档尺寸）"
    return True, f"{st}（2 张图 × 2 档；动图被单独点名）"


# ------------------------------------------------- 10. 三种来源与取图模式
def test_image_files_source():
    """「自选图片」：只选一张也成立，多张也行，删掉的文件自动过滤。"""
    folder = tempfile.mkdtemp(prefix="fx_files_")
    from PIL import Image
    try:
        ps = []
        for i, nm in enumerate(("a.png", "b.jpg", "c.bmp")):
            p = os.path.join(folder, nm)
            Image.new("RGB", (50 + i * 10, 40), (10 * i, 100, 200)).save(p)
            ps.append(p)
        raw_all = "\n".join(ps)
        one = F.list_image_files(ps[0])
        if one != [ps[0]]:
            return False, f"只选一张时解析成 {one}"
        got = F.list_image_files(raw_all)
        if got != ps:
            return False, f"三张图解析成 {[os.path.basename(x) for x in got]}"

        # 池子：只选一张 -> 1 图 × 2 档 = 2 个条目
        pool = F.CLICK_IMAGE_POOL
        pool.clear()
        pool.configure("files", ps[0], 40)
        st = _wait_pool_at(ps[0], 40, kind="files")
        if not st.startswith("已载入 2 张"):
            return False, f"只选一张时的状态不对：{st!r}"

        # 多张 -> 6 个条目
        pool.clear()
        pool.configure("files", raw_all, 40)
        st = _wait_pool_at(raw_all, 40, kind="files")
        if not st.startswith("已载入 6 张"):
            return False, f"三张图时的状态不对：{st!r}"

        # 删掉中间那张：池子下一批就该少一个，而不是整批作废。
        # 这里查的是**池子**而不是面板那份缓存列表 —— 缓存有两秒 TTL，
        # 拿它判"立刻生效"必然失败，而用户真正在意的是"画面上不再出现它"。
        os.remove(ps[1])
        if len(F.scan_image_files(raw_all)) != 2:
            return False, "删掉一个文件后没被过滤掉"
        pool.clear()
        pool.configure("files", raw_all, 40)
        st = _wait_pool_at(raw_all, 40, kind="files")
        if not st.startswith("已载入 4 张"):
            return False, f"删掉一张后应当剩 4 个条目：{st!r}"
        pool.clear()
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return True, ("单张 -> 2 个条目、三张 -> 6 个；删掉一个文件后自动过滤，"
                  "剩下的照常可用")


def test_pick_mode_one_vs_multi():
    """「整簇同图」一次点击只用一张；「随机多图」各颗粒子各自随机。"""
    folder = tempfile.mkdtemp(prefix="fx_pick_")
    from PIL import Image
    try:
        ps = []
        for i in range(5):
            p = os.path.join(folder, "i%d.png" % i)
            Image.new("RGBA", (40, 40), (20 * i, 90, 200, 255)).save(p)
            ps.append(p)
        raw = "\n".join(ps)
        base = _ecfg(click_emoji_source=F.CLICK_SOURCE_FILES,
                     click_emoji_files=raw, click_emoji_count=16.0,
                     click_emoji_spread=150.0)
        pool = F.CLICK_IMAGE_POOL
        pool.clear()
        pool.configure("files", raw, 22)
        st = _wait_pool_at(raw, 22, kind="files")
        if not st.startswith("已载入"):
            pool.clear()
            return False, f"素材没载入：{st!r}"

        f = ClickEmojiField(W, H)
        n_one, n_multi, kinds = set(), set(), set()
        for mode in (F.CLICK_PICK_ONE, F.CLICK_PICK_MULTI):
            for _ in range(20):
                f.reset()
                f.set_pointer(W * 0.5, H * 0.5, 1)
                f.update(1.0 / 60.0, dict(base, click_emoji_pick=mode))
                used = set(f.engine.spr_id[:f.engine.count].tolist())
                if mode == F.CLICK_PICK_ONE:
                    n_one.add(len(used))
                else:
                    n_multi.add(len(used))
                    kinds |= used
        pool.clear()
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if n_one != {1}:
        return False, f"整簇同图应当是每次恰好 1 张，实得 {sorted(n_one)}"
    if max(n_multi) < 2:
        return False, f"随机多图每次只用 {sorted(n_multi)} 张 —— 没随机起来"
    if len(kinds) < 2:
        return False, f"随机多图 20 次只用到了 {len(kinds)} 种图"
    return True, ("整簇同图 20 次都是 1 张；随机多图每簇 2 张以上、累计用到 "
                  f"{len(kinds)} 种")


def test_source_normalize_and_target():
    """来源归一化：旧值「自定义图片」等价于文件夹，未知值退回内置图案。"""
    cases = [(F.CLICK_SOURCE_FILES, F.CLICK_SOURCE_FILES),
             (F.CLICK_SOURCE_FOLDER, F.CLICK_SOURCE_FOLDER),
             (F.CLICK_SOURCE_LEGACY, F.CLICK_SOURCE_FOLDER),
             (F.CLICK_SOURCE_BUILTIN, F.CLICK_SOURCE_BUILTIN),
             ("", F.CLICK_SOURCE_BUILTIN),
             ("乱写一通", F.CLICK_SOURCE_BUILTIN),
             (None, F.CLICK_SOURCE_BUILTIN)]
    for src, want in cases:
        got = F.normalize_source(src)
        if got != want:
            return False, f"normalize_source({src!r}) = {got!r}，期望 {want!r}"
    if F.CLICK_SOURCE_LEGACY in F.CLICK_SOURCES:
        return False, "旧值不该出现在新的选项表里（会被用户选到）"
    tgt = [
        (dict(click_emoji_source=F.CLICK_SOURCE_BUILTIN), ("", "")),
        (dict(click_emoji_source=F.CLICK_SOURCE_FILES,
              click_emoji_files="a\nb"), ("files", "a\nb")),
        (dict(click_emoji_source=F.CLICK_SOURCE_FOLDER,
              click_emoji_custom_dir="C:/x"), ("folder", "C:/x")),
        (dict(click_emoji_source=F.CLICK_SOURCE_LEGACY,
              click_emoji_custom_dir="C:/x"), ("folder", "C:/x")),
        ({}, ("", "")),
    ]
    for cfg, want in tgt:
        got = F.click_source_target(cfg)
        if got != want:
            return False, f"click_source_target({cfg}) = {got}，期望 {want}"
    return True, "7 种取值归一化正确；旧配置「自定义图片」仍按文件夹处理"


def test_click_source_defaults():
    """新增的素材配置键：类型正确、默认值合理、坏值被钳制。"""
    for k, t in (("click_emoji_files", str), ("click_emoji_pick", str)):
        if k not in d.DEFAULTS:
            return False, f"DEFAULTS 缺 {k}"
        if not isinstance(d.DEFAULTS[k], t):
            return False, f"{k} 默认值类型是 {type(d.DEFAULTS[k])}，期望 {t}"
    if d.DEFAULTS["click_emoji_pick"] not in F.CLICK_PICKS:
        return False, f"默认取图模式 {d.DEFAULTS['click_emoji_pick']!r} 不在选项表里"
    if d.DEFAULTS["click_emoji_source"] not in F.CLICK_SOURCES:
        return False, f"默认来源 {d.DEFAULTS['click_emoji_source']!r} 不在选项表里"
    bad = []
    for k, v in (("click_emoji_files", 3), ("click_emoji_pick", 1.5),
                 ("click_emoji_pick", ["a"])):
        ok, _ = d._coerce_cfg_value(k, v)
        if ok:
            bad.append((k, v))
    if bad:
        return False, f"坏值被收下了：{bad}"
    return True, (f"两键类型正确；默认来源 {d.DEFAULTS['click_emoji_source']!r} "
                  f"与取图模式 {d.DEFAULTS['click_emoji_pick']!r} 都在选项表里；"
                  f"坏值被拒")


def test_no_shadowed_module_defs():
    """产品模块里不能有**同名重复的顶层定义**。

    后定义的那个会**静默**顶掉前面的（Python 不报错），症状全落在别处：本轮
    就中过一次 —— 改 `count_audio` 时只替换了它上面的常量块，旧那份（引用了
    已被删掉的缓存变量）留在文件末尾继续生效，调用抛 NameError，而调用方
    （面板刷文件夹状态的 try/except）把它吞了，表现只是"状态那一截不显示了"。
    读代码看不出来，手动点界面也未必踩得到（要先选中那个文件夹）。
    """
    import ast
    mods = ("fx_effects.py", "fx_image.py", "fx_click.py", "fx_scene.py",
            "fx_ui.py", "desktop_fx.py", "fx_kinds.py", "fx_winlayer.py",
            "fx_tray.py", "_paths.py")
    bad, checked = [], 0
    for fn in mods:
        path = os.path.join(HERE, fn)
        if not os.path.exists(path):
            continue
        checked += 1
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), fn)
        seen = {}
        for node in tree.body:          # 只看顶层（嵌套同名是合法的）
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef)):
                continue
            if node.name in seen:
                bad.append("%s 的 %s：第 %d 行顶掉第 %d 行"
                           % (fn, node.name, node.lineno, seen[node.name]))
            else:
                seen[node.name] = node.lineno
    if bad:
        return False, "存在被静默顶掉的重复定义 —— " + "；".join(bad[:3])
    return True, f"{checked} 个产品模块的顶层 def/class 无重名"


def test_img_align_rotation_direction():
    """「图片方向随扩散方向」：旋转角度必须**真的朝飞行方向**。

    判据用一张"顶部亮"的贴纸：飞向右边时亮带必须落在**右侧**。只断言"转过了"
    是不够的 —— 转错 90°（甚至倒过来转）同样能通过"形状变了"这种弱判据，
    而用户看到的会是"图片横着飞"。
    """
    if F._PImage is None:
        return None
    spr = np.zeros((20, 20, 4), np.float32)
    spr[0:4, :, :3] = 1.0
    spr[0:4, :, 3] = 1.0
    right = F._rotate_premul(spr, 3, 12)       # 飞行方向 = 向右（ang=0 -> 档 3）
    cols = right[:, :, 3].sum(axis=0)
    rows = right[:, :, 3].sum(axis=1)
    cx = float((np.arange(right.shape[1]) * cols).sum() / max(1e-6, cols.sum()))
    cy = float((np.arange(right.shape[0]) * rows).sum() / max(1e-6, rows.sum()))
    mid = (right.shape[1] - 1) / 2.0
    if cx < mid + 2:
        return False, (f"飞向右时亮带却停在 x={cx:.1f}（图宽 {right.shape[1]}，"
                       f"中点 {mid}）—— 旋转方向或角度不对")
    if abs(cy - (right.shape[0] - 1) / 2.0) > 3:
        return False, f"转过之后亮带没落在水平中线上（y={cy:.1f}）"
    if right.shape[2] != 4 or right.dtype != np.float32:
        return False, f"旋转后格式变了：{right.shape} {right.dtype}"
    if not bool((right[..., :3] <= right[..., 3:4] + 1e-5).all()):
        return False, "旋转后不再是预乘（边缘会渗出错色）"
    if float(right.max()) > 1.0 + 1e-5:
        return False, f"旋转后取值超过 1：{float(right.max())}"
    return True, (f"顶部亮带旋转后落在 x={cx:.1f}/{right.shape[1] - 1}"
                  f"（右侧），仍是预乘 float32 RGBA")


def test_img_align_wiring():
    """档位写进 `rot`：必须正好等于 `(飞行角 + 90°)` 的量化档。

    90° 的偏移不能省：精灵"朝上"在屏幕坐标里是 -90°，要让图头指向飞行方向
    ang，顺时针得转 ang + 90°。写成 ang 就会让所有图都歪 90°。
    """
    eng = F._BurstEngine(seed=5)
    for strength in (0.0,):
        pass
    eng.spawn(100.0, 100.0, "", [(255, 255, 255)], count=20, size=20.0,
              spread=200.0, gravity=0.0, rotate=False, life=1.0,
              spr_ids=np.zeros(20, np.int32), align=True)
    if eng.count != 20:
        return False, f"粒子数 {eng.count}"
    # `rot` 存的是**弧度**（角度 + 90°），两条上屏路径各自换算成档位 ——
    # 所以这里比的是弧度本身，不是档下标。
    want = (np.arctan2(eng.vy[:20], eng.vx[:20]) + np.pi * 0.5) % (2.0 * np.pi)
    got = eng.rot[:20]
    diff = int((np.abs(got - want) > 1e-4).sum())
    if diff:
        return False, f"{diff}/20 个粒子的旋转角与飞行方向不符"
    steps = [F._rot_step(x) for x in got]
    if len(set(steps)) < 8:
        return False, f"20 个粒子只落在 {len(set(steps))} 个方向档（对不上）"
    for i in range(20):
        exp = F._rot_step(want[i])
        if F._rot_step(got[i]) != exp:
            return False, f"第 {i} 颗粒子换算出的档位 {F._rot_step(got[i])} != {exp}"
    if bool((eng.spin[:20] != 0).any()):
        return False, "align 模式下 spin 必须为 0（否则档位会被逐帧搅乱）"
    # 默认（align=False）时 rot 仍是随机角：判据是"不是整数档"而不是"超过 12"
    # —— 随机角恒在 [0, 2π) 内，本来就 ≤ 12，用"上界"判等于什么都没验。
    eng2 = F._BurstEngine(seed=5)
    eng2.spawn(100.0, 100.0, "", [(255, 255, 255)], count=20, size=20.0,
               spread=200.0, gravity=0.0, rotate=False, life=1.0,
               spr_ids=np.zeros(20, np.int32))
    r2 = eng2.rot[:20]
    if not bool(((r2 >= 0) & (r2 < 2.0 * math.pi)).all()):
        return False, f"不勾选时 rot 不是随机角：{r2.min()}~{r2.max()}"
    # 随机角与"角度 + 90°"那族角几乎不可能逐个吻合（20 个全中 = 方差为 0）
    if bool(np.allclose(r2, want, atol=1e-3)):
        return False, "不勾选时 rot 也被写成了方向角（应当仍是随机角）"
    return True, ("20 个粒子的 rot = (飞行角 + 90°) 的弧度，换算成 12 档后与"
                  "方向一一对应；不勾选时仍是随机角")


def test_img_align_builtin_unaffected():
    """内置图案不吃图片那条旋转路径（它自带 12 档旋转，两者会打架）。"""
    from _verify_kinds import _base_cfg
    cfg = _base_cfg(click_emoji_enabled=True, click_emoji_source="内置图案")
    f = ClickEmojiField(W, H)
    # 强制留下"随扩散方向"的脏状态（模拟上一簇用过图片 + 对齐）
    f.engine._img_align = True
    f._spawn_builtin(cfg)
    if f.engine._img_align:
        return False, "回退内置图案后 _img_align 仍是 True —— 内置图案会去走位图旋转"
    return True, "回退到内置图案时显式关掉图片旋转路径"


def test_ui_align_list_matches_impl():
    """fx_ui 的「图片方向」选项表必须与 fx_effects 的常量一一对应。"""
    import fx_ui
    if list(fx_ui.IMG_ALIGNS) != list(F.CLICK_ALIGNS):
        return False, (f"选项表不一致：UI={fx_ui.IMG_ALIGNS} / "
                       f"实现={F.CLICK_ALIGNS}")
    for v in fx_ui.IMG_ALIGNS:
        if not isinstance(v, str) or not v:
            return False, f"选项值非法：{v!r}"
    if d.DEFAULTS.get("click_emoji_align") != F.CLICK_ALIGN_VERTICAL:
        return False, f"默认值应是「竖直」：{d.DEFAULTS.get('click_emoji_align')!r}"
    return True, f"{len(fx_ui.IMG_ALIGNS)} 个取向选项与实现一致，默认竖直"


# ------------------------------------------------- 12. 点击音效（独立分类）
def test_builtin_sfx_available():
    """内置音效目录必须真的有音频，且能被扫到、名字能被清洗。"""
    items = F.list_builtin_sfx()
    if len(items) < 3:
        return False, f"内置音效只有 {len(items)} 个（assets/sfx 是不是没带上？）"
    bad = [p for p in items if not os.path.isfile(p)]
    if bad:
        return False, f"扫出来的路径不存在：{bad[:2]}"
    names = F.builtin_sfx_names()
    if len(names) != len(items) or any(not n for n in names):
        return False, f"显示名异常：{names}"
    if not any("青轴" in n for n in names):
        return False, f"没扫到「青轴」：{names}"
    return True, f"{len(items)} 个内置音效：{' / '.join(names)}"


def test_sfx_source_normalize_and_target():
    """三个来源 + 旧值归一化 + `sfx_target` 的选择逻辑。"""
    if F.normalize_sfx_source("") != F.SFX_SOURCE_BUILTIN:
        return False, "空值应当归到内置音效"
    if F.normalize_sfx_source("内置") != F.SFX_SOURCE_BUILTIN:
        return False, "旧值「内置」没认出来"
    if F.normalize_sfx_source("自定义音效") != F.SFX_SOURCE_FOLDER:
        return False, "旧值「自定义音效」没归到文件夹"
    if F.normalize_sfx_source("乱七八糟") != F.SFX_SOURCE_BUILTIN:
        return False, "未知取值应当兜底到内置音效"

    # 内置：不指定就是全部；指定了那个文件就只要它
    items, rnd = F.sfx_target({"click_sfx_source": F.SFX_SOURCE_BUILTIN})
    if not items or rnd is not True:
        return False, f"内置（未指定）应给全部并随机：{len(items)}/{rnd}"
    one = os.path.basename(F.list_builtin_sfx()[0])
    items2, rnd2 = F.sfx_target({"click_sfx_source": F.SFX_SOURCE_BUILTIN,
                                 "click_sfx_builtin": one})
    if len(items2) != 1 or rnd2 is not False:
        return False, f"指定了内置音效应收敛到 1 个且不随机：{items2}/{rnd2}"
    # 指定的名字不存在 -> 回退全部（不能变成"一声不响"）
    items3, _ = F.sfx_target({"click_sfx_source": F.SFX_SOURCE_BUILTIN,
                              "click_sfx_builtin": "不存在.wav"})
    if len(items3) != len(items):
        return False, "指定的内置音效不见了时应当回退到全部"

    # 单个文件：存在才算，不存在给空（调用方据此什么都不播）
    folder = _mk_audio_folder()
    p0 = os.path.join(folder, sorted(os.listdir(folder))[0])
    items4, rnd4 = F.sfx_target({"click_sfx_source": F.SFX_SOURCE_FILE,
                                 "click_sfx_file": p0})
    if items4 != [p0] or rnd4 is not False:
        return False, f"单个文件来源不对：{items4}/{rnd4}"
    items5, _ = F.sfx_target({"click_sfx_source": F.SFX_SOURCE_FILE,
                              "click_sfx_file": os.path.join(folder, "无.wav")})
    if items5:
        return False, "文件不存在时应当返回空表"
    # 文件夹：扫出全部音频
    F.invalidate_media_cache()
    items6, rnd6 = F.sfx_target({"click_sfx_source": F.SFX_SOURCE_FOLDER,
                                 "click_sfx_dir": folder})
    n_expect = F.count_audio(folder)
    shutil.rmtree(folder, ignore_errors=True)
    if len(items6) != n_expect or n_expect < 2 or rnd6 is not True:
        return False, f"文件夹来源：{len(items6)}（期望 {n_expect}）/{rnd6}"
    return True, ("三个来源都选对；未知值兜底内置；指定的内置音效缺失时回退全部；"
                  "文件不存在返回空表")


def test_click_sfx_triggers_and_overlaps():
    """`ClickSfx` 只在点到时触发，且**明确要求 overlap=True**（允许叠着播）。

    判据是"传给播放器的那个标志"，不是"播放器忙的时候丢弃了" —— 那是语音
    的规则。音效这边改成允许重叠之后，这里要守的是"别哪天又退回串行"：
    连点时串行听起来像卡顿，不是用户要的手感。
    """
    played = []

    class _Player:
        """模拟真实播放器：有条数有限的设备池，占满了就返回 False。"""

        def __init__(self):
            self.overlaps = []
            self.cap = 4          # 假装设备条数有限

        def play_path(self, p, overlap=False, volume=None):
            self.overlaps.append(overlap)
            if self.cap <= 0:
                return False
            played.append(p)
            self.cap -= 1
            return True

    pl = _Player()
    sfx = F.ClickSfx(pl)
    cfg = dict(click_sfx_enabled=True, click_sfx_source=F.SFX_SOURCE_BUILTIN,
               click_sfx_builtin="")
    if sfx.active() is not False:
        return False, "active() 必须恒 False（它不上屏，否则鼠标层每帧白提交）"
    # 连点 5 次：前 4 次占满设备，第 5 次该被丢弃 —— 但**每一次**都得传
    # overlap=True，否则真实播放器会按"忙则丢弃"处理，重叠根本没发生。
    cap = pl.cap
    got = [sfx.trigger(cfg) for _ in range(cap + 1)]
    if not all(pl.overlaps):
        return False, f"有触发没带 overlap=True：{pl.overlaps}"
    if got != [True] * cap + [False]:
        return False, f"设备占满前后的返回值不对：{got}"
    if len(played) != cap:
        return False, f"实际播了 {len(played)} 次（设备 {cap} 条）"
    if not os.path.isfile(played[0]) or not played[0].endswith(
            (".wav", ".mp3", ".wma", ".m4a")):
        return False, f"播的不是音频文件：{played[0]!r}"
    # 换成"文件不存在"的来源：不该播，也不该抛
    if sfx.trigger(dict(cfg, click_sfx_source=F.SFX_SOURCE_FILE,
                        click_sfx_file="")):
        return False, "来源没配到东西时不该播"
    return True, ("每次触发都带 overlap=True（可重叠）；设备占满后丢弃不排队；"
                  "来源没配到时静默跳过，不抛异常")


def test_sfx_voices_use_separate_aliases():
    """音效的每一条并发播放必须**各占一条 MCI 别名**。

    这是"允许重叠"能不能成立的技术前提：共用一条别名时，第二个 `open` 会把
    第一条正在播的顶掉 —— 听到的是一串被截断的"咔"，而不是叠起来的连击。
    判据直接查发出去的 open 命令里出现了几个不同别名。
    """
    cmds = []
    playing = [True]

    def fake(cmd):
        cmds.append(cmd)
        if cmd.startswith("status"):
            return 0, "playing" if playing[0] else "stopped"
        return 0, ""

    orig = fx_click._mci_send
    fx_click._mci_send = fake
    try:
        vp = F.VoicePlayer()
        n = min(4, F._SFX_VOICES)
        for _ in range(n):
            vp.play_path("C:/x/a.wav", overlap=True)
        # 语音那条独占设备**不能**被音效占用
        vp.play_path("C:/x/a.wav")
    finally:
        fx_click._mci_send = orig

    aliases = [c.rsplit("alias ", 1)[1] for c in cmds if c.startswith("open ")]
    uniq = set(aliases)
    if len(aliases) != n + 1:
        return False, f"期望 {n + 1} 次 open，实际 {len(aliases)} 次"
    if len(uniq) != n + 1:
        return False, (f"{n + 1} 次并发只用了 {len(uniq)} 条别名 —— "
                       f"共用别名会把前一条顶掉：{sorted(uniq)}")
    if F._VOICE_ALIAS not in uniq:
        return False, "语音那条独占别名没被用到"
    for a in uniq:
        if a.startswith(F._VOICE_ALIAS[:9]) and a != F._VOICE_ALIAS:
            return False, f"音效用错了语音的别名空间：{a}"
    return True, (f"{n} 条并发音效各占一条别名（{sorted(uniq)[:2]}…），"
                  f"语音仍是独占的 {F._VOICE_ALIAS}")


def test_sfx_voice_pool_is_bounded_and_released():
    """别名池有上限，且**放完就还回来** —— 否则狂点几次之后音效永久哑掉。"""
    playing = [True]

    def fake(cmd):
        if cmd.startswith("status"):
            return 0, "playing" if playing[0] else "stopped"
        return 0, ""

    orig = fx_click._mci_send
    fx_click._mci_send = fake
    try:
        vp = F.VoicePlayer()
        # 超过池容量的连点：前 N 条起播，之后必须开始返回 False（不是无限开设备）
        over = F._SFX_VOICES + 4
        got = [vp.play_path("C:/x/a.wav", overlap=True) for _ in range(over)]
        if got != [True] * F._SFX_VOICES + [False] * (over - F._SFX_VOICES):
            return False, f"池没封顶：{got}"
        if vp.voices_busy() != F._SFX_VOICES:
            return False, (f"占用数 {vp.voices_busy()} != "
                           f"{F._SFX_VOICES}")
        # 全播完之后必须全部还回来，且还能再叠
        playing[0] = False
        t0 = time.time()
        while time.time() - t0 < 8.0 and vp.voices_busy():
            time.sleep(0.05)
        if vp.voices_busy():
            return False, f"播完之后仍占着 {vp.voices_busy()} 条（永远哑掉）"
        if not vp.play_path("C:/x/a.wav", overlap=True):
            return False, "释放之后仍然起不了播（池没还回来）"
    finally:
        fx_click._mci_send = orig
    return True, (f"池封顶在 {F._SFX_VOICES} 条（超出丢弃不排队）；"
                  "播完全部归还，可再次起播")


def test_scene_sfx_mouse_and_keyboard():
    """场景接线：鼠标点击触发；键盘**默认不触发**，勾了才触发。"""
    from _verify_kinds import _base_cfg
    from fx_scene import Scene

    calls = []

    class _Spy:
        def play_path(self, p, overlap=False, volume=None):
            calls.append(p)
            return True

        def play_random(self, f):
            return False

    sc = Scene(W, H)
    sc.voice = _Spy()
    sc.click_sfx.player = sc.voice
    cfg_extra = {}
    # 造两个假音效文件放进临时目录，走「音效文件夹」来源
    folder = tempfile.mkdtemp(prefix="fx_sfx_")
    for nm in ("a.wav", "b.wav"):
        with open(os.path.join(folder, nm), "wb") as fh:
            fh.write(b"\x00" * 16)
    F.invalidate_media_cache()

    state = {"mouse": 0, "key": 0}

    def fake_update(dt, ox, oy, keys=False):
        sc.mouse.mx, sc.mouse.my = W * 0.5, H * 0.5
        sc.mouse.clicked = state["mouse"]
        sc.mouse.key_clicked = state["key"] if keys else 0
        return 0.0

    sc.mouse.update = fake_update
    buf = np.zeros(W * H, np.uint32)
    base = dict(click_sfx_enabled=True, click_sfx_source=F.SFX_SOURCE_FOLDER,
                click_sfx_dir=folder, click_sfx_keyboard=False)
    sc.cfg = _base_cfg(**base)
    state.update(mouse=1, key=0)
    sc.step(1.0 / 60.0, buf, W, H, buf)
    n_mouse = len(calls)
    state.update(mouse=0, key=1)
    for _ in range(3):
        sc.step(1.0 / 60.0, buf, W, H, buf)
    n_after_key_off = len(calls)
    # 勾上键盘：这时才该响应
    sc.cfg = _base_cfg(**dict(base, click_sfx_keyboard=True))
    state.update(mouse=0, key=1)
    sc.step(1.0 / 60.0, buf, W, H, buf)
    n_key_on = len(calls)
    # 关掉总开关：鼠标点击也不该响
    sc.cfg = _base_cfg(**dict(base, click_sfx_enabled=False,
                              click_sfx_keyboard=True))
    state.update(mouse=1, key=1)
    for _ in range(3):
        sc.step(1.0 / 60.0, buf, W, H, buf)
    n_off = len(calls)
    shutil.rmtree(folder, ignore_errors=True)
    if n_mouse != 1:
        return False, f"鼠标点击触发了 {n_mouse} 次（期望 1）"
    if n_after_key_off != n_mouse:
        return False, "键盘开关关着时仍然因按键发声"
    if n_key_on != n_after_key_off + 1:
        return False, f"勾上键盘后按了键却没发声（{n_after_key_off} -> {n_key_on}）"
    if n_off != n_key_on:
        return False, "总开关关掉后仍在发声"
    last = calls[-1]
    if not last.startswith(folder):
        return False, f"播的不是那个文件夹里的音频：{last!r}"
    return True, ("鼠标点击触发一次；键盘默认不触发、勾上后按键触发；"
                  "总开关关掉后都不触发")


def test_sfx_independent_from_click_effects():
    """「点击音效」是独立分类：与两个点击特效**互不挤占**，可同时开着。"""
    app = d.App.__new__(d.App)
    app.cfg = dict(d.DEFAULTS)
    app.snapshot = dict(app.cfg)
    app.user_presets = {}
    app._autosave_dirty = False
    app.set("click_emoji_enabled", True)
    app.set("click_sfx_enabled", True)
    if not app.cfg["click_emoji_enabled"]:
        return False, "开启点击音效把点击表情挤掉了（分类没独立）"
    if not app.cfg["click_sfx_enabled"]:
        return False, "点击音效没打开"
    # 同类的点击文字开起来，只该挤掉点击表情 —— 音效必须活下来
    app.set("click_text_enabled", True)
    if app.cfg["click_emoji_enabled"]:
        return False, "点击文字与点击表情之间应当仍然互斥"
    if not app.cfg["click_text_enabled"]:
        return False, "点击文字没打开"
    if not app.cfg["click_sfx_enabled"]:
        return False, "点击文字开启时把独立分类的点击音效也挤掉了"
    if fx_kinds.category_of("点击音效") == fx_kinds.category_of("点击表情"):
        return False, "点击音效与点击表情仍在同一个分类里"
    if fx_kinds.effects_of("鼠标点击音效") != ["点击音效"]:
        return False, ("「鼠标点击音效」分类内容不对："
                       f"{fx_kinds.effects_of('鼠标点击音效')}")
    return True, "点击音效单独成类：可与点击文字同时开着，点击组内仍互斥"


def test_sfx_never_reported_as_layer_content():
    """开着点击音效不能让鼠标层被误判成"有内容"（否则每帧白提交一次全屏）。"""
    from _verify_kinds import _base_cfg
    from fx_scene import Scene
    s = Scene(W, H)
    s.cfg = _base_cfg(click_sfx_enabled=True)
    fs, ms = s.layer_activity()
    if ms:
        return False, "只开着点击音效，鼠标层却被报成有内容（白付一次全屏提交）"
    if fs:
        return False, "只开着点击音效，全屏层却被报成有内容"
    return True, "纯听觉特效不参与层活动信号"


def test_click_sfx_defaults():
    """点击音效的几个配置键：类型与默认值。"""
    want = {"click_sfx_enabled": False,
            "click_sfx_source": F.SFX_SOURCE_BUILTIN,
            "click_sfx_builtin": "",
            "click_sfx_file": "",
            "click_sfx_dir": "",
            "click_sfx_keyboard": False,
            "click_sfx_volume": 1.0}
    for k, v in want.items():
        if k not in d.DEFAULTS:
            return False, f"缺配置键 {k}"
        if d.DEFAULTS[k] != v:
            return False, f"{k} 默认值应为 {v!r}，实为 {d.DEFAULTS[k]!r}"
    if "点击音效" not in d.PRESET_GROUPS:
        return False, "「点击音效」页没有内置预设"
    for nm in d.PRESET_GROUPS["点击音效"]:
        if nm not in d.PRESETS:
            return False, f"预设 {nm} 没有实体"
    # 预设引用的内置音效文件必须真的在
    for nm in d.PRESET_GROUPS["点击音效"]:
        b = d.PRESETS[nm].get("click_sfx_builtin", "")
        if b and not any(os.path.basename(p) == b for p in F.list_builtin_sfx()):
            return False, f"预设「{nm}」引用的内置音效 {b} 不存在"
    return True, (f"{len(want)} 个键默认值正确；"
                  f"{len(d.PRESET_GROUPS['点击音效'])} 个预设引用的内置音效都在")


def test_sfx_volume_command_per_device_type():
    """MCI 路径的音量命令只发给 mpegvideo；waveaudio 一律不发。

    演进（这条测试被实测推翻过一次，别再退回去）：
    * 第一版：统一发 0~1000 -> wav 被压成约 1.5% 音量（"音量只对旅店铃声生效"）。
    * 第二版：给 waveaudio 发 0~65535 双声道打包 -> **实测 winmm 对 waveaudio
      根本不认 `setaudio volume`（错误 261 "Unrecognized command"）**，白发。
    * 现在：wav 在 play_path 上游就分流到 waveOut 软件混音池，`_worker` 只
      处理 mpegvideo（mp3/wma，0~1000 合法区间）与 wav 兜底；兜底那条没有
      音量可设。所以这里断言：mp3 发 500，wav **一个 setaudio 都不发**。
    """
    cmds = []
    def fake(cmd):
        cmds.append(cmd)
        if cmd.startswith("status"):
            return 0, "stopped"   # 一查就停，线程立刻收尾
        return 0, ""
    orig = fx_click._mci_send
    fx_click._mci_send = fake
    try:
        vp = F.VoicePlayer()
        # 直接同步调 _worker（避开线程竞态）；用 _VOICE_ALIAS 不会污染别名池。
        vp._worker("C:/x/a.wav", F._VOICE_ALIAS, 0.5)
        vp._worker("C:/x/b.mp3", F._VOICE_ALIAS, 0.5)
    finally:
        fx_click._mci_send = orig

    def vol_of(path):
        saw_open = False
        for c in cmds:
            if c.startswith("open"):
                saw_open = (path in c)     # 换文件就重置，别把别人的算过来
            elif saw_open and c.startswith("setaudio") and "volume" in c:
                return int(c.rsplit("to ", 1)[1])
        return None

    wav_v = vol_of("a.wav")
    mp3_v = vol_of("b.mp3")
    if wav_v is not None:
        return False, (f"waveaudio 居然收到 setaudio（winmm 实测不认，错误 261）："
                       f"音量={wav_v}")
    if mp3_v != 500:
        return False, f"mp3 音量应为 500（0.5×1000），实际 {mp3_v}"
    return True, ("mpegvideo 走 MCI setaudio volume to 500（0~1000）；waveaudio "
                  "一个 setaudio 都不发（wav 音量走 waveOut 软件混音池）")


def test_text_pack_defaults():
    """语音目录已并入 `click_sfx_dir`；这里守住类型、钳制与默认值。"""
    for k, t in (("click_sfx_dir", str), ("click_text_voice_match", bool)):
        if k not in d.DEFAULTS:
            return False, f"DEFAULTS 缺 {k}"
        if not isinstance(d.DEFAULTS[k], t):
            return False, f"{k} 默认值类型是 {type(d.DEFAULTS[k])}，期望 {t}"
    if d.DEFAULTS["click_text_voice_match"] is not False:
        return False, "「语音与文字统一」默认应当是关闭的"
    if d.DEFAULTS["click_sfx_dir"] != "":
        return False, "音效目录默认应当是空"
    bad = []
    for k, v in (("click_sfx_dir", 7), ("click_sfx_dir", ["a"]),
                 ("click_text_voice_match", "也许")):
        ok, _ = d._coerce_cfg_value(k, v)
        if ok:
            bad.append((k, v))
    if bad:
        return False, f"坏值被收下了：{bad}"
    for v, exp in (("是", True), ("关", False)):
        ok, got = d._coerce_cfg_value("click_text_voice_match", v)
        if not ok or got is not exp:
            return False, f"统一开关 {v!r} 应转成 {exp}，实得 {got!r}"
    return True, "两键类型正确、默认关闭、坏值被拒、中文开关能按意图转换"


def test_scene_plays_the_matched_audio():
    """场景接线：统一模式播**指定的那一条**；没有指定时退回随机语音源。

    这条守的是"字与声音对不上"最容易复发的位置 —— 特效内部算对了，场景那
    一层却仍去 `play_random` 另挑一条，两边就又各自随机了。
    """
    from _verify_kinds import _base_cfg
    from fx_scene import Scene

    folder = _mk_pack()
    calls = []

    class _Spy:
        def play_path(self, p, overlap=False, volume=None):
            calls.append(("path", p))
            return True

        def play_random(self, f):
            calls.append(("random", f))
            return True

    def _drive(sc, frames=4, first_click=True):
        box = {"fired": not first_click}

        def fake_update(dt, ox, oy, keys=False):
            sc.mouse.mx, sc.mouse.my = W * 0.5, H * 0.5
            sc.mouse.clicked = 0 if box["fired"] else 1
            return 0.0

        sc.mouse.update = fake_update
        buf = np.zeros(W * H, np.uint32)
        for k in range(frames):
            # 只在**第一帧**给一次点击（第二帧起置位）—— 写在循环体最前面
            # 会把第一帧也置成"已点过"，于是整个驱动一次点击都没有。
            box["fired"] = (k >= 1)
            sc.step(1.0 / 60.0, buf, W, H, buf)

    # 1) 统一模式：必须走 play_path，且参数就是那行字对应的那条音频
    sc = Scene(W, H)
    sc.voice = _Spy()
    sc.click_sfx.player = sc.voice
    sc.cfg = _base_cfg(click_text_enabled=True, click_text_voice_match=True,
                       click_text_deco="无", click_text_list="自定义甲",
                       **_sfx_pack(folder))
    _drive(sc)
    if len(calls) != 1 or calls[0][0] != "path":
        shutil.rmtree(folder, ignore_errors=True)
        return False, f"统一模式没走 play_path，而是 {calls}"
    path = calls[0][1]
    pop = sc.click_text.pops[-1] if sc.click_text.pops else None
    if not pop or pop["text"] != F.audio_label(path):
        shutil.rmtree(folder, ignore_errors=True)
        return False, (f"场景播的 {F.audio_label(path)!r} 与弹出的 "
                       f"{pop and pop['text']!r} 不一致")

    # 2) 关闭统一、开着「点击音效」-> 走那一档的随机播放
    calls.clear()
    sc2 = Scene(W, H)
    sc2.voice = _Spy()
    sc2.click_sfx.player = sc2.voice
    sc2.cfg = _base_cfg(click_text_enabled=True, click_text_voice_match=False,
                        click_text_list="自定义甲", click_text_deco="无",
                        click_sfx_enabled=True, **_sfx_pack(folder))
    _drive(sc2)
    if len(calls) != 1 or calls[0][0] != "path" \
            or not calls[0][1].startswith(folder):
        shutil.rmtree(folder, ignore_errors=True)
        return False, f"未指定音频时没走「点击音效」那一档：{calls}"

    # 3) 文字特效关着时队列必须是空的（否则下次开起来会播旧音频）
    calls.clear()
    sc3 = Scene(W, H)
    sc3.voice = _Spy()
    sc3.click_sfx.player = sc3.voice
    sc3.cfg = _base_cfg(click_text_enabled=False, click_text_voice_match=True,
                        click_sfx_enabled=False, **_sfx_pack(folder))
    _drive(sc3)
    shutil.rmtree(folder, ignore_errors=True)
    if calls:
        return False, f"文字特效关着却播了音频：{calls}"
    return True, ("统一模式播的正是那行字对应的音频；未指定时退回随机源；"
                  "特效关着时不播")


def test_scene_volume_covers_voice_and_sfx():
    """音量必须覆盖**两条**点击发声路径：语音与文字统一 + 点击音效。

    之前只有「点击音效」传了 volume，统一模式挑中的语音 `play_path(path)` 没传，
    表现就是"开着统一时点击声不听音量"—— 即"还有部分音效不受控"。这条把两条
    路径都钉死：都得带上 cfg 里的 click_sfx_volume。
    """
    import queue as _q  # noqa: F401  仅为与驱动同款、本测试不直接用
    from _verify_kinds import _base_cfg
    from fx_scene import Scene

    folder = _mk_pack()
    seen = []

    class _Spy:
        def play_path(self, p, overlap=False, volume=None):
            seen.append(volume)
            return True

        def play_random(self, f):
            return True

    def _drive(sc, frames=4):
        box = {"fired": False}

        def fake_update(dt, ox, oy, keys=False):
            sc.mouse.mx, sc.mouse.my = W * 0.5, H * 0.5
            sc.mouse.clicked = 0 if box["fired"] else 1
            return 0.0

        sc.mouse.update = fake_update
        buf = np.zeros(W * H, np.uint32)
        for k in range(frames):
            box["fired"] = (k >= 1)      # 只第一帧给一次点击
            sc.step(1.0 / 60.0, buf, W, H, buf)

    VOL = 0.4
    try:
        # 1) 统一模式：语音路径也要带音量
        sc = Scene(W, H)
        sc.voice = _Spy()
        sc.click_sfx.player = sc.voice
        sc.cfg = _base_cfg(click_text_enabled=True, click_text_voice_match=True,
                           click_text_deco="无", click_text_list="自定义甲",
                           click_sfx_volume=VOL, **_sfx_pack(folder))
        _drive(sc)
        if not seen or seen[-1] != VOL:
            return False, f"统一模式的语音没带音量：{seen}，期望 {VOL}"

        # 2) 关统一、开「点击音效」：那一档也要带音量
        seen.clear()
        sc2 = Scene(W, H)
        sc2.voice = _Spy()
        sc2.click_sfx.player = sc2.voice
        sc2.cfg = _base_cfg(click_text_enabled=True, click_text_voice_match=False,
                            click_text_list="自定义甲", click_text_deco="无",
                            click_sfx_enabled=True, click_sfx_volume=VOL,
                            **_sfx_pack(folder))
        _drive(sc2)
        if not seen or seen[-1] != VOL:
            return False, f"「点击音效」没带音量：{seen}，期望 {VOL}"
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return True, f"统一模式语音与「点击音效」两条路径都带上 click_sfx_volume={VOL}"


def test_preview_audio_routes_to_player_with_volume():
    """试听接线：面板 preview_audio 入队，渲染线程 _drain_audio_previews 用**同一个
    播放器**按同音量播出（所见即所闻）。守住"选内置音效 / 拖音量 → 立即听到"这条链，
    杜绝又回到"选了没声音、要等下次真实点击才生效"。
    """
    import queue as _q
    app = d.App.__new__(d.App)          # 不跑 __init__：避开配置/线程副作用
    app.audio_cmds = _q.Queue()
    seen = []

    class _VP:
        def play_path(self, p, overlap=False, volume=None):
            seen.append((p, overlap, volume))
            return True

    class _Scene:
        voice = _VP()

    app.preview_audio("C:/x/a.wav", 0.4)
    if app.audio_cmds.qsize() != 1:
        return False, "preview_audio 没入队"
    app._drain_audio_previews(_Scene())
    if seen != [("C:/x/a.wav", True, 0.4)]:
        return False, f"试听没按 (路径, overlap=True, 音量) 播出：{seen}"
    if app.audio_cmds.qsize() != 0:
        return False, "试听队列没排空（下次会补播旧的）"
    # 空路径不入队（面板在来源没配到时不该误播）
    app.preview_audio("", 0.4)
    if app.audio_cmds.qsize() != 0:
        return False, "空路径也入了队"
    return True, "试听经同一播放器、overlap=True、带音量播出；空路径忽略、队列排空"


# ------------------------------------------------ wav 软件音量（waveOut 池）
def test_wav_pcm16_decode_and_volume():
    """wav 解码 + 软件混音：16-bit PCM 读出、44.1k 立体声、音量语义正确。

    这条守两件事：
    1. `wav_pcm16_mono_44k` 对**真实内置 wav**解得开（内置音效全是 16-bit
       PCM，读不出来就会整条退回 MCI 无音量播放）；
    2. `mix_pcm16_stereo` 的音量语义：1.0 原样、0.5 幅度减半、0 全静音、
       超 1 不溢出（clip）。
    """
    wavs = [p for p in F.list_builtin_sfx()
            if os.path.splitext(p)[1].lower() == ".wav"]
    if not wavs:
        return False, "内置 wav 一个都没有"
    a = fx_click.wav_pcm16_mono_44k(wavs[0])
    if a is None:
        return False, f"{os.path.basename(wavs[0])} 解不出 PCM"
    if a.dtype != np.float32 or len(a) <= 0:
        return False, f"解码结果异常：dtype={a.dtype} len={len(a)}"
    # 解码出的样本必须落在 int16 量程内（读的是 16-bit 源）
    if len(a) and (a.max() > 32767.5 or a.min() < -32768.5):
        return False, "解码样本超出 int16 量程"
    full = np.frombuffer(fx_click.mix_pcm16_stereo(a, 1.0), dtype="<i2")
    half = np.frombuffer(fx_click.mix_pcm16_stereo(a, 0.5), dtype="<i2")
    zero = np.frombuffer(fx_click.mix_pcm16_stereo(a, 0.0), dtype="<i2")
    if len(full) != len(a) * 2:
        return False, "立体声展开后长度不对（应为 mono × 2）"
    if len(zero) and zero.any():
        return False, "音量 0 仍有非零样本（静音失效）"
    ref = np.clip(a, -32768.0, 32767.0).astype("<i2")
    if not np.array_equal(full[0::2], ref):
        return False, "音量 1.0 不是原样（左声道对不上）"
    # 0.5 幅度减半：逐样本比对（clip 前的源没有贴着边界的样本）
    mid = np.abs(a) < 32000.0
    if mid.any() and not np.allclose(half[0::2][mid], ref[mid] * 0.5, atol=1.0):
        return False, "音量 0.5 不是线性减半"
    over = np.frombuffer(fx_click.mix_pcm16_stereo(a, 2.5), dtype="<i2")
    if len(over) and (over.max() > 32767 or over.min() < -32768):
        return False, "音量超 1 没被 clip（溢出）"
    # 读不出 PCM 的必须返回 None（交给上层退 MCI，不能抛）
    if fx_click.wav_pcm16_mono_44k(os.path.join(HERE, "desktop_fx.py")) is not None:
        return False, "非 wav 文件没有返回 None"
    return True, (f"{len(wavs)} 个内置 wav 解得出 PCM；音量 1.0 原样 / 0.5 减半 / "
                  f"0 静音 / 超 1 clip；非 wav 返回 None")


def test_wav_volume_never_via_mci_setaudio():
    """wav 的音量**绝不走 MCI setaudio**；mp3 仍走 MCI 的 0~1000。

    根因（2026-10-16 实测）：MCI 的 waveaudio 设备对 `setaudio volume`
    直接返回错误 261（不认这条命令），发 0~1000 / 0~65535 / 双声道打包值
    统统被拒 —— 这就是"音量只对旅店铃声（mp3）生效、对 wav 无效"的真凶。
    现在 wav 改走 waveOut 软件混音池，所以：
    * 有音频设备 -> wav 一条 MCI 命令都不该发；
    * 无设备 / 解不出 PCM -> 退 MCI 兜底，但**记录里不允许出现 setaudio**
      （旧代码会给 waveaudio 发打包音量，纯属白发）。
    mp3 对照组：仍走 MCI，且 setaudio 值 = int(v*1000)。
    """
    orig = fx_click._mci_send
    cmds = []
    try:
        fx_click._mci_send = lambda c: (cmds.append(c), (0, ""))[1]
        vp = fx_click.VoicePlayer()
        wavs = [p for p in F.list_builtin_sfx()
                if os.path.splitext(p)[1].lower() == ".wav"]
        if wavs:
            cmds.clear()
            ok = vp.play_path(wavs[0], overlap=True, volume=0.5)
            if not ok:
                return False, "wav 起播失败（池没起、MCI 兜底也没成）"
            if any("setaudio" in c for c in cmds):
                return False, f"wav 居然发了 setaudio：{cmds}"
            if not fx_click._WAV_POOL.playing() and not any(
                    "open" in c for c in cmds):
                return False, "wav 既没进池也没退 MCI（链路断了）"
        mp3s = [p for p in F.list_builtin_sfx()
                if os.path.splitext(p)[1].lower() == ".mp3"]
        if mp3s:
            cmds.clear()
            if not vp.play_path(mp3s[0], overlap=True, volume=0.5):
                return False, "mp3 起播失败"
            # 别名从池尾借，编号不定（fxclicksfxN），只比对命令形态与取值
            if not any(c.startswith("setaudio fxclicksfx")
                       and c.endswith("volume to 500") for c in cmds):
                return False, f"mp3 的音量命令不对：{cmds}"
    finally:
        fx_click._mci_send = orig
    return True, ("wav 全程无 setaudio（走 waveOut 软件混音池）；mp3 仍走 MCI "
                  "setaudio volume to 500（0~1000 区间）")


def test_scene_panel_click_three_states():
    """面板点击三态：交互控件抑制 / 面板空白放行 / 面板外照常。

    演进记录（两次用户反馈逼出来的）：
    * 第一版：命中面板（含所有子窗口）一律清零 clicked —— 修掉了"点按钮
      双响"（按下触发旧音效 + pick() 试听新音效），但把面板空白处的点击
      也一刀切没了，用户报"控制面板上不生效"。
    * 现在：只有命中**交互控件**（按钮/滑块/侧栏导航…）才抑制；命中面板
      其它区域放行，并置 panel_fx_until —— 渲染循环据此在播放期间把鼠标层
      临时提到面板之上，文字/表情才不被面板本身挡住。
    """
    import fx_scene
    TW, TH = 320, 240
    # 一维缓冲：Scene.step 的绘制端按扁平索引写（u32[iy*stride+ix]），
    # 传 2D 数组会错形状。
    buf = np.zeros(TW * TH, np.uint32)
    sc = fx_scene.Scene(TW, TH)
    # 完整配置：step 里各特效直接下标读键（如 cfg["rain_amount"]），缺键会
    # KeyError —— 用 DEFAULTS 做基底最稳。
    sc.cfg = dict(d.DEFAULTS)
    sc.cfg["click_sfx_enabled"] = True     # 默认关着的，本测试要它响
    sc.cfg["click_text_enabled"] = True
    calls = []
    sc.click_sfx.trigger = lambda cfg: calls.append(1)
    # 不让真鼠标探针覆盖 clicked（本测试只验过滤逻辑）
    sc.mouse.update = lambda *a, **k: 0
    orig_at = _win32.window_at
    orig_root = _win32.root_window
    try:
        # A) 面板没设（ui_hwnd=0）：不过滤，点击照常触发
        calls.clear()
        sc.ui_hwnd = 0
        sc.ui_btn_hwnds = frozenset()
        sc.mouse.clicked = 1
        sc.step(0.016, buf, TW, TH, buf)
        if sc.mouse.clicked != 1 or len(calls) != 1:
            return False, f"未设面板时点击被误吞：clicked={sc.mouse.clicked} calls={len(calls)}"
        # B) 命中面板交互控件：抑制，trigger 一次都不该调，也不置提升标志
        calls.clear()
        sc.ui_hwnd = 777
        sc.ui_btn_hwnds = frozenset({555})     # 555 = 某个交互控件
        fx_scene._win32.window_at = lambda x, y: 555
        fx_scene._win32.root_window = lambda h: 777       # 归一是面板根
        sc.mouse.clicked = 1
        sc.panel_fx_until = 0.0
        sc.step(0.016, buf, TW, TH, buf)
        if sc.mouse.clicked != 0:
            return False, "命中交互控件但 clicked 没被清零"
        if calls:
            return False, f"点按钮仍触发了 {len(calls)} 次点击特效（双响回归）"
        if sc.panel_fx_until != 0.0:
            return False, "被抑制的点击不该置 panel_fx_until"
        # C) 命中面板**非**交互区域（另一个子窗口）：放行 + 置提升标志
        calls.clear()
        fx_scene._win32.window_at = lambda x, y: 556      # 不在交互集合里
        sc.mouse.clicked = 1
        sc.panel_fx_until = 0.0
        sc.step(0.016, buf, TW, TH, buf)
        if sc.mouse.clicked != 1:
            return False, "面板空白处的点击被误吞（用户报的不生效）"
        if len(calls) != 1:
            return False, f"面板空白处该触发一次，实际 {len(calls)}"
        if not sc.panel_fx_until > 0.0:
            return False, "面板空白点击应置 panel_fx_until（临时提升鼠标层用）"
        # D) 面板外：不过滤
        calls.clear()
        fx_scene._win32.root_window = lambda h: 999       # 别的窗口
        sc.mouse.clicked = 1
        sc.step(0.016, buf, TW, TH, buf)
        if sc.mouse.clicked != 1 or len(calls) != 1:
            return False, f"面板外的点击被误吞：clicked={sc.mouse.clicked} calls={len(calls)}"
    finally:
        fx_scene._win32.window_at = orig_at
        fx_scene._win32.root_window = orig_root
    return True, ("三态判定：交互控件抑制（无双响）、面板空白放行且置提升标志、"
                  "面板外照常")


# ------------------------------------------------------------------ 驱动
def test_presets_never_carry_assets():
    """预设**不携带**素材/来源：套用预设不能把图案来源改回内置图案。

    根因是 `save_preset` 快照的是**全部** DEFAULTS 键 —— 不排除就会把保存
    那一刻的 `click_emoji_source` / 图片文件夹 / 音效文件夹一起冻进预设。
    用户报的现象就是"点了预设，来源悄悄跳回内置图案"。所以两端都要挡：
    保存时不取，套用时也跳过（老 presets_user.json 里已经存着了）。
    """
    for k in d.PRESET_EXCLUDE:
        for name, pre in d.PRESETS.items():
            if k in pre:
                return False, f"内置预设「{name}」不该带 {k}"
    # 反过来：内置音效是随程序打包的文件，预设**可以**带（否则「青轴打字音」
    # 就没有身份了），而且必须真的指向一个存在的文件
    for name, pre in d.PRESETS.items():
        b = pre.get("click_sfx_builtin", "")
        if b and not any(os.path.basename(x) == b for x in F.list_builtin_sfx()):
            return False, f"预设「{name}」指向的内置音效 {b} 不存在"
    app = d.App.__new__(d.App)
    app.cfg = dict(d.DEFAULTS)
    app.snapshot = dict(app.cfg)
    app.user_presets = {}
    app._autosave_dirty = False
    # 老预设里存着来源与路径（模拟历史文件）
    app.user_presets["老预设"] = dict(
        d.PRESETS["爱心雨"], click_emoji_source="图片文件夹",
        click_emoji_custom_dir=r"C://表情//猫猫",
        click_sfx_source="音效文件夹", click_sfx_dir=r"C://音效",
        click_text_voice_match=True)
    app.cfg["click_emoji_source"] = "自选图片"
    app.cfg["click_emoji_files"] = r"C://图//a.png"
    app.cfg["click_sfx_dir"] = r"C://我的音效"
    app.apply_preset("老预设")
    if app.cfg["click_emoji_source"] != "自选图片":
        return False, f"套用预设把图案来源改成了 {app.cfg['click_emoji_source']}"
    if app.cfg["click_emoji_files"] != r"C://图//a.png":
        return False, "套用预设把自选图片换掉了"
    if app.cfg["click_sfx_dir"] != r"C://我的音效":
        return False, "套用预设把音效文件夹换掉了"
    if app.cfg["click_text_voice_match"]:
        return False, "套用预设把「语音与文字统一」打开了"
    if app.cfg["click_emoji_shape"] != "爱心":
        return False, "预设该生效的键（图案形状）反而没生效"
    # 保存预设时也不该把这些键存进去
    app.cfg["click_emoji_source"] = "图片文件夹"
    ok, info = app.save_preset("我的", "点击表情")
    saved = app.user_presets.get("我的", {})
    if not ok:
        return False, f"保存预设失败：{info}"
    for k in d.PRESET_EXCLUDE:
        if k in saved:
            return False, f"保存的自定义预设里混进了 {k}"
    if saved.get("click_emoji_shape") != app.cfg["click_emoji_shape"]:
        return False, "保存预设时把该存的键弄丢了"
    return True, ("预设两端都不带素材/来源：套用旧预设不会换掉当前来源与路径，"
                  "保存的新预设里也没有这些键")


def test_align_also_applies_to_builtin_shapes():
    """「图片方向」是**通用**选项：内置图案也要跟着方向转。

    判据不看像素，看 `rot`：勾了之后每一颗粒子的旋转角都必须等于
    `atan2(vy, vx) + 90°`（而不是随机角），且 `spin` 被关掉 —— 勾着自旋
    的话 rot 会被逐帧累加，方向瞬间就散了，这个选项等于不起作用。
    """
    from _verify_kinds import _base_cfg
    cfg = _base_cfg(click_emoji_enabled=True,
                    click_emoji_source=F.CLICK_SOURCE_BUILTIN,
                    click_emoji_align=F.CLICK_ALIGN_SPREAD,
                    click_emoji_count=16.0, click_emoji_spread=300.0,
                    click_emoji_gravity=0.0, click_emoji_rotate=True)
    f = ClickEmojiField(W, H)
    f.set_pointer(W * 0.5, H * 0.5, 1)
    f.update(1.0 / 60.0, cfg)
    n = f.engine.count
    if n <= 0:
        return False, "内置图案没有生成粒子"
    rot = f.engine.rot[:n]
    want = (np.arctan2(f.engine.vy[:n], f.engine.vx[:n])
            + math.pi * 0.5) % (2.0 * math.pi)
    if int((np.abs(rot - want) > 1e-4).sum()):
        return False, "内置图案的旋转角没有跟着飞行方向"
    if bool((f.engine.spin[:n] != 0).any()):
        return False, "勾了随扩散方向却还留着自旋（方向会被搅散）"
    # 默认（竖直）时恢复随机角
    cfg2 = dict(cfg, click_emoji_align=F.CLICK_ALIGN_VERTICAL)
    f2 = ClickEmojiField(W, H)
    f2.set_pointer(W * 0.5, H * 0.5, 1)
    f2.update(1.0 / 60.0, cfg2)
    r2 = f2.engine.rot[:f2.engine.count]
    if bool(np.allclose(r2, want, atol=1e-3)):
        return False, "竖直档下 rot 也被写成了方向角"
    return True, "内置图案在「随扩散方向」下角度＝飞行角+90° 且自旋关闭；竖直档仍是随机角"


def test_voice_match_only_with_folder_source():
    """「语音与文字统一」只在「音效文件夹」那一档下成立。

    统一模式要把**多条**音频各自变成一行弹出文字 —— 内置音效与单个音效都
    给不出多行，硬撑着"开着"只会让文字框没被接管、用户以为自己哪里配错了。
    """
    folder = _mk_pack()
    for src in (F.SFX_SOURCE_BUILTIN, F.SFX_SOURCE_FILE):
        cfg = {"click_text_voice_match": True, "click_sfx_source": src,
               "click_sfx_file": os.path.join(folder, "a.wav"),
               "click_sfx_dir": folder, "click_text_list": "甲\n乙",
               "click_text_deco": "无"}
        if F.voice_pack_dir(cfg):
            return False, f"来源={src} 时不该给出统一模式的目录"
        if F.voice_match_on(cfg):
            return False, f"来源={src} 时统一模式被判成生效"
        t = ClickTextField(W, H)
        t.set_pointer(W * 0.5, H * 0.5, 1)
        t.update(1.0 / 60.0, cfg)
        t.pops = [p for p in t.pops if p["t"] < 0.05]
        if not t.pops or t.pops[-1]["text"] not in ("甲", "乙"):
            shutil.rmtree(folder, ignore_errors=True)
            return False, f"来源={src} 时统一模式没有退回自定义文字"
        if t.take_voice() is not None:
            shutil.rmtree(folder, ignore_errors=True)
            return False, f"来源={src} 时仍排了待播音频"
    cfg = dict(click_text_voice_match=True, **_sfx_pack(folder),
               click_text_list="甲\n乙", click_text_deco="无")
    if not F.voice_match_on(cfg):
        shutil.rmtree(folder, ignore_errors=True)
        return False, "音效文件夹 + 统一开着时反而判定为不生效"
    if F.voice_pack_dir(cfg) != folder:
        shutil.rmtree(folder, ignore_errors=True)
        return False, f"统一模式取到的目录不对：{F.voice_pack_dir(cfg)!r}"
    # 开着但还没选目录 -> 不生效（退回自定义文字）
    if F.voice_match_on({"click_text_voice_match": True,
                         **_sfx_pack("")}):
        shutil.rmtree(folder, ignore_errors=True)
        return False, "目录为空时不该判定统一生效"
    shutil.rmtree(folder, ignore_errors=True)
    return True, ("只有「音效文件夹」才让统一生效；内置/单文件两档退回自定义"
                  "文字且不排音频；目录为空也不生效")


def test_voice_config_migration():
    """旧配置里的语音目录会自动搬进「点击音效」的音效文件夹。"""
    folder = _mk_pack()
    for old_key in ("click_text_voice_dir", "click_voice_dir"):
        data = {old_key: folder, "click_sfx_dir": ""}
        d.App._migrate_voice_keys(data)
        if data.get("click_sfx_dir") != folder:
            shutil.rmtree(folder, ignore_errors=True)
            return False, f"{old_key} 没被搬进 click_sfx_dir"
        if data.get("click_sfx_source") != F.SFX_SOURCE_FOLDER:
            shutil.rmtree(folder, ignore_errors=True)
            return False, f"{old_key} 迁移后来源没切到音效文件夹"
    # 目标已有值时不动（用户的当前选择优先）
    data = {"click_text_voice_dir": folder, "click_sfx_dir": r"C://我的音效"}
    d.App._migrate_voice_keys(data)
    if data["click_sfx_dir"] != r"C://我的音效":
        shutil.rmtree(folder, ignore_errors=True)
        return False, "已有音效目录时不该被旧键覆盖"
    # 目录已经不存在 -> 不搬（搬过去只是个坏路径）
    data = {"click_text_voice_dir": r"C://早没了//目录", "click_sfx_dir": ""}
    d.App._migrate_voice_keys(data)
    if data["click_sfx_dir"]:
        shutil.rmtree(folder, ignore_errors=True)
        return False, "目录不存在时不该搬"
    shutil.rmtree(folder, ignore_errors=True)
    return True, "两个旧语音键都会搬进音效文件夹；已有值/目录不存在时不搬"


def main():
    tests = [
        test_all_shapes_draw,
        test_shape_names_and_ui_lists_match,
        test_rotation_steps_differ,
        test_shape_cache_bounded,
        test_burst_spawn_and_expire,
        test_spread_controls_travel,
        test_gravity_pulls_down,
        test_rotate_spins,
        test_pool_overflow_is_safe,
        test_wild_input_no_nan,
        test_confetti_uses_palette,
        test_draws_into_buffer,
        test_text_list_parsing,
        test_text_rotation,
        test_text_pop_scales,
        test_text_has_outline,
        test_text_empty_is_safe,
        test_text_deco,
        test_text_draws_and_rises,
        test_categories,
        test_click_effects_are_exclusive,
        test_registry_and_defaults,
        test_scene_wiring,
        test_negative_copy_instead_of_over,
        test_text_not_clipped_horizontally,
        test_text_clip_check_is_sensitive,
        test_shape_default_colors,
        test_rainbow_is_per_particle,
        test_confetti_is_curved,
        test_text_tilt_random,
        test_custom_image_pool,
        test_custom_image_sprite_format,
        test_custom_image_rect_not_clipped,
        test_custom_image_fallback,
        test_voice_no_overlap,
        test_voice_releases_on_failure,
        test_click_asset_defaults,
        test_scene_voice_wiring,
        test_audio_label_cleaning,
        test_audio_labels_dedup_sorted,
        test_text_voice_match_pairs_text_and_audio,
        test_text_voice_match_falls_back,
        test_text_voice_off_is_independent,
        test_text_pack_defaults,
        test_scene_plays_the_matched_audio,
        test_frame_indexes_spread,
        test_gif_decodes_all_frames,
        test_gif_frame_duration_compensated,
        test_sprite_set_frames_cycle,
        test_animated_sprite_moves_on_screen,
        test_animated_flag_in_state,
        test_image_files_source,
        test_pick_mode_one_vs_multi,
        test_source_normalize_and_target,
        test_click_source_defaults,
        test_no_shadowed_module_defs,
        # ---- 本轮新增：图片取向 + 点击音效 ----
        test_img_align_rotation_direction,
        test_img_align_wiring,
        test_img_align_builtin_unaffected,
        test_ui_align_list_matches_impl,
        test_builtin_sfx_available,
        test_presets_never_carry_assets,
        test_align_also_applies_to_builtin_shapes,
        test_voice_match_only_with_folder_source,
        test_voice_config_migration,
        test_sfx_source_normalize_and_target,
        test_click_sfx_triggers_and_overlaps,
        test_sfx_voices_use_separate_aliases,
        test_sfx_voice_pool_is_bounded_and_released,
        test_scene_sfx_mouse_and_keyboard,
        test_sfx_independent_from_click_effects,
        test_sfx_never_reported_as_layer_content,
        test_click_sfx_defaults,
        test_sfx_volume_command_per_device_type,
        test_scene_volume_covers_voice_and_sfx,
        test_preview_audio_routes_to_player_with_volume,
        test_wav_pcm16_decode_and_volume,
        test_wav_volume_never_via_mci_setaudio,
        test_scene_panel_click_three_states,
    ]
    print("== 点击特效 + 分组 ==")
    bad = 0
    for fn in tests:
        try:
            r = fn()
        except Exception as e:
            import traceback
            r = (False, f"exception: {type(e).__name__}: {e}\n"
                        + traceback.format_exc().splitlines()[-3])
        if r is None:
            print(f"  [SKIP] {fn.__name__}")
            continue
        ok, msg = r
        if not ok:
            bad += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {fn.__name__}: {msg}")
    print()
    if bad:
        print(f"== CLICK FAILED ==  {bad} 项失败")
        raise SystemExit(1)
    print(f"== CLICK VERIFIED ==  {len(tests)} 项")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
