# -*- coding: utf-8 -*-
"""算 WorkBuddy 窗口有多少面积落在 DesktopFX 覆盖层范围内。

背景：覆盖层的像素范围 = 虚拟屏（SM_CXVIRTUALSCREEN 等），而窗口可以
被放到虚拟屏之外（多屏布局的负坐标区、屏幕缝隙外的坐标）。落在范围外的
那部分，**特效物理上不可能显示** —— 这正是"点 WorkBuddy 没反应"最可能的
真实原因，而不是特效逻辑坏了。

用法：python tools/diag/geom.py
"""
import ctypes
import sys
from ctypes import wintypes as wt

sys.path.insert(0, __file__.rsplit("tools", 1)[0])
import _win32  # noqa: E402

u = _win32.user32
u.GetSystemMetrics.restype = ctypes.c_int
u.GetSystemMetrics.argtypes = [ctypes.c_int]
u.GetWindowRect.restype = wt.BOOL
u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u.GetClassNameW.restype = ctypes.c_int
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowTextW.restype = ctypes.c_int
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.IsWindowVisible.restype = wt.BOOL
u.IsWindowVisible.argtypes = [wt.HWND]
PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
MINIMIZED = (-32000, -32000, -31840, -31972)


def main():
    vx, vy = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
    vw, vh = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
    print("虚拟屏（= 覆盖层像素范围）: (%d,%d) %dx%d" % (vx, vy, vw, vh))
    print("主屏: %dx%d   副屏起点 x=%d" % (
        u.GetSystemMetrics(0), u.GetSystemMetrics(1), vx))

    rows = []

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        t = ctypes.create_unicode_buffer(200)
        u.GetWindowTextW(h, t, 200)
        rows.append((h, c.value, t.value))
        return True

    u.EnumWindows(PROC(cb), 0)

    for h, cls, title in rows:
        if not (("WorkBuddy" in title) or ("Chrome_WidgetWin" in cls
                                            and "WorkBuddy" in title)):
            continue
        r = wt.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        rect = (r.left, r.top, r.right, r.bottom)
        vis = bool(u.IsWindowVisible(h))
        print("\nWorkBuddy hwnd=%d cls=%s visible=%s rect=%s"
              % (h, cls, vis, rect))
        if rect == MINIMIZED:
            print("  -> **窗口是最小化状态**（坐标是 Windows 的屏外哨兵值）。")
            print("     最小化时窗口没有可见像素，点它也点不到 —— "
                  "请先把它恢复到屏幕上再测。")
            continue
        aw = (rect[2] - rect[0]) * (rect[3] - rect[1])
        ix0, iy0 = max(rect[0], vx), max(rect[1], vy)
        ix1, iy1 = min(rect[2], vx + vw), min(rect[3], vy + vh)
        ain = max(0, ix1 - ix0) * max(0, iy1 - iy0)
        pct = 100.0 * ain / max(aw, 1)
        print("  窗口面积 %d px，在覆盖层范围内的 %d px -> %.1f%%"
              % (aw, ain, pct))
        if pct < 99.5:
            print("  ⚠️ 有 %.1f%% 的窗口面积落在覆盖层之外，那部分点上不会有特效"
                  % (100.0 - pct))
            print("     超出量: 左 %d px / 右 %d px / 上 %d px / 下 %d px"
                  % (vx - rect[0], rect[2] - (vx + vw),
                     vy - rect[1], rect[3] - (vy + vh)))
        else:
            print("  ✅ 窗口完全在覆盖层范围内")
    return 0


if __name__ == "__main__":
    sys.exit(main())