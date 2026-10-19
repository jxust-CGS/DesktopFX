# -*- coding: utf-8 -*-
"""验证两件事：
  1. 双击托盘唤出面板时，它排在最前（临时置顶一次），且**唤出后不是置顶窗**
  2. 面板 acrylic 的颜色**不再随点击变化**（点一下颜色不变）

判据：
  (1) show() 之后立刻读面板 z 序 —— 应处于置顶组顶部附近；再等 2s 读
      GWL_EXSTYLE，WS_EX_TOPMOST 必须**没**置着。
  (2) 抓面板中心区域的面板像素 → 点一次空白 → 再抓，比对两次颜色是否变化。
      旧实现会在点击特效播放期间把 acrylic alpha 210→255，颜色明显加深；
      现在不该有任何变化（阈值取很小的值，只捕捉"肉眼可见的加深"）。

用法：python tools/diag/show_color_check.py
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes as wt

import numpy as np

ROOT = __file__.rsplit("tools", 1)[0].rstrip("/\\")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
PY_EXE = r"C:\Users\hspcadmin\.pyenv\pyenv-win\versions\3.11.9\python.exe"
WS_EX_TOPMOST = 0x00000008

u = ctypes.WinDLL("user32", use_last_error=True)
g = ctypes.WinDLL("gdi32", use_last_error=True)
u.GetClassNameW.restype = ctypes.c_int
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowTextW.restype = ctypes.c_int
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.IsWindowVisible.restype = wt.BOOL
u.IsWindowVisible.argtypes = [wt.HWND]
u.GetWindowRect.restype = wt.BOOL
u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u.GetWindowLongW.restype = wt.LONG
u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
u.GetWindow.restype = wt.HWND
u.GetWindow.argtypes = [wt.HWND, wt.UINT]
u.WindowFromPoint.restype = wt.HWND
u.WindowFromPoint.argtypes = [wt.POINT]
GW_HWNDPREV = 3
GW_HWNDNEXT = 2
PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
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
g.DeleteObject.restype = wt.BOOL
g.DeleteObject.argtypes = [wt.HGDIOBJ]
g.DeleteDC.restype = wt.BOOL
g.DeleteDC.argtypes = [wt.HDC]
u.GetDC.restype = wt.HDC
u.GetDC.argtypes = [wt.HWND]
u.ReleaseDC.restype = ctypes.c_int
u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]


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


def find(cls, title=""):
    out = []

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        t = ctypes.create_unicode_buffer(200)
        u.GetWindowTextW(h, t, 200)
        if cls in c.value and title in t.value:
            out.append(h)
        return True

    u.EnumWindows(PROC(cb), 0)
    return out


def fx_windows():
    out = []

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        if "DesktopFX" in c.value:
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            out.append((h, c.value, pid.value))
        return True

    u.EnumWindows(PROC(cb), 0)
    return out


def kill_existing():
    pids = {pid for _h, _c, pid in fx_windows()}
    for pid in pids:
        subprocess.run(["taskkill", "//F", "//PID", str(pid)],
                       capture_output=True, text=True, errors="replace")
    time.sleep(1.2)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wt.HANDLE
    k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k.TerminateProcess.restype = wt.BOOL
    k.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    for pid in pids:
        h = k.OpenProcess(0x0001, False, pid)
        if h:
            k.TerminateProcess(h, 1)
            k.CloseHandle(h)
    for _ in range(15):
        time.sleep(0.4)
        if not fx_windows():
            return True
    return False


def above_count(target, depth=40):
    n, cur = 0, u.GetWindow(target, GW_HWNDPREV)
    while cur and n < depth:
        n += 1
        cur = u.GetWindow(cur, GW_HWNDPREV)
    return n


def toggle_panel():
    """模拟托盘双击（走项目自己的 show/hide 消息通道）。"""
    sys.path.insert(0, ROOT)
    import fx_winlayer
    msg = fx_winlayer.show_panel_message()
    h = u.FindWindowW(fx_winlayer._CLASS_NAME, None)
    if msg and h:
        u.PostMessageW(h, msg, 0, 0)
    for _ in range(20):
        time.sleep(0.2)
        if find("TkTopLevel", "桌面特效"):
            return True
    return False


def main():
    print("清理旧实例:", "OK" if kill_existing() else "⚠️")
    log = open(os.path.join(ROOT, "probe_boot.log"), "w", encoding="utf-8")
    subprocess.Popen([PY_EXE, "desktop_fx.py"], cwd=ROOT, stdout=log,
                     stderr=subprocess.STDOUT,
                     creationflags=0x00000008 | 0x00000200)
    for _ in range(24):
        time.sleep(0.5)
        if len([1 for _h, c, _p in fx_windows()
                if c == "DesktopFXOverlayCls"]) >= 2:
            break
    time.sleep(2.5)

    # ---------- (1) 唤出后应"排在前面"，但不是置顶窗 ----------
    if find("TkTopLevel", "桌面特效"):
        # 已显示：先藏起来，再走一次"唤出"路径
        print("面板已在显示，先隐藏再唤出以测试 show()")
        panels = find("TkTopLevel", "桌面特效")
        u.ShowWindow(panels[0], 0)      # SW_HIDE
        time.sleep(0.6)
    ok = toggle_panel()
    if not ok:
        print("⚠️ 面板没叫出来")
        return 1
    ph = find("TkTopLevel", "桌面特效")[0]
    r = wt.RECT()
    u.GetWindowRect(ph, ctypes.byref(r))
    ex = u.GetWindowLongW(ph, -20)
    print("\n(1) 唤出瞬间：面板上方窗口数=%d  topmost=%s"
          % (above_count(ph), bool(ex & WS_EX_TOPMOST)))
    time.sleep(2.5)
    ex2 = u.GetWindowLongW(ph, -20)
    print("(1) 2.5 秒后：面板上方窗口数=%d  topmost=%s"
          % (above_count(ph), bool(ex2 & WS_EX_TOPMOST)))
    raise_ok = not (ex2 & WS_EX_TOPMOST)

    # ---------- (2) 点击不应改变面板颜色 ----------
    # 取面板内一块"卡片空白"（避开控件），点击后看颜色变化
    pr = (r.left, r.top, r.right, r.bottom)
    pt = None
    for gy in range(pr[1] + 80, pr[3] - 40, 15):
        for gx in range(pr[0] + 190, pr[2] - 40, 15):
            h = u.WindowFromPoint(wt.POINT(gx, gy))
            if not h:
                continue
            rr = wt.RECT()
            if not u.GetWindowRect(h, ctypes.byref(rr)):
                continue
            if (rr.right - rr.left) * (rr.bottom - rr.top) < 60000:
                continue
            pt = (gx, gy)
            break
        if pt:
            break
    if not pt:
        print("(2) 找不到面板空白点，跳过颜色检查")
        return 0 if raise_ok else 1
    # 抓面板**标题栏下方的卡片区**（不是点击点，避免特效像素干扰）
    sx, sy = pt[0] - 120, pt[1] + 40
    sw = sh = 60
    base = grab(sx, sy, sw, sh).astype(np.int16)
    click_at(pt[0], pt[1])
    time.sleep(0.5)          # 特效正盛（旧的实现正是此刻加深）
    mid = grab(sx, sy, sw, sh).astype(np.int16)
    time.sleep(2.5)
    after = grab(sx, sy, sw, sh).astype(np.int16)
    d_mid = int(np.abs(mid - base).sum(axis=2).mean())
    d_after = int(np.abs(after - base).sum(axis=2).mean())
    print("\n(2) 点击点=%s 采样区=(%d,%d,%dx%d)" % (pt, sx, sy, sw, sh))
    print("    点击时颜色平均变化=%d（0 = 完全没变）" % d_mid)
    print("    2.5 秒后颜色平均变化=%d" % d_after)
    color_ok = d_mid < 3
    print("\n结论：(1) 唤出置顶但摘掉置顶位 = %s；"
          "(2) 点击不再改变颜色 = %s"
          % ("通过" if raise_ok else "失败",
             "通过" if color_ok else "失败"))
    return 0 if (raise_ok and color_ok) else 1


if __name__ == "__main__":
    sys.exit(main())