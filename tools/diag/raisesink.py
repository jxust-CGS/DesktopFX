# -*- coding: utf-8 -*-
"""面板点击特效的**层级生命周期**检测：点击面板空白后，鼠标特效层应当
临时骑到面板之上（文字/表情才看得见），播完再降回面板之下。

判据：沿 GW_HWNDPREV 从面板往上走，能不能碰到鼠标层 ov_ms。
能 = 面板之上（特效显示在上层，符合用户诉求），不能 = 之下（被面板盖住）。

作为模块被 probe_all 导入，也可直接跑：
    python tools/diag/raisesink.py
"""
import ctypes
import sys
import time
from ctypes import wintypes as wt

sys.path.insert(0, __file__.rsplit("tools", 1)[0])
import _win32  # noqa: E402

u = _win32.user32
u.GetWindow.restype = wt.HWND
u.GetWindow.argtypes = [wt.HWND, wt.UINT]
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
GW_HWNDPREV = 3
GW_HWNDNEXT = 2
PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def wins():
    out = []

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        t = ctypes.create_unicode_buffer(200)
        u.GetWindowTextW(h, t, 200)
        r = wt.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        out.append((h, c.value, t.value,
                    (r.left, r.top, r.right, r.bottom),
                    bool(u.IsWindowVisible(h))))
        return True

    u.EnumWindows(PROC(cb), 0)
    return out


def above(target, depth=40):
    out, cur = [], u.GetWindow(target, GW_HWNDPREV)
    for _ in range(depth):
        if not cur:
            break
        out.append(cur)
        cur = u.GetWindow(cur, GW_HWNDPREV)
    return out


def zrank(h):
    top, n = u.GetTopWindow(None), 0
    while top and n < 80:
        if top == h:
            return n
        top = u.GetWindow(top, GW_HWNDNEXT)
        n += 1
    return 99


def pick_layers():
    ovs = [(h, c, t, r, v) for h, c, t, r, v in wins()
           if c == "DesktopFXOverlayCls"]
    if len(ovs) < 2:
        return None, None
    ovs.sort(key=lambda w: zrank(w[0]))
    return ovs[0][0], ovs[1][0]        # (ov_ms 更上的, ov_fs)


def panel_hwnd():
    for h, c, t, r, v in wins():
        if c == "TkTopLevel" and "桌面特效" in t:
            return h, r
    return None, None


def panel_blank_point(ph, pr):
    """面板上一块"大 Frame 空白"（非交互区）：命中自身、面积大、避开侧栏。"""
    for gy in range(pr[1] + 70, pr[3] - 30, 13):
        for gx in range(pr[0] + 180, pr[2] - 30, 13):
            h = u.WindowFromPoint(wt.POINT(gx, gy))
            if not h:
                continue
            r = wt.RECT()
            if not u.GetWindowRect(h, ctypes.byref(r)):
                continue
            if (r.right - r.left) * (r.bottom - r.top) < 60000:
                continue
            if u.WindowFromPoint(wt.POINT(gx, gy)) != h:
                continue
            return gx, gy
    return None


def click_at(x, y, hold=0.06):
    """模拟一次左键点击。hold 不能省 —— DesktopFX 靠逐帧轮询
    GetAsyncKeyState 的**边沿**置 clicked，按下+松开连发会整帧错过（踩过）。"""
    class _MI(ctypes.Structure):
        _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                    ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                    ("dwExtraInfo", ctypes.c_void_p)]

    class _II(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("mi", _MI)]

    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    nx, ny = int(x * 65535 / max(sw - 1, 1)), int(y * 65535 / max(sh - 1, 1))

    def send(flags):
        arr = (_II * 1)()
        arr[0].type = 0
        arr[0].mi.dx, arr[0].mi.dy, arr[0].mi.dwFlags = nx, ny, flags
        u.SendInput(1, arr, ctypes.sizeof(_II))

    send(0x0001 | 0x8000)
    time.sleep(0.03)
    send(0x0002)
    time.sleep(hold)
    send(0x0004)


def main():
    ov_ms, _ov_fs = pick_layers()
    ph, pr = panel_hwnd()
    if not ov_ms:
        print("没有 DesktopFX 覆盖层（程序没跑？）")
        return 1
    if not ph:
        print("控制面板没开（先双击托盘图标把面板叫出来）")
        return 1
    pt = panel_blank_point(ph, pr)
    if not pt:
        print("面板上找不到大 Frame 空白点")
        return 1
    print("面板 hwnd=%d rect=%s；ov_ms=%d" % (ph, pr, ov_ms))

    def where():
        return "面板之上" if ov_ms in above(ph) else "面板之下"

    print("点击前:", where())
    click_at(pt[0], pt[1])
    samples = []
    for delay in (0.35, 0.5, 0.5, 0.5, 1.0, 2.0):
        time.sleep(delay)
        w = where()
        samples.append(w)
        print("点击后 %4.1fs: %s" % (sum([0.35, 0.5, 0.5, 0.5, 1.0, 2.0]
                                        [:len(samples)]), w))
    raised = "面板之上" in samples
    sunk = samples[-1] == "面板之下"
    if raised and sunk:
        print("\n结论: 通过 —— 播放期间骑在面板之上（特效显示在上层），"
              "播完降回面板之下")
        return 0
    if raised and not sunk:
        print("\n结论: 部分通过 —— 提升了，但播完没降回（会一直盖着面板）")
        return 1
    print("\n结论: 失败 —— ov_ms 始终在面板之下，点击文字/表情被面板盖住")
    return 1


if __name__ == "__main__":
    sys.exit(main())