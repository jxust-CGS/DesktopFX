# -*- coding: utf-8 -*-
"""一条命令内完成：启动实例 -> 调出面板 -> 查覆盖层 z 序 -> 抓屏 -> 结束。

**为什么必须写在一条命令里**：从 Bash 启动的进程会在命令结束时被沙箱的
job object 杀掉（观察到的现象：fxctl 启动的实例几分钟后就没了）。所以
"启动"和"观察"必须发生在同一个进程内，否则观察到的永远是"实例没了"。

用法：python tools/diag/one_shot.py
"""
import ctypes
import json
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
u.GetWindowTextW.restype = ctypes.c_int
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.IsWindowVisible.restype = wt.BOOL
u.IsWindowVisible.argtypes = [wt.HWND]
u.GetParent.restype = wt.HWND
u.GetParent.argtypes = [wt.HWND]
u.GetWindowLongW.restype = wt.LONG
u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
u.GetWindowRect.restype = wt.BOOL
u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u.SetCursorPos.restype = wt.BOOL
u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
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
PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_VISIBLE = 0x10000000


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
    return np.frombuffer(bytes(buf), dtype=np.uint8).reshape(h, w, 4)[
        :, :, :3].copy()


def windows():
    rows = []

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        t = ctypes.create_unicode_buffer(200)
        u.GetWindowTextW(h, t, 200)
        r = wt.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        st = u.GetWindowLongW(h, GWL_STYLE)
        ex = u.GetWindowLongW(h, GWL_EXSTYLE)
        rows.append((h, c.value, t.value,
                     (r.left, r.top, r.right, r.bottom),
                     bool(u.IsWindowVisible(h)), u.GetParent(h),
                     bool(st & WS_VISIBLE), ex & 0xFFFFFFFF))
        return True

    u.EnumWindows(PROC(cb), 0)
    return rows


def main():
    _awake.keep_awake()
    # 配置：只留光环
    from _paths import atomic_write_json
    cfg_path = os.path.join(ROOT, "config.json")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # 样式与幅度可由命令行指定：python one_shot.py [样式] [幅度] [大小]
    style = sys.argv[1] if len(sys.argv) > 1 else "彩虹光环"
    swing = float(sys.argv[2]) if len(sys.argv) > 2 else 0.55
    size = float(sys.argv[3]) if len(sys.argv) > 3 else 70.0
    cfg.update({"halo_enabled": True, "halo_style": style,
                "halo_swing": swing,
                "halo_size": size,
                "rain_enabled": False, "snow_enabled": False,
                "petal_enabled": False, "meteor_enabled": False,
                "spotlight_enabled": False, "paint_enabled": False,
                "wave_enabled": False, "win_ripple_enabled": False,
                "ripple_enabled": False, "pet_enabled": False})
    atomic_write_json(cfg_path, cfg)

    log = open(os.path.join(ROOT, "probe_boot.log"), "w", encoding="utf-8")
    proc = subprocess.Popen([PY_EXE, "desktop_fx.py"], cwd=ROOT, stdout=log,
                            stderr=subprocess.STDOUT,
                            creationflags=0x00000008 | 0x00000200)
    time.sleep(4.0)
    u.SetCursorPos(700, 500)
    time.sleep(2.5)

    rows = windows()
    print("=== z 序最前面的 16 个窗口（EnumWindows = 从顶到底）===")
    for i, (h, c, t, r, v, p, vis, ex) in enumerate(rows[:16]):
        mark = ""
        if "DesktopFX" in c:
            mark = "   <== DesktopFX"
        print("  [%2d] hwnd=%-9d %-24s vis=%-5s ex=%08x rect=%-26s %s%s"
              % (i, h, c[:24], v, ex, str(r), t[:20], mark))
    print("  ... 共 %d 个窗口" % len(rows))

    img = grab(560, 360, 320, 320)
    from PIL import Image
    Image.fromarray(img).save(os.path.join(ROOT, "one_shot.png"))
    print("\n近景抓屏 (560,360)-(880,640) -> one_shot.png  最亮=%d 均值=%.1f"
          % (int(img.max()), float(img.mean())))
    # 再抓整块主屏 —— 光环万一被放到别处（全屏对比能一眼看出）
    full = grab(0, 0, 1920, 1200)
    Image.fromarray(full).save(os.path.join(ROOT, "one_shot_full.png"))
    print("整屏抓屏 -> one_shot_full.png")
    # 找出画面里"既亮又与壁纸不同"的区域
    ys, xs = np.nonzero(full.max(axis=2) > 200)
    if len(xs):
        print("高亮像素 %d 个，bbox=(%d,%d,%d,%d)"
              % (len(xs), xs.min(), ys.min(), xs.max(), ys.max()))
    else:
        print("整屏没有 >200 的亮像素")
    return 0


if __name__ == "__main__":
    sys.exit(main())
