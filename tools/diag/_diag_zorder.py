# -*- coding: utf-8 -*-
"""盯住覆盖层与控制面板在 Z 序里的相对位置，看两者是否在周期性互压。

背景：同步进来的新代码给覆盖层加了 0.5s 一次的 keep_on_top()（SetWindowPos
HWND_TOPMOST），而控制面板本来就有 2s 一次的 _raise_top()。两者都是置顶窗口，
可能互相把对方压下去；每次 Z 序翻转都可能触发 DWM 重新合成 —— 这正是"隔几秒
闪一下"的典型成因。这里以 30ms 间隔轮询 Z 序，记录翻转时刻与间隔。

用法：
    python _diag_zorder.py [秒数]
"""
import ctypes
import sys
import time
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)

GW_HWNDNEXT = 2


def find_windows():
    found = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        cls = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(hwnd, cls, 256)
        if cls.value == "DesktopFXOverlayCls":
            found["overlay"] = hwnd
        elif cls.value == "TkTopLevel":
            txt = ctypes.create_unicode_buffer(256)
            u32.GetWindowTextW(hwnd, txt, 256)
            if txt.value == "桌面特效":
                found["panel"] = hwnd
        return True

    u32.EnumWindows(cb, 0)
    return found


def zmap():
    """返回 {hwnd: 名} 的 Z 序（从顶到底）。"""
    seq = []
    h = u32.GetTopWindow(None)
    guard = 0
    while h and guard < 4000:
        seq.append(h)
        h = u32.GetWindow(h, GW_HWNDNEXT)
        guard += 1
    return seq


def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
    ws = find_windows()
    if "overlay" not in ws:
        print("没找到覆盖层窗口")
        return
    print("overlay hwnd=%s  panel hwnd=%s"
          % (ws.get("overlay"), ws.get("panel", "未找到（面板可能已关闭）")))

    ov, pn = ws["overlay"], ws.get("panel")
    t0 = time.perf_counter()
    last_rel = None
    events = []
    last_fg = None
    fg_events = []
    while time.perf_counter() - t0 < dur:
        t = time.perf_counter() - t0
        seq = zmap()
        try:
            io = seq.index(ov)
        except ValueError:
            io = -1
        ip = seq.index(pn) if pn else -1
        if pn:
            rel = "overlay-above" if io < ip else "panel-above"
            rel = rel if (io >= 0 and ip >= 0) else "n/a"
        else:
            rel = "overlay-idx=%d" % io
        if rel != last_rel:
            events.append((t, rel))
            last_rel = rel
        fg = u32.GetForegroundWindow()
        if fg != last_fg:
            fg_events.append((t, fg))
            last_fg = fg
        time.sleep(0.03)

    print("\n=== Z 序变化 ===")
    for t, r in events:
        print("  t=%.2fs  -> %s" % (t, r))
    if len(events) >= 3:
        gaps = [events[i + 1][0] - events[i][0] for i in range(len(events) - 1)]
        print("  翻转间隔: %s" % " ".join("%.2f" % g for g in gaps))
        print("  间隔中位 %.2fs  （共 %d 次翻转 / %.0fs）"
              % (sorted(gaps)[len(gaps) // 2], len(events), dur))
    print("\n=== 前台窗口变化 ===")
    for t, f in fg_events:
        print("  t=%.2fs  hwnd=%s" % (t, f))
    print("  共 %d 次" % len(fg_events))


if __name__ == "__main__":
    main()
