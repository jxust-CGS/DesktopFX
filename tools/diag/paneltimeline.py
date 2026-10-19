# -*- coding: utf-8 -*-
"""点击面板空白后，**逐时刻**采样点击点附近的屏幕，把"特效什么时候出现、
持续多久、然后消失"的时间线打出来。

用途：前面的探测在"有"和"没有"之间摇摆（一次 4 万 px、一次 136 px），
单点采样分不清是"没触发"还是"采样时机错过了播放窗口"。这里每 0.2s 抓
一张，把整条曲线画出来，结论自然唯一。

判据读法：
    每一格是"这一帧与稳态基线的差异像素数" > 阈值 = 该时刻有特效；
    出现一串连续的高值 = 特效在播；全低 = 压根没触发。

用法：python tools/diag/paneltimeline.py
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes as wt

import numpy as np

ROOT = __file__.rsplit("tools", 1)[0].rstrip("/\\")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import _win32  # noqa: E402

u = _win32.user32
g = _win32.gdi32
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
u.GetClassNameW.restype = ctypes.c_int
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowTextW.restype = ctypes.c_int
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.IsWindowVisible.restype = wt.BOOL
u.IsWindowVisible.argtypes = [wt.HWND]
u.GetWindowRect.restype = wt.BOOL
u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u.WindowFromPoint.restype = wt.HWND
u.WindowFromPoint.argtypes = [wt.POINT]
PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


class _BIH(ctypes.Structure):
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
    bi = _BIH()
    ctypes.memset(ctypes.byref(bi), 0, ctypes.sizeof(bi))
    bi.biSize, bi.biWidth, bi.biHeight = ctypes.sizeof(bi), w, -h
    bi.biPlanes, bi.biBitCount = 1, 32
    buf = (ctypes.c_char * (w * h * 4))()
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    arr = np.frombuffer(bytes(buf), dtype=np.uint8).reshape(h, w, 4)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(None, hdc)
    return arr[:, :, :3].copy()


def click_at(x, y, hold=0.08):
    class _MI(ctypes.Structure):
        _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                    ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                    ("dwExtraInfo", ctypes.c_void_p)]

    class _II(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("mi", _MI)]

    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    nx, ny = int(x * 65535 / max(sw - 1, 1)), int(y * 65535 / max(sh - 1, 1))

    def send(fl):
        a = (_II * 1)()
        a[0].type = 0
        a[0].mi.dx, a[0].mi.dy, a[0].mi.dwFlags = nx, ny, fl
        u.SendInput(1, a, ctypes.sizeof(_II))

    send(0x0001 | 0x8000)
    time.sleep(0.03)
    send(0x0002)
    time.sleep(hold)
    send(0x0004)


def find_panel():
    out = []

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        t = ctypes.create_unicode_buffer(200)
        u.GetWindowTextW(h, t, 200)
        if c.value == "TkTopLevel" and "桌面特效" in t.value \
                and u.IsWindowVisible(h):
            r = wt.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            out.append((h, (r.left, r.top, r.right, r.bottom)))
        return True

    u.EnumWindows(PROC(cb), 0)
    return out[0] if out else None


def blank_point(pr):
    for gy in range(pr[1] + 70, pr[3] - 30, 13):
        for gx in range(pr[0] + 180, pr[2] - 30, 13):
            h = u.WindowFromPoint(wt.POINT(gx, gy))
            if not h:
                continue
            r = wt.RECT()
            if not u.GetWindowRect(h, ctypes.byref(r)):
                continue
            if (r.right - r.left) * (r.bottom - r.top) < 60000:
                continue
            if u.WindowFromPoint(wt.POINT(gx, gy)) != h:
                continue
            return gx, gy
    return None


def main():
    got = find_panel()
    if not got:
        print("控制面板没开（先双击托盘图标）")
        return 1
    ph, pr = got
    pt = blank_point(pr)
    if not pt:
        print("面板上找不到大 Frame 空白点")
        return 1
    r = 110
    x0, y0 = pt[0] - r, pt[1] - r
    w = h = 2 * r
    print("面板 hwnd=%d rect=%s  空白点=%s  采样区 %dx%d"
          % (ph, pr, pt, w, h))

    # 先彻底静置（确保上一次播放的观感切换已结束），取稳定基线
    time.sleep(5.0)
    base = grab(x0, y0, w, h).astype(np.int16)

    def diff(img):
        d = np.abs(img.astype(np.int16) - base).sum(axis=2)
        return int((d > 16).sum())

    print("\n点击前: %d px（噪声本底）" % diff(grab(x0, y0, w, h)))
    click_at(pt[0], pt[1])
    t0 = time.time()
    row = []
    for i in range(26):                 # 26 * 0.2s ≈ 5.2s
        time.sleep(0.2)
        t = time.time() - t0
        n = diff(grab(x0, y0, w, h))
        row.append((t, n))
        bar = "#" * min(40, n // 200)
        print("  t=%4.1fs  %6d px %s" % (t, n, bar))
    peak = max(n for _t, n in row)
    hits = sum(1 for _t, n in row if n > 300)
    print("\n峰值 %d px，>300px 的采样点 %d/26" % (peak, hits))
    if hits >= 2:
        print("结论: 特效在面板上**有播放**（连续多个采样点都检测到）")
        return 0
    print("结论: 特效**没有**在面板上画出来")
    return 1


if __name__ == "__main__":
    sys.exit(main())