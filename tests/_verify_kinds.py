# -*- coding: utf-8 -*-
"""特效分类 / 分层 / 多开 专项验证。

覆盖四件事：
  1. 分类表自洽：每个特效页都有启停键、键都在 DEFAULTS 里、导航顺序覆盖全部页
  2. 层级与多开的默认值（最上层 + 不允许同类多开）
  3. 同类互斥：面板开关路径 set() 与预设套用路径 apply_preset() 都必须生效
  4. Scene.step 按层分流：全屏缓冲 / 鼠标缓冲互不串味，旧单缓冲签名行为不变
"""
import numpy as np

import os
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
import fx_kinds


def _app():
    """不落盘、不起线程的 App 外壳（与 _verify_config 同套路）。"""
    app = d.App.__new__(d.App)
    app.cfg = dict(d.DEFAULTS)
    app.snapshot = dict(app.cfg)
    app.user_presets = {}
    app._autosave_dirty = False
    return app


def _base_cfg(**over):
    cfg = dict(d.DEFAULTS)
    cfg.update(dict(rain_enabled=False, snow_enabled=False, petal_enabled=False,
                    meteor_enabled=False, spotlight_enabled=False,
                    paint_enabled=False, pet_enabled=False,
                    ripple_enabled=False,
                    wave_enabled=False, win_ripple_enabled=False))
    cfg.update(over)
    return cfg


# ---------------------------------------------------------------- 分类表
def test_registry_self_consistent():
    pages = set(fx_kinds.EFFECTS)
    assert pages | set(fx_kinds.SETTINGS_PAGES) == set(fx_kinds.nav_order()), \
        "导航顺序与分类表覆盖的页不一致"
    for page, info in fx_kinds.EFFECTS.items():
        assert info["cat"] in fx_kinds.CATEGORY_ORDER, f"{page} 的分类不在类别表里"
        assert info["layer"] in fx_kinds.LAYER_KEYS, f"{page} 的渲染层非法"
        keys = fx_kinds.off_keys(page)
        assert keys, f"{page} 没有启停键，互斥会失效"
        for k in keys:
            assert k in d.DEFAULTS, f"{page} 的启停键 {k} 不在 DEFAULTS 里"
    # 每个分类都得有特效，且启停键唯一映射回特效
    for cat in fx_kinds.CATEGORY_ORDER:
        assert fx_kinds.effects_of(cat), f"{cat} 分类是空的"
    for k, name in fx_kinds.ENABLE_TO_EFFECT.items():
        assert fx_kinds.EFFECTS[name].get("enable") == k
    return (f"{len(fx_kinds.EFFECTS)} 个特效 / {len(fx_kinds.CATEGORY_ORDER)} 个分类，"
            f"启停键与 DEFAULTS 一致")


def test_defaults():
    assert d.DEFAULTS["layer_fullscreen"] == fx_kinds.LAYER_TOPMOST
    assert d.DEFAULTS["layer_mouse"] == fx_kinds.LAYER_TOPMOST
    assert d.DEFAULTS["multi_fullscreen"] is False
    assert d.DEFAULTS["multi_mouse"] is False
    assert fx_kinds.layer_key_of("下雨") == "layer_fullscreen"
    assert fx_kinds.layer_key_of("聚光灯") == "layer_mouse"
    # 窗口水波属"其他特效"，但它跟涟漪共用一个池子，必须画在鼠标层
    assert fx_kinds.category_of("窗口水波") == "其他特效"
    assert fx_kinds.layer_of("窗口水波") == "mouse"
    return "默认 最上层 + 不允许多开；窗口水波归其他特效、画在鼠标层"


# ---------------------------------------------------------------- 同类互斥
def test_exclusive_on_set():
    app = _app()
    app.set("rain_enabled", True)
    app.set("snow_enabled", True)
    assert app.cfg["rain_enabled"] is False, "同类第二个开启时没关掉第一个"
    assert app.cfg["snow_enabled"] is True
    assert app.snapshot["rain_enabled"] is False, "snapshot 没跟上互斥结果"

    # 全屏互斥不影响鼠标类
    app.set("spotlight_enabled", True)
    assert app.cfg["snow_enabled"] is True, "跨分类被误伤"
    app.set("ripple_enabled", False)
    assert app.cfg["spotlight_enabled"] is True

    # 鼠标类里开第二个 -> 关掉第一个
    app.set("wave_enabled", True)
    assert app.cfg["spotlight_enabled"] is False, "鼠标类没有互斥"
    assert app.cfg["wave_enabled"] is True

    # 打开多开后不再挤掉同类
    app.set("multi_fullscreen", True)
    app.set("snow_enabled", True)
    app.set("rain_enabled", True)
    assert app.cfg["snow_enabled"] and app.cfg["rain_enabled"], "多开打开后仍被挤掉"

    # 关掉某个特效不该连累别人
    app.set("snow_enabled", False)
    assert app.cfg["rain_enabled"] is True
    return "set() 路径：同类互斥 / 跨类不误伤 / 多开生效"


def test_exclusive_on_preset():
    app = _app()
    # 涟漪默认开着，套用「真实水波」必须留下水波、关掉涟漪
    app.apply_preset("真实水波")
    assert app.cfg["wave_enabled"] is True, "预设要开的特效被默认开着的涟漪顶掉了"
    assert app.cfg["ripple_enabled"] is False, "同类里的旧特效没被关掉"
    assert app.snapshot["wave_enabled"] is True

    app.apply_preset("聚光灯")
    assert app.cfg["spotlight_enabled"] is True
    assert app.cfg["wave_enabled"] is False

    # 全屏预设同样只留一个
    app.apply_preset("中雨")
    app.apply_preset("初雪")
    assert app.cfg["snow_enabled"] is True
    assert app.cfg["rain_enabled"] is False, "预设套用没有走同类互斥"

    # 多开打开时，预设不该再挤掉同类
    app2 = _app()
    app2.cfg["multi_mouse"] = True
    app2.snapshot = dict(app2.cfg)
    app2.apply_preset("聚光灯")
    assert app2.cfg["ripple_enabled"] is True, "多开打开后预设仍挤掉了同类"
    assert app2.cfg["spotlight_enabled"] is True
    return "apply_preset() 路径：预设意图优先，多开打开时不挤"


def test_no_global_cull():
    """收拢只发生在"预设显式打开某特效"的分类里，绝不全局清理。

    全局收拢会在启动时静默改掉用户当前的桌面（他可能就要同类叠开），
    也会让"套用下雨预设"顺手关掉另一分类的真实水波。
    """
    app = _app()
    app.cfg["rain_enabled"] = True
    app.cfg["snow_enabled"] = True
    app._normalize_exclusive(None)          # 空 prefer
    assert app.cfg["snow_enabled"] is True, "空 prefer 不该做任何收拢"

    app2 = _app()
    app2.cfg.update(rain_enabled=True, wave_enabled=True, ripple_enabled=False)
    app2.apply_preset("中雨")
    assert app2.cfg["rain_enabled"] is True
    assert app2.cfg["wave_enabled"] is True, "套用下雨预设误伤了鼠标分类"

    app3 = _app()
    app3.cfg.update(snow_enabled=True, wave_enabled=True, ripple_enabled=False)
    app3.apply_preset("中雨")
    assert app3.cfg["snow_enabled"] is False, "同类里的旧特效没被收拢"
    assert app3.cfg["wave_enabled"] is True, "跨分类被误伤"
    return "空 prefer 不动手；只收拢预设显式打开的类别"


# ---------------------------------------------------------------- 分层渲染
def test_scene_layer_split():
    W, H = 320, 200
    sc = d  # noqa  仅保持 import 一致性
    from fx_scene import Scene

    # 只开全屏特效（下雨）：全屏层有像素，鼠标层必须是全透明
    s1 = Scene(W, H)
    s1.cfg = _base_cfg(rain_enabled=True, rain_amount=200, splash=False)
    fs = np.zeros(W * H, dtype=np.uint32)
    ms = np.zeros(W * H, dtype=np.uint32)
    for _ in range(30):
        fs[:] = 0
        ms[:] = 0
        s1.step(1.0 / 60.0, fs, W, H, ms)
    assert fs.any(), "全屏层没有画出下雨"
    assert not ms.any(), "鼠标层混进了全屏特效的像素"

    # 只开鼠标特效（聚光灯压暗整屏）：鼠标层有像素，全屏层必须全透明
    s2 = Scene(W, H)
    s2.cfg = _base_cfg(spotlight_enabled=True, spotlight_dim=0.6)
    fs2 = np.zeros(W * H, dtype=np.uint32)
    ms2 = np.zeros(W * H, dtype=np.uint32)
    for _ in range(3):
        fs2[:] = 0
        ms2[:] = 0
        s2.step(1.0 / 60.0, fs2, W, H, ms2)
    assert ms2.any(), "鼠标层没有画出聚光灯"
    assert not fs2.any(), "全屏层混进了鼠标特效的像素"

    # 旧签名（不传第二缓冲）必须仍然把全部特效画进同一张 —— 老测试靠它
    s3 = Scene(W, H)
    s3.cfg = _base_cfg(rain_enabled=True, rain_amount=200, splash=False,
                       spotlight_enabled=True, spotlight_dim=0.6)
    one = np.zeros(W * H, dtype=np.uint32)
    for _ in range(5):
        one[:] = 0
        s3.step(1.0 / 60.0, one, W, H)
    assert one.any(), "旧的单缓冲调用方式画不出东西了"
    return "全屏层/鼠标层互不串味；旧单缓冲签名保持兼容"


def test_water_ripple_layer():
    """雨滴溅水花落进鼠标层的涟漪池 —— 分层后这条依赖必须仍然成立。"""
    W, H = 320, 200
    from fx_scene import Scene
    s = Scene(W, H)
    s.cfg = _base_cfg(rain_enabled=True, rain_amount=260, splash=True)
    fs = np.zeros(W * H, dtype=np.uint32)
    ms = np.zeros(W * H, dtype=np.uint32)
    seen_ms = False
    for _ in range(120):
        fs[:] = 0
        ms[:] = 0
        s.step(1.0 / 60.0, fs, W, H, ms)
        if ms.any():
            seen_ms = True
    assert seen_ms, "雨滴溅起的水花没有出现在鼠标层（涟漪池画错层了）"
    assert fs.any(), "雨本身不在全屏层"
    return "溅水花随涟漪池落在鼠标层，雨本身在全屏层"


# ---------------------------------------------------------------- 层间顺序
def test_restack_guard():
    """鼠标层纠正到全屏层之上，只在**两层都是最上层**时才允许。

    任一层切到"仅桌面图层"后就是 WorkerW 的 WS_CHILD 子窗口。此时把一个顶层窗
    插到"别人的子窗口"之后语义未定义（Windows 只保证同父窗口间的相对位置），
    可能把鼠标层压到壁纸之下 —— 表现就是鼠标特效整片消失，而全屏特效照常。
    """
    class _FakeOv:
        def __init__(self, z_mode, hwnd):
            self.z_mode = z_mode
            self.hwnd = hwnd
            self.calls = []
            self._riding = False

        def is_riding_panel(self):
            return self._riding

        def place_above(self, other):
            self.calls.append(other)
            return True

    app = _app()
    app.overlays = []
    assert app._restack_overlays() is False, "覆盖层未建好时不该动手"

    fs = _FakeOv(fx_kinds.LAYER_TOPMOST, 111)
    ms = _FakeOv(fx_kinds.LAYER_TOPMOST, 222)
    app.overlays = [fs, ms]
    assert app._restack_overlays() is True
    assert ms.calls == [111], "两层都在最上层时没有纠正顺序"

    # 临时骑在面板之上（点击特效播放中）时不纠正两层先后 —— 那次
    # SetWindowPos 可能把鼠标层压回面板之下，正在播的特效就看不见了
    ms.calls.clear()
    ms._riding = True
    assert app._restack_overlays() is False
    assert ms.calls == [], "骑面板期间仍在重排两层"
    ms._riding = False
    ms.calls.clear()
    assert app._restack_overlays() is True, "结束骑乘后应恢复纠正"

    fs.z_mode = fx_kinds.LAYER_DESKTOP          # 全屏层沉到桌面壁纸
    ms.calls.clear()
    assert app._restack_overlays() is False
    assert ms.calls == [], "全屏层在桌面层时仍去纠正顺序（会把鼠标层压到壁纸下）"

    fs.z_mode = fx_kinds.LAYER_TOPMOST
    ms.z_mode = fx_kinds.LAYER_DESKTOP          # 鼠标层自己在桌面层
    assert app._restack_overlays() is False
    assert ms.calls == [], "鼠标层在桌面层时不该去抢到顶层窗之上"
    return "仅两层同为最上层时纠正顺序；混合层级 / 未建层时不动手"


# ---------------------------------------------------------------- 层活动信号
def test_layer_activity():
    """空层跳过用的保守信号：绝不能把"其实有内容"的层报成空。

    信号报空时调用方还会用真实像素复核，所以保守方向（报有内容）只是不省，
    报空报错了才会漏擦、把上一帧冻在桌面上。这里把两个方向都钉住。
    """
    W, H = 320, 200
    from fx_scene import Scene
    s = Scene(W, H)

    s.cfg = _base_cfg()                       # 全关、池子空
    assert s.layer_activity() == (False, False), "全关时两层却被报成有内容"

    s.cfg = _base_cfg(rain_enabled=True)
    assert s.layer_activity()[0] is True, "开着雨，全屏层却报空 -> 会漏画"

    s.cfg = _base_cfg(spotlight_enabled=True)
    assert s.layer_activity()[1] is True, "开着聚光灯，鼠标层却报空 -> 会漏画"

    # 关键区别：涟漪开关开着但池子里没东西（用户没动鼠标）—— 该报空，否则
    # 空层跳过永久失效。池子里一有水花，就必须立刻报有内容。
    s.cfg = _base_cfg(ripple_enabled=True)
    assert s.layer_activity()[1] is False, "涟漪开着但池子空，却报成有内容（省不下来）"
    s.ripples.spawn(100.0, 100.0, 30.0, 1.0, 0.5, (255, 200, 120), r0=2.0)
    assert s.layer_activity()[1] is True, "池子里有水花了却报空 -> 水花会被冻住"
    return "保守信号：开着的报有内容；涟漪池空着时报空、有水花立刻报有"


def main():
    tests = [
        test_registry_self_consistent,
        test_defaults,
        test_exclusive_on_set,
        test_exclusive_on_preset,
        test_no_global_cull,
        test_scene_layer_split,
        test_water_ripple_layer,
        test_restack_guard,
        test_layer_activity,
    ]
    print("== 特效分类 / 分层 / 多开 验证 ==")
    ok_all = True
    for fn in tests:
        try:
            msg = fn()
            ok = True
        except Exception as e:
            ok, msg = False, f"exception: {type(e).__name__}: {e}"
        ok_all = ok_all and ok
        print(f"  [{'OK' if ok else 'FAIL'}] {fn.__name__:28s} {msg}")
    print()
    print("== KINDS", "VERIFIED ==" if ok_all else "FAILED ==")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
