# -*- coding: utf-8 -*-
"""托盘图标健壮性验证。

核心场景：explorer.exe 重启（崩溃 / Windows 更新 / 用户手动重启 / 开机自启
时本进程早于任务栏创建）会广播 TaskbarCreated。不重新挂载，托盘图标就永久
消失 —— 程序还在跑、雨还在下，但用户既打不开面板也没法退出。

这里有一极易踩的坑：托盘宿主若用 message-only 窗口（父窗口 = HWND_MESSAGE），
则**根本收不到 HWND_BROADCAST 广播**（实测见 _probe_broadcast.py），
TaskbarCreated 处理逻辑写得再对也是死代码。所以本脚本第一条就卡这个。
"""
import ctypes
import os
import queue
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _win32 import user32, HWND, UINT, WPARAM, LPARAM  # noqa: E402

HWND_MESSAGE = -3
HWND_BROADCAST = 0xFFFF
SMTO_ABORTIFHUNG = 0x0002

NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETVERSION = 0x00, 0x01, 0x02, 0x04


# ------------------------------------------------------------------ 工具
class Recorder:
    """顶替 fx_tray.shell32，记录 Shell_NotifyIconW 调用（不碰真实托盘）。"""

    def __init__(self, fail_first=0):
        self.ops = []
        self.fail_first = fail_first
        self._lock = threading.Lock()

    def Shell_NotifyIconW(self, op, nid):
        with self._lock:
            self.ops.append(op)
            n = len(self.ops)
        return 0 if n <= self.fail_first else 1

    def count(self, op):
        with self._lock:
            return self.ops.count(op)

    def snapshot(self):
        with self._lock:
            return list(self.ops)


def wait_for(pred, timeout=5.0, interval=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return False


def broadcast(wm, timeout_ms=2000):
    res = ctypes.c_size_t()
    return user32.SendMessageTimeoutW(HWND(HWND_BROADCAST), wm, 0, 0,
                                      SMTO_ABORTIFHUNG, timeout_ms,
                                      ctypes.byref(res))


_seq = [0]


def start_tray(fail_first=0):
    """起一个托盘宿主（shell32 已被录音器顶替）。返回上下文 dict。

    每个实例用独立的窗口类名：RegisterClassExW 是进程级的，同名注册第二次
    会失败但 CreateWindowExW 照样成功，导致新窗口沿用第一个实例的窗口过程，
    消息被别的实例截走 —— 测试之间会互相污染。
    """
    import fx_tray
    _seq[0] += 1
    rec = Recorder(fail_first=fail_first)
    saved = fx_tray.shell32
    fx_tray.shell32 = rec
    q = queue.Queue()
    host = fx_tray.TrayHost(q, cls_name=f"TrayTestCls{_seq[0]}")
    t = threading.Thread(target=host.run, name=f"tray-test{_seq[0]}", daemon=True)
    t.start()
    ready = wait_for(lambda: host.hwnd is not None)
    return {"mod": fx_tray, "saved": saved, "rec": rec, "q": q,
            "host": host, "thread": t, "ready": ready}


def stop_tray(ctx):
    ctx["host"].stop()
    ctx["thread"].join(timeout=3)
    ctx["mod"].shell32 = ctx["saved"]


# ------------------------------------------------------------------ 测试
def test_taskbar_created_message():
    print("== test_taskbar_created_message ==")
    wm = user32.RegisterWindowMessageW("TaskbarCreated")
    print(f"  TaskbarCreated = 0x{wm:X} ({wm})")
    assert wm, "注册 TaskbarCreated 失败"
    again = user32.RegisterWindowMessageW("TaskbarCreated")
    assert again == wm, f"注册消息不唯一: 0x{wm:X} vs 0x{again:X}"
    print("  [OK] 系统范围内唯一，重复注册返回同一值")


def test_window_receives_broadcast():
    """托盘窗口不能是 message-only 窗口，否则收不到广播。"""
    print("== test_window_receives_broadcast ==")
    ctx = start_tray()
    try:
        assert ctx["ready"], "托盘窗口创建失败"
        hwnd = ctx["host"].hwnd
        assert user32.IsWindow(hwnd), "托盘窗口句柄无效"

        parent = user32.GetParent(hwnd)
        is_msg_only = (parent is not None and int(parent) == HWND_MESSAGE)
        print(f"  hwnd={hwnd}  GetParent={parent}  message-only={is_msg_only}")
        assert not is_msg_only, (
            "托盘窗口是 message-only 窗口，收不到 HWND_BROADCAST，"
            "TaskbarCreated 处理会是死代码")

        # 更硬的判据：真的广播一次，看有没有被处理（ops 会增加）
        before = len(ctx["rec"].snapshot())
        broadcast(ctx["host"].wm_taskbar_created)
        grew = wait_for(lambda: len(ctx["rec"].snapshot()) > before, timeout=3.0)
        after = len(ctx["rec"].snapshot())
        print(f"  广播前 ops={before} -> 广播后 ops={after}")
        assert grew, "广播后托盘没有任何反应 —— 窗口收不到广播"
        print("  [OK] 托盘窗口能收到并处理广播")
    finally:
        stop_tray(ctx)


def test_initial_mount():
    print("== test_initial_mount ==")
    ctx = start_tray()
    try:
        assert ctx["ready"], "托盘窗口创建失败"
        assert wait_for(lambda: ctx["rec"].count(NIM_ADD) >= 1), "没有调用 NIM_ADD"
        print(f"  ops = {ctx['rec'].snapshot()}")
        assert ctx["host"].icon_added, "icon_added 应为 True"
        print("  [OK] 首次挂载调用了 NIM_ADD，icon_added=True")
    finally:
        stop_tray(ctx)


def test_taskbar_created_remounts():
    """资源管理器重启 -> 必须重新 NIM_ADD，且不能用 NIM_MODIFY。"""
    print("== test_taskbar_created_remounts ==")
    ctx = start_tray()
    try:
        assert ctx["ready"], "托盘窗口创建失败"
        assert wait_for(lambda: ctx["rec"].count(NIM_ADD) >= 1)
        n0 = ctx["rec"].count(NIM_ADD)

        broadcast(ctx["host"].wm_taskbar_created)
        assert wait_for(lambda: ctx["rec"].count(NIM_ADD) >= n0 + 1,
                        timeout=3.0), "TaskbarCreated 后没有重新挂载图标"
        n1 = ctx["rec"].count(NIM_ADD)
        print(f"  NIM_ADD 次数 {n0} -> {n1}")
        assert n1 >= n0 + 1

        # 重新挂载必须用 NIM_ADD：图标对新的任务栏是不存在的，MODIFY 无效
        mods = ctx["rec"].count(NIM_MODIFY)
        print(f"  NIM_MODIFY 次数 = {mods}")
        assert mods == 0, "重新挂载用了 NIM_MODIFY —— 对新任务栏无效，必须用 NIM_ADD"
        print("  [OK] 收到 TaskbarCreated 后重新 NIM_ADD（未误用 MODIFY）")
    finally:
        stop_tray(ctx)


def test_autostart_race_recovers():
    """开机自启时本进程可能早于任务栏创建，首次挂载失败，靠广播补救。"""
    print("== test_autostart_race_recovers ==")
    ctx = start_tray(fail_first=1)          # 首次 NIM_ADD 返回失败
    try:
        assert ctx["ready"], "托盘窗口创建失败"
        assert wait_for(lambda: ctx["rec"].count(NIM_ADD) >= 1)
        print(f"  首次挂载失败后: icon_added = {ctx['host'].icon_added}")
        assert ctx["host"].icon_added is False, "首次失败时 icon_added 应为 False"

        broadcast(ctx["host"].wm_taskbar_created)
        assert wait_for(lambda: ctx["host"].icon_added, timeout=3.0), \
            "收到 TaskbarCreated 后仍未能挂上图标"
        print(f"  广播后: icon_added = {ctx['host'].icon_added}")
        print("  [OK] 首次挂载失败后能被 TaskbarCreated 补救")
    finally:
        stop_tray(ctx)


def test_icon_not_leaked_on_stop():
    print("== test_icon_not_leaked_on_stop ==")
    ctx = start_tray()
    try:
        assert ctx["ready"], "托盘窗口创建失败"
        assert wait_for(lambda: ctx["rec"].count(NIM_ADD) >= 1)
        stop_tray(ctx)
        assert ctx["rec"].count(NIM_DELETE) == 1, "stop() 没有摘掉托盘图标"
        assert ctx["host"].icon_added is False, "stop() 后 icon_added 应为 False"
        print(f"  ops = {ctx['rec'].snapshot()}")
        print("  [OK] stop() 正确摘除图标并复位状态")
    finally:
        ctx["mod"].shell32 = ctx["saved"]


def test_real_tray_icon_visible():
    """真机端到端：托盘图标必须真的出现在系统托盘里。

    窗口存在 != 图标可见。Shell_NotifyIconGetRect 是唯一能程序化确认图标
    已挂上任务栏并取得实际位置的 API —— 图标被折叠进溢出区时尺寸会是 0。
    """
    print("== test_real_tray_icon_visible ==")
    import _verify_launcher as VL

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class NOTIFYICONIDENTIFIER(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("hWnd", ctypes.c_void_p),
                    ("uID", ctypes.c_uint), ("guidItem", ctypes.c_ubyte * 16)]

    import ctypes.wintypes as wt
    sh = ctypes.WinDLL("shell32", use_last_error=True)
    sh.Shell_NotifyIconGetRect.argtypes = [ctypes.POINTER(NOTIFYICONIDENTIFIER),
                                           ctypes.POINTER(RECT)]
    sh.Shell_NotifyIconGetRect.restype = ctypes.HRESULT

    bat = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "启动桌面特效.bat")
    assert os.path.exists(bat), f"launcher not found: {bat}"

    # 顺带断言"干净启动不留错误日志"：程序全程静默（pythonw 无控制台），
    # error.log 是唯一出口。若首启就产生日志，说明托盘/热键/渲染/配置里
    # 有一条路径在报错 —— 这条断言能一次性兜住全部。
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "error.log")
    if os.path.exists(log_path):
        os.remove(log_path)

    VL.kill_all_pythonw()
    time.sleep(0.5)
    try:
        os.startfile(bat)
        tray = None
        for _ in range(60):                      # 最多等 ~12s
            time.sleep(0.2)
            found = VL.count_windows_by_class("DesktopFXTrayCls")
            if found:
                tray = found[0]
                break
        assert tray, "实机启动后找不到托盘窗口"

        # 窗口先出现、NIM_ADD 后执行，所以要重试：窗口一冒头就查会拿到 E_FAIL。
        # 托盘窗口为了能收广播必须是顶层窗口，但绝不能因此冒出来：
        # 未设 WS_VISIBLE 就不会进任务栏，WS_EX_TOOLWINDOW 再挡住 Alt+Tab。
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.IsWindowVisible.restype = ctypes.c_int
        assert not user32.IsWindowVisible(tray), "托盘窗口不该可见"
        gwl_exstyle = -20
        user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        ex = user32.GetWindowLongW(tray, gwl_exstyle)
        print(f"  可见性={user32.IsWindowVisible(tray)}  WS_EX=0x{ex & 0xFFFFFFFF:08X}"
              f"  TOOLWINDOW={bool(ex & 0x80)}")
        assert not user32.IsWindowVisible(tray), "托盘窗口出现在了任务栏/屏幕上"
        assert ex & 0x80, "缺少 WS_EX_TOOLWINDOW，托盘窗口会混进 Alt+Tab"

        nid = NOTIFYICONIDENTIFIER()
        nid.cbSize = ctypes.sizeof(NOTIFYICONIDENTIFIER)
        nid.hWnd = tray
        nid.uID = 1
        rc = RECT()
        hr = -1
        for _ in range(50):                      # 最多再等 ~10s
            rc = RECT()
            try:
                hr = sh.Shell_NotifyIconGetRect(ctypes.byref(nid),
                                                ctypes.byref(rc))
            except OSError as exc:
                hr = exc.winerror
            if hr == 0:
                break
            time.sleep(0.2)
        print(f"  tray hwnd = 0x{tray:x}")
        print(f"  Shell_NotifyIconGetRect hr = 0x{hr & 0xFFFFFFFF:08X}")
        assert hr == 0, (
            f"查不到托盘图标位置，HRESULT=0x{hr & 0xFFFFFFFF:08X} "
            f"(0x80004005 = 图标尚未注册)")

        w, h = rc.right - rc.left, rc.bottom - rc.top
        print(f"  图标矩形: ({rc.left},{rc.top})-({rc.right},{rc.bottom})  {w}x{h}")
        assert w > 0 and h > 0, "图标矩形为空（可能被折叠进溢出区）"
        print("  [OK] 托盘图标确实显示在系统托盘中")

        if os.path.exists(log_path):
            body = open(log_path, encoding="utf-8", errors="replace").read()
            raise AssertionError("干净启动却写了 error.log:\n" + body)
        print("  [OK] 干净启动未产生 error.log（无静默错误）")

        # 暂停热键 Ctrl+Alt+R 也曾随托盘窗口一起失效（注册它的 hwnd 根本不存在）。
        # 没有直接查询"某程序是否已注册某热键"的 API，所以用抢占法：
        # 从本进程尝试注册同一个组合键，失败 == 已被程序占用。
        MOD_CONTROL, MOD_ALT, MOD_NOREPEAT = 0x0002, 0x0001, 0x4000
        user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                          ctypes.c_uint, ctypes.c_uint]
        user32.RegisterHotKey.restype = ctypes.c_int
        user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.UnregisterHotKey.restype = ctypes.c_int
        taken = not user32.RegisterHotKey(None, 0x7B1D,
                                          MOD_CONTROL | MOD_ALT | MOD_NOREPEAT,
                                          ord("R"))
        print(f"  Ctrl+Alt+R 抢占注册结果: {'失败(已被占用)' if taken else '成功(无人占用)'}")
        if taken:
            print("  [OK] 程序已占用 Ctrl+Alt+R，暂停热键可用")
        else:
            user32.UnregisterHotKey(None, 0x7B1D)
            raise AssertionError("Ctrl+Alt+R 未被占用 —— 暂停热键没有注册成功")
    finally:
        VL.kill_all_pythonw()


def test_tray_icon_loads():
    """托盘图标必须真的能拿到一个非空 HICON。

    历史坑：原来用手搓 DIBSection + CreateIconIndirect 生成图标，在受限会话
    （无显示器 / 远程 / 某些 pythonw 场景）里 CreateDIBSection 直接失败，
    build_icon 返回 None -> NIF_ICON 带一个 NULL 图标 -> 托盘里空空如也。
    现在优先用 LoadImageW 从 tray_icon.ico 加载（内含多尺寸），这条路径不依赖
    GDI 创建 DC，稳得多；拿不到才回退到自绘。
    """
    import os
    import fx_tray
    print("== test_tray_icon_loads ==")
    ico = os.path.join(fx_tray.APP_DIR, "assets", "tray_icon.ico")
    assert os.path.exists(ico), f"缺少托盘图标文件: {ico}"
    print(f"  tray_icon.ico: {os.path.getsize(ico)} bytes")

    h = fx_tray._load_ico_icon()
    print(f"  _load_ico_icon -> {h}")
    assert h, "_load_ico_icon 返回空 —— 托盘会没有图标"

    h2 = fx_tray.build_icon(32)
    print(f"  build_icon(32) -> {h2}")
    assert h2, "build_icon 返回空 —— 托盘会没有图标"
    print("  [OK] 托盘图标可加载且非空")


def test_popup_dispatch():
    """菜单项 id 必须翻译成正确的主程序事件 —— 这是右键菜单能不能用的核心逻辑。

    之前的 bug：`_popup` 用了 `TPM_NONOTIFY`（抑制 WM_COMMAND）却没用
    `TPM_RETURNCMD`，被选中的项 id 被整个丢弃，点菜单毫无反应。修复后逻辑
    收敛到 `_dispatch_menu` 一处，这里直接验证映射，并守住会再次踩坑的两条
    声明（TrackPopupMenu 返回类型 & TPM_RETURNCMD 的存在）。
    """
    import fx_tray
    print("== test_popup_dispatch ==")
    # 守住声明：TrackPopupMenu 返回类型必须是能装下 32 位 id 的无符号整型，
    # 否则配合 TPM_RETURNCMD 时取到的 id 会被截断。
    assert user32.TrackPopupMenu.restype is UINT, (
        f"TrackPopupMenu.restype 应为 UINT，实际 {user32.TrackPopupMenu.restype}")
    assert fx_tray.TPM_RETURNCMD, "fx_tray 未定义 TPM_RETURNCMD"

    q = queue.Queue()
    host = fx_tray.TrayHost(q, cls_name="TrayDispatchCls")
    for cmd, expect in ((fx_tray.IDM_PANEL, "panel"),
                        (fx_tray.IDM_PAUSE, "pause"),
                        (fx_tray.IDM_QUIT, "quit")):
        q.queue.clear()
        host._dispatch_menu(cmd)
        assert not q.empty(), f"cmd=0x{cmd:X} 没有产出事件"
        kind, _ = q.get_nowait()
        assert kind == expect, f"cmd=0x{cmd:X} -> {kind!r}, 期望 {expect!r}"
        print(f"  [OK] IDM 0x{cmd:X} -> {kind!r}")
    # 未知 id 不应误触发
    q.queue.clear()
    host._dispatch_menu(0xDEAD)
    assert q.empty(), "未知菜单项 id 不应触发任何事件"
    print("  [OK] 未知 id 不触发事件")


def _wait_for_event_kind(q, kinds, timeout=2.0):
    """忽略热键/托盘失败等异步事件，只等待目标菜单事件。"""
    end = time.time() + timeout
    while time.time() < end:
        while not q.empty():
            kind, _ = q.get_nowait()
            if kind in kinds:
                return kind
        time.sleep(0.01)
    return None


def test_wm_command_routes_to_event():
    """端到端：向真实托盘窗口发 WM_COMMAND（模拟"菜单项被点击"），
    窗口过程应把它翻译成正确的事件。这验证的是 `_proc` 里 WM_COMMAND ->
    `_dispatch_menu` 这条真实路径，而不只是 `_dispatch_menu` 自身。"""
    print("== test_wm_command_routes_to_event ==")
    import fx_tray
    ctx = start_tray()
    try:
        assert ctx["ready"], "托盘窗口创建失败"
        hwnd = ctx["host"].hwnd
        WM_COMMAND = 0x0111
        for cmd, expect in ((fx_tray.IDM_PANEL, "panel"),
                            (fx_tray.IDM_PAUSE, "pause"),
                            (fx_tray.IDM_QUIT, "quit")):
            ctx["q"].queue.clear()
            # 菜单来源的 WM_COMMAND：wParam 低字为菜单项 id，高字为 0
            user32.SendMessageW(hwnd, WM_COMMAND, cmd, 0)
            kind = _wait_for_event_kind(ctx["q"], {"panel", "pause", "quit"})
            assert kind is not None, f"发 WM_COMMAND(0x{cmd:X}) 后窗口过程没有产出菜单事件"
            assert kind == expect, f"WM_COMMAND 0x{cmd:X} -> {kind!r}, 期望 {expect!r}"
            print(f"  [OK] WM_COMMAND 0x{cmd:X} -> {kind!r}")
    finally:
        stop_tray(ctx)


def main():
    print("== tray / TaskbarCreated robustness verification ==")
    print()
    for fn in (test_taskbar_created_message,
               test_window_receives_broadcast,
               test_initial_mount,
               test_taskbar_created_remounts,
               test_autostart_race_recovers,
               test_icon_not_leaked_on_stop,
               test_tray_icon_loads,
               test_real_tray_icon_visible,
               test_popup_dispatch,
               test_wm_command_routes_to_event):
        fn()
        print()
    print("== TRAY VERIFIED ==")


if __name__ == "__main__":
    main()
