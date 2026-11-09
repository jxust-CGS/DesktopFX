# -*- coding: utf-8 -*-
"""诊断：暂停 -> 恢复后，两个覆盖层的 z 序与样式到底怎么了。

从**外部**观测（不动产品代码）：读两个覆盖层的
  - WS_EX_TOPMOST（是不是还在置顶组里）
  - WS_VISIBLE
  - 在 z 序里的相对位置（谁在上面）
  - 屏幕矩形
以及同一时刻屏幕上的像素（有没有东西盖住桌面）。

用法：python tools/diag/pause_zorder.py
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes as wt

import numpy as np

_ROOT = os.path.dirname(os.path.abspath(__file__))
ROOT = _ROOT
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) \
        and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _awake  # noqa: E402

PY_EXE = r"C:\Users\hspcadmin\.pyenv\pyenv-win\versions\3.11.9\python.exe"

u = ctypes.WinDLL("user32")
g = ctypes.WinDLL("gdi32")
g.BitBlt.restype = wt.BOOL
g.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                     ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
g.CreateCompatibleDC.restype = wt.HDC
g.CreateCompatibleDC.argtypes = [wt.HDC]
g.CreateCompatibleBitmap.restype = wt.HBITMAP
g.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
g.SelectObject.restype = wt.HGDIOBJ
g.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
g.GetDIBits.restype = ctypes.c_int
g.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, wt.LPVOID,
                         wt.LPVOID, wt.UINT]
u.GetDC.restype = wt.HDC
u.GetDC.argtypes = [wt.HWND]
u.ReleaseDC.restype = ctypes.c_int
u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
u.SetCursorPos.restype = wt.BOOL
u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
u.keybd_event.restype = None
u.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]
u.GetClassNameW.restype = ctypes.c_int
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowLongW.restype = wt.LONG
u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
u.IsWindowVisible.restype = wt.BOOL
u.IsWindowVisible.argtypes = [wt.HWND]
u.GetWindowRect.restype = wt.BOOL
u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u.GetWindow.restype = wt.HWND
u.GetWindow.argtypes = [wt.HWND, wt.UINT]
u.ShowWindow.restype = wt.BOOL
u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
u.EnumWindows.restype = wt.BOOL
u.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM),
                          wt.LPARAM]

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
GW_HWNDPREV = 3
VK_CONTROL, VK_MENU, VK_R = 0x11, 0x12, 0x52
KEYEVENTF_KEYUP = 0x0002


class _B(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def grab(x0, y0, w, h):
    hdc = u.GetDC(None)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    g.BitBlt(mdc, 0, 0, w, h, hdc, x0, y0, 0x00CC0020)
    bi = _B()
    ctypes.memset(ctypes.byref(bi), 0, ctypes.sizeof(bi))
    bi.biSize = ctypes.sizeof(bi)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = (ctypes.c_char * (w * h * 4))()
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    return np.frombuffer(bytes(buf), dtype=np.uint8).reshape(h, w, 4)[:, :, :3]


def all_windows():
    rows = []
    PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        rows.append((h, c.value))
        return True

    u.EnumWindows(PROC(cb), 0)
    return rows


def overlays():
    return [h for h, c in all_windows() if "DesktopFXOverlayCls" in c]


def z_order_index(target):
    """target 在 z 序里的位置（0 = 最顶）。"""
    # 从最顶往下走 GW_HWNDPREV
    h = u.GetTopWindow(0)
    idx = 0
    seen = 0
    while h and seen < 4000:
        if h == target:
            return idx
        h = u.GetWindow(h, GW_HWNDPREV)
        idx += 1
        seen += 1
    return -1


def report(tag):
    ovs = overlays()
    print("  [%s]" % tag)
    for h in ovs:
        ex = u.GetWindowLongW(wt.HWND(h), GWL_EXSTYLE)
        r = wt.RECT()
        u.GetWindowRect(wt.HWND(h), ctypes.byref(r))
        print("      hwnd=%-9d TOPMOST=%-5s visible=%-5s z#=%-5d rect=%s"
              % (h, bool(ex & WS_EX_TOPMOST),
                 bool(u.IsWindowVisible(wt.HWND(h))),
                 z_order_index(h),
                 (r.left, r.top, r.right, r.bottom)))
    img = grab(120, 120, 360, 360)
    print("      屏幕(120,120)区: 均值=%.1f 最亮=%d"
          % (float(img.mean()), int(img.max())))
    return ovs


def press_hotkey():
    for vk in (VK_CONTROL, VK_MENU, VK_R):
        u.keybd_event(vk, 0, 0, 0)
        time.sleep(0.05)
    for vk in (VK_R, VK_MENU, VK_CONTROL):
        u.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)


def hide_panels():
    n = 0
    for h, c in all_windows():
        if c == "TkTopLevel":
            u.ShowWindow(wt.HWND(h), 0)
            n += 1
    return n


def main():
    _awake.keep_awake()
    log = open(os.path.join(ROOT, "pause_live.log"), "w", encoding="utf-8")
    proc = subprocess.Popen([PY_EXE, "desktop_fx.py"], cwd=ROOT, stdout=log,
                            stderr=subprocess.STDOUT,
                            creationflags=0x00000008 | 0x00000200)
    try:
        print("等待实例启动…")
        time.sleep(6.0)
        print("隐藏面板 %d 个" % hide_panels())
        u.SetCursorPos(300, 300)
        time.sleep(1.5)

        report("运行中")
        print("\n按 Ctrl+Alt+R …")
        press_hotkey()
        time.sleep(2.0)
        report("暂停中")
        print("\n再按 Ctrl+Alt+R …")
        press_hotkey()
        time.sleep(3.0)
        report("恢复后")
        return 0
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
