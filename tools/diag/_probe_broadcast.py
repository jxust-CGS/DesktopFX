# -*- coding: utf-8 -*-
"""实测：message-only 窗口（父 = HWND_MESSAGE）能否收到 HWND_BROADCAST 广播。

MSDN 说 message-only 窗口「does not receive broadcast messages」。
如果属实，托盘宿主就不能用 HWND_MESSAGE，必须改成隐藏的顶层窗口，
否则 TaskbarCreated 永远收不到 —— 资源管理器重启后托盘图标就回不来了。

注意：窗口属于创建它的线程，所以每个窗口必须**在自己的线程里创建**，
否则消息泵（GetMessageW 带 NULL hwnd）收不到它。
"""
import ctypes
import ctypes.wintypes as wt
import threading
import time

import os
import sys

# 本脚本住在 tools/ 子目录里：`sys.path[0]` 与 `__file__` 的父目录都不再是项目根。
# 向上找到含 desktop_fx.py 的目录（= 项目根）当基准，import 与输出路径才成立。
ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from _win32 import user32, kernel32, HWND, UINT, WPARAM, LPARAM, LRESULT

HWND_MESSAGE = HWND(-3)
HWND_BROADCAST = HWND(0xFFFF)
SMTO_ABORTIFHUNG = 0x0002

HWND_MESSAGE_PY = -3

received = {}
ready = {}
_procs = []


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p)]


def spawn(cls_name, parent, target_msg):
    """在自己的线程里建窗口 + 跑消息泵。返回 (thread, hwnd_holder)。"""
    holder = {}

    def body():
        def proc(hwnd, msg, wparam, lparam):
            received.setdefault(cls_name, []).append(msg)
            if msg == 0x0002:            # WM_DESTROY
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        cb = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)(proc)
        _procs.append(cb)                # 防止被 GC

        w = WNDCLASSW()
        w.lpfnWndProc = ctypes.cast(cb, ctypes.c_void_p)
        w.hInstance = kernel32.GetModuleHandleW(None)
        w.lpszClassName = cls_name
        user32.RegisterClassW(ctypes.byref(w))

        hwnd = user32.CreateWindowExW(0, cls_name, cls_name, 0, 0, 0, 0, 0,
                                      parent, None,
                                      kernel32.GetModuleHandleW(None), None)
        holder["hwnd"] = hwnd
        holder["target"] = target_msg
        ready[cls_name] = True
        if not hwnd:
            return

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    t = threading.Thread(target=body, name=cls_name, daemon=True)
    t.start()
    return t, holder


def main():
    wm = user32.RegisterWindowMessageW("TaskbarCreated")
    print(f"RegisterWindowMessageW('TaskbarCreated') = 0x{wm:X} ({wm})")
    assert wm, "注册消息失败"
    assert user32.RegisterWindowMessageW("TaskbarCreated") == wm, "注册消息不唯一"
    print("  二次注册返回相同值: OK（系统范围内唯一）")
    print()

    t_a, h_a = spawn("ProbeMsgOnlyCls", HWND(HWND_MESSAGE_PY), wm)
    t_b, h_b = spawn("ProbeTopLevelCls", HWND(0), wm)

    for _ in range(50):
        if h_a.get("hwnd") and h_b.get("hwnd"):
            break
        time.sleep(0.05)

    hwnd_a, hwnd_b = h_a.get("hwnd"), h_b.get("hwnd")
    print(f"message-only 窗口 hwnd = {hwnd_a}")
    print(f"隐藏顶层窗口   hwnd = {hwnd_b}")
    assert hwnd_a and hwnd_b, "窗口创建失败"
    time.sleep(0.4)

    print()
    print("--- 广播 TaskbarCreated (HWND_BROADCAST) ---")
    res = ctypes.c_size_t()
    ok = user32.SendMessageTimeoutW(HWND_BROADCAST, wm, 0, 0,
                                    SMTO_ABORTIFHUNG, 2000, ctypes.byref(res))
    print(f"SendMessageTimeout 返回 {ok}")
    time.sleep(0.8)

    got_a = wm in received.get("ProbeMsgOnlyCls", [])
    got_b = wm in received.get("ProbeTopLevelCls", [])
    print()
    print(f"  message-only 窗口 收到 TaskbarCreated : {got_a}")
    print(f"  隐藏顶层窗口     收到 TaskbarCreated : {got_b}")

    user32.DestroyWindow(hwnd_a)
    user32.DestroyWindow(hwnd_b)
    t_a.join(timeout=2)
    t_b.join(timeout=2)

    print()
    print("=" * 64)
    if got_a:
        print("结论: message-only 窗口【能】收到广播 -> 托盘保持 HWND_MESSAGE 即可")
    else:
        print("结论: message-only 窗口【收不到】广播 -> 托盘必须改成隐藏顶层窗口")
        if got_b:
            print("      隐藏顶层窗口能收到，可作为替代方案")
    print("=" * 64)
    return got_a, got_b


if __name__ == "__main__":
    main()
