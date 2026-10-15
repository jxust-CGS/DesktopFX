# -*- coding: utf-8 -*-
"""最坏情况性能基准：暴雨 (620 drops) + 大量涟漪，看是否还能 60 FPS。

**退出码就是判据**（`_run_all_verify.py` 只认 returncode）：
    0  < 14 ms/帧  最坏情况稳过 60 FPS
    0  14~20 ms/帧 50~60 FPS，满帧刚好够（警告但不拦）
    1  >= 20 ms/帧 明显掉帧，回归不通过
本机 2026-10-14 实测基线：纯雨 6.43 ms、雨+120 涟漪 17.40 ms（57 FPS）。
"""
import os
import sys
import time
import numpy as np

# 复用真实的特效类
os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")
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
from fx_effects import RainField, RippleField, hex_to_bgr
import desktop_fx as d


def main():
    """跑基准。返回 True = 通过（退出码 0），False = 掉帧（退出码 1）。"""
    W, H = 3840, 1200          # 用户的 4K 双屏
    cfg = dict(d.PRESETS["暴雨"])
    print(f"benchmark on {W}x{H}, 暴雨 preset:")
    print(f"  rain_amount={cfg['rain_amount']}  rain_speed={cfg['rain_speed']}"
          f"  rain_length={cfg['rain_length']}  rain_opacity={cfg['rain_opacity']}")

    rain = RainField(W, H, seed=1)
    rain.set_color(cfg["rain_color"])
    rain.set_amount(int(cfg["rain_amount"]), W, H)
    ripples = RippleField()

    buf = np.zeros((H, W, 4), np.uint8)
    u32 = buf.view(np.uint32).reshape(-1)

    # 预热
    for _ in range(30):
        rain.update(0.016, cfg)
    for _ in range(5):
        u32[:] = 0
        rain.draw(u32, W, H, cfg)

    # 测纯雨 100 帧
    N = 100
    t0 = time.perf_counter()
    for _ in range(N):
        rain.update(0.016, cfg)
        u32[:] = 0
        rain.draw(u32, W, H, cfg)
    dt_rain = (time.perf_counter() - t0) * 1000 / N
    print(f"\n  纯雨 100 帧: {dt_rain:.2f} ms/frame  (理论 FPS = {1000/dt_rain:.0f})")

    # 测 雨+120 涟漪（最坏情况）
    color = hex_to_bgr("#7FE7FF")
    np.random.seed(2)
    for i in range(120):
        ripples.spawn(np.random.uniform(50, W-50), np.random.uniform(50, H-50),
                      rmax=200, life=1.0, strength=0.8, color_bgr=color)
    t0 = time.perf_counter()
    for _ in range(N):
        rain.update(0.016, cfg)
        ripples.update(0.016)
        u32[:] = 0
        rain.draw(u32, W, H, cfg)
        ripples.draw(u32, W, H)
    dt_full = (time.perf_counter() - t0) * 1000 / N
    print(f"  雨+120 涟漪 100 帧: {dt_full:.2f} ms/frame  (理论 FPS = {1000/dt_full:.0f})")
    print()
    if dt_full < 14:
        print(f"  ✓ 最坏情况还能 60+ FPS（{1000/dt_full:.0f} FPS）")
        return True
    elif dt_full < 20:
        print(f"  ~ 50-60 FPS（{1000/dt_full:.0f} FPS）— 满帧刚好够")
        # 警告但不拦：4K 双屏极端组合下贴着 60 帧可接受，落在硬线以内
        return True
    print(f"  ✗ 掉帧（{1000/dt_full:.0f} FPS）— 需要降上限或优化")
    return False


if __name__ == "__main__":
    # 只打印不退出码的脚本在 `_run_all_verify.py` 眼里永远是绿的 ——
    # 这个脚本存在的全部意义就是当性能回归的判据，所以结论必须落到退出码。
    sys.exit(0 if main() else 1)
