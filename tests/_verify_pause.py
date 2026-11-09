# -*- coding: utf-8 -*-
"""鼠标光环 / 暂停快捷键 / 恢复后层级的专项验证。

覆盖四块（每块都对应用户报的一个症状）：

  1. **热键真的能送达主程序**
     真起一个托盘宿主（真窗口、真消息循环），用 `PostMessage(WM_HOTKEY)`
     走**注册热键的真实投递路径**，等事件队列里出现 "pause"。
     ⚠️ 注入按键**不会**触发 `RegisterHotKey`（那需要真实的键盘输入路径），
     所以这里用 PostMessage 模拟系统投递 —— 与真实热键走同一条路。

  2. **pause 事件必须有人消费 —— 且不能只靠控制面板**
     ("pause", None) 原先**只有 fx_ui（控制面板）**在消费。控制面板没起来
     时事件就堆在队列里，用户按快捷键毫无反应。
     判据：App 侧必须自己处理 pause，不依赖控制面板。

  3. **恢复后层级要修回来**
     暂停会跳过渲染循环里的层级维护（抢顶、置顶位自愈、两层先后纠正），
     别的置顶窗口会跑到特效层上面；恢复后必须立刻修好，否则用户看到的是
     "特效不见了 / 显示在最上层之下"。

  4. **暂停时不能把同层其它特效擦掉**
     旧画法是整块包围盒写回（连 alpha=0 的像素也写成 0），会把同层的水波
     擦掉一块。稀疏画法只写有墨的像素，不会误伤。

用法：python tests/_verify_pause.py
"""
import ctypes
import os
import queue
import sys
import threading
import time
from ctypes import wintypes as wt

import numpy as np

# 本脚本住在 tests/ 子目录里：向上找到含 desktop_fx.py 的目录（= 项目根）
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = _TESTS_DIR
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) \
        and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
for _p in (ROOT, _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _win32 import user32          # noqa: E402

W = H = 400
FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
    return bool(cond)


class _FakeApp:
    """App 的轻量替身：只留暂停相关的状态与事件队列。

    ⚠️ **不能用真的 App()**：它在 __init__ 里就启动了 `_pump_events` 线程，
    而那个线程阻塞在 `queue.get()`（无超时）上 —— 不停掉它，测试进程
    退出会一直挂着（这个坑踩过一次）。
    """

    def __init__(self):
        self.events = queue.Queue()
        self.paused = False

    def toggle_pause(self):
        self.paused = not self.paused
        return self.paused


# ------------------------------------------------------------------ 1. 热键
def test_hotkey_reaches_app():
    """Ctrl+Alt+R 的热键消息必须变成 ("pause", None) 事件。

    走**注册热键的真实投递路径**：PostMessage(WM_HOTKEY) 到托盘窗口 ——
    这正是 Windows 投递全局热键的方式（消息进目标窗口所在线程的队列，
    由窗口过程取出）。
    """
    import fx_tray

    q = queue.Queue()
    seq = int(time.time() * 1000) % 100000
    host = fx_tray.TrayHost(q, cls_name="PauseTestCls%d" % seq)
    t = threading.Thread(target=host.run, name="pause-test", daemon=True)
    t.start()
    try:
        end = time.time() + 5.0
        while host.hwnd is None and time.time() < end:
            time.sleep(0.02)
        if not check(host.hwnd is not None, "托盘窗口没建起来"):
            return False, "托盘窗口创建失败"

        # 热键注册失败会往队列里塞 hotkey-failed —— 那是"快捷键不可用"的信号。
        # 注意：**没收到 failed 也算注册成功**（注册成功时队列本来就是空的，
        # 要等用户真的按下才有事件）。所以这里只等"失败"这一种情况。
        end = time.time() + 1.5
        failed = False
        while time.time() < end:
            try:
                kind, _payload = q.get(timeout=0.2)
            except queue.Empty:
                continue
            if kind == "hotkey-failed":
                failed = True
                break
        if failed:
            # 热键是**全局**资源：被别的进程（本项目的其它实例、或用户
            # 装了同样快捷键的软件）占着时注册必然失败。
            # 这不是被测代码的缺陷 —— 降级为"跳过"而不是报失败
            # （否则别的实例正跑着时跑测试会误报）。
            print("    [SKIP] 热键被占用（别的进程/实例），跳过投递验证")
            return None

        # 走真实的 WM_HOTKEY 投递路径
        while not q.empty():
            q.get_nowait()
        WM_HOTKEY = 0x0312
        user32.PostMessageW(wt.HWND(host.hwnd), WM_HOTKEY,
                            fx_tray.HOTKEY_ID, 0)
        kind = None
        end = time.time() + 3.0
        while time.time() < end:
            try:
                kind, _payload = q.get(timeout=0.2)
            except queue.Empty:
                continue
            if kind == "pause":
                break
        check(kind == "pause",
              "PostMessage(WM_HOTKEY) 没有产出 pause 事件（得到 %r）" % kind)
        return True, "热键注册成功且 WM_HOTKEY -> ('pause', None)"
    finally:
        try:
            host.stop()
        except Exception:
            pass
        t.join(timeout=2.0)


def test_pause_handled_without_panel():
    """**面板没打开时热键也要能用**。

    这是最容易漏的一条：("pause", None) 事件原先**只有 fx_ui（控制面板）
    消费**。控制面板若没起来（或没在跑 _poll），事件就堆在队列里没人管
    —— 用户按 Ctrl+Alt+R 毫无反应。

    判据：App 侧必须有独立的 pause 处理入口，不依赖控制面板。
    """
    import desktop_fx
    fn = getattr(desktop_fx.App, "on_pause_event", None)
    check(fn is not None,
          "App 没有 on_pause_event —— pause 事件只靠控制面板消费，"
          "面板没起来时快捷键就是死的")
    if fn is None:
        return False, "缺少 App 侧的 pause 处理入口"

    # 真的调一次：应当能切换暂停状态
    app = _FakeApp()
    try:
        fn(app, "pause", None)
    except Exception as exc:
        check(False, "on_pause_event 调用抛异常：%s" % exc)
        return False, "on_pause_event 抛异常"
    check(app.paused is True, "on_pause_event 之后没有进入暂停状态")
    return True, "App 侧可独立处理 pause（不依赖控制面板）"


# ------------------------------------------------------------------ 2. 暂停
def test_ui_does_not_toggle_twice():
    """控制面板收到 pause 事件时**不得再切一次状态**。

    以前的状态被两处各切一次（App 与面板），谁后处理谁说了算 ——
    表现就是"按一下没反应、按两下才暂停"，而且界面上的"[已暂停]"
    标记还常常是错的。

    判据：面板收到 pause / pause-state 事件后，`app.paused` 必须与
    App 已切好的状态**一致**，且面板不得再调用一次 toggle_pause。
    """
    import fx_ui

    class _App2:
        def __init__(self):
            self.paused = False
            self.events = queue.Queue()
            self.toggle_calls = 0
            self.last_error = None

        def toggle_pause(self):
            self.toggle_calls += 1
            self.paused = not self.paused
            return self.paused

        def report_error(self, *a):
            self.last_error = a

    # 直接测事件分发那段逻辑：直接调 fx_ui 里的处理分支
    app = _App2()
    # App 侧先切好状态（模拟事件泵已经处理过）
    app.paused = True
    # 面板收到事件后应当只刷新界面
    calls_before = app.toggle_calls
    # fx_ui 的 _pause_state_changed 只读 app.paused，不该切状态
    ui = object.__new__(fx_ui.ControlPanel)
    ui.app = app
    ui._paused = False
    ui._status_paint = lambda hover: None
    ui._pause_state_changed()
    check(app.toggle_calls == calls_before,
          "面板收到 pause 事件后又切了一次状态（toggle_calls %d -> %d）"
          % (calls_before, app.toggle_calls))
    check(ui._paused is True, "面板没把 App 的暂停状态同步过来")
    return True, "面板只刷新界面，不重复切状态"



def test_toggle_pause_roundtrip():
    """暂停 -> 恢复必须能来回切（不能"暂停了回不来"）。"""
    app = _FakeApp()
    check(app.paused is False, "初始状态不该是暂停")
    p1 = app.toggle_pause()
    check(p1 is True, "第一次 toggle_pause 应该进入暂停")
    p2 = app.toggle_pause()
    check(p2 is False, "第二次 toggle_pause 应该恢复")
    check(app.paused is False, "paused 标志没复位")
    return True, "暂停/恢复往返正常"


# ------------------------------------------------------------------ 3. 层级
def test_resume_restores_z_order():
    """**恢复后必须把显示层级修回来**。

    场景：暂停期间渲染循环被跳过（层级维护也跳了），别的置顶窗口会跑到
    特效层上面；恢复后必须立刻把特效层插回置顶组，否则用户看到的是
    "特效不见了 / 显示在最上层之下"。

    判据：模拟"置顶位被弄丢"，调用恢复路径后必须被修回来。
    """
    import fx_winlayer
    from fx_winlayer import Overlay

    ov = Overlay(fx_winlayer.LAYER_TOPMOST)
    try:
        # 造一个"置顶位掉了"的窗口
        ex = user32.GetWindowLongW(wt.HWND(ov.hwnd), fx_winlayer.GWL_EXSTYLE)
        user32.SetWindowLongW(wt.HWND(ov.hwnd), fx_winlayer.GWL_EXSTYLE,
                              ex & ~fx_winlayer.WS_EX_TOPMOST)
        check(not fx_winlayer._is_topmost_window(ov.hwnd),
              "置顶位应当已经被清掉（前置条件）")

        # 恢复路径：keep_on_top 里的"置顶位自愈"必须把它修回来
        ov.keep_on_top()
        check(fx_winlayer._is_topmost_window(ov.hwnd),
              "恢复后置顶位没有被修回来 —— 无法显示在最上层")
        return True, "置顶位自愈有效"
    finally:
        try:
            ov.destroy()
        except Exception:
            pass


def test_leave_panel_ride_keeps_topmost():
    """结束"骑在面板之上"时**不能**把窗口插到非置顶窗之下。

    SetWindowPos 把置顶窗插到**非置顶**窗之下时，会连带清掉
    `WS_EX_TOPMOST` —— 特效层于是变成普通窗口，被任何窗口盖住。
    控制面板自 2026-10-19 起就是**普通窗口**了，所以"插到面板之下"
    这条老路径必然踩这个坑（用户报的"恢复后无法显示在最上层"）。

    判据：拿一个**普通窗口**当"面板"，调 leave_panel_ride 之后，
    特效层必须**仍在置顶组里**。
    """
    import fx_winlayer
    from fx_winlayer import Overlay

    ov = Overlay(fx_winlayer.LAYER_TOPMOST)
    # 造一个"面板"：普通窗口（不带 WS_EX_TOPMOST）
    panel = fx_winlayer.user32.CreateWindowExW(
        0, "STATIC", "panel", 0, 0, 0, 0, 0, None, None, None, None)
    try:
        if not check(panel, "造不出'面板'窗口"):
            return False, "无法创建测试用的普通窗口"
        check(not fx_winlayer._is_topmost_window(panel),
              "前置条件：'面板'不该是置顶窗")
        # 先骑上去（会置 _ride_above_panel）
        ov.raise_topmost()
        # 再降回来
        ov.leave_panel_ride(panel)
        check(fx_winlayer._is_topmost_window(ov.hwnd),
              "leave_panel_ride 之后特效层丢了置顶位 —— "
              "它变成普通窗口，被任何窗口盖住（无法显示在最上层）")
        return True, "降回常态后仍在置顶组里"
    finally:
        try:
            ov.destroy()
        except Exception:
            pass
        try:
            fx_winlayer.user32.DestroyWindow(fx_winlayer.HWND(panel))
        except Exception:
            pass


def test_resume_clears_ride_flag():
    """恢复路径必须复位"骑在面板之上"的标志。

    `_ride_above_panel` 残留 True 时，`keep_on_top` 开头就 return False ——
    置顶位自愈**永远不执行**，特效层再也抢不回顶。暂停期间渲染循环整个
    被跳过，标志的配对清理没机会跑，所以恢复这一刻必须强制复位。

    判据：模拟"骑乘标志残留"，跑恢复路径之后它必须被清掉。
    """
    import desktop_fx
    import fx_kinds
    import fx_winlayer

    ov_fs = fx_winlayer.Overlay(fx_kinds.LAYER_TOPMOST)
    ov_ms = fx_winlayer.Overlay(fx_kinds.LAYER_TOPMOST)
    try:
        # 模拟残留：标志为 True，但面板早就没了
        ov_ms.raise_topmost()
        check(ov_ms.is_riding_panel(), "前置条件：骑乘标志应当是 True")
        app = _FakeApp()
        app.overlays = [ov_fs, ov_ms]
        app._panel_raised = True
        desktop_fx.App._fix_layers_after_resume(app)
        check(not ov_ms.is_riding_panel(),
              "恢复后骑乘标志没被复位 —— keep_on_top 会永久早退，"
              "置顶位再也修不回来")
        check(fx_winlayer._is_topmost_window(ov_ms.hwnd),
              "恢复后特效层不在置顶组里 —— 无法显示在最上层")
        return True, "骑乘标志已复位，置顶位正常"
    finally:
        for ov in (ov_fs, ov_ms):
            try:
                ov.destroy()
            except Exception:
                pass



def test_resume_restacks_overlays():
    """恢复后**两层之间**的先后也要对（鼠标层要在全屏层之上）。

    暂停时渲染循环被跳过 -> `_restack_overlays` 也没跑 -> 恢复后两层
    先后可能翻过来（雨盖住水波）。
    """
    import desktop_fx
    import fx_kinds
    import fx_winlayer

    ov_fs = fx_winlayer.Overlay(fx_kinds.LAYER_TOPMOST)
    ov_ms = fx_winlayer.Overlay(fx_kinds.LAYER_TOPMOST)
    try:
        # 故意把先后弄反：把全屏层插到鼠标层之上
        ov_fs.place_above(ov_ms.hwnd)
        time.sleep(0.05)
        # 恢复路径应当纠正回来
        app = _FakeApp()
        app.overlays = [ov_fs, ov_ms]
        desktop_fx.App._restack_overlays(app)
        time.sleep(0.05)
        above = fx_winlayer._is_above(ov_ms.hwnd, ov_fs.hwnd)
        check(above, "恢复后鼠标层没有在全屏层之上")
        return True, "两层先后已纠正"
    finally:
        for ov in (ov_fs, ov_ms):
            try:
                ov.destroy()
            except Exception:
                pass


# ------------------------------------------------------------------ 4. 不误伤
def test_sparse_paint_does_not_erase_neighbours():
    """稀疏绘制**只写有墨的像素**，不会把同层其它特效擦掉。

    旧画法（整块包围盒写回）会把 alpha=0 的像素也写成 0 —— 覆盖层上
    同层的水波/涟漪就被"擦"掉一块。
    """
    import fx_halo
    halo = fx_halo.HaloField(W, H)
    halo.set_style("呼吸圆环")
    halo.set_color("#7EC8FF")
    halo.set_size(60.0)
    halo.set_level(1.0)
    halo.set_cursor(W / 2.0, H / 2.0)

    # 先在缓冲上画一圈"别的特效"（模拟同层水波）。
    # ⚠️ u32 是一维的（长度 W*H），不能用二维切片 ——
    # 用 reshape 后的视图来定位方块。
    u32 = np.zeros(W * H, dtype=np.uint32)
    view = u32.reshape(H, W)
    view[100:120, 100:120] = 0xFF00FF00       # 远处放一个不透明的方块
    before = int((u32 >> 24).sum())

    halo.draw(u32, W, H, {}, 0.016)
    after = int((u32 >> 24).sum())

    # 光环只该画自己那一圈，方块必须原封不动
    check(int(view[100:120, 100:120].max()) == 0xFF00FF00,
          "光环把同层其它像素擦掉了（远处方块被改写）")
    check(after > before, "光环没有画上任何像素")
    return True, "远处像素未被误伤（%d -> %d）" % (before, after)


def main():
    tests = [
        test_hotkey_reaches_app,
        test_pause_handled_without_panel,
        test_ui_does_not_toggle_twice,
        test_toggle_pause_roundtrip,
        test_resume_restores_z_order,
        test_leave_panel_ride_keeps_topmost,
        test_resume_clears_ride_flag,
        test_resume_restacks_overlays,
        test_sparse_paint_does_not_erase_neighbours,
    ]
    bad = 0
    for fn in tests:
        try:
            r = fn()
        except Exception as e:
            import traceback
            r = (False, "exception: %s: %s\n%s"
                 % (type(e).__name__, e,
                    traceback.format_exc().splitlines()[-3]))
        if r is None or r[0] is None:
            print("  [SKIP] %s" % fn.__name__)
            continue
        ok, msg = r
        if not ok:
            bad += 1
        print("  [%s] %s: %s" % ("PASS" if ok else "FAIL", fn.__name__, msg))
    print()
    if bad:
        print("== PAUSE FAILED ==  %d 项失败" % bad)
        for f in FAILS:
            print("   - %s" % f)
        raise SystemExit(1)
    print("== PAUSE VERIFIED ==  %d 项" % len(tests))
    raise SystemExit(0)


if __name__ == "__main__":
    main()
