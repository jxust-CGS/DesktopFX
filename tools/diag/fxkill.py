# -*- coding: utf-8 -*-
"""强制终止仍持有窗口的 DesktopFX 实例进程。

为什么需要它：`taskkill //F //PID` 对 pythonw 报"成功"但窗口常常还在
（进程处于 pythonw 的无控制台消息泵里，收不到强杀广播），于是单实例
互斥体一直被占着，新实例启动即被拒 —— 表现为"改了代码但行为没变"。
这里直接 OpenProcess + TerminateProcess，并确认窗口真的消失。

用法：python tools/diag/fxkill.py [pid ...]     # 不给 pid 就杀所有 DesktopFX 实例
"""
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes as wt

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)
PROCESS_TERMINATE = 0x0001
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.TerminateProcess.restype = wt.BOOL
k32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
u32.GetClassNameW.restype = ctypes.c_int
u32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def fx_pids():
    out = {}

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(h, c, 256)
        if "DesktopFX" in c.value:
            pid = wt.DWORD()
            u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            out[pid.value] = True
        return True

    u32.EnumWindows(PROC(cb), 0)
    return set(out)


def terminate(pid):
    h = k32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not h:
        return False
    try:
        return bool(k32.TerminateProcess(h, 1))
    finally:
        k32.CloseHandle(h)


def main():
    targets = set(int(a) for a in sys.argv[1:] if a.isdigit())
    if not targets:
        targets = fx_pids()
    if not targets:
        print("没有运行中的 DesktopFX 实例")
        return 0
    print("目标 pid:", sorted(targets))
    ok = []
    for pid in sorted(targets):
        # 先 taskkill（走正常的退出路径，能让配置落盘），再补刀
        subprocess.run(["taskkill", "//PID", str(pid)],
                       capture_output=True, text=True, errors="replace")
    time.sleep(1.2)
    for pid in sorted(targets):
        if terminate(pid):
            ok.append(pid)
    print("TerminateProcess 成功:", ok)
    for _ in range(20):
        time.sleep(0.4)
        left = fx_pids()
        if not (left & targets):
            break
    left = fx_pids()
    if left:
        print("⚠️ 仍有窗口 pid:", sorted(left))
        return 1
    print("OK：所有 DesktopFX 实例已退出，窗口已销毁（互斥体已释放）")
    return 0


if __name__ == "__main__":
    sys.exit(main())