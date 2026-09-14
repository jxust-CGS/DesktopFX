# -*- coding: utf-8 -*-
"""置顶保活（z 序）回归验证。

背景（2026-09-12）：远端提交 2a76000 引入 Overlay.keep_on_top()，主循环每
0.5s 无条件 SetWindowPos(HWND_TOPMOST)。控制面板自身每 2s 也做一次同类调用
（ControlPanel._raise_top）—— 两者互把对方挤出 topmost 组顶部，形成 ~2 秒
一次的 z 序翻转，用户看到的现象就是"每隔几秒画面闪一下"。

本测试把四条不变量锁死，防止"无条件抢顶"被改回来：

    A. 没有真实窗口盖住我时，keep_on_top 一次 SetWindowPos 都不许发。
    B. 控制面板盖在我上面是预期状态，同样不得触发抢顶。
    C. 可见但零面积 / 1x1 的系统与驱动辅助窗（IME、ForegroundStaging、
       触摸板 SynTP、缩略图 helper）不算遮挡 —— 它们是导致"每 0.5s 都误判"
       的真正元凶。
    D. 真被第三方置顶窗口盖住时，必须复位一次，插到面板之下且带 NOCOPYBITS。
"""
import ctypes
import ctypes.wintypes as wt

import fx_winlayer as fw
from _win32 import user32, hwnd_int

_REAL_USER32 = fw.user32

OVERLAY = 1000
PANEL = 2000
OTHER = 3000
TINY = 4000
TRAY = 5000


class FakeUser32:
    """把 z 序链 / 可见性 / 窗口矩形做成可控数据，并统计 SetWindowPos 调用。

    链的语义与 GW_HWNDPREV 一致：chain[a] = 位于 a **上方**的邻窗，0 表示到底。
    窗口矩形默认 200x200（够大，算真实窗口）。
    """

    def __init__(self, chain=None, visible=(), sizes=None, classes=None):
        self.chain = dict(chain or {})
        self.visible = set(visible)
        self.sizes = dict(sizes or {})       # hwnd -> (w, h)
        self.classes = dict(classes or {})   # hwnd -> 类名
        self.setpos = []

    def GetWindow(self, h, flag):
        return self.chain.get(hwnd_int(h), 0)

    def IsWindowVisible(self, h):
        return hwnd_int(h) in self.visible

    def GetWindowRect(self, h, ptr):
        w, hh = self.sizes.get(hwnd_int(h), (200, 200))
        r = ctypes.cast(ptr, ctypes.POINTER(wt.RECT)).contents
        r.left, r.top, r.right, r.bottom = 0, 0, w, hh
        return True

    def GetClassNameW(self, h, buf, n):
        name = self.classes.get(hwnd_int(h), "FakeAppWnd")
        for i, ch in enumerate(name[:max(0, n - 1)]):
            buf[i] = ch
        buf[len(name)] = "\0"
        return len(name)

    def SetWindowPos(self, h, after, x, y, cx, cy, flags):
        self.setpos.append({"hwnd": hwnd_int(h), "after": hwnd_int(after),
                            "flags": int(flags)})
        return True


def _patched(chain, visible, panel=None, **kw):
    ov = fw.Overlay()
    ov.hwnd = OVERLAY
    if panel is not None:
        ov.set_panel_hwnd(panel)
    fake = FakeUser32(chain, visible, **kw)
    fw.user32 = fake
    return ov, fake


# ---------------------------------------------------------------- 不变量 A
def test_steady_state_is_noop():
    """overlay 之上只剩辅助窗（链结束前没有真实窗口）：不得发 SetWindowPos。"""
    ov, fake = _patched({OVERLAY: TINY, TINY: 0}, {TINY}, sizes={TINY: (1, 1)})
    moved = ov.keep_on_top()
    if moved or fake.setpos:
        return False, f"无真实遮挡仍抢顶 {len(fake.setpos)} 次（周期性闪烁的来源）"
    return True, "仅辅助窗 -> 0 次 SetWindowPos"


def test_empty_chain_is_noop():
    """已在最顶：不得发任何 SetWindowPos。"""
    ov, fake = _patched({}, set())
    moved = ov.keep_on_top()
    if moved or fake.setpos:
        return False, f"链为空仍抢顶 {len(fake.setpos)} 次"
    return True, "链为空 -> 0 次 SetWindowPos"


# ---------------------------------------------------------------- 不变量 B
def test_panel_above_is_not_a_cover():
    """面板浮在 overlay 之上属预期状态，不能被判成遮挡。"""
    ov, fake = _patched({OVERLAY: PANEL, PANEL: 0}, {PANEL}, panel=PANEL)
    moved = ov.keep_on_top()
    if moved or fake.setpos:
        return False, f"把自家面板当成遮挡，抢顶 {len(fake.setpos)} 次 -> 与面板互抢"
    return True, "面板在上 -> 0 次 SetWindowPos"


def test_hidden_panel_ignored():
    """面板不可见时同样不计入遮挡。"""
    ov, fake = _patched({OVERLAY: PANEL, PANEL: 0}, set(), panel=PANEL)
    moved = ov.keep_on_top()
    if moved or fake.setpos:
        return False, "不可见的面板被当成遮挡"
    return True, "不可见面板不计入遮挡"


# ---------------------------------------------------------------- 不变量 C
def test_tiny_visible_window_is_not_occluder():
    """可见但 1x1 的辅助窗（缩略图 helper / 触摸板）不算遮挡。"""
    ov, fake = _patched({OVERLAY: TINY, TINY: 0}, {TINY},
                        sizes={TINY: (1, 1)},
                        classes={TINY: "ThumbnailDeviceHelperWnd"})
    moved = ov.keep_on_top()
    if moved or fake.setpos:
        return False, "1x1 可见辅助窗被误判为遮挡"
    return True, "1x1 可见辅助窗 -> 不算遮挡"


def test_zero_area_visible_window_is_not_occluder():
    """可见但 0x0 的辅助窗（SynTP 触摸板光标层）不算遮挡。"""
    ov, fake = _patched({OVERLAY: TINY, TINY: 0}, {TINY},
                        sizes={TINY: (0, 0)},
                        classes={TINY: "SynTrackCursorWindowClass"})
    moved = ov.keep_on_top()
    if moved or fake.setpos:
        return False, "0x0 可见辅助窗被误判为遮挡"
    return True, "0x0 可见辅助窗 -> 不算遮挡"


def test_shell_windows_are_not_occluders():
    """桌面 / 任务栏即使又大又"可见"也不算遮挡。"""
    for cls in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        ov, fake = _patched({OVERLAY: TRAY, TRAY: 0}, {TRAY},
                            sizes={TRAY: (1920, 40)}, classes={TRAY: cls})
        if ov.keep_on_top() or fake.setpos:
            return False, f"{cls} 被误判为遮挡"
    return True, "Progman/WorkerW/任务栏 -> 不算遮挡"


def test_invisible_window_ignored():
    """不可见的大窗口不算遮挡。"""
    ov, fake = _patched({OVERLAY: OTHER, OTHER: 0}, set())
    if ov.keep_on_top():
        return False, "不可见窗口被误判为遮挡"
    return True, "不可见窗口 -> 不算遮挡"


# ---------------------------------------------------------------- 不变量 D
def test_other_topmost_triggers_once():
    """被第三方置顶窗口盖住：复位一次，插到面板之下，且带 NOCOPYBITS。"""
    ov, fake = _patched({OVERLAY: OTHER, OTHER: PANEL, PANEL: 0},
                        {OTHER, PANEL}, panel=PANEL)
    moved = ov.keep_on_top()
    if not moved or len(fake.setpos) != 1:
        return False, f"被第三方遮挡却未复位: moved={moved} calls={len(fake.setpos)}"
    c = fake.setpos[0]
    if c["after"] != PANEL:
        return False, f"复位应插到面板之下(after={PANEL})，实际 after={c['after']}"
    if not (c["flags"] & fw.SWP_NOCOPYBITS):
        return False, "复位未带 SWP_NOCOPYBITS（重排瞬间可能翻出无效内容）"
    if not (c["flags"] & fw.SWP_NOACTIVATE):
        return False, "复位未带 SWP_NOACTIVATE（会抢焦点）"
    return True, "第三方遮挡 -> 1 次复位，插到面板之下，带 NOCOPYBITS|NOACTIVATE"


def test_other_topmost_without_panel():
    """无面板时复位应插到 topmost 组顶部。"""
    ov, fake = _patched({OVERLAY: OTHER, OTHER: 0}, {OTHER})
    ov.keep_on_top()
    if len(fake.setpos) != 1:
        return False, f"应复位 1 次，实际 {len(fake.setpos)} 次"
    if fake.setpos[0]["after"] != hwnd_int(fw.HWND_TOPMOST):
        return False, f"无面板时应插到 HWND_TOPMOST，实际 after={fake.setpos[0]['after']}"
    return True, "无面板 -> 插到 topmost 组顶部"


def test_occluder_found_past_helpers():
    """遮挡者藏在若干辅助窗之后时仍要能找到。"""
    ov, fake = _patched(
        {OVERLAY: TINY, TINY: OTHER, OTHER: 0}, {TINY, OTHER},
        sizes={TINY: (1, 1), OTHER: (800, 600)},
        classes={TINY: "IME"})
    moved = ov.keep_on_top()
    if not moved or len(fake.setpos) != 1:
        return False, f"辅助窗之后的真实窗口应触发 1 次复位，实际 moved={moved}"
    return True, "跳过辅助窗后找到真实遮挡者 -> 复位 1 次"


# ---------------------------------------------------------------- 真实窗口冒烟
class _CountProxy:
    """包装真实 user32，只统计 SetWindowPos 次数。"""

    def __init__(self, real):
        self._real = real
        self.setpos = 0

    def __getattr__(self, name):
        return getattr(self._real, name)

    def SetWindowPos(self, *a):
        self.setpos += 1
        return self._real.SetWindowPos(*a)


def test_real_window_steady_state():
    """真实 overlay：进入稳态后连续 10 次调用必须 0 次实际重排。

    首次调用允许一次自愈复位（桌面上可能本来就有别人的置顶窗口压着）；
    重点是从第二次起必须彻底安静 —— 无条件抢顶的实现会在这里每次 +1。
    """
    try:
        ov = fw.Overlay().create()
    except Exception as e:
        return None, f"SKIP（无法创建真实窗口：{type(e).__name__}: {e}）"
    try:
        proxy = _CountProxy(user32)
        fw.user32 = proxy
        first = ov.keep_on_top()
        proxy.setpos = 0
        for _ in range(10):
            ov.keep_on_top()
        n = proxy.setpos
    finally:
        fw.user32 = _REAL_USER32
        ov.destroy()
    if n:
        return False, f"进入稳态后仍抢顶 {n} 次/10 次调用（应为 0）"
    return True, f"真实窗口稳态 10 次调用 -> 0 次 SetWindowPos（首次自愈={first}）"


def test_real_window_geometry_sane():
    """真实环境下 _covered() 必须为假：桌面上没有能盖住全屏 overlay 的窗口。"""
    try:
        ov = fw.Overlay().create()
    except Exception as e:
        return None, f"SKIP（{type(e).__name__}: {e}）"
    try:
        covered = ov._covered()
    finally:
        ov.destroy()
    if covered:
        return False, "_covered() 为真 —— 判据过宽，会导致周期性抢顶"
    return True, "_covered() 为假（无真实窗口压在全屏 overlay 之上）"


# ---------------------------------------------------------------- 显示层级
_GWL = ctypes.windll.user32.GetWindowLongW
_GWL.restype = ctypes.c_long
_GWL.argtypes = [ctypes.c_void_p, ctypes.c_int]
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008


def _is_topmost(hwnd):
    return bool(_GWL(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


def test_workerw_lookup_safe():
    """找不到壁纸层时必须老实返回 0（调用方靠它降级），不得抛异常。"""
    w = fw._find_wallpaper_workerw()
    if not isinstance(w, int):
        return False, f"返回类型不对：{type(w)}"
    return True, f"WorkerW 查找返回 {w or '0（本机未找到，将走沉底降级）'}"


def test_real_desktop_layer_roundtrip():
    """真实窗口：最上层 <-> 仅桌面图层 来回切，窗口必须始终有效。

    桌面层要么真挂上壁纸层（SetParent 到 WorkerW），要么明确走"沉底"降级 ——
    不允许出现"既没挂上、又还置顶"的中间态（那等于这个设置压根没生效）。
    """
    try:
        ov = fw.Overlay().create()
    except Exception as e:
        return None, f"SKIP（{type(e).__name__}: {e}）"
    detail = ""
    try:
        if not _is_topmost(ov.hwnd):
            return False, "默认层级（最上层）没把 WS_EX_TOPMOST 加上"

        ov.apply_z_mode(fw.LAYER_DESKTOP)
        if ov.z_mode != fw.LAYER_DESKTOP:
            return False, f"层级没切过去：{ov.z_mode}"
        if not user32.IsWindow(ov.hwnd):
            return False, "切到桌面层后窗口失效"
        if _is_topmost(ov.hwnd):
            return False, "桌面层窗口仍带 WS_EX_TOPMOST —— 设置没真正生效"
        if ov._parented:
            # 必须真的建立起父子关系：只说"挂上了"不算数 —— WS_POPUP 的窗口
            # 即便 SetParent 过，GetParent 也返回 owner（恒为 0）。
            parent = hwnd_int(user32.GetParent(ov.hwnd))
            if not parent:
                return False, "报告已挂壁纸层，但 GetParent 为 0（父子关系没建立）"
            buf = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(parent, buf, 64)
            if buf.value != "WorkerW":
                return False, f"挂到的不是 WorkerW，而是 {buf.value or '?'}"
            detail = f"已挂到壁纸层 WorkerW={parent}"
        elif ov.is_desktop_fallback():
            detail = "未找到可用壁纸层，已降级为沉底窗口"
        else:
            return False, "既没挂上壁纸层、也没走降级 —— 层级状态不明"
        ov.u32[:] = 0
        ov.present()          # 桌面层仍旧要能上屏

        ov.apply_z_mode(fw.LAYER_TOPMOST)
        if ov.z_mode != fw.LAYER_TOPMOST or not _is_topmost(ov.hwnd):
            return False, "切回最上层失败"
        if ov.ensure_alive():
            return False, "窗口明明活着，ensure_alive 却报告重建"
        if user32.GetParent(ov.hwnd):
            return False, "切回最上层后仍然挂在别的窗口下"
    finally:
        ov.destroy()
    return True, f"最上层 <-> 仅桌面图层 往返正常；{detail}"


def test_real_two_overlay_render_loop():
    """端到端：真实渲染循环起两个覆盖层，两层各画各的，且能运行时切换层级。

    只起渲染线程（不碰托盘 / 面板），也**不落盘**（不走 quit()）。
    注意逐帧统计必须挂在 Scene.step 上：从外部读 ov.u32 会读到"已清零、
    还没画完"的瞬时状态（鼠标层的聚光灯要画 ~10ms，这期间全屏层就是空的）。
    """
    import threading
    import time

    import desktop_fx as d
    import fx_scene

    stats = []
    orig = fx_scene.Scene.step

    def _spy(self, dt, u32, w, h, u32_mouse=None):
        orig(self, dt, u32, w, h, u32_mouse)
        stats.append((int((u32 > 0).sum()),
                      -1 if u32_mouse is None else int((u32_mouse > 0).sum())))

    fx_scene.Scene.step = _spy
    app = None
    try:
        app = d.App()
        app.cfg.update(dict(rain_enabled=True, rain_amount=220, splash=False,
                            snow_enabled=False, petal_enabled=False,
                            meteor_enabled=False, spotlight_enabled=True,
                            spotlight_dim=0.15, paint_enabled=False,
                            wave_enabled=False, ripple_enabled=False))
        app.snapshot = dict(app.cfg)
        app._thread = threading.Thread(target=app._render_loop, name="probe",
                                       daemon=True)
        app._thread.start()

        deadline = time.time() + 10
        while time.time() < deadline and len(app.overlays) < 2:
            time.sleep(0.1)
        time.sleep(1.2)
        if len(app.overlays) != 2:
            return None, f"SKIP/FAIL 覆盖层数量={len(app.overlays)}（窗口建不起来？）"
        fs_max = max((s[0] for s in stats), default=0)
        ms_max = max((s[1] for s in stats), default=0)
        if fs_max <= 0:
            return False, "全屏特效层没有画任何东西"
        if ms_max <= 0:
            return False, "鼠标特效层没有画任何东西"

        ov_fs, ov_ms = app.overlays
        if user32.GetParent(ov_ms.hwnd):
            return False, "鼠标层默认不该挂在别的窗口下"

        app.snapshot = dict(app.snapshot, layer_mouse="desktop")
        deadline = time.time() + 6
        while time.time() < deadline and ov_ms.z_mode != fw.LAYER_DESKTOP:
            time.sleep(0.1)
        if ov_ms.z_mode != fw.LAYER_DESKTOP or not user32.IsWindow(ov_ms.hwnd):
            return False, "运行时切到「仅桌面图层」没生效"
        if _is_topmost(ov_ms.hwnd):
            return False, "桌面层的鼠标层仍带 WS_EX_TOPMOST"

        app.snapshot = dict(app.snapshot, layer_mouse="topmost")
        deadline = time.time() + 6
        while time.time() < deadline and ov_ms.z_mode != fw.LAYER_TOPMOST:
            time.sleep(0.1)
        if ov_ms.z_mode != fw.LAYER_TOPMOST or not _is_topmost(ov_ms.hwnd):
            return False, "运行时切回「最上层」失败"
        if user32.GetParent(ov_ms.hwnd):
            return False, "切回最上层后仍挂在壁纸层下"
        if app.last_error:
            return False, f"渲染循环报错：{app.last_error}"
    finally:
        if app is not None:
            app.stop_evt.set()
            if app._thread is not None:
                app._thread.join(timeout=3)
        fx_scene.Scene.step = orig
    return True, (f"两层各画各的（全屏≈{fs_max}px / 鼠标≈{ms_max}px），"
                  f"层级运行时往返切换正常")


def test_real_empty_layer_skip():
    """空层跳过呈现：只在"真有像素"或"刚由有变空/窗口重建"时才提交。

    分两层后每层每帧一次全屏 UpdateLayeredWindow 约 4ms，而常态是只有一类特效
    在跑、另一层全空 —— 白白提交一次全 0 画面完全一样。这条测试锁住两件事：
      1. 鼠标层确实**没有内容时不提交**（否则优化被改回去也没人发现）；
      2. 内容一出现立刻恢复提交（跳过逻辑不能把有内容的帧也吞掉）。
    判据必须是缓冲区真实像素，所以这里开雨但关掉溅水花 —— 水花会往涟漪池里
    落点，把鼠标层弄成"有内容"。
    """
    import threading
    import time

    import desktop_fx as d
    import fx_winlayer

    counts = {"fs": 0, "ms": 0}
    holder = {}
    orig = fx_winlayer.Overlay.present

    def _spy(self):
        ovs = holder.get("app").overlays if holder.get("app") else []
        key = "fs" if (len(ovs) > 0 and self.hwnd == ovs[0].hwnd) else "ms"
        counts[key] += 1
        return orig(self)

    fx_winlayer.Overlay.present = _spy
    app = None
    try:
        app = d.App()
        holder["app"] = app
        app.cfg.update(dict(rain_enabled=True, rain_amount=200, splash=False,
                            snow_enabled=False, petal_enabled=False,
                            meteor_enabled=False, spotlight_enabled=False,
                            paint_enabled=False, wave_enabled=False,
                            win_ripple_enabled=False, ripple_enabled=False))
        app.snapshot = dict(app.cfg)
        app._thread = threading.Thread(target=app._render_loop, name="probe",
                                       daemon=True)
        app._thread.start()

        deadline = time.time() + 10
        while time.time() < deadline and len(app.overlays) < 2:
            time.sleep(0.1)
        if len(app.overlays) != 2:
            return None, "SKIP（覆盖层建不起来）"
        time.sleep(0.8)                 # 预热：让"由有变空"的补提交走完
        counts["fs"] = counts["ms"] = 0
        time.sleep(1.2)
        fs_idle, ms_idle = counts["fs"], counts["ms"]
        if fs_idle < 20:
            return False, f"全屏层有内容却几乎没提交（{fs_idle} 次/1.2s）"
        if ms_idle > 2:
            return False, (f"鼠标层全空仍在每帧提交（{ms_idle} 次/1.2s，"
                           f"说明空层跳过没生效）")

        counts["ms"] = 0                # 开聚光灯 -> 鼠标层立刻该有内容
        app.snapshot = dict(app.snapshot, spotlight_enabled=True, spotlight_dim=0.5)
        deadline = time.time() + 5
        while time.time() < deadline and counts["ms"] < 15:
            time.sleep(0.1)
        if counts["ms"] < 15:
            return False, f"鼠标层有内容后没有恢复提交（仅 {counts['ms']} 次）"
        if app.last_error:
            return False, f"渲染循环报错：{app.last_error}"
    finally:
        fx_winlayer.Overlay.present = orig
        if app is not None:
            app.stop_evt.set()
            if app._thread is not None:
                app._thread.join(timeout=3)
    return True, (f"空鼠标层 1.2s 仅提交 {ms_idle} 次（全屏层 {fs_idle} 次），"
                  f"开启聚光灯后立即恢复提交")


def main():
    tests = [
        test_steady_state_is_noop,
        test_empty_chain_is_noop,
        test_panel_above_is_not_a_cover,
        test_hidden_panel_ignored,
        test_tiny_visible_window_is_not_occluder,
        test_zero_area_visible_window_is_not_occluder,
        test_shell_windows_are_not_occluders,
        test_invisible_window_ignored,
        test_other_topmost_triggers_once,
        test_other_topmost_without_panel,
        test_occluder_found_past_helpers,
        test_real_window_steady_state,
        test_real_window_geometry_sane,
        test_workerw_lookup_safe,
        test_real_desktop_layer_roundtrip,
        test_real_two_overlay_render_loop,
        test_real_empty_layer_skip,
    ]
    failed = 0
    skipped = 0
    for t in tests:
        try:
            ok, msg = t()
        except Exception as e:
            ok, msg = False, f"{type(e).__name__}: {e}"
        finally:
            fw.user32 = _REAL_USER32   # 任何异常都不得把假 user32 泄漏给下一个用例
        if ok is None:
            skipped += 1
            print(f"[SKIP] {t.__name__}: {msg}")
        elif ok:
            print(f"[PASS] {t.__name__}: {msg}")
        else:
            failed += 1
            print(f"[FAIL] {t.__name__}: {msg}")
    total = len(tests) - skipped
    print(f"\n{total - failed}/{total} passed" + (f", {skipped} skipped" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
