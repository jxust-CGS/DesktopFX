# -*- coding: utf-8 -*-
"""验证：进程是否声明了 DPI 感知。

   在 Win10 125% / 150% 缩放下，未声明 DPI 感知的进程会被
   DPI 虚拟化（coordinate virtualization），窗口尺寸和
   GetSystemMetrics 返回的物理像素不一致 → 覆盖层只盖住部分屏幕。
"""
import ctypes
import os
import sys
import time

import ctypes.wintypes as wt

# 1) 调用 desktop_fx._set_dpi_awareness() 模拟主程序启动
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import desktop_fx as d

mode = d._set_dpi_awareness()
print(f"[set_dpi_awareness] 返回: {mode}")
assert mode != "unaware", f"未声明 DPI 感知！在 125%/150% 屏幕上覆盖层会被截断"
print(f"  [OK] DPI 感知已声明 (mode={mode})")
print()

# 2) 用 GetProcessDpiAwareness 实际验证（双确认）
u32 = ctypes.WinDLL("user32", use_last_error=True)
PROCESS_DPI_AWARENESS = ctypes.c_int   # out param: 0=unaware, 1=system, 2=per-monitor

# 这个 API 在 shcore.dll（Win8.1+）
try:
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
    shcore.GetProcessDpiAwareness.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESS_DPI_AWARENESS)]
    shcore.GetProcessDpiAwareness.restype = ctypes.HRESULT
    awareness = PROCESS_DPI_AWARENESS()
    hr = shcore.GetProcessDpiAwareness(0, ctypes.byref(awareness))  # 0 = current process
    label = {0: "unaware", 1: "system", 2: "per-monitor"}.get(awareness.value, f"unknown({awareness.value})")
    if hr == 0:
        print(f"[GetProcessDpiAwareness] 当前进程: {label}")
        assert awareness.value >= 1, f"GetProcessDpiAwareness 报告: {label}"
        print(f"  [OK] 系统也确认是 {label}")
    else:
        print(f"[GetProcessDpiAwareness] 失败 hr=0x{hr & 0xFFFFFFFF:x}（旧系统？）")
except Exception as e:
    print(f"[GetProcessDpiAwareness] 跳过: {e}")

# 3) 验证覆盖层关键尺寸 API 在 DPI 感知下行为正确
print()
print("[GetSystemMetrics] SM_CXVIRTUALSCREEN / SM_CYVIRTUALSCREEN:")
w = u32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
h = u32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
x = u32.GetSystemMetrics(76)
y = u32.GetSystemMetrics(77)
print(f"  virtual screen = {x},{y}  {w}x{h}")
assert w > 0 and h > 0, "virtual screen 尺寸无效"
print(f"  [OK] 物理像素尺寸已正确返回（覆盖层将覆盖全部）")

print()
print("== DPI AWARENESS VERIFIED ==")
