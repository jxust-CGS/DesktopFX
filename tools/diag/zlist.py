# -*- coding: utf-8 -*-
"""列出 z-order 里的可见顶层窗口，并标出 DesktopFX 的两层覆盖窗与控制面板。

用法：C:/Users/hspcadmin/.pyenv/pyenv-win/versions/3.11.9/python.exe tools/diag/zlist.py
"""
import ctypes
import sys
from ctypes import wintypes as wt

sys.path.insert(0, __file__.rsplit("tools", 1)[0])
import _win32  # noqa: E402

user32 = _win32.user32
user32.GetWindow.restype = wt.HWND
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowRect.restype = wt.BOOL
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowLongW.restype = wt.LONG
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]

GW_HWNDNEXT = 2


def desc(h):
    c = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, c, 256)
    t = ctypes.create_unicode_buffer(200)
    user32.GetWindowTextW(h, t, 200)
    r = wt.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    ex = user32.GetWindowLongW(h, -20)
    return (c.value, t.value, (r.left, r.top, r.right, r.bottom),
            bool(ex & 0x00000008), bool(ex & 0x00000020))


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    print("=== z-order 可见顶层窗口（0 = 最上）===")
    h = user32.GetTopWindow(None)
    n = 0
    while h and n < limit:
        if user32.IsWindowVisible(h):
            cls, title, rect, top, trans = desc(h)
            mark = ""
            if cls == "DesktopFXOverlayCls":
                mark = "  <== DesktopFX 覆盖层"
            elif cls == "TkTopLevel":
                mark = "  <== 控制面板"
            elif "Chrome_WidgetWin" in cls or "Electron" in cls:
                mark = "  <== Electron/Chromium"
            print("%2d hwnd=%-9d top=%-5s trans=%-5s %-24s rect=%-28s %s%s"
                  % (n, h, top, trans, cls[:24], str(rect), title[:26], mark))
            n += 1
        h = user32.GetWindow(h, GW_HWNDNEXT)
    print("(trans=True 是 WS_EX_TRANSPARENT：鼠标穿透，不接收点击)")


if __name__ == "__main__":
    main()