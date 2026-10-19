# -*- coding: utf-8 -*-
"""查 DesktopFX 覆盖层与指定窗口的**真实 z 序关系**（谁在谁上面）。

为什么要专门写这个：EnumWindows 的遍历结果里 WS_EX_TRANSPARENT 的
layered 窗口不好直接按"看起来的位置"下结论，而且 `GetTopWindow` 链在
多显示器 + 隐藏窗口混合时容易读错。本脚本只回答一个问题：
    ov_ms（全屏之上那层鼠标特效层）在 target 窗口的上面还是下面？

判据：沿 GW_HWNDPREV 从 target 往上走，能碰到 ov_ms 就说明 ov 在 target
之上；反之在下面。（GW_HWNDPREV = 取 z 序中"在我上面"的窗口）

用法：python tools/diag/zprobe.py "WorkBuddy"
"""
import ctypes
import sys
from ctypes import wintypes as wt

u = ctypes.WinDLL("user32", use_last_error=True)
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
u.GetWindowLongW.restype = wt.LONG
u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
GW_HWNDPREV = 3
PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def info(h):
    c = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(h, c, 256)
    t = ctypes.create_unicode_buffer(200)
    u.GetWindowTextW(h, t, 200)
    r = wt.RECT()
    u.GetWindowRect(h, ctypes.byref(r))
    ex = u.GetWindowLongW(h, -20)
    return (c.value, t.value, (r.left, r.top, r.right, r.bottom),
            bool(ex & 0x8), bool(ex & 0x20))


def find(pred):
    out = []

    def cb(h, _):
        if not u.IsWindowVisible(h):
            return True
        if pred(*info(h)[:2]):
            out.append(h)
        return True

    u.EnumWindows(PROC(cb), 0)
    return out


def above_chain(target, depth=30):
    """从 target 往 z 序上方走，返回 [hwnd,...]"""
    out = []
    cur = u.GetWindow(target, GW_HWNDPREV)
    for _ in range(depth):
        if not cur:
            break
        out.append(cur)
        cur = u.GetWindow(cur, GW_HWNDPREV)
    return out


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else "WorkBuddy"
    ovs = find(lambda c, t: c == "DesktopFXOverlayCls")
    panel = find(lambda c, t: c == "TkTopLevel" and "桌面特效" in t)
    tgt = find(lambda c, t: want in t and "DesktopFXOverlayCls" not in c
               and "TkTopLevel" not in c)
    if not ovs:
        print("没有 DesktopFX 覆盖层（程序没跑？）")
        return 1
    # 鼠标层 = 两个覆盖层里 z 序更上的那个（渲染循环保证 ms 在 fs 之上）
    def zrank(h):
        ch, cur, n = [], h, 0
        top = u.GetTopWindow(None)
        while top and n < 60:
            if top == h:
                return n
            top = u.GetWindow(top, 2)
            n += 1
        return 99
    ovs.sort(key=zrank)
    ov_ms, ov_fs = ovs[0], ovs[1] if len(ovs) > 1 else ovs[0]
    print("鼠标层 ov_ms hwnd=%d  全屏层 ov_fs hwnd=%d" % (ov_ms, ov_fs))
    if panel:
        print("控制面板 hwnd=%d" % panel[0])

    if not tgt:
        print("找不到目标窗口: %s" % want)
        return 1
    for th in tgt[:2]:
        cls, title, rect, top, trans = info(th)
        chain = above_chain(th)
        print("\n目标 hwnd=%d cls=%s title=%r rect=%s top=%s"
              % (th, cls, title, rect, top))
        print("  它上面共 %d 个窗口（含隐藏），前 6 个：" % len(chain))
        for h in chain[:6]:
            c, t, r, tp, tr = info(h)
            tag = " <== ov_ms" if h == ov_ms else (
                " <== ov_fs" if h == ov_fs else (
                    " <== 面板" if panel and h == panel[0] else ""))
            print("     %-9d %-22s top=%-5s %s%s" % (h, c[:22], tp, t[:20], tag))
        print("  结论：ov_ms %s %s" % (
            "在目标之上" if ov_ms in chain else "在目标之下",
            "（特效能显示在上面）" if ov_ms in chain else
            "（特效会被目标窗口盖住！）"))
        if panel and panel[0] in chain:
            print("  面板也在目标之上")
    return 0


if __name__ == "__main__":
    sys.exit(main())