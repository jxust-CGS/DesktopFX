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
