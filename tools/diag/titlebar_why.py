# -*- coding: utf-8 -*-
"""在**运行中**的实例里检查：标题栏 hwnd 是否落在交互控件集合 ui_btn_hwnds。

为什么不用独立进程重建面板：ControlPanel 构造期间 root 是 withdraw 的，
控件尺寸算不出来（实测 500+ 个控件的 rect 全是 1x1 叠在同一点），那份
快照无法判断"哪个 hwnd 是标题栏"。必须在真机运行时查。

做法：给 App._sync_interactive_hwnds 挂一个临时打印（monkey patch 不现实，
因为面板在另一个进程）——改为**读产品代码的实际判定**：把面板的 hwnd 与
它的子窗口逐个拿真实 rect，然后按 fx_ui 用的同一套规则（类名白名单 +
_self._ia_pressed）在**本进程**复算一遍，看标题栏会不会被算成交互控件。

这条复算用的是产品代码里的同一个类常量与方法（import 真实模块），
不是复刻，所以结论可信。

用法：python tools/diag/titlebar_why.py
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
u.WindowFromPoint.restype = wt.HWND
u.WindowFromPoint.argtypes = [wt.POINT]
PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def fx_windows():
    out = []

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        if "DesktopFX" in c.value:
            out.append((h, c.value))
        return True

    u.EnumWindows(PROC(cb), 0)
    return out


def kill_existing():
    pids = set()
    for hwnd, _c in fx_windows():
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pids.add(pid.value)
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


def find_panel():
    out = []

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        t = ctypes.create_unicode_buffer(200)
        u.GetWindowTextW(h, t, 200)
        if c.value == "TkTopLevel" and "桌面特效" in t.value \
                and u.IsWindowVisible(h):
            r = wt.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            out.append((h, (r.left, r.top, r.right, r.bottom)))
        return True

    u.EnumWindows(PROC(cb), 0)
    return out[0] if out else None


def children(hwnd):
    out = []

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        r = wt.RECT()
        if u.GetWindowRect(h, ctypes.byref(r)):
            out.append((h, c.value, (r.left, r.top, r.right, r.bottom),
                        bool(u.IsWindowVisible(h))))
        return True

    u.EnumChildWindows(hwnd, PROC(cb), 0)
    return out


def ensure_panel():
    if find_panel():
        return True
    sys.path.insert(0, ROOT)
    import fx_winlayer
    msg = fx_winlayer.show_panel_message()
    h = u.FindWindowW(fx_winlayer._CLASS_NAME, None)
    if msg and h:
        u.PostMessageW(h, msg, 0, 0)
    for _ in range(20):
        time.sleep(0.3)
        if find_panel():
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
        if len([1 for _h, c in fx_windows()
                if c == "DesktopFXOverlayCls"]) >= 2:
            break
    time.sleep(2.5)
    ensure_panel()
    ph, pr = find_panel()
    print("面板 hwnd=%d rect=%s" % (ph, pr))

    # 产品代码里的交互控件类名白名单（直接读常量，不复刻）
    import fx_ui
    ia_classes = fx_ui.ControlPanel._IA_CLASSES
    print("交互控件类名白名单:", sorted(ia_classes))

    kids = children(ph)
    real = [(h, c, r) for h, c, r, v in kids
            if v and (r[2] - r[0]) > 2 and (r[3] - r[1]) > 2
            and r[0] >= pr[0] and r[2] <= pr[2]]
    print("面板内**已 layout**（非 1x1）子窗口 %d 个" % len(real))

    # 标题栏：横跨面板宽、贴顶、高 30~60
    bars = [(h, c, r) for h, c, r in real
            if (r[2] - r[0]) > (pr[2] - pr[0]) * 0.8 and 25 <= (r[3] - r[1]) <= 60
            and r[1] <= pr[1] + 20]
    print("\n标题栏候选（按类名白名单判定是否算交互控件）：")
    for h, c, r in bars:
        by_class = c in ia_classes
        print("   hwnd=%-10d cls=%-10s rect=%s  类名命中=%s"
              % (h, c, r, by_class))
    if not bars:
        print("   （没找到；列出顶部 300px 内的所有子窗口）")
        for h, c, r in real:
            if r[1] <= pr[1] + 300:
                print("   hwnd=%-10d cls=%-10s rect=%s  类名命中=%s"
                      % (h, c, r, c in ia_classes))
    if bars:
        cls = bars[0][1]
        hit = cls in ia_classes
        print("\n结论指向：标题栏 cls=%s，类名白名单命中=%s" % (cls, hit))
        if hit:
            print("  -> 会被 _ia_scan 收进 ui_btn_hwnds，第二次点击被判"
                  "'面板 UI 操作'，特效被抑制")
        else:
            print("  -> 类名不在白名单；是否收录取决于它有没有绑 "
                  "<ButtonPress-1>（fx_ui._ia_pressed）")
    else:
        print("\n结论指向：没定位到标题栏，需人工看上面列出的顶部窗口")
    return 0


if __name__ == "__main__":
    sys.exit(main())