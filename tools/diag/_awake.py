# -*- coding: utf-8 -*-
"""保持屏幕唤醒（诊断脚本共用）。

**为什么需要**：长时间无人操作时显示器会休眠，此时 BitBlt 抓屏**全黑**
（实测整屏最亮=0、均值=0），探测脚本会得出"没有特效"的假结论 ——
曾因此误判光环没画出来。

两件事一起做：
  1. `SetThreadExecutionState(ES_CONTINUOUS|ES_DISPLAY_REQUIRED|ES_SYSTEM_REQUIRED)`
     —— 声明"有程序在用屏幕"，系统不休眠；
  2. 一次极小的鼠标移动 —— 当作输入事件把已经休眠的屏幕唤醒。

用：`import _awake; _awake.keep_awake()`
"""
import ctypes
from ctypes import wintypes as wt

u = ctypes.WinDLL("user32", use_last_error=True)
u.SendInput.restype = wt.UINT
u.SendInput.argtypes = [wt.UINT, ctypes.c_void_p, ctypes.c_int]

_ES_CONTINUOUS = 0x80000000
_ES_DISPLAY_REQUIRED = 0x00000002
_ES_SYSTEM_REQUIRED = 0x00000001


def keep_awake():
    """声明屏幕常亮并唤醒（幂等，可重复调用）。"""
    try:
        u.SetThreadExecutionState.restype = wt.DWORD
        u.SetThreadExecutionState.argtypes = [wt.DWORD]
        u.SetThreadExecutionState(_ES_CONTINUOUS | _ES_DISPLAY_REQUIRED
                                  | _ES_SYSTEM_REQUIRED)
    except Exception:
        pass

    class _MI(ctypes.Structure):
        _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                    ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                    ("dwExtraInfo", ctypes.c_void_p)]

    class _II(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("mi", _MI)]

    arr = (_II * 1)()
    arr[0].type = 0
    arr[0].mi.dx, arr[0].mi.dy = 1, 0
    arr[0].mi.dwFlags = 0x0001          # MOUSEEVENTF_MOVE
    try:
        u.SendInput(1, ctypes.byref(arr), ctypes.sizeof(_II))
    except Exception:
        pass
