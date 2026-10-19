# -*- coding: utf-8 -*-
"""扫描控制面板上**所有**可点区域，逐个点两次，看哪些位置"第二次被盖住"。

背景：用户在面板上点某个位置，第一次特效在上层、第二次被盖住。而用"卡片
Frame 空白"作为点击点始终复现不出来 —— 说明问题**与点击的具体控件类型
有关**，不是时间/状态机问题。逐区域扫一遍就能定位到是哪一类控件。

分区（都在面板 rect 内，避开标题栏与侧栏）：
  A. 卡片 Frame 空白        —— 已知正常（放行区）
  B. 配置项名称 Label       —— 挂了提示，绑了 ButtonPress-1 -> 判为交互控件
  C. 开关（Checkbutton）    —— 交互控件
  D. 滑块（_Slider/Canvas） —— 交互控件
  E. 按钮组（choices）      —— 交互控件
  F. 预设区按钮             —— 交互控件
  G. 页头/分组标题          —— bind 型

判据：每个位置点两次，每次采样 0.4s 那一帧的差异像素（>300 = 有特效清晰
显示）。两次都高 = 正常；第一次高第二次低 = 复现了用户的问题。

用法：python tools/diag/panelscan.py
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

u = ctypes.WinDLL("user32", use_last_error=True)
g = ctypes.WinDLL("gdi32", use_last_error=True)
u.GetClassNameW.restype = ctypes.c_int
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowTextW.restype = ctypes.c_int
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowTextLengthW.restype = ctypes.c_int
u.GetWindowTextLengthW.argtypes = [wt.HWND]
u.IsWindowVisible.restype = wt.BOOL
u.IsWindowVisible.argtypes = [wt.HWND]
u.GetWindowRect.restype = wt.BOOL
u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u.WindowFromPoint.restype = wt.HWND
u.WindowFromPoint.argtypes = [wt.POINT]
u.GetAncestor.restype = wt.HWND
u.GetAncestor.argtypes = [wt.HWND, wt.UINT]
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


def children(hwnd):
    out = []

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        r = wt.RECT()
        if u.GetWindowRect(h, ctypes.byref(r)):
            out.append((h, c.value, (r.left, r.top, r.right, r.bottom),
                        bool(u.IsWindowVisible(h))))
        return True

    u.EnumChildWindows(hwnd, PROC(cb), 0)
    return out


def ensure_panel():
    if find_panel():
        return True
    sys.path.insert(0, ROOT)
    import fx_winlayer
    msg = fx_winlayer.show_panel_message()
    h = u.FindWindowW(fx_winlayer._CLASS_NAME, None)
    if msg and h:
        u.PostMessageW(h, msg, 0, 0)
    for _ in range(20):
        time.sleep(0.3)
        if find_panel():
            return True
    return False


def probe_twice(pt, label):
    """点两次同一处，返回 (第一次峰值, 第二次峰值) 像素差。

    必须**逐时刻采样取峰值**，不能只抓 0.42s 那一帧：面板在播放瞬间会切一次
    观感（毛玻璃↔纯色），那是整窗重绘；固定单点采样会撞上瞬态、把"特效正常"
    误判成"没有特效"（这个坑踩过一次，一度以为取消抢顶引入了回归）。
    """
    r = 90
    x0, y0 = pt[0] - r, pt[1] - r
    w = h = 2 * r

    def one_click():
        base = grab(x0, y0, w, h).astype(np.int16)
        click_at(pt[0], pt[1])
        peak = 0
        for _ in range(10):              # 10 * 0.2s = 2s，覆盖整个播放窗口
            time.sleep(0.2)
            img = grab(x0, y0, w, h).astype(np.int16)
            n = int((np.abs(img - base).sum(axis=2) > 16).sum())
            if n > peak:
                peak = n
        return peak

    a = one_click()
    time.sleep(2.5)
    b = one_click()
    flag = "" if (a > 300 and b > 300) else "   <== 不一致!"
    print("  %-26s 第一次=%-7d 第二次=%-7d%s" % (label, a, b, flag))
    return a, b


def main():
    print("清理旧实例:", "OK" if kill_existing() else "⚠️")
    log = open(os.path.join(ROOT, "probe_boot.log"), "w", encoding="utf-8")
    subprocess.Popen([PY_EXE, "desktop_fx.py"], cwd=ROOT, stdout=log,
                     stderr=subprocess.STDOUT,
                     creationflags=0x00000008 | 0x00000200)
    for _ in range(24):
        time.sleep(0.5)
        if len([1 for _h, c in fx_windows()
                if c == "DesktopFXOverlayCls"]) >= 2:
            break
    time.sleep(2.0)
    if not ensure_panel():
        print("⚠️ 面板没叫出来")
        return 1
    ph, pr = find_panel()
    print("面板 hwnd=%d rect=%s" % (ph, pr))

    kids = children(ph)
    in_panel = [(h, c, r, v) for h, c, r, v in kids
                if v and r[0] >= pr[0] and r[2] <= pr[2]
                and r[1] >= pr[1] and r[3] <= pr[3]]
    print("面板内子窗口 %d 个" % len(in_panel))

    # 挑代表点：每种 Win32 类名取第一个（避开侧栏：x > left+175）
    picked = {}
    for h, c, r, v in sorted(in_panel, key=lambda x: -(x[2][2] - x[2][0])):
        cx, cy = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
        if cx < pr[0] + 175 or r[2] - r[0] < 8 or r[3] - r[1] < 8:
            continue
        key = c
        if key in picked:
            continue
        # 只测标题栏（可选参数 like=TkChild）：分维排查"第二次没特效"
        # 是不是"前面点过别的控件"造成的连锁
        only = os.environ.get("SCAN_ONLY", "").strip()
        if only and key != only:
            continue
        # 确认这个点命中的是它自己
        if u.WindowFromPoint(wt.POINT(cx, cy)) != h:
            continue
        picked[key] = (cx, cy, r)
    print("选中的代表点：")
    for c, (cx, cy, r) in sorted(picked.items()):
        print("   %-22s (%d,%d) rect=%s" % (c, cx, cy, r))

    print("\n逐类点两次：")
    results = []
    for c, (cx, cy, r) in sorted(picked.items()):
        results.append((c,) + probe_twice((cx, cy), c))
        time.sleep(0.6)
    print("\n=== 汇总 ===")
    bad = [x for x in results if not (x[1] > 300 and x[2] > 300)]
    for c, a, b in results:
        mark = "" if (a > 300 and b > 300) else "  <== 复现了问题"
        print("  %-22s 第一次=%-7d 第二次=%-7d%s" % (c, a, b, mark))
    print("\n有不一致的类别：%d 种" % len(bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())