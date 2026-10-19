# -*- coding: utf-8 -*-
"""在**指定窗口**（WorkBuddy / 桌面 / 面板空白）上模拟点击，并用屏幕像素差
判定"点击特效有没有真的画出来"。

判据（不靠肉眼）：抓三张屏 —— 点前 / 点后 0.30s / 点后 2.8s。
    出现量 = |点后0.30s - 点前|   有东西冒出来
    消散量 = |点后2.8s - 点后0.30s|  东西又退了（特效是会消散的）
面板自身的 UI 变化是**持久的**（不会中途再变），所以"消散量 > 0"能把它和
UI 变化区分开。

为什么覆盖层之上/之下不影响这个判据：覆盖层是置顶的，实测 z 序恒在
WorkBuddy 之上；且点击特效画在鼠标层（全屏虚拟坐标），只要触发了就会
出现在屏幕上。

用法：python tools/diag/clickprobe.py WorkBuddy
      python tools/diag/clickprobe.py panel
      python tools/diag/clickprobe.py desktop
"""
import ctypes
import sys
import time
from ctypes import wintypes as wt

import numpy as np

sys.path.insert(0, __file__.rsplit("tools", 1)[0])
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
u.GetSystemMetrics.restype = ctypes.c_int
u.GetSystemMetrics.argtypes = [ctypes.c_int]
u.WindowFromPoint.restype = wt.HWND
u.WindowFromPoint.argtypes = [wt.POINT]
u.GetWindow.restype = wt.HWND
u.GetWindow.argtypes = [wt.HWND, wt.UINT]
u.GetClassNameW.restype = ctypes.c_int
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowTextW.restype = ctypes.c_int
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowRect.restype = wt.BOOL
u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u.IsWindowVisible.restype = wt.BOOL
u.IsWindowVisible.argtypes = [wt.HWND]
GW_HWNDPREV = 3
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
    got = (ctypes.c_char * (w * h * 4))()
    g.GetDIBits(mdc, bmp, 0, h, got, ctypes.byref(bi), 0)
    arr = np.frombuffer(bytes(got), dtype=np.uint8).reshape(h, w, 4)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(None, hdc)
    return arr[:, :, :3].copy()


def click_at(x, y, hold=0.05):
    """在 (x,y) 模拟一次左键点击。

    `hold` 不能省：DesktopFX 的点击检测是**逐帧轮询 GetAsyncKeyState 的
    边沿**（按下瞬间置 clicked=1）。把按下和松开塞进同一个 SendInput 批次，
    两句之间不插延时的话，按键可能在两次轮询之间已经松开 —— 边沿就丢了，
    探测会得出"点了没反应"的假结论（这个坑踩过一次）。所以这里按下后
    真的等 hold 秒再松开，保证至少跨越一帧。
    """
    class _MI(ctypes.Structure):
        _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                    ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                    ("dwExtraInfo", ctypes.c_void_p)]

    class _II(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("mi", _MI)]

    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    nx, ny = int(x * 65535 / max(sw - 1, 1)), int(y * 65535 / max(sh - 1, 1))

    def send(flags):
        arr = (_II * 1)()
        arr[0].type = 0
        arr[0].mi.dx, arr[0].mi.dy, arr[0].mi.dwFlags = nx, ny, flags
        u.SendInput(1, arr, ctypes.sizeof(_II))

    send(0x0001 | 0x8000)          # 先把指针移到目标点
    time.sleep(0.03)
    send(0x0002)                  # 左键按下
    time.sleep(hold)              # 保持住，让渲染线程至少轮询到一次
    send(0x0004)                  # 左键松开


def win_info(h):
    c = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(h, c, 256)
    t = ctypes.create_unicode_buffer(200)
    u.GetWindowTextW(h, t, 200)
    r = wt.RECT()
    u.GetWindowRect(h, ctypes.byref(r))
    return c.value, t.value, (r.left, r.top, r.right, r.bottom)


def find(pred):
    out = []

    def cb(h, _):
        if not u.IsWindowVisible(h):
            return True
        cls, title, rect = win_info(h)
        if pred(cls, title, rect):
            out.append((h, cls, title, rect))
        return True

    u.EnumWindows(PROC(cb), 0)
    return out


def probe(label, x, y, half=160, t_a=0.30, t_b=2.8):
    x0, y0, w = x - half, y - half, 2 * half
    before = grab(x0, y0, w, w)
    click_at(x, y)
    time.sleep(t_a)
    a = grab(x0, y0, w, w)
    time.sleep(t_b - t_a)
    b = grab(x0, y0, w, w)
    show = int((np.abs(a.astype(int) - before.astype(int)).sum(2) > 24).sum())
    gone = int((np.abs(b.astype(int) - a.astype(int)).sum(2) > 24).sum())
    if show > 30 and gone > 30:
        v = "有特效"
    elif show <= 30 and gone <= 30:
        v = "无特效"
    else:
        v = "形态不明"
    print("%-20s (%5d,%5d) 出现=%-6d 消散=%-6d => %s"
          % (label, x, y, show, gone, v))
    return v


def target_workbuddy():
    wins = [w for w in find(lambda c, t, r: "Chrome_WidgetWin_1" in c
                            and "WorkBuddy" in t
                            and r[0] > -30000 and r[2] - r[0] > 200)]
    if not wins:
        print("找不到在屏上的 WorkBuddy 窗口")
        return None
    h, cls, title, r = wins[0]
    print("WorkBuddy: hwnd=%d rect=%s" % (h, r))
    return (r[0] + r[2]) // 2, max(r[1] + 80, (r[1] + r[3]) // 2 - 150)


def target_panel():
    wins = [w for w in find(lambda c, t, r: c == "TkTopLevel"
                            and "桌面特效" in t)]
    if not wins:
        print("控制面板没开（先双击托盘图标）")
        return None
    h, cls, title, r = wins[0]
    print("控制面板: hwnd=%d rect=%s" % (h, r))
    # 找一块"大 Frame 空白"（非交互区）：命中自身、面积大、不在侧栏/标题栏
    for gy in range(r[1] + 70, r[3] - 30, 15):
        for gx in range(r[0] + 175, r[2] - 30, 15):
            hh = u.WindowFromPoint(wt.POINT(gx, gy))
            if not hh:
                continue
            rr = wt.RECT()
            if not u.GetWindowRect(hh, ctypes.byref(rr)):
                continue
            if (rr.right - rr.left) * (rr.bottom - rr.top) < 60000:
                continue
            if u.WindowFromPoint(wt.POINT(gx, gy)) != hh:
                continue
            return gx, gy
    print("面板上找不到大 Frame 空白点")
    return None


def target_desktop():
    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    x, y = sw // 2, 300
    for _ in range(30):
        hh = u.WindowFromPoint(wt.POINT(x, y))
        cls = ""
        if hh:
            cls, _t, _r = win_info(hh)
        if cls in ("Progman", "WorkerW", "SysListView32"):
            return x, y
        y += 24
    return sw // 2, 300


def main():
    what = (sys.argv[1] if len(sys.argv) > 1 else "workbuddy").lower()
    if what.startswith("wb") or what.startswith("work"):
        pt = target_workbuddy()
    elif what.startswith("panel"):
        pt = target_panel()
    else:
        pt = target_desktop()
    if not pt:
        return 1
    probe(what, pt[0], pt[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())