# -*- coding: utf-8 -*-
"""检查运行中的控制面板当前到底是不是**置顶窗**（WS_EX_TOPMOST）。

背景：取消面板的周期性抢顶后，`Overlay._anchor` 的第一道关
`_is_topmost_window(panel_hwnd)` 可能不再通过 —— 那样每次抢顶都退回
HWND_TOPMOST 锚点，鼠标层会被插到**置顶组最顶**，而面板也被挤到它下面，
"面板浮在特效之上"的常态就反了；反过来若锚点判过、插入面板之下，
点击特效的提升又会被下一帧抢顶打回。本脚本直接读样式位定性。

用法：python tools/diag/panel_topmost_bit.py
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes as wt

ROOT = __file__.rsplit("tools", 1)[0].rstrip("/\\")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
PY_EXE = r"C:\Users\hspcadmin\.pyenv\pyenv-win\versions\3.11.9\python.exe"
WS_EX_TOPMOST = 0x00000008

u = ctypes.WinDLL("user32", use_last_error=True)
u.GetClassNameW.restype = ctypes.c_int
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowTextW.restype = ctypes.c_int
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.IsWindowVisible.restype = wt.BOOL
u.IsWindowVisible.argtypes = [wt.HWND]
u.GetWindowLongW.restype = wt.LONG
u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
u.GetWindow.restype = wt.HWND
u.GetWindow.argtypes = [wt.HWND, wt.UINT]
GW_HWNDPREV = 3
PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


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


def ensure_panel():
    if find("TkTopLevel", "桌面特效"):
        return True
    sys.path.insert(0, ROOT)
    import fx_winlayer
    msg = fx_winlayer.show_panel_message()
    h = u.FindWindowW(fx_winlayer._CLASS_NAME, None)
    if msg and h:
        u.PostMessageW(h, msg, 0, 0)
    for _ in range(20):
        time.sleep(0.3)
        if find("TkTopLevel", "桌面特效"):
            return True
    return False


def above_count(target, depth=40):
    n, cur = 0, u.GetWindow(target, GW_HWNDPREV)
    while cur and n < depth:
        n += 1
        cur = u.GetWindow(cur, GW_HWNDPREV)
    return n


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
    time.sleep(2.0)
    ensure_panel()
    ph = find("TkTopLevel", "桌面特效")[0]
    ovs = [h for h, c, _p in fx_windows() if c == "DesktopFXOverlayCls"]

    def ex(h):
        return u.GetWindowLongW(h, -20)

    def cls(h):
        b = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, b, 256)
        return b.value

    print("\n窗口扩展样式（GWL_EXSTYLE，WS_EX_TOPMOST=0x8）：")
    print("  面板   hwnd=%-10d ex=0x%08x topmost=%s 上方窗口数=%d"
          % (ph, ex(ph) & 0xFFFFFFFF, bool(ex(ph) & WS_EX_TOPMOST),
             above_count(ph)))
    for h in ovs:
        print("  覆盖层 hwnd=%-10d ex=0x%08x topmost=%s 上方窗口数=%d  %s"
              % (h, ex(h) & 0xFFFFFFFF, bool(ex(h) & WS_EX_TOPMOST),
                 above_count(h), cls(h)))
    # 观察 4 秒，看覆盖层的 topmost 位会不会掉、位置会不会被反复拉回
    print("\n观察 4 秒（覆盖层 topmost 位 + 面板上方窗口数）：")
    for i in range(8):
        time.sleep(0.5)
        print("   面板上方=%-3d | 覆盖层: %s"
              % (above_count(ph),
                 "  ".join("%s topmost=%s above=%d"
                           % (h, bool(ex(h) & WS_EX_TOPMOST), above_count(h))
                           for h in ovs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())