# -*- coding: utf-8 -*-
"""验证「控制面板已不再周期性抢顶」。

判据：连续观察若干秒，面板 hwnd 在 z 序里的**位置不再每 1 秒被顶一次**。
做法：每 0.25s 读一次"面板上方有多少个窗口"（沿 GW_HWNDPREV 走），
抢顶发生时这个值会在某一帧突然归零（面板被插到最顶）。取消后它应当
只做**微小波动**（别人抢顶时它被压下去几层），不再周期性归零。

同时对照：特效层的提升时长（`paneltimeline` 的思路）应变得更稳定 ——
面板不再抢顶，`keep_riding_panel` 的补提升次数会明显下降。

用法：python tools/diag/noraise_check.py
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

u = ctypes.WinDLL("user32", use_last_error=True)
u.GetClassNameW.restype = ctypes.c_int
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowTextW.restype = ctypes.c_int
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.IsWindowVisible.restype = wt.BOOL
u.IsWindowVisible.argtypes = [wt.HWND]
u.GetWindow.restype = wt.HWND
u.GetWindow.argtypes = [wt.HWND, wt.UINT]
GW_HWNDPREV = 3
PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def hwnd_int(h):
    return int(h) if h else 0


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
    """target 之上有多少个窗口。抢顶成功时这个值会掉到 0。"""
    n, cur = 0, u.GetWindow(target, GW_HWNDPREV)
    while cur and n < depth:
        n += 1
        cur = u.GetWindow(cur, GW_HWNDPREV)
    return n


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
    if not ensure_panel():
        print("⚠️ 面板没叫出来")
        return 1
    panels = find("TkTopLevel", "桌面特效")
    ph = panels[0]
    print("面板 hwnd=%d，开始观察 6 秒（0.25s 一次）\n" % ph)

    series = []
    t0 = time.time()
    for i in range(24):
        time.sleep(0.25)
        c = above_count(ph)
        series.append((round(time.time() - t0, 2), c))
        bar = "#" * c
        print("  t=%4.2fs  上方窗口数=%-3d %s" % (time.time() - t0, c, bar))

    vals = [c for _t, c in series]
    zeros = sum(1 for v in vals if v == 0)
    print("\n统计：最小=%d 最大=%d 归零次数=%d/%d"
          % (min(vals), max(vals), zeros, len(vals)))
    if zeros == 0:
        print("结论: 通过 —— 面板从未被顶到最顶（上方始终有窗口）")
        print("  说明周期性抢顶已移除：否则每~1s 会看到一次 0")
        return 0
    print("结论: 仍在抢顶 —— 6 秒内归零 %d 次（约每 %.1fs 一次）"
          % (zeros, 6.0 / max(zeros, 1)))
    return 1


if __name__ == "__main__":
    sys.exit(main())