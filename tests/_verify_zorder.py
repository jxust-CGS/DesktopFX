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
    E. **插入锚点不得自封顶**（2026-09-14 补）。Win32 的 SetWindowPos(A, B)
       把 A 插到 B **之下**，所以 after=面板 就是"插到面板正下方"。面板压得住
       对手时这正好；面板自己被对手压住（Citrix 那类十几 Hz 重新置顶的全屏窗
       逼的）时就变成天花板 —— 每次抢顶只是插回面板之下，永远回不到对手之上。
       合成窗口实测：只换锚点，被盖采样 0% -> 100%，且与帧率无关（不是频率不够，
       是方向错了）。
    F. 锚点自己不是置顶窗时同样不能用：SetWindowPos 到非置顶窗之后会把本窗口的
       WS_EX_TOPMOST 一起清掉，而 keep_on_top 自己修不回来（实测连抢 30 次仍丢）。
    G. 置顶位若因故丢失，下一次 keep_on_top 必须自愈。
    H. place_above 的方向：必须让 self 真的位于 other **之上**，不能写反。
"""
import ctypes
import ctypes.wintypes as wt
import os
import tempfile
import time

import sys

# 本脚本住在 tests/ 子目录里：`sys.path[0]` 与 `__file__` 的父目录都不再是项目根。
# 向上找到含 desktop_fx.py 的目录（= 项目根）：产品模块从那里 import；脚本里已有的
# HERE 也一律以它为基准（读 desktop_fx.py / fx_effects.py / .gitattributes /
# 启动桌面特效.bat、写临时配置都按项目根算）。
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = _TESTS_DIR
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
# tests/ 自己也要进 path：有几个脚本互相 import（_verify_click / _verify_pet ->
# _verify_kinds、_verify_tray -> _verify_launcher）。
for _p in (ROOT, _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import fx_winlayer as fw
from _win32 import user32, hwnd_int

# 本脚本要起**真实 App**，而 App.__init__ 会读用户真实的 config.json。
# 不隔离就会让断言依赖用户设置：实测用户把两层都设成"仅桌面图层"后，
# `test_real_two_overlay_render_loop` 里的"鼠标层默认不该挂在别的窗口下"
# 立刻失败（那层已经变成了 WorkerW 的子窗口）—— 是测试读了用户配置，不是
# 产品坏了。测试必须自带配置，顺便也保证它不去写用户的文件。
import desktop_fx as d

d.CONFIG_PATH = os.path.join(tempfile.mkdtemp(prefix="fx_zorder_"),
                             "config.json")

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

    def __init__(self, chain=None, visible=(), sizes=None, classes=None,
                 not_topmost=()):
        self.chain = dict(chain or {})
        self.visible = set(visible)
        self.sizes = dict(sizes or {})       # hwnd -> (w, h)
        self.classes = dict(classes or {})   # hwnd -> 类名
        # 明确标成"非置顶窗"的 hwnd。默认所有窗口都在置顶组里（覆盖层、面板、
        # 对手本来就都是 topmost），只有要验"锚点资格"时才把某个标掉。
        self.not_topmost = set(not_topmost)
        self.setpos = []

    def GetWindowLongW(self, h, index):
        if index != fw.GWL_EXSTYLE:
            return 0
        return 0 if hwnd_int(h) in self.not_topmost else fw.WS_EX_TOPMOST

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


def test_anchor_refuses_panel_below_blocker():
    """**面板自己被对手压住时，不许再拿它当插入锚点。**

    这是"特效不能保持在最上层"的根因。Win32 语义：SetWindowPos(A, B) 把 A 插到
    B 的**下面**，所以 after=面板 就是"插到面板正下方"。面板压得住对手时这正好
    （复位 + 不遮住设置界面）；面板被对手压住时就变成了**自封顶** —— 每次"抢顶"
    都只是把覆盖层重新插回面板之下，而面板在对手之下，于是永远回不到对手之上。
    合成窗口实测：同样只换锚点，被盖采样从 0% 跳到 100%，抢顶 78 次全部白费；
    且与帧率无关（20fps 与 60fps 结果相同）—— 不是抢顶频率不够，是方向错了。
    """
    # 链里没有 PANEL 的上一级 => 面板不在对手之上
    ov, fake = _patched({OVERLAY: OTHER, OTHER: 0}, {OTHER}, panel=PANEL)
    moved = ov.keep_on_top()
    if not moved or len(fake.setpos) != 1:
        return False, f"被盖应复位 1 次，实际 moved={moved} n={len(fake.setpos)}"
    after = fake.setpos[0]["after"]
    if after != hwnd_int(fw.HWND_TOPMOST):
        return False, (f"面板在对手之下仍拿它当锚点（after={after}）-> 自封顶，"
                       f"每次抢顶只是插回面板之下，永远上不去")
    return True, "面板被对手压住 -> 改插置顶组顶部（不再自封顶）"


def test_anchor_refuses_non_topmost_panel():
    """锚点自己不是置顶窗时同样不能用 —— 它会把我们的置顶位一起吃掉。

    实测：把带 WS_EX_TOPMOST 的窗口 SetWindowPos 到**非置顶窗**之后，它的
    WS_EX_TOPMOST 会被清掉；此后任何普通窗口都能盖住特效，而 keep_on_top 每次
    "抢顶"只是又插回同一个非置顶锚点之后 —— 连抢 30 次仍丢失，自己修不回来。
    """
    ov, fake = _patched({OVERLAY: OTHER, OTHER: PANEL, PANEL: 0},
                        {OTHER, PANEL}, panel=PANEL, not_topmost=(PANEL,))
    moved = ov.keep_on_top()
    if not moved or len(fake.setpos) != 1:
        return False, f"被盖应复位 1 次，实际 moved={moved} n={len(fake.setpos)}"
    after = fake.setpos[0]["after"]
    if after != hwnd_int(fw.HWND_TOPMOST):
        return False, (f"锚点不是置顶窗却仍拿它当锚点（after={after}）-> "
                       f"覆盖层的 WS_EX_TOPMOST 会被一起清掉")
    return True, "锚点非置顶窗 -> 改插置顶组顶部（置顶位不会被吃掉）"


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


def test_raise_is_throttled():
    """**每帧**调用是安全的：被盖住时不许变成每秒 60 次 z 序重排。

    主循环现在每帧问一次"我是不是被盖住了"（反应延迟压到一帧，才抢得过
    Citrix 那类以十几 Hz 重新置顶自己的远控全屏窗）。判"被盖"很便宜但仍要
    两道闸门，这里把两道都锁住：

    * 连续 30 帧（同一毫秒内）调用，一次额外 SetWindowPos 都不许发；
    * 把抢顶限流显式关掉（`min_interval=0.0`）后，**过了链签名短路窗口**就
      该每次都动手 —— 证明"安静"确实是限流带来的，不是别的东西顺手挡住了。

    同时锁死"闸门不得挡住判定"：`min_interval=0.0` 时必须每次都动手。
    """
    ov, fake = _patched({OVERLAY: OTHER, OTHER: 0}, {OTHER}, panel=PANEL)
    ov.keep_on_top()
    n1 = len(fake.setpos)
    for _ in range(30):                 # 模拟紧接着的 30 帧
        ov.keep_on_top()
    n2 = len(fake.setpos) - n1
    if n1 != 1:
        return False, f"首次被盖应复位 1 次，实际 {n1}"
    if n2 != 0:
        return False, f"限流失效：紧接着的 30 次调用又抢了 {n2} 次顶"
    ov2, fake2 = _patched({OVERLAY: OTHER, OTHER: 0}, {OTHER}, panel=PANEL)
    ov2.keep_on_top(min_interval=0.0)
    time.sleep(fw._FULL_CHECK_INTERVAL + 0.05)   # 走出链签名短路窗口
    ov2.keep_on_top(min_interval=0.0)
    if len(fake2.setpos) != 2:
        return False, (f"min_interval=0 且已过复查间隔时本该每次都动手，"
                       f"实际 {len(fake2.setpos)} 次")
    return True, "被盖时首帧复位 1 次，随后 30 帧 0 次额外重排；关掉限流则每帧动手"


def test_chain_change_triggers_immediate_recheck():
    """链一变就必须**当帧**重查 —— 这是"每帧自检 + 链签名短路"的正确性核心。

    用户报的场景：Citrix 那类远控全屏窗把自己抬到我们头上。它一抬上来，我们
    之上的 z 序链就变了（签名随之改变），必须当帧发现并抢回，而不是等满复查
    间隔。所以短路只能在"链没变"时才生效。
    """
    ov, fake = _patched({OVERLAY: TINY, TINY: 0}, {TINY}, sizes={TINY: (1, 1)})
    if ov.keep_on_top() or fake.setpos:
        return False, "初始（只有 1x1 辅助窗）不该抢顶"
    # 对方抬到我们头上：链首换成真实大窗口（同一毫秒内立刻再问一次）
    fake.chain = {OVERLAY: OTHER, OTHER: TINY, TINY: 0}
    fake.sizes[OTHER] = (800, 600)
    fake.visible.add(OTHER)
    moved = ov.keep_on_top()
    if not moved or len(fake.setpos) != 1:
        return False, (f"链变了却没有当帧重查（moved={moved}, "
                       f"setpos={len(fake.setpos)}）")
    return True, "遮挡者抬上来（链变）当帧即重查并抢回，不必等满复查间隔"


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
    """真实 overlay：进入稳态后**只有真被盖住才许动手**。

    首次调用允许一次自愈复位（桌面上可能本来就有别人的置顶窗口压着）。

    判据是"抢顶次数 ≤ 真被盖的次数"，而不是"必须是 0"：这台机器上真的跑着
    Citrix（Desktop Viewer 那类会自己重新置顶的全屏窗），它在 1.5 秒里完全可能
    抢上来一次 —— 那一次抢顶是**正确行为**，不该算失败（早先按"必须 0 次"判，
    全量跑时偶发假失败）。而不变量本身没有被削弱：无条件抢顶的实现会 5 次全 +1，
    真被盖的次数通常只有 0~1，照样判失败。

    每次调用之间 sleep 过链签名短路窗口，否则后几次会被"链没变"直接短路掉，
    这道不变量就变成没测到（短路本身在 test_chain_change_triggers_immediate_recheck
    里单独锁）。
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
        covered_n = 0
        for _ in range(5):
            time.sleep(fw._FULL_CHECK_INTERVAL + 0.05)
            if ov._covered():          # 只统计，不改状态
                covered_n += 1
            ov.keep_on_top()
        n = proxy.setpos
        still_covered = ov._covered()
    finally:
        fw.user32 = _REAL_USER32
        ov.destroy()
    if still_covered:
        return False, "5 次全量检查结束时仍被判为被盖 —— 抢顶没真正生效"
    if n > covered_n:
        return False, (f"进入稳态后抢顶 {n} 次，而真正被盖只有 {covered_n} 次 "
                       f"—— 多出来的就是无条件抢顶（会导致周期性闪烁）")
    return True, (f"真实窗口稳态 5 次全量检查 -> 抢顶 {n} 次 / 真被盖 {covered_n} 次"
                  f"（首次自愈={first}）")


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


def test_real_foreign_topmost_is_recovered():
    """真机端到端：别人的置顶窗压上来，keep_on_top 必须真的抢回置顶。

    用一个 `WS_EX_TOPMOST` 的 STATIC 窗口当"Citrix 那类全屏远控窗"的替身 ——
    先把窗口建出来，再 `SetWindowPos(HWND_TOPMOST)` 显式抬到 topmost 组顶部，
    模拟对方"被激活后压过我们"。这正是用户报的"展示层级还不是最高的"。

    抢回之后还要**连续调用 20 次**确认一次都不再动手：主循环是每帧调用的，
    若抢回后仍反复重排 z 序，画面会闪。
    """
    import time as _time

    WS_VISIBLE = 0x10000000
    try:
        ov = fw.Overlay().create()
    except Exception as e:
        return None, f"SKIP（{type(e).__name__}: {e}）"
    occ = None
    try:
        if ov._covered():               # 先把桌面上本来就有的东西让开，基线要干净
            ov.keep_on_top()
        occ = user32.CreateWindowExW(
            fw.WS_EX_TOPMOST | fw.WS_EX_TOOLWINDOW, "Static", "dfx-occluder",
            fw.WS_POPUP | WS_VISIBLE, ov.x + 40, ov.y + 40, 320, 240,
            None, None, None, None)
        if not occ:
            return None, "SKIP（造不出替身遮挡窗）"
        # 关键一步：抬到 topmost 组**顶部**（光带 WS_EX_TOPMOST 会被放在该组
        # 最下面，压不到我们头上，就测不出东西了）。
        user32.SetWindowPos(occ, fw.HWND_TOPMOST, ov.x + 40, ov.y + 40, 320, 240,
                            fw.SWP_SHOWWINDOW | fw.SWP_NOACTIVATE)
        deadline = _time.time() + 2.0
        while _time.time() < deadline and not ov._covered():
            _time.sleep(0.05)
        if not ov._covered():
            return False, "自己造的置顶遮挡窗没被判成遮挡（判定过窄，抢不回置顶）"

        proxy = _CountProxy(user32)
        fw.user32 = proxy
        try:
            raised = ov.keep_on_top()
            n_raise = proxy.setpos
            for _ in range(20):         # 模拟之后 20 帧
                ov.keep_on_top()
            n_steady = proxy.setpos - n_raise
        finally:
            fw.user32 = _REAL_USER32
        if not raised or n_raise != 1:
            return False, f"被真实置顶窗盖住却没抢回（raised={raised}, n={n_raise}）"
        if ov._covered():
            return False, "抢顶之后仍被判为被盖 —— 抢占没真正生效"
        if n_steady:
            return False, f"抢回后仍反复重排 z 序（20 帧里又抢了 {n_steady} 次）"
    finally:
        fw.user32 = _REAL_USER32
        if occ:
            user32.DestroyWindow(occ)
        ov.destroy()
    return True, "置顶窗压上来 -> 判定为被盖 -> 抢回成功，随后 20 帧 0 次重排"


# ------------------------------------------------- 插入锚点（2026-09-14 根因）
WS_VISIBLE = 0x10000000


def _make_helper(name, x, y, w, h, topmost=True):
    """造一个替身窗口（真实窗口，用于合成"面板 / 对手"）。"""
    ex = fw.WS_EX_TOOLWINDOW | (fw.WS_EX_TOPMOST if topmost else 0)
    h = user32.CreateWindowExW(ex, "Static", name, fw.WS_POPUP | WS_VISIBLE,
                               x, y, w, h, None, None, None, None)
    if not h:
        return 0
    if topmost:
        user32.SetWindowPos(h, fw.HWND_TOPMOST, x, y, w, h,
                            fw.SWP_SHOWWINDOW | fw.SWP_NOACTIVATE)
    return h


def _above(target, other):
    """other 是否位于 target 之上（沿 GW_HWNDPREV 从 target 往上找 other）。

    注意别把方向记反：SetWindowPos(A, B) 是把 A 插到 B **之下**。
    """
    h = user32.GetWindow(fw.HWND(target), fw.GW_HWNDPREV)
    n = 0
    while h and n < 200:
        if hwnd_int(h) == hwnd_int(other):
            return True
        h = user32.GetWindow(h, fw.GW_HWNDPREV)
        n += 1
    return False


def test_real_anchor_ceiling_recovered():
    """真机端到端：面板被对手压住时，覆盖层仍必须能爬到对手之上。

    自己造两个替身把相对位置钉死（不依赖真实远控窗此刻恰好排在哪）：
        fake_panel 先抬到最顶，rival 后抬到最顶  => rival 稳在 fake_panel 之上
    然后把覆盖层压到 rival 之下，并把 panel_hwnd 指向 fake_panel —— 这正是
    "面板已被对手压住"的现场。

    修复前：抢顶一律插回 fake_panel 之下（仍低于 rival），持续被判为被盖；
    修复后：_anchor 发现面板压不住对手，改插置顶组顶部，立刻回到 rival 之上。
    """
    ov = fake_panel = rival = None
    try:
        ov = fw.Overlay().create()
        fake_panel = _make_helper("dfx-fake-panel", ov.x + 20, ov.y + 20, 400, 600)
        rival = _make_helper("dfx-rival", ov.x, ov.y, 1600, 900)
        if not (fake_panel and rival):
            return None, "SKIP（造不出替身窗口）"
        time.sleep(0.3)
        ov.set_panel_hwnd(fake_panel)
        # 压到对手之下（SetWindowPos(me, X) = 插到 X 之下）
        user32.SetWindowPos(ov.hwnd, fw.HWND(rival), 0, 0, 0, 0,
                            fw.SWP_NOMOVE | fw.SWP_NOSIZE | fw.SWP_NOACTIVATE
                            | fw.SWP_NOOWNERZORDER)
        time.sleep(0.15)
        if not ov._covered():
            return None, "SKIP（没能把覆盖层压到替身对手之下，条件不成立）"
        # 模拟主循环帧：被盖就抢
        for _ in range(6):
            ov._last_sig = None
            ov._last_full_check = 0.0
            ov._last_raise = 0.0
            if not ov.keep_on_top(min_interval=0.0):
                break
            time.sleep(0.05)
        if ov._covered():
            return False, ("面板被对手压住时抢顶只是把覆盖层插回面板之下 —— "
                           "永远回不到对手之上（自封顶）")
        if not _is_topmost(ov.hwnd):
            return False, "抢顶之后覆盖层丢了 WS_EX_TOPMOST"
    finally:
        for h in (rival, fake_panel):
            if h:
                try:
                    user32.DestroyWindow(h)
                except Exception:
                    pass
        if ov is not None:
            ov.destroy()
    return True, "面板被对手压住 -> 覆盖层仍抢回对手之上，且置顶位保住"


def test_real_demoted_overlay_self_heals():
    """置顶位被吃掉时必须能自愈。

    怎么会被吃掉：把置顶窗 SetWindowPos 到**非置顶窗**之后（历史锚点用法就会踩中：
    控制面板从未被置顶过、又一直缩在托盘里）。丢掉之后这个窗口被任何普通窗口
    盖住，而且 _covered() 赖以成立的"我们之上的窗口同属 topmost 组"前提也不成立。
    """
    ov = plain = None
    try:
        ov = fw.Overlay().create()
        if not _is_topmost(ov.hwnd):
            return False, "刚建好的覆盖层就没带 WS_EX_TOPMOST"
        plain = _make_helper("dfx-plain", ov.x + 30, ov.y + 30, 300, 200,
                             topmost=False)
        if not plain:
            return None, "SKIP（造不出非置顶窗）"
        user32.SetWindowPos(ov.hwnd, fw.HWND(plain), 0, 0, 0, 0,
                            fw.SWP_NOMOVE | fw.SWP_NOSIZE | fw.SWP_NOACTIVATE
                            | fw.SWP_NOOWNERZORDER)
        time.sleep(0.2)
        if _is_topmost(ov.hwnd):
            return None, "SKIP（本机行为与预期不符：插到非置顶窗之后没丢置顶位）"
        ov._last_sig = None
        ov._last_full_check = 0.0
        ov.keep_on_top(min_interval=0.0)
        if not _is_topmost(ov.hwnd):
            return False, "置顶位被吃掉后 keep_on_top 没有把它补回来"
    finally:
        if plain:
            try:
                user32.DestroyWindow(plain)
            except Exception:
                pass
        if ov is not None:
            ov.destroy()
    return True, "丢掉 WS_EX_TOPMOST -> 下一次 keep_on_top 即自愈"


def test_real_place_above_direction():
    """place_above 必须真的把"自己"放到对方**之上**（方向别再写反）。

    win32 里 SetWindowPos(A, B) 把 A 插到 B **之下**，所以"插到对方之上"不能直接
    把对方当插入点 —— 那正好是反的。早先的实现就是这么写的，于是"鼠标层浮在全屏层
    之上"实际做成了"鼠标层被压在全屏层之下"。两层都是 per-pixel alpha 的透明窗，
    肉眼看不出来，所以一直没暴露。
    """
    a = b = None
    try:
        a = fw.Overlay().create()
        b = fw.Overlay().create()
        # 摆位：先把 b 抬到最顶，再把 a 压到 b 之下
        user32.SetWindowPos(b.hwnd, fw.HWND_TOPMOST, 0, 0, 0, 0,
                            fw.SWP_NOMOVE | fw.SWP_NOSIZE | fw.SWP_NOACTIVATE
                            | fw.SWP_NOOWNERZORDER)
        user32.SetWindowPos(a.hwnd, fw.HWND(b.hwnd), 0, 0, 0, 0,
                            fw.SWP_NOMOVE | fw.SWP_NOSIZE | fw.SWP_NOACTIVATE
                            | fw.SWP_NOOWNERZORDER)
        time.sleep(0.2)
        if not _above(a.hwnd, b.hwnd):
            return None, "SKIP（摆位没成功，b 不在 a 之上）"
        if not a.place_above(b.hwnd):
            return False, "place_above 返回假"
        time.sleep(0.2)
        if _above(a.hwnd, b.hwnd):
            return False, "place_above 之后 b 仍在 a 之上 —— 方向反了"
        if not _above(b.hwnd, a.hwnd):
            return False, "place_above 之后 a 与 b 不在同一条相邻序上"
    finally:
        for ov in (a, b):
            if ov is not None:
                try:
                    ov.destroy()
                except Exception:
                    pass
    return True, "place_above 之后 self 确实位于 other 之上（方向正确）"


# ---------------------------------------------------------------- 显示层级
_GWL = ctypes.windll.user32.GetWindowLongW
_GWL.restype = ctypes.c_long
_GWL.argtypes = [ctypes.c_void_p, ctypes.c_int]
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008


def _is_topmost(hwnd):
    return bool(_GWL(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


def _settle_layer(ov, mode, want_topmost, timeout=6.0):
    """等一次层级切换**真正落地**；返回是否在超时内达到目标状态。

    只等 `z_mode` 会读到中间态：它先翻，窗口样式要等渲染循环下一帧才跟上。
    实测（2026-09-20）切回「最上层」时 `z_mode` 0.1s 内就翻过来了，而
    `WS_EX_TOPMOST` 要再等约 0.25s 才补上 —— 中间那 0.25s 里断言就会在一个
    "正在切"的窗口上判失败。这就是项目里"等层级切换完成不能只等 z_mode"
    那条老坑，只是这次犯在测试自己身上。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ov.z_mode == mode and _is_topmost(ov.hwnd) == bool(want_topmost):
            return True
        time.sleep(0.05)
    return False


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

    层级往返必须用 `_settle_layer` 等**样式**也落定，不能只等 `z_mode`：
    这里换层是渲染循环异步做的，z_mode 先翻、`WS_EX_TOPMOST` 下一帧才补上。
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
        if not _settle_layer(ov_ms, fw.LAYER_DESKTOP, False):
            return False, "运行时切到「仅桌面图层」没生效"
        if not user32.IsWindow(ov_ms.hwnd):
            return False, "切到桌面层后窗口没了"
        if _is_topmost(ov_ms.hwnd):
            return False, "桌面层的鼠标层仍带 WS_EX_TOPMOST"

        app.snapshot = dict(app.snapshot, layer_mouse="topmost")
        if not _settle_layer(ov_ms, fw.LAYER_TOPMOST, True):
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


# ------------------------------------------- 不变量 I：骑面板期间不抢顶
def test_panel_ride_suppresses_keep_on_top():
    """临时"骑在面板之上"（面板空白处的点击特效播放中）时，keep_on_top
    必须整段让位，一次 SetWindowPos 都不许发。

    背景（实测踩过的坑）：提升之后本窗口排在面板之上，`_anchor` 的资格
    检查恰好判"面板压得住任何对手"，锚点就变成面板 —— keep_on_top 每帧把
    我们插回面板之下，提升只活一帧，面板上永远看不到文字/表情。所以
    raise_topmost 置 _ride_above_panel，keep_on_top 必须认这个标志。
    """
    ov, fake = _patched({OVERLAY: PANEL, PANEL: 0}, {PANEL}, panel=PANEL)
    ov._ride_above_panel = True
    moved = ov.keep_on_top()
    if moved or fake.setpos:
        return False, (f"骑面板期间 keep_on_top 仍发 {len(fake.setpos)} 次 "
                       f"SetWindowPos —— 提升会被自己打回去，面板上"
                       f"永远看不到点击特效")
    return True, "骑面板期间 -> 0 次 SetWindowPos（提升不被自我否定）"


def test_panel_ride_restored_after_leave():
    """结束骑乘后 keep_on_top 必须恢复正常抢顶能力。"""
    ov, fake = _patched({OVERLAY: OTHER, OTHER: 0}, {OTHER}, panel=PANEL,
                        classes={hwnd_int(OTHER): "SomeTopmostApp"})
    ov._ride_above_panel = True
    if ov.keep_on_top() or fake.setpos:
        return False, "骑乘期间就抢顶了"
    # 结束骑乘（fake 的 SetWindowPos 只记录，不改标志 —— 标志由 leave 管）
    ov._ride_above_panel = False
    fake.setpos.clear()
    ov.keep_on_top()
    if not fake.setpos:
        return False, "结束骑乘后仍不抢顶（真实遮挡存在却没恢复）"
    return True, "结束骑乘 -> 抢顶恢复"


def test_raise_topmost_sets_ride_flag():
    """raise_topmost 必须置 _ride_above_panel（否则上面两条都不成立）。"""
    ov, fake = _patched({OVERLAY: PANEL, PANEL: 0}, {PANEL}, panel=PANEL)
    if ov.raise_topmost() is False:
        return False, "raise_topmost 返回失败"
    if not ov._ride_above_panel:
        return False, "raise_topmost 没有置 _ride_above_panel"
    if not fake.setpos:
        return False, "raise_topmost 没发 SetWindowPos"
    if ov.leave_panel_ride(PANEL) is False:
        return False, "leave_panel_ride 返回失败"
    if ov._ride_above_panel:
        return False, "leave_panel_ride 没清 _ride_above_panel"
    return True, "raise_topmost 置位骑乘标志、leave_panel_ride 清位"


def test_keep_riding_panel_self_heals():
    """骑乘期间每帧自检：被面板抢顶打下去后必须补一次提升。

    背景（实测）：控制面板自己有 1s 一次的置顶保活（fx_ui._raise_top ->
    SetWindowPos(HWND_TOPMOST)），会把面板顶到我们之上，把点击特效重新压回
    面板底下 —— 实测提升只活 ~0.3s。keep_riding_panel 就是每帧问一次
    "我还在面板之上吗"，掉了就补一次 SetWindowPos。
    """
    # 链上找不到面板 -> 说明面板确实在我们之上（我们被打下去了）-> 应补提升
    ov, fake = _patched({OVERLAY: 0}, set(), panel=PANEL)
    ov._ride_above_panel = True
    healed = ov.keep_riding_panel(PANEL)
    if not healed:
        return False, "被打到面板之下后没有补提升（特效只活 0.3s 就消失）"
    if not fake.setpos:
        return False, "补提升时没发 SetWindowPos"
    # 仍在面板之上 -> 不该乱动
    fake.setpos.clear()
    ov, fake = _patched({OVERLAY: PANEL, PANEL: 0}, {PANEL}, panel=PANEL)
    ov._ride_above_panel = True
    if ov.keep_riding_panel(PANEL):
        return False, "位置正常时不该重复提升（每帧发 SetWindowPos 会闪）"
    if fake.setpos:
        return False, f"位置正常时仍发了 {len(fake.setpos)} 次 SetWindowPos"
    # 不在骑乘状态 -> 什么都不做
    ov, fake = _patched({OVERLAY: 0}, set(), panel=PANEL)
    if ov.keep_riding_panel(PANEL) or fake.setpos:
        return False, "非骑乘状态不该动 z 序"
    return True, "骑乘自愈：掉了就补、没掉不动、非骑乘不碰"


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
        test_anchor_refuses_panel_below_blocker,
        test_anchor_refuses_non_topmost_panel,
        test_occluder_found_past_helpers,
        test_raise_is_throttled,
        test_chain_change_triggers_immediate_recheck,
        test_panel_ride_suppresses_keep_on_top,
        test_panel_ride_restored_after_leave,
        test_raise_topmost_sets_ride_flag,
        test_keep_riding_panel_self_heals,
        test_real_window_steady_state,
        test_real_window_geometry_sane,
        test_real_foreign_topmost_is_recovered,
        test_real_anchor_ceiling_recovered,
        test_real_demoted_overlay_self_heals,
        test_real_place_above_direction,
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
