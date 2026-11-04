# -*- coding: utf-8 -*-
"""直接对比「开着光环」与「关着光环」时**光标所在区域**的像素。

为什么不用帧间差异：电平恒定时光环半径不变，画面完全不动（帧间差异 = 0），
那是**正确行为**不是故障。要证明"光环画出来了"，该比的是**开 vs 关**。

用法：python tools/diag/halo_diff.py
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
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _awake  # noqa: E402
PY_EXE = r"C:\Users\hspcadmin\.pyenv\pyenv-win\versions\3.11.9\python.exe"

u = ctypes.WinDLL("user32", use_last_error=True)
g = ctypes.WinDLL("gdi32", use_last_error=True)
u.GetClassNameW.restype = ctypes.c_int
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.IsWindowVisible.restype = wt.BOOL
u.IsWindowVisible.argtypes = [wt.HWND]
u.SetCursorPos.restype = wt.BOOL
u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
u.GetCursorPos.restype = wt.BOOL
u.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
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
g.DeleteObject.restype = wt.BOOL
g.DeleteObject.argtypes = [wt.HGDIOBJ]
g.DeleteDC.restype = wt.BOOL
g.DeleteDC.argtypes = [wt.HDC]
u.GetDC.restype = wt.HDC
u.GetDC.argtypes = [wt.HWND]
u.ReleaseDC.restype = ctypes.c_int
u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
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
    time.sleep(1.0)
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
    for _ in range(12):
        time.sleep(0.4)
        if not fx_windows():
            return
    return


def set_cfg(**kw):
    from _paths import atomic_write_json
    p = os.path.join(ROOT, "config.json")
    import json
    with open(p, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.update(kw)
    atomic_write_json(p, cfg)


def spawn():
    """启动实例并**确认它真的起来了**。

    ⚠️ 不能只等"两个覆盖层窗口出现"：kill_existing() 之后单实例互斥体
    还没释放，新实例启动即被拒（会弹"残留进程"消息框并退出），而**旧实例
    的窗口还在** —— 于是"窗口出现了"这个条件被旧窗口满足，脚本以为新实例
    起来了，实际抓屏时屏幕上什么都没有。必须校验窗口的 pid 属于新进程。
    """
    log = open(os.path.join(ROOT, "probe_boot.log"), "w", encoding="utf-8")
    proc = subprocess.Popen([PY_EXE, "desktop_fx.py"], cwd=ROOT, stdout=log,
                            stderr=subprocess.STDOUT,
                            creationflags=0x00000008 | 0x00000200)
    for _ in range(40):
        time.sleep(0.5)
        ovs = [(h, c, pid) for h, c, pid in fx_windows()
               if c == "DesktopFXOverlayCls"]
        if len(ovs) >= 2 and all(pid == proc.pid for _h, _c, pid in ovs):
            time.sleep(2.0)
            return proc.pid
    print("⚠️ 新实例没真正起来（pid=%d）—— 单实例互斥体可能还没释放"
          % proc.pid)
    return None


def desktop_spot():
    for gy in range(200, 1100, 40):
        for gx in range(200, 1800, 40):
            h = u.WindowFromPoint(wt.POINT(gx, gy))
            if not h:
                continue
            c = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(h, c, 256)
            if c.value in ("Progman", "WorkerW", "SysListView32"):
                return gx, gy
    return 700, 500


def main():
    _awake.keep_awake()
    time.sleep(1.5)
    kill_existing()
    cx, cy = desktop_spot()
    u.SetCursorPos(cx, cy)
    time.sleep(1.0)
    pt = wt.POINT()
    u.GetCursorPos(ctypes.byref(pt))
    print("光标在 (%d,%d)，桌面空白点 (%d,%d)" % (pt.x, pt.y, cx, cy))
    r = 160
    x0, y0 = pt.x - r, pt.y - r

    print("\n[A] 开着光环：")
    set_cfg(halo_enabled=True, halo_style="彩虹光环", halo_size=70.0,
            halo_sensitivity=1.0)
    spawn()
    u.SetCursorPos(cx, cy)
    time.sleep(2.5)
    on = grab(x0, y0, 2 * r, 2 * r).astype(int)

    print("[B] 关着光环：")
    kill_existing()
    set_cfg(halo_enabled=False)
    spawn()
    u.SetCursorPos(cx, cy)
    time.sleep(2.5)
    off = grab(x0, y0, 2 * r, 2 * r).astype(int)

    d = np.abs(on - off).sum(axis=2)
    changed = int((d > 24).sum())
    print("\n光标区域 320x320：")
    print("  开着：最亮=%d 均值=%.1f" % (int(on.max()), float(on.mean())))
    print("  关着：最亮=%d 均值=%.1f" % (int(off.max()), float(off.mean())))
    print("  差异像素=%d (%.1f%%)" % (changed, 100.0 * changed / d.size))
    if changed > 300:
        ys, xs = np.where(d > 24)
        print("  差异区域 bbox=(%d,%d,%d,%d)（相对抓取区左上）"
              % (xs.min(), ys.min(), xs.max(), ys.max()))
        print("\n结论: ✅ 光环确实画在屏幕上（开 vs 关 有明显差异）")
        rc = 0
    else:
        print("\n结论: ❌ 没检测到光环")
        rc = 1
    kill_existing()
    return rc


if __name__ == "__main__":
    sys.exit(main())
