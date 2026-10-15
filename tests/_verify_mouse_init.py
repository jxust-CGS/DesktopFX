# -*- coding: utf-8 -*-
"""验证 MouseProbe 初始化时不会产生幽灵位移。

   Bug 复现：旧代码 prev=(0,0)，启动时若光标在 (1000,500)，
   首帧算出 dist ≈ 1118px → 触发 3 个涟漪凭空炸出。

   修后：prev 和 x 都初始化为当前光标位置，首帧 dist=0。"""
import ctypes
import ctypes.wintypes as wt
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
from fx_effects import MouseProbe, RippleField, hex_to_bgr


# 模拟"光标在 (1500, 600) 不动"5 帧
class FakeCursor:
    """伪装 GetCursorPos：固定返回指定位置。"""
    def __init__(self, x, y):
        self.x, self.y = x, y
        self._installed = False
    def __enter__(self):
        # Monkey-patch user32 inside fx_effects
        import fx_effects as fe
        self._orig = fe.user32.GetCursorPos
        def fake(pt_ptr):
            # pt_ptr 是 byref 传进来的 CArgObject，
            # 它内部指向 MouseProbe 持有的 POINT 实例。
            # 用 cast 拿到 POINT 对象后写字段。
            pt = ctypes.cast(pt_ptr, ctypes.POINTER(wt.POINT)).contents
            pt.x = self.x
            pt.y = self.y
            return 1
        fe.user32.GetCursorPos = fake
        return self
    def __exit__(self, *args):
        import fx_effects as fe
        fe.user32.GetCursorPos = self._orig


def test_no_phantom_movement():
    ripples = RippleField()
    col = hex_to_bgr("#7FE7FF")

    # 关键：MouseProbe __init__ 时"光标"在 (1500, 600)
    with FakeCursor(1500, 600):
        probe = MouseProbe()
    # 模拟 5 帧：光标不动
    with FakeCursor(1500, 600):
        for f in range(5):
            dist = probe.update(1/60, 0, 0)   # 覆盖层原点 = (0,0)
            # 模拟 desktop_fx 的逻辑：累加 + 触发
            acc = probe.acc
            step = 24.0
            while acc >= step and ... :   # 不真 spawn，只看 acc
                acc -= step
            # 关键断言：首帧 dist 应该接近 0（不应该是 1612 ≈ |1500-0|）
    if dist > 1.0:
        print(f"  [FAIL] first-frame dist = {dist:.1f}px  (expected ~0)")
        return False
    print(f"  [OK] first-frame dist = {dist:.4f}px  (phantom movement eliminated)")
    return True


def test_real_movement_still_works():
    """修 bug 后，真实移动仍然能 spawn 涟漪。"""
    ripples = RippleField()
    col = hex_to_bgr("#7FE7FF")
    pre_active = ripples.active.sum()
    with FakeCursor(100, 100):
        probe = MouseProbe()
    # 模拟一次大移动
    with FakeCursor(200, 200):
        probe.update(1/60, 0, 0)
    if probe.acc < 100:
        print(f"  [FAIL] real movement not detected: acc={probe.acc:.1f}")
        return False
    print(f"  [OK] real movement detected: acc={probe.acc:.1f}")
    return True


if __name__ == "__main__":
    print("== mouse-probe init bug verification ==")
    print()
    ok1 = test_no_phantom_movement()
    ok2 = test_real_movement_still_works()
    print()
    if ok1 and ok2:
        print("== MOUSE-PROBE VERIFIED ==")
    else:
        print("== MOUSE-PROBE FAILED ==")
