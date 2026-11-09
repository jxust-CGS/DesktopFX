# -*- coding: utf-8 -*-
"""诊断：当前屏幕是不是锁着（决定屏幕抓屏类验证有没有意义）。

⚠️ **Windows 11 的新锁屏不是 LogonUI** —— 前台窗口标题是
"Windows 默认锁屏界面"，类名 `Windows.UI.Core.CoreWindow`。
`FindWindowW('LogonUI', None)` 在 Win11 上**找不到**（那个类属于旧锁屏），
用它判断会得出"没锁"的错误结论 —— 我因此把整整一轮真机验证的结论
全判反了（以为"恢复后屏幕全黑"是产品 bug，其实是锁屏）。

锁屏时普通窗口一律不可见，BitBlt 抓到的只是锁屏壁纸 —— 所以任何
"屏幕上有没有特效"的验证在锁屏下都**无效**。

用法：python tools/diag/screen_state.py
"""
import ctypes
import sys
from ctypes import wintypes as wt

u = ctypes.WinDLL("user32")
u.GetForegroundWindow.restype = wt.HWND
u.GetForegroundWindow.argtypes = []
u.GetClassNameW.restype = ctypes.c_int
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowTextW.restype = ctypes.c_int
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.FindWindowW.restype = wt.HWND
u.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]


def is_locked():
    """屏幕是否锁着。"""
    # 1) Win11 新锁屏：前台窗口就是锁屏界面
    fg = u.GetForegroundWindow()
    if fg:
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(fg, cls, 256)
        txt = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(fg, txt, 256)
        if "锁屏" in txt.value or "Windows.UI.Core.CoreWindow" in cls.value:
            return True, "前台是锁屏界面（%s / %s）" % (cls.value, txt.value)
    # 2) 旧锁屏（Win10 及更早）：LogonUI 存在
    if u.FindWindowW("LogonUI", None):
        return True, "存在 LogonUI（旧版锁屏）"
    # 3) Win11 的锁屏也可能以 CoreWindow 出现在别处
    h = u.FindWindowW("Windows.UI.Core.CoreWindow", None)
    if h:
        return True, "存在 Windows.UI.Core.CoreWindow"
    return False, "未锁定"


def main():
    locked, why = is_locked()
    print("屏幕状态: %s" % ("🔒 已锁定" if locked else "🔓 未锁定"))
    print("  依据: %s" % why)
    if locked:
        print()
        print("⚠️ 锁屏时普通窗口一律不可见，BitBlt 抓到的只是锁屏壁纸。")
        print("   任何「屏幕上有没有特效」的验证在锁屏下都**无效** ——")
        print("   必须先解锁再测，否则会把结论判反。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
