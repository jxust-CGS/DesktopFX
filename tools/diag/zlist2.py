# -*- coding: utf-8 -*-
"""按 **z 序从顶到底**列出所有可见顶层窗口（EnumWindows）。

为什么不用 GetTopWindow 链：那条链只走**当前活动窗口所属的那一个 z 序带**
（normal band），**topmost 窗口在另一个带里**，链上根本走不到 —— 于是
"DesktopFX 的置顶覆盖层"在旧工具里根本看不到，害我误判"覆盖层被压住了"。
EnumWindows 才是跨带的、按 z 序从顶到底的真实顺序。

用法：python tools/diag/zlist2.py [个数]
"""
import ctypes
import sys
from ctypes import wintypes as wt

sys.path.insert(0, __file__.rsplit("tools", 1)[0].rstrip("/\\"))
import _win32  # noqa: E402

u = _win32.user32
u.GetClassNameW.restype = ctypes.c_int
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowTextW.restype = ctypes.c_int
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.IsWindowVisible.restype = wt.BOOL
u.IsWindowVisible.argtypes = [wt.HWND]
u.GetWindowLongW.restype = wt.LONG
u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
u.GetWindowRect.restype = wt.BOOL
u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

WS_EX_TOPMOST = 0x8
WS_EX_TRANSPARENT = 0x20
WS_EX_LAYERED = 0x80000


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    rows = []

    def cb(h, _):
        if not u.IsWindowVisible(h):
            return True
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        t = ctypes.create_unicode_buffer(200)
        u.GetWindowTextW(h, t, 200)
        r = wt.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        ex = u.GetWindowLongW(h, -20)
        rows.append((h, c.value, t.value,
                     (r.left, r.top, r.right, r.bottom),
                     bool(ex & WS_EX_TOPMOST), bool(ex & WS_EX_TRANSPARENT),
                     bool(ex & WS_EX_LAYERED)))
        return True

    u.EnumWindows(PROC(cb), 0)
    print("=== 按 z 序从顶到底（%d 个可见窗口）===" % len(rows))
    for i, (h, c, t, r, top, trans, layered) in enumerate(rows[:limit]):
        mark = ""
        if c == "DesktopFXOverlayCls":
            mark = "   <== DesktopFX 覆盖层"
        elif c == "TkTopLevel":
            mark = "   <== 控制面板"
        print("%2d hwnd=%-9d top=%-5s trans=%-5s layered=%-5s %-22s %s%s"
              % (i, h, top, trans, layered, c[:22], t[:24], mark))


if __name__ == "__main__":
    main()
