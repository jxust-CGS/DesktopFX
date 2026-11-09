# -*- coding: utf-8 -*-
"""真机验证：Ctrl+Alt+R 能不能真的暂停 / 恢复（可靠判据版）。

上一版的问题：抓屏区域 (560,360)-(960,760) 正好**包含控制面板**（浅色
设置窗口），判据"亮像素数"会被面板干扰 —— 暂停时覆盖层清空、面板还在，
数字的变化说不清是谁造成的。

这一版：
  - 先把控制面板**藏起来**（托盘里的面板隐藏态就是用户平时的状态），
    抓屏区域只剩桌面 + 特效；
  - 每一档都**存 PNG**，能直接看画面，不用猜数字；
  - 顺便记录全屏层与鼠标层的缓冲统计（诊断开关）。

用法：python tools/diag/pause_live.py
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
import screen_state  # noqa: E402

PY_EXE = r"C:\Users\hspcadmin\.pyenv\pyenv-win\versions\3.11.9\python.exe"
SHOTS = os.path.join(ROOT, "..", "ui-shots")

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
u.ShowWindow.restype = wt.BOOL
u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
u.GetClassNameW.restype = ctypes.c_int
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.EnumWindows.restype = wt.BOOL
u.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM),
                          wt.LPARAM]

VK_CONTROL = 0x11
VK_MENU = 0x12
VK_R = 0x52
KEYEVENTF_KEYUP = 0x0002
SW_HIDE = 0


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


def press_hotkey():
    for vk in (VK_CONTROL, VK_MENU, VK_R):
        u.keybd_event(vk, 0, 0, 0)
        time.sleep(0.05)
    for vk in (VK_R, VK_MENU, VK_CONTROL):
        u.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)


def hide_panels():
    """把控制面板藏起来 —— 用户平时就是这样用的。"""
    hidden = []
    PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        if c.value == "TkTopLevel":
            u.ShowWindow(wt.HWND(h), SW_HIDE)
            hidden.append(h)
        return True

    u.EnumWindows(PROC(cb), 0)
    return hidden


def shot(tag, x0, y0, w, h):
    img = grab(x0, y0, w, h)
    os.makedirs(SHOTS, exist_ok=True)
    try:
        from PIL import Image
        Image.fromarray(img).save(os.path.join(SHOTS, "pause_%s.png" % tag))
    except Exception:
        pass
    bright = int((img.max(axis=2) > 90).sum())
    print("  [%s] 亮像素 = %-7d 均值 = %.1f  -> ui-shots/pause_%s.png"
          % (tag, bright, float(img.mean()), tag))
    return bright


def main():
    _awake.keep_awake()

    # ⚠️ **锁屏时这个验证毫无意义**：普通窗口一律不可见，抓到的只是
    # 锁屏壁纸 —— 会把「锁屏」误判成「特效没显示」。先查，锁了就退出。
    locked, why = screen_state.is_locked()
    if locked:
        print("❌ 屏幕处于锁定状态（%s）" % why)
        print("   锁屏下抓屏只能抓到锁屏壁纸，无法判断特效有没有显示。")
        print("   请先解锁再跑这个验证。")
        return 2

    log = open(os.path.join(ROOT, "pause_live.log"), "w", encoding="utf-8")
    env = dict(os.environ)
    env["DESKTOPFX_DIAG_RESUME"] = "1"
    proc = subprocess.Popen([PY_EXE, "desktop_fx.py"], cwd=ROOT, stdout=log,
                            stderr=subprocess.STDOUT, env=env,
                            creationflags=0x00000008 | 0x00000200)
    try:
        print("等待实例启动…")
        time.sleep(6.0)
        panels = hide_panels()
        print("已隐藏控制面板 %d 个" % len(panels))
        time.sleep(1.0)

        # 光标放到主屏靠左下的空桌面区（避开可能的其它窗口）
        cx, cy = 300, 300
        u.SetCursorPos(cx, cy)
        time.sleep(1.5)

        x0, y0, w, h = cx - 180, cy - 180, 360, 360
        a = shot("before", x0, y0, w, h)

        print("\n按 Ctrl+Alt+R（应暂停）…")
        press_hotkey()
        time.sleep(2.0)
        b = shot("paused", x0, y0, w, h)

        print("\n再按 Ctrl+Alt+R（应恢复）…")
        press_hotkey()
        time.sleep(2.5)
        c = shot("resumed", x0, y0, w, h)

        print("\n=== 判定 ===")
        ok = True
        if b >= a * 0.9:
            print("  ❌ 暂停后画面几乎没有变化 —— 快捷键没生效")
            ok = False
        if c <= b * 1.1:
            print("  ❌ 恢复后画面没有回来 —— 恢复失效")
            ok = False
        print("  %s" % ("✅ 暂停与恢复都生效"
                        if ok else "❌ 暂停/恢复有问题"))
        return 0 if ok else 1
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
