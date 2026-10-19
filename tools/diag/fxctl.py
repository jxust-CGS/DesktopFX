# -*- coding: utf-8 -*-
"""启动/停止 DesktopFX，完全脱离本脚本的进程树。

为什么不用 `pythonw desktop_fx.py` 直接跑：那样 pythonw 会成为 bash 的
子进程，任务被 stop 时 pythonw 往往还活着 —— 单实例互斥体仍在，新的
实例启动即被拒，于是"重启加载新代码"实际没发生，诊断读到的还是旧代码
的运行结果（这个坑踩过一次）。

用法：
    python tools/diag/fxctl.py start     # 启动，返回新 pid
    python tools/diag/fxctl.py stop      # 停掉当前实例
    python tools/diag/fxctl.py restart   # 重启
    python tools/diag/fxctl.py status    # 看当前实例与代码时间
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes as wt

ROOT = __file__.rsplit("tools", 1)[0].rstrip("/\\")
PYW = r"C:\Users\hspcadmin\.pyenv\pyenv-win\versions\3.11.9\pythonw.exe"
DETACHED = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def fx_windows():
    """当前存在的 DesktopFX 顶层窗口 -> [(hwnd, cls, title, pid)]"""
    out = []

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, c, 256)
        if "DesktopFX" not in c.value:
            return True
        t = ctypes.create_unicode_buffer(200)
        user32.GetWindowTextW(h, t, 200)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        out.append((h, c.value, t.value, pid.value))
        return True
    user32.EnumWindows(PROC(cb), 0)
    return out


def pids():
    return {w[3] for w in fx_windows()}


def kill_pids(target):
    killed = []
    for pid in sorted(target):
        h = subprocess.run(["taskkill", "//F", "//PID", str(pid)],
                           capture_output=True, text=True, errors="replace")
        if h.returncode == 0:
            killed.append(pid)
    time.sleep(1.5)
    return killed


def wait_gone(target, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not (pids() & target):
            return True
        time.sleep(0.4)
    return False


def start():
    for f in ("diag.log",):
        p = os.path.join(ROOT, f)
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    proc = subprocess.Popen([PYW, "desktop_fx.py"], cwd=ROOT,
                            creationflags=DETACHED)
    time.sleep(3.0)
    wins = fx_windows()
    pids_live = {w[3] for w in wins}
    print("启动返回 pid=%d；当前 DesktopFX 窗口:" % proc.pid)
    for h, cls, title, pid in wins:
        print("   hwnd=%-9d cls=%-22s pid=%-7d %s" % (h, cls, pid, title))
    if not pids_live:
        print("⚠️ 没看到窗口 —— 可能单实例互斥体未释放，或启动失败（看 error.log）")
        return 1
    if proc.pid not in pids_live:
        print("⚠️ 新进程没撑住（pid %d 不在窗口 pid 集合 %s）—— "
              "大概率是旧实例还活着挡住了" % (proc.pid, sorted(pids_live)))
        return 2
    print("OK：新实例 pid=%d 已持有窗口" % proc.pid)
    return 0


def stop():
    live = pids()
    if not live:
        print("没有运行中的 DesktopFX 实例")
        return 0
    print("当前实例 pid:", sorted(live))
    killed = kill_pids(live)
    if wait_gone(live):
        print("已停止 pid=%s，窗口全部消失" % killed)
        return 0
    print("⚠️ 停止后仍有窗口：", fx_windows())
    return 1


def status():
    wins = fx_windows()
    print("DesktopFX 窗口数:", len(wins))
    for h, cls, title, pid in wins:
        print("   hwnd=%-9d cls=%-22s pid=%-7d %s" % (h, cls, pid, title))
    for f in ("desktop_fx.py", "fx_scene.py", "fx_ui.py", "fx_winlayer.py"):
        p = os.path.join(ROOT, f)
        print("%-16s 最后修改 %s" % (f, time.strftime(
            "%m-%d %H:%M:%S", time.localtime(os.path.getmtime(p)))))
    return 0


def restart():
    rc = stop()
    if rc:
        return rc
    return start()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"start": start, "stop": stop, "restart": restart,
     "status": status}[cmd]()
    sys.exit(0)