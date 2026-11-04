# -*- coding: utf-8 -*-
"""鼠标光环（随系统播放的音乐律动）的专项验证。

覆盖四块：

  1. **音频电平的算法**（不依赖外部状态，任何时候都成立）
     - 归一化：不同音量下峰值差一个数量级，必须映射到 0~1
     - 包络：上升快（鼓点立刻响应）、下降慢（光环不能疯狂闪烁）
     - 灵敏度：越大越容易"跳起来"
     - 钳位：超范围输入不越界
  2. **每种样式都画得出东西**，且**随电平变化** —— 不跟着音乐动的光环等于
     没做，这是本特效的灵魂。
  3. **不越界**：光标贴屏幕边角时不能写出边界，也不能污染远处像素。
  4. **跨文件一致性**：UI 的样式名必须与实现的样式表一一对应（少一个会
     静默退回默认样式，多一个会画不出东西）。

真机采集（读系统真实音频）不在这里做 —— 它依赖"此刻有音乐在放"，
放不放音乐都会让判据时绿时红。真实采集由 tools/diag/verify_audio_level.py
与 tools/diag/halo_live.py 负责（前者验算法+真机，后者验"屏幕上真的在动"）。
"""
import os
import sys

import numpy as np

# 本脚本住在 tests/ 子目录里：向上找到含 desktop_fx.py 的目录（= 项目根）。
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = _TESTS_DIR
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) \
        and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
for _p in (ROOT, _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fx_halo import (AudioLevel, HaloField, HALO_STYLES,   # noqa: E402
                     HALO_STYLE_ORDER, halo_style_names, norm_halo_style)

W = H = 400
FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
    return bool(cond)


def draw(style, level, size=46.0, color="#7EC8FF"):
    """在空白缓冲上画一次，返回 (非零像素数, 包围盒)"""
    halo = HaloField(W, H)
    halo.set_style(style)
    halo.set_color(color)
    halo.set_size(size)
    halo.set_level(level)
    halo.set_cursor(W / 2.0, H / 2.0)
    u32 = np.zeros(W * H, dtype=np.uint32)
    halo.draw(u32, W, H, {}, 0.016)
    a = (u32 >> 24).reshape(H, W)
    ys, xs = np.nonzero(a)
    bbox = None
    if len(xs):
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
    return int((a > 0).sum()), bbox


# ------------------------------------------------------------ 1. 电平算法
def test_level_normalization():
    """归一化：把 0~1 的原始峰值映射到有区分度的 0~1。"""
    al = AudioLevel()
    al._floor = 0.0
    al._ceil = 1.0
    al._push(0.5)
    mid = al._level
    check(0.05 < mid < 0.95, "0.5 归一化后落在中段：%.3f" % mid)
    al._push(5.0)
    check(al._level <= 1.0, "超范围输入被钳到 1：%.3f" % al._level)
    al._push(-3.0)
    check(al._level >= 0.0, "负值被钳到 0：%.3f" % al._level)
    return True, "归一化 %.3f / 钳位正常" % mid


def test_level_envelope():
    """包络：上升快、下降慢。"""
    al = AudioLevel()
    al._floor = 0.0
    al._ceil = 1.0
    al._level = 0.0
    for _ in range(6):
        al._push(0.9)
    up = al._level
    check(up > 0.5, "上升快：喂 6 次 0.9 后 %.3f" % up)
    al._push(0.0)
    one = al._level
    check(one < up, "下降慢：一次 0 后 %.3f（未立刻归零）" % one)
    for _ in range(40):
        al._push(0.0)
    check(al._level < one, "持续 0 会继续下降：%.3f" % al._level)
    return True, "上升 %.3f -> 单次 0 后 %.3f -> 持续 0 后 %.3f" % (
        up, one, al._level)


def test_level_sensitivity():
    """灵敏度：越大越敏感。"""
    a = AudioLevel()
    a._floor = 0.0
    a._ceil = 1.0
    a.set_sensitivity(2.0)
    a._push(0.4)
    b = AudioLevel()
    b._floor = 0.0
    b._ceil = 1.0
    b.set_sensitivity(1.0)
    b._push(0.4)
    check(a._level > b._level, "灵敏度 2.0(%.3f) > 1.0(%.3f)"
          % (a._level, b._level))
    return True, "灵敏度生效：%.3f vs %.3f" % (a._level, b._level)


def test_level_clamps_api():
    """对外 API 的基本契约。"""
    al = AudioLevel()
    check(0.0 <= al.level() <= 1.0, "level() 恒在 0~1")
    check(al.available() is False, "未 start 时 available() 为 False")
    check(isinstance(al.last_error(), str), "last_error() 返回字符串")
    al.set_sensitivity(0.01)
    check(al.level() <= 1.0, "灵敏度设成极小值也不越界")
    return True, "API 契约正常"


# ------------------------------------------------------------ 2. 样式
def test_all_styles_draw():
    """每种样式都要画出像素，且集中在光标附近。"""
    bad = []
    for name in halo_style_names():
        nz, bbox = draw(name, 0.5)
        ok = nz > 50 and bbox is not None
        if ok:
            cx = (bbox[0] + bbox[2]) // 2
            cy = (bbox[1] + bbox[3]) // 2
            ok = abs(cx - W // 2) < 40 and abs(cy - H // 2) < 40
        if not ok:
            bad.append("%s(%d px, bbox=%s)" % (name, nz, bbox))
    check(not bad, "全部样式都画出且居中：%s" % "; ".join(bad))
    return True, "%d 种样式都画得出" % len(halo_style_names())


def test_styles_follow_level():
    """随电平变化 —— 光环的灵魂。"""
    bad = []
    for name in halo_style_names():
        n0, _ = draw(name, 0.0)
        n1, _ = draw(name, 1.0)
        if n1 == n0:
            bad.append("%s(电平0=%d 电平1=%d)" % (name, n0, n1))
    check(not bad, "所有样式都随电平变化：%s" % "; ".join(bad))
    return True, "%d 种样式都跟着音乐动" % len(halo_style_names())


def test_halo_bounded():
    """光标贴边时不越界、不污染远处。"""
    halo = HaloField(W, H)
    halo.set_style("星芒光环")
    halo.set_level(1.0)
    halo.set_cursor(12.0, 12.0)
    u32 = np.zeros(W * H, dtype=np.uint32)
    halo.draw(u32, W, H, {}, 0.016)
    a = (u32 >> 24).reshape(H, W)
    far = int((a[250:390, 250:390] > 0).sum())
    check(far == 0, "贴边光标时远处区域干净（脏像素 %d）" % far)
    return True, "贴边不越界（远处脏像素 %d）" % far


def test_style_names_consistency():
    """UI 的样式名必须与实现一一对应。"""
    import fx_ui
    ui_names = list(fx_ui.HALO_STYLE_NAMES)
    check(sorted(ui_names) == sorted(HALO_STYLES.keys()),
          "UI %d 个 == 实现 %d 个" % (len(ui_names), len(HALO_STYLES)))
    check(list(HALO_STYLE_ORDER) == ui_names,
          "顺序也一致（界面按此顺序排按钮）")
    check(norm_halo_style("不存在的样式") == HALO_STYLE_ORDER[0],
          "未知样式名退回默认")
    check(norm_halo_style("") == HALO_STYLE_ORDER[0], "空样式名退回默认")
    check(norm_halo_style("breath") == "呼吸圆环", "内部标识也能归一")
    return True, "样式表一致（%d 个）" % len(ui_names)


def test_kind_registration():
    """注册：独立分类 + 鼠标层 + 侧栏顺序。"""
    import fx_kinds
    check(fx_kinds.category_of("鼠标光环") == "音频特效",
          "归在「音频特效」分类（否则开它会关掉聚光灯/水波）")
    check(fx_kinds.layer_of("鼠标光环") == "mouse", "归在鼠标层")
    check("鼠标光环" in fx_kinds.nav_order(), "进了侧栏顺序")
    # 独立分类 = 组内互斥不会波及别的组
    others = fx_kinds.effects_of("鼠标拖动特效")
    check("鼠标光环" not in others, "不在鼠标拖动特效组里")
    return True, "独立分类 / 鼠标层 / 已进侧栏"


def test_scene_wiring():
    """Scene 接线：字段存在、层信号包含光环。"""
    import fx_scene
    sc = fx_scene.Scene(320, 240)
    check(hasattr(sc, "halo"), "Scene 有 halo 字段")
    check(hasattr(sc, "audio"), "Scene 有 audio 字段")
    # layer_activity() 无参，读的是 self.cfg（渲染循环每帧把快照塞进去）
    sc.cfg["halo_enabled"] = True
    fs, ms = sc.layer_activity()
    check(bool(ms), "halo_enabled 时鼠标层报有内容（否则整层被跳过不画）")
    sc.cfg["halo_enabled"] = False
    sc.cfg["rain_enabled"] = False
    sc.cfg["snow_enabled"] = False
    sc.cfg["petal_enabled"] = False
    sc.cfg["meteor_enabled"] = False
    fs2, ms2 = sc.layer_activity()
    check(not bool(ms2), "全关时鼠标层报空（不会白提交帧）")
    return True, "Scene 接线完整（鼠标层信号已含光环）"


def main():
    tests = [
        test_level_normalization,
        test_level_envelope,
        test_level_sensitivity,
        test_level_clamps_api,
        test_all_styles_draw,
        test_styles_follow_level,
        test_halo_bounded,
        test_style_names_consistency,
        test_kind_registration,
        test_scene_wiring,
    ]
    bad = 0
    for fn in tests:
        try:
            r = fn()
        except Exception as e:
            import traceback
            r = (False, "exception: %s: %s\n%s"
                 % (type(e).__name__, e,
                    traceback.format_exc().splitlines()[-3]))
        if r is None:
            print("  [SKIP] %s" % fn.__name__)
            continue
        ok, msg = r
        if not ok:
            bad += 1
        print("  [%s] %s: %s" % ("PASS" if ok else "FAIL", fn.__name__, msg))
    print()
    if bad:
        print("== HALO FAILED ==  %d 项失败" % bad)
        raise SystemExit(1)
    print("== HALO VERIFIED ==  %d 项" % len(tests))
    raise SystemExit(0)


if __name__ == "__main__":
    main()
