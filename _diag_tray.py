# -*- coding: utf-8 -*-
"""托盘图标诊断（请用**带控制台**的 python 运行，不要用 pythonw）：

    python _diag_tray.py

用途：程序已经在跑的情况下，确认托盘图标到底挂上没有、挂在哪里。
一步一步打印结论，任何一步 FAIL 都直接指明原因。
"""
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _win32 import user32  # noqa: E402

import fx_tray  # noqa: E402

APP_DIR = os.path.dirname(os.path.abspath(__file__))


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("hWnd", ctypes.c_void_p),
                ("uID", ctypes.c_uint), ("guidItem", ctypes.c_ubyte * 16)]


def step(n, title):
    print(f"\n[{n}] {title}")


def main():
    print("=" * 60)
    print("托盘图标诊断")
    print("=" * 60)

    # 1. 图标文件
    step(1, "图标文件是否存在")
    ico = os.path.join(APP_DIR, "tray_icon.ico")
    png = os.path.join(APP_DIR, "tray_icon.png")
    for p in (ico, png):
        if os.path.exists(p):
            print(f"    正常 {os.path.basename(p)}  {os.path.getsize(p)} 字节")
        else:
            print(f"    提示 {os.path.basename(p)} 不存在（非必需，ico 优先）")
    if not os.path.exists(ico):
        print("    失败 缺少 tray_icon.ico —— 托盘会没有图标")
        return 1

    # 2. 图标能否加载成 HICON
    step(2, "图标能否加载为 HICON")
    h = fx_tray._load_ico_icon()
    if h:
        print(f"    正常 _load_ico_icon -> {h}")
    else:
        print("    失败 _load_ico_icon 返回空")
    h2 = fx_tray.build_icon(32)
    print(f"    {'正常' if h2 else '失败'} build_icon(32) -> {h2}")
    if not h2:
        return 1

    # 3. 程序是否在运行（托盘窗口是否存在）
    step(3, "程序是否在运行")
    hwnd = user32.FindWindowW(fx_tray.TrayHost.CLS_NAME, None)
    if not hwnd:
        print("    失败 找不到托盘窗口 —— 程序没在运行。")
        print("         请先双击 启动桌面特效.bat，再跑本脚本。")
        return 1
    print(f"    正常 托盘窗口 hwnd = 0x{hwnd:X}")

    # 4. 图标是否真的挂在托盘上
    step(4, "图标是否真的挂在系统托盘里")
    sh = ctypes.WinDLL("shell32", use_last_error=True)
    sh.Shell_NotifyIconGetRect.argtypes = [ctypes.POINTER(NOTIFYICONIDENTIFIER),
                                           ctypes.POINTER(RECT)]
    sh.Shell_NotifyIconGetRect.restype = ctypes.HRESULT
    nid = NOTIFYICONIDENTIFIER()
    nid.cbSize = ctypes.sizeof(NOTIFYICONIDENTIFIER)
    nid.hWnd = hwnd
    nid.uID = 1

    hr, rc = -1, RECT()
    for _ in range(20):
        rc = RECT()
        try:
            hr = sh.Shell_NotifyIconGetRect(ctypes.byref(nid), ctypes.byref(rc))
        except OSError as exc:
            hr = exc.winerror
        if hr == 0:
            break
        time.sleep(0.2)

    if hr != 0:
        print(f"    失败 查不到图标位置 HRESULT=0x{hr & 0xFFFFFFFF:08X}")
        print("         （0x80004005 通常表示图标尚未注册）")
        return 1

    w, hh = rc.right - rc.left, rc.bottom - rc.top
    print(f"    正常 图标矩形 ({rc.left},{rc.top})-({rc.right},{rc.bottom})  {w}x{hh}")
    if w <= 0 or hh <= 0:
        print("    注意 图标尺寸为 0 —— 很可能被 Windows 折叠进了托盘溢出区（^）")
        print("         打开溢出区找找，或在 设置 > 任务栏 里让它常驻显示。")
        return 0
    print("    图标已显示在托盘中。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
