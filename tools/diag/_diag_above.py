# -*- coding: utf-8 -*-
"""只读体检：把「谁压在我的覆盖层之上」逐行摊开，用程序自己的判据算一遍。

排查"特效被遮住 / 不能保持在最上层"这类问题的第一站 —— 比盯着屏幕猜快得多。
对每个 `DesktopFXOverlayCls` 窗口，沿 GW_HWNDPREV 向上列出所有压着它的窗口，
标注：类名 / 标题 / 尺寸 / 可见 / 是否置顶 / **程序是否会把它是为遮挡物**。

再直接把 hwnd 塞进一个不 `create()` 的 Overlay 里，调它的 `_covered()` /
`_chain_sig()`，看运行中的实例此刻会得出什么结论。

**只读，不改动任何东西。** 需要程序正在运行。

判读要点：
  * `_covered() = True` 却一直没恢复 -> 看插在链上第一个真遮挡物与**控制面板**
    的相对位置：面板若在对手之下，抢顶就会被面板封顶（见 SKILL 的 §8）。
  * 覆盖层 `置顶=False` -> 它的 `WS_EX_TOPMOST` 被吃掉了（多半是曾经把锚点插到
    非置顶窗之后），此时任何普通窗口都能盖住它。

用法：python _diag_above.py
"""
import ctypes
import ctypes.wintypes as wt

import os
import sys

# 本脚本住在 tools/ 子目录里：`sys.path[0]` 与 `__file__` 的父目录都不再是项目根。
# 向上找到含 desktop_fx.py 的目录（= 项目根）当基准，import 与输出路径才成立。
ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import fx_winlayer as fw
from _win32 import user32, RECT

GW_HWNDPREV = 3


def cls_of(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def txt_of(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(h, b, 256)
    return b.value


def rect_of(h):
    r = RECT()
    if user32.GetWindowRect(h, ctypes.byref(r)):
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    return (0, 0, 0, 0)


def to_topmost(h):
    return bool(user32.GetWindowLongW(h, -20) & 0x8)


def find_overlays():
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, lp):
        if cls_of(h) == "DesktopFXOverlayCls":
            out.append(h)
        return True

    user32.EnumWindows(cb, 0)
    return out


def find_panel():
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, lp):
        if cls_of(h) in ("TkTopLevel", "TkChild") and txt_of(h) == "桌面特效":
            found.append(h)
        return True

    user32.EnumWindows(cb, 0)
    return found


def main():
    ovs = find_overlays()
    panels = find_panel()
    ph = panels[0] if panels else 0
    print(f"覆盖层 {len(ovs)} 个，面板 hwnd={ph}（置顶={to_topmost(ph) if ph else '-'}）")
    if not ovs:
        return

    for i, oh in enumerate(ovs):
        probe = fw.Overlay()
        probe.hwnd = oh
        probe.panel_hwnd = ph or None
        print(f"\n================ 覆盖层 #{i} hwnd={oh} ================")
        print(f"  _covered()   = {probe._covered()}")
        print(f"  _chain_sig() = {probe._chain_sig()}")
        print("  --- 它之上的窗口（GW_HWNDPREV 顺序）---")
        h = user32.GetWindow(oh, GW_HWNDPREV)
        n = 0
        while h and n < 40:
            x, y, w, hh = rect_of(h)
            occ = probe._is_occluder(h)
            mine = cls_of(h) == fw._CLASS_NAME
            mark = "  <== 我自己的另一层" if mine else (
                "  <== 判为遮挡物" if occ else "")
            print(f"   #{n:<3} {h:<10} 类={cls_of(h):<28} 标题={txt_of(h)[:28]!r:<30} "
                  f"{w}x{hh} 可见={bool(user32.IsWindowVisible(h))} "
                  f"置顶={to_topmost(h)}{mark}")
            h = user32.GetWindow(h, GW_HWNDPREV)
            n += 1


if __name__ == "__main__":
    main()
