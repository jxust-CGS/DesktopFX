# -*- coding: utf-8 -*-
"""在**同一个前台进程**里：拉起 DesktopFX -> 探测 -> 收尾。

为什么需要这个：DesktopFX 用 pythonw 跑时 stdout/异常都不可见，而它的
单实例互斥体又让"先 kill 再 start"极易踩空（旧实例窗口没销毁，新实例启动
即被拒，于是"改了代码但行为没变"——这个坑踩过两次）。把生命周期收进本
脚本，探测的一定是本脚本拉起的那个实例。

用法：
    python tools/diag/probe_all.py          # 全量探测（含 WorkBuddy）
    python tools/diag/probe_all.py panel    # 只探面板上层显示
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
u.GetWindowRect.restype = wt.BOOL
u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u.GetWindow.restype = wt.HWND
u.GetWindow.argtypes = [wt.HWND, wt.UINT]
GW_HWNDPREV = 3
PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def fx_windows():
    out = []

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        if "DesktopFX" in c.value:
            t = ctypes.create_unicode_buffer(200)
            u.GetWindowTextW(h, t, 200)
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            out.append((h, c.value, t.value, pid.value, bool(u.IsWindowVisible(h))))
        return True

    u.EnumWindows(PROC(cb), 0)
    return out


def kill_existing():
    """杀掉所有 DesktopFX 实例（含持有窗口但 taskkill 无效的）。"""
    pids = {w[3] for w in fx_windows()}
    for pid in sorted(pids):
        subprocess.run(["taskkill", "//F", "//PID", str(pid)],
                       capture_output=True, text=True, errors="replace")
    time.sleep(1.5)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wt.HANDLE
    k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k.TerminateProcess.restype = wt.BOOL
    k.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    for pid in sorted(pids):
        h = k.OpenProcess(0x0001, False, pid)
        if h:
            k.TerminateProcess(h, 1)
            k.CloseHandle(h)
    for _ in range(15):
        time.sleep(0.4)
        if not fx_windows():
            return True
    return False


def spawn():
    """后台拉起 DesktopFX（stdout 落盘，便于事后看异常）。"""
    for stale in ("diag.log",):
        p = os.path.join(ROOT, stale)
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    log = open(os.path.join(ROOT, "probe_boot.log"), "w", encoding="utf-8")
    p = subprocess.Popen([PY_EXE, "desktop_fx.py"], cwd=ROOT,
                         stdout=log, stderr=subprocess.STDOUT,
                         creationflags=0x00000008 | 0x00000200)
    return p


def read_diag():
    p = os.path.join(ROOT, "diag.log")
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return f.read().strip()


def wait_ready(timeout=12.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        ovs = [w for w in fx_windows() if w[1] == "DesktopFXOverlayCls"]
        if len(ovs) >= 2:
            return ovs
        time.sleep(0.5)
    return []


def read_boot_log():
    p = os.path.join(ROOT, "probe_boot.log")
    if not os.path.exists(p):
        return "(无)"
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return f.read().strip()


def ensure_panel(timeout=8.0):
    """确保控制面板处于可见状态。

    面板启动后是 withdraw 的（要等用户双击托盘或托盘右键"显示面板"）。
    探测面板点击前必须先把它叫出来 —— 否则 _panel_hit 里 ui_hwnd 还是 0，
    放行/抑制的分支根本不会走，测出来的结论毫无意义。
    借项目自己的跨进程消息（fx_winlayer.SHOW_PANEL_MSG_NAME），
    与用户双击托盘图标走的是同一条路径。
    """
    if _panel_visible():
        return True
    sys.path.insert(0, ROOT)
    try:
        import fx_winlayer
        msg = fx_winlayer.show_panel_message()
        hwnd = u.FindWindowW(fx_winlayer._CLASS_NAME, None)
        if msg and hwnd:
            u.PostMessageW(hwnd, msg, 0, 0)
    except Exception as exc:
        print("唤出面板失败:", exc)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _panel_visible():
            return True
        time.sleep(0.3)
    return False


def _panel_visible():
    for h, c, t, r, v in fx_windows():
        if c == "TkTopLevel" and "桌面特效" in t and v:
            return True
    return False


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else "all"
    print("清理旧实例:", "OK" if kill_existing() else "⚠️ 仍有残留")
    proc = spawn()
    ovs = wait_ready()
    if len(ovs) < 2:
        print("启动失败，覆盖层不足。boot log:")
        print(read_boot_log())
        return 1
    print("新实例 pid=%d，覆盖层 %s" % (
        proc.pid, [(w[0], w[4]) for w in ovs]))
    time.sleep(2.0)
    need_panel = want in ("all", "panel")
    if need_panel:
        print("确保面板可见:", "OK" if ensure_panel() else "⚠️ 面板没叫出来")
    try:
        rc = 0
        sys.path.insert(0, os.path.join(ROOT, "tools", "diag"))
        import clickprobe
        import raisesink
        if want in ("all", "panel"):
            print("\n--- 面板空白点击：逐时刻时间线 ---")
            import paneltimeline
            rc |= paneltimeline.main()
        if want in ("all", "wb"):
            print("\n--- WorkBuddy 窗口内点击 ---")
            pt = clickprobe.target_workbuddy()
            if pt:
                clickprobe.probe("WorkBuddy", pt[0], pt[1])
        if want in ("all", "desktop"):
            print("\n--- 桌面空白点击（基线）---")
            pt = clickprobe.target_desktop()
            clickprobe.probe("桌面", pt[0], pt[1])
    finally:
        d = read_diag()
        print("\n--- diag.log（fx_scene 记录的真实点击判定）---")
        print(d if d else "(空 —— 一次点击都没被探测到)")
        print("\nboot log 尾部:")
        print(read_boot_log()[-600:])
    return rc


if __name__ == "__main__":
    sys.exit(main())