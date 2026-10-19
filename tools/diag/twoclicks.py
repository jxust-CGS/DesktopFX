# -*- coding: utf-8 -*-
"""复现「面板点击特效：第一次在上层，第二次被盖住」，并把真相打出来。

流程（都在本进程内，探测的一定是自己拉起的实例）：
  1. 清理旧实例 → 用 DESKTOPFX_DIAG2=1 拉起 DesktopFX（开启渲染循环日志）
  2. 调出控制面板，点一次空白，等播放结束（这时该看清观感/z序有没有复位）
  3. **第二次**点同一处，逐时刻采样，输出时间线
  4. 打印 diag2.log

用法：python tools/diag/twoclicks.py
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
DIAG = os.path.join(ROOT, "diag2.log")

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
u.WindowFromPoint.restype = wt.HWND
u.WindowFromPoint.argtypes = [wt.POINT]
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
# GDI 句柄在 64 位下是大整数：不设 argtypes 会被当 c_int，DeleteObject
# 之类的调用直接 OverflowError（"int too long to convert"）。
g.DeleteObject.restype = wt.BOOL
g.DeleteObject.argtypes = [wt.HGDIOBJ]
g.DeleteDC.restype = wt.BOOL
g.DeleteDC.argtypes = [wt.HDC]
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


def fx_windows():
    out = []

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        if "DesktopFX" in c.value:
            out.append((h, c.value))
        return True

    u.EnumWindows(PROC(cb), 0)
    return out


def kill_existing():
    pids = set()
    for hwnd, _c in fx_windows():
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pids.add(pid.value)
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


def ensure_panel():
    if find_panel():
        return True
    sys.path.insert(0, ROOT)
    import fx_winlayer
    msg = fx_winlayer.show_panel_message()
    hwnd = u.FindWindowW(fx_winlayer._CLASS_NAME, None)
    if msg and hwnd:
        u.PostMessageW(hwnd, msg, 0, 0)
    for _ in range(20):
        time.sleep(0.3)
        if find_panel():
            return True
    return False


def timeline(tag, pt, samples=22):
    r = 110
    x0, y0 = pt[0] - r, pt[1] - r
    w = h = 2 * r
    base = grab(x0, y0, w, h).astype(np.int16)
    click_at(pt[0], pt[1])
    t0 = time.time()
    out = []
    for _ in range(samples):
        time.sleep(0.2)
        t = time.time() - t0
        img = grab(x0, y0, w, h).astype(np.int16)
        d = np.abs(img - base).sum(axis=2)
        n = int((d > 16).sum())
        out.append((t, n))
    print("\n=== %s ===" % tag)
    for t, n in out:
        bar = "#" * min(36, n // 300)
        print("  t=%4.1fs %6d px %s" % (t, n, bar))
    peak = max(n for _t, n in out)
    hits = sum(1 for _t, n in out if n > 300)
    print("  峰值 %d px，>300 的采样点 %d/%d" % (peak, hits, samples))
    return hits


def main():
    if os.path.exists(DIAG):
        os.remove(DIAG)
    mode = sys.argv[1] if len(sys.argv) > 1 else "spaced"
    gap = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
    print("清理旧实例:", "OK" if kill_existing() else "⚠️")
    env = dict(os.environ)
    env["DESKTOPFX_DIAG2"] = "1"
    log = open(os.path.join(ROOT, "probe_boot.log"), "w", encoding="utf-8")
    proc = subprocess.Popen([PY_EXE, "desktop_fx.py"], cwd=ROOT, env=env,
                            stdout=log, stderr=subprocess.STDOUT,
                            creationflags=0x00000008 | 0x00000200)
    for _ in range(24):
        time.sleep(0.5)
        if len([1 for _h, c in fx_windows()
                if c == "DesktopFXOverlayCls"]) >= 2:
            break
    print("实例 pid=%d 已启动（模式=%s，间隔=%.1fs）" % (proc.pid, mode, gap))
    time.sleep(2.0)
    if not ensure_panel():
        print("⚠️ 面板没叫出来")
        return 1
    ph, pr = find_panel()
    pt = blank_point(pr)
    if not pt:
        print("找不到面板空白点")
        return 1
    print("面板 hwnd=%d rect=%s 空白点=%s" % (ph, pr, pt))

    time.sleep(3.0)
    a = timeline("第 1 次点击", pt)
    time.sleep(gap)          # spaced=3s 等彻底复位；rapid=0.4s 模拟"马上再点"
    b = timeline("第 2 次点击", pt)

    print("\n=== diag2.log（渲染循环状态流转）===")
    if os.path.exists(DIAG):
        with open(DIAG, encoding="utf-8", errors="replace") as f:
            lines = [x for x in f.read().splitlines() if x.strip()]
        # 只打关键转折：raised 变 False 的时刻（=骑乘结束）、vis 变 False
        keep = []
        prev = None
        for ln in lines:
            key = None
            if "raised=True" in ln:
                key = "R+"
            elif "raised=False" in ln:
                key = "R-"
            if key and key != prev:
                keep.append(ln)
                prev = key
        print("共 %d 行，只打 raised 的翻转点（前后各一行）：" % len(lines))
        for ln in keep:
            print("  ", ln)
    else:
        print("(diag2.log 没生成 —— 实例没带 DESKTOPFX_DIAG2 或已退出)")
    print("\n判读：第1次 hits=%d，第2次 hits=%d（各 %d 个采样点）"
          % (a, b, 22))
    return 0


if __name__ == "__main__":
    sys.exit(main())