# -*- coding: utf-8 -*-
"""Win32 per-pixel-alpha 分层覆盖窗口。依赖 _win32 预设的函数签名。"""
import ctypes
import ctypes.wintypes as wt
import time

import numpy as np

from _win32 import (user32, gdi32, kernel32, HANDLE, HWND, HDC, HBITMAP,
                    WPARAM, LPARAM, LRESULT, BOOL, UINT, DWORD, INT, RECT,
                    hwnd_int)
from fx_kinds import LAYER_DESKTOP, LAYER_TOPMOST

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
SW_SHOWNOACTIVATE = 4
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
BI_RGB = 0
DIB_RGB_COLORS = 0
HWND_TOPMOST = wt.HWND(-1)
HWND_NOTOPMOST = wt.HWND(-2)   # 取消置顶
HWND_BOTTOM = wt.HWND(1)       # 沉到 z 序最底（仅桌面层级的降级方案）
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_SHOWWINDOW = 0x0040
GWL_STYLE = -16
WS_CHILD = 0x40000000
WS_POPUP_STYLE = 0x80000000
SWP_NOACTIVATE = 0x0010
# 不保存/恢复被遮挡处的位图：本窗口内容 100% 由 UpdateLayeredWindow 提供，
# 让系统去 copy bits 反而会在 z 序变动时翻出无效内容（可见的一帧闪烁）。
SWP_NOCOPYBITS = 0x0100
SWP_NOOWNERZORDER = 0x0200
# GetWindow 的 z 序方向：返回"位于该窗口之上"的邻窗
GW_HWNDPREV = 3
GWL_EXSTYLE = -20

# 沿 z 序链向上找窗口时的步数上限。链上常夹着一堆 0x0 / 1x1 的系统辅助窗
# （IME、MSCTFIME、tooltips、ForegroundStaging），给足余量但绝不无限走。
_CHAIN_GUARD = 64


def _is_topmost_window(hwnd):
    """窗口是否在置顶组里（WS_EX_TOPMOST）。

    它决定一个窗口能不能当"插入锚点"：SetWindowPos(A, B) 是把 A 插到 **B 之下**，
    而插到**非置顶**窗之下会把 A 的 WS_EX_TOPMOST 一起清掉（实测 —— 见下方
    Overlay._anchor 的说明）。所以当锚点之前必须先验这一位。
    """
    return bool(user32.GetWindowLongW(HWND(hwnd), GWL_EXSTYLE) & WS_EX_TOPMOST)


def _is_above(hwnd, other_hwnd):
    """other_hwnd 是否位于 hwnd 之上（沿 GW_HWNDPREV 往上找）。

    注意 win32 的语义：SetWindowPos(A, B) 把 A 插到 B **之下**（B 在 A 之上），
    与 HWND_TOP / HWND_TOPMOST（插到最顶）是同一条规则的两个端点。
    """
    if not hwnd or not other_hwnd:
        return False
    target = hwnd_int(other_hwnd)
    h = user32.GetWindow(HWND(hwnd), GW_HWNDPREV)
    n = 0
    while h and n < _CHAIN_GUARD:
        if hwnd_int(h) == target:
            return True
        h = user32.GetWindow(h, GW_HWNDPREV)
        n += 1
    return False

# 这些窗口即使 IsWindowVisible 为真也从不真正遮住别人（桌面 / 任务栏 / 阴影）。
# 判"是否被盖住"时必须排除，否则会一直误判成被盖。
_NON_OCCLUDERS = frozenset((
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "SysShadow", "ForegroundStaging",
))
# 构成遮挡所需的最小边长（px）。系统与驱动辅助窗普遍是 0x0 / 1x1（IME、
# ForegroundStaging、SynTP 触摸板、缩略图 helper），只看可见性会把它们
# 全算成遮挡 —— 那样每 0.5s 都会触发一次抢顶。
_OCCLUDER_MIN_PX = 32

# 同一层两次"真正抢顶"之间的最小间隔（秒）。主循环现在**每帧**问一次
# "我是不是被盖住了"（反应要快，见 keep_on_top），但真动手复位要限流：
# 万一对面是个抢不过的窗口、或 SetWindowPos 反复失败，不限流就退化成
# 每秒 60 次 z 序重排 —— 每次都让 DWM 重新合成整屏，闪且白烧 GPU。
_RAISE_MIN_INTERVAL = 0.1

# 每帧自检的廉价短路：只读我们之上最近这么多个窗口的 hwnd 当"链签名"。
#
# 为什么这是**正确**的而不只是省事：能盖住全屏覆盖窗的窗口，一定是被对方抬到了
# topmost 组顶部（不抬到我们头上它就盖不住我们），因此必然出现在这前几个之内 ——
# 它的出现一定会改变签名，于是那一帧就会触发全量检查。取 4 是留余量：链首常常是
# 1x1 的驱动辅助窗（ThumbnailDeviceHelperWnd 之类），多发几个也只是几次 GetWindow。
#
# 为什么需要它：全量检查要读每个窗口的矩形（实测 18µs/次）与类名（4.3µs/次），
# 一次约 74µs（渲染循环里因 GIL 争用还会放大到 ~340µs）；而 GetWindow 只要 0.73µs。
# 每帧做两次全量检查 = 白烧几个点的 CPU，只读签名则几乎免费。
_SIG_DEPTH = 4

# 两次"全量被盖检查"之间的最小间隔（秒）：链**没变**时才受它限制，链一变就立刻
# 重查。它兜的是"链没变、但链上某个窗口自己变可见 / 长到能盖住我们"这种极端情况，
# 同时也给"已经知道被盖、正在限流"的状态一个上界（最多 1/该值 次/秒的复查）。
_FULL_CHECK_INTERVAL = 0.25


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_ulong), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_ushort),
                ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_ulong),
                ("biSizeImage", ctypes.c_ulong), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_ulong),
                ("biClrImportant", ctypes.c_ulong)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_ulong * 3)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
                ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", HANDLE),
                ("hIcon", HANDLE), ("hCursor", HANDLE), ("hbrBackground", HANDLE),
                ("lpszMenuName", ctypes.c_wchar_p), ("lpszClassName", ctypes.c_wchar_p),
                ("hIconSm", HANDLE)]


def virtual_screen():
    x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return x, y, max(1, w), max(1, h)


# 系统任务栏的窗口类：主屏一个 Shell_TrayWnd，每块副屏各一个 Shell_SecondaryTrayWnd。
_SYSTEM_BAR_CLASSES = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd")


def system_bar_rects():
    """当前所有系统任务栏的**屏幕矩形** `[(left, top, right, bottom), ...]`。

    为什么需要它：覆盖窗是 `WS_EX_TOPMOST`，**任务栏不是** —— 于是特效会被直接
    画在任务栏上面，用户报的"真实水波在下方任务栏会有残影"就是这么来的（一道
    半透明的明暗条纹糊在任务栏上）。上屏前把这几块矩形擦干净，任务栏就始终
    干净；观感上等于"任务栏浮在特效之前"，这也正是用户的预期。

    用窗口类枚举、而不是 `SHAppBarMessage(ABM_GETTASKBARPOS)`：后者只认主屏
    那一个（微软文档明说多显示器时返回主任务栏的位置）。按类枚举则天然拿到
    每块屏的任务栏，而且：

    * 自动隐藏时系统会把任务栏整个挪到屏幕外，矩形与虚拟屏不相交 —— 不用特判；
    * 任务栏挪到屏幕上/左/右也一样成立，不假设它在底部。

    拿不到、或某个任务栏不可见时直接跳过（擦空气是白费力气）。
    """
    out = []
    for cls in _SYSTEM_BAR_CLASSES:
        h = user32.FindWindowW(cls, None)
        while h:
            try:
                if user32.IsWindowVisible(h):
                    r = RECT()
                    if user32.GetWindowRect(h, ctypes.byref(r)) and \
                            r.right > r.left and r.bottom > r.top:
                        out.append((r.left, r.top, r.right, r.bottom))
            except Exception:
                pass
            # 同一类的下一个（多显示器下每块屏一个）
            h = user32.FindWindowExW(None, h, cls, None)
    return out


def blank_rects(u32, w, h, ox, oy, rects):
    """把若干**屏幕坐标**矩形从 32 位缓冲区里清零（越界自动裁剪）。

    `ox, oy` 是缓冲区左上角对应的屏幕坐标（虚拟屏原点，常为负）。返回被擦掉的
    像素数，供诊断与测试断言用。
    """
    if not rects:
        return 0
    arr = u32.reshape(int(h), int(w))
    n = 0
    for (l, t, r, b) in rects:
        x0, x1 = max(0, int(l) - ox), min(int(w), int(r) - ox)
        y0, y1 = max(0, int(t) - oy), min(int(h), int(b) - oy)
        if x1 > x0 and y1 > y0:
            arr[y0:y1, x0:x1] = 0
            n += (x1 - x0) * (y1 - y0)
    return n


def _wnd_proc(hwnd, msg, wparam, lparam):
    global _show_panel_flag
    if _MSG_SHOW_PANEL and msg == _MSG_SHOW_PANEL:
        # 第二个实例启动时发来的：把已运行实例的控制面板叫出来
        _show_panel_flag = True
        return 1
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_WNDPROC_CB = ctypes.WINFUNCTYPE(LRESULT, HANDLE, UINT, WPARAM, LPARAM)(_wnd_proc)
_CLASS_NAME = "DesktopFXOverlayCls"
_CLASS_REGISTERED = False

# 跨进程"调出面板"消息。RegisterWindowMessageW 返回的值在整个系统内一致，
# 所以第二个进程用同一个名字注册就能拿到同一个 id。
SHOW_PANEL_MSG_NAME = "DesktopFX_ShowPanel_v1"
_MSG_SHOW_PANEL = 0
_show_panel_flag = False


def show_panel_message():
    """返回"调出面板"消息 id（系统内唯一）；0 表示注册失败。"""
    global _MSG_SHOW_PANEL
    if _MSG_SHOW_PANEL == 0:
        _MSG_SHOW_PANEL = user32.RegisterWindowMessageW(SHOW_PANEL_MSG_NAME) or 0
    return _MSG_SHOW_PANEL


def take_show_panel_request():
    """消费一次"调出面板"请求（UI 线程轮询，避免跨线程直接操作 tkinter）。"""
    global _show_panel_flag
    if _show_panel_flag:
        _show_panel_flag = False
        return True
    return False


def _register_class():
    global _CLASS_REGISTERED
    if _CLASS_REGISTERED:
        return
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = ctypes.cast(_WNDPROC_CB, ctypes.c_void_p)
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = _CLASS_NAME
    user32.RegisterClassExW(ctypes.byref(wc))
    _CLASS_REGISTERED = True


# 给 Progman 发这条消息，资源管理器会确保"承载壁纸的 WorkerW"存在
WM_SPAWN_WORKER = 0x052C
SMTO_NORMAL = 0x0000
_ENUM_CB = ctypes.WINFUNCTYPE(BOOL, HWND, LPARAM)


def _find_wallpaper_workerw():
    """找到"承载桌面壁纸"的那个 WorkerW；找不到返回 0。

    Windows 把桌面拆成两层：Progman / SHELLDLL_DefView 放图标，另一个
    WorkerW 放壁纸（在图标层之下）。0x052C 是让资源管理器把这个壁纸层
    建出来的既成手法 —— 失败也不影响（返回 0 由调用方降级）。

    返回的窗口**不保证覆盖整个虚拟屏**：多显示器下每块屏各自一个 WorkerW。
    调用方必须自己核对覆盖范围，否则挂上去会让副屏特效整片消失。
    """
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        return 0
    dw = ctypes.c_size_t(0)
    try:
        user32.SendMessageTimeoutW(progman, WM_SPAWN_WORKER, 0, 0, SMTO_NORMAL,
                                   1000, ctypes.byref(dw))
    except Exception:
        pass
    found = []

    def _cb(hwnd, _lp):
        # 挂着 SHELLDLL_DefView（图标层）的 WorkerW 的下一个兄弟 = 壁纸层
        if user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None):
            w = user32.FindWindowExW(None, hwnd, "WorkerW", None)
            if w:
                found.append(w)
        return True

    try:
        cb = _ENUM_CB(_cb)      # 必须持引用：临时对象会在枚举期间被回收
        user32.EnumWindows(cb, 0)
    except Exception:
        return 0
    return hwnd_int(found[0]) if found else 0


def _client_origin(hwnd):
    """窗口**客户区原点**在屏幕坐标里的位置。

    挂成子窗口后，窗口的 x/y 是相对父窗口客户区的，而客户区左上角在屏幕上的
    位置未必是父窗口矩形的左上角（有边框/菜单时两者不同）。所以统一用
    ClientToScreen 精确取，不要拿 GetWindowRect 的 left/top 顶替。
    """
    pt = POINT(0, 0)
    if user32.ClientToScreen(HWND(hwnd), ctypes.byref(pt)):
        return (pt.x, pt.y)
    r = RECT()                      # 兜底：取不到就当没有非客户区
    if user32.GetWindowRect(HWND(hwnd), ctypes.byref(r)):
        return (r.left, r.top)
    return (0, 0)


class Overlay:
    def __init__(self, z_mode=LAYER_TOPMOST):
        self.z_mode = z_mode
        self._parented = False          # 是否已挂成 WorkerW 的子窗口
        self._desktop_fallback = False  # 桌面层是否走了"沉底"降级
        self.hwnd = None
        self._hdc_screen = None
        self._hdc_mem = None
        self._hbmp = None
        self._old_bmp = None
        self._bits = ctypes.c_void_p()
        self._cbuf = None
        self.x = self.y = 0
        self.w = self.h = 1
        # 窗口本地原点(0,0)对应的屏幕坐标。顶层窗就是 (0,0)；挂成 WorkerW 的
        # 子窗口后等于父窗口**客户区**原点。present() 必须按它把屏幕坐标折算成
        # 当前坐标系下的坐标，否则子窗口会被越挪越远（见 present 注释）。
        self._origin = (0, 0)
        self.buffer = None
        self.flat = None
        self.u32 = None
        self._stride = np.int32(1)
        # 控制面板 hwnd。面板是我们自己的窗口，必须从"谁盖住了我"的判定里排除，
        # 否则 overlay 会跟面板无限互抢 z 序（见 keep_on_top）。
        self.panel_hwnd = None
        # 临时"骑在面板之上"（面板空白处的点击特效播放期间）。这期间
        # keep_on_top 必须整段跳过，否则它会把我们又插回面板之下 ——
        # 详见 raise_topmost 的说明。
        self._ride_above_panel = False
        self._last_raise = 0.0          # 上次真正抢顶的时刻（单调钟），供限流
        # 每帧自检的短路状态：链签名 + 上次全量检查时刻（见 _chain_sig）
        self._last_sig = None
        self._last_full_check = 0.0

    def create(self):
        self._create_window()
        self._alloc_bitmap(self.w, self.h)
        self.apply_z_mode(self.z_mode)
        return self

    def _create_window(self):
        """只建窗口（不含位图）。显示层级统一由 apply_z_mode 施加。"""
        _register_class()
        show_panel_message()  # 必须在窗口创建前注册，wndproc 才知道要拦哪个号
        x, y, w, h = virtual_screen()
        self.x, self.y, self.w, self.h = x, y, w, h
        self._origin = (0, 0)           # 新建的是顶层窗，本地原点即屏幕原点
        # 刻意**不带** WS_EX_TOPMOST：层级运行时可改（最上层 / 仅桌面图层），
        # 统一交给 SetWindowPos 施加，省得来回改扩展样式。
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            _CLASS_NAME, "DesktopFX Overlay", WS_POPUP,
            x, y, w, h, None, None, kernel32.GetModuleHandleW(None), None)
        if not self.hwnd:
            raise OSError("CreateWindowExW failed", ctypes.get_last_error())
        # 必须有这一步：CreateWindowExW 不带 WS_VISIBLE 时窗口默认隐藏，
        # UpdateLayeredWindow 只会更新位图，不会让它出现在桌面上。
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        if not user32.IsWindowVisible(self.hwnd):
            raise OSError("overlay window is not visible after ShowWindow")
        self._parented = False

    def _alloc_bitmap(self, w, h):
        if self._hbmp:
            gdi32.SelectObject(self._hdc_mem, self._old_bmp)
            gdi32.DeleteObject(self._hbmp)
            self._hbmp = None
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        if not self._hdc_screen:
            self._hdc_screen = user32.GetDC(None)
        if not self._hdc_mem:
            self._hdc_mem = gdi32.CreateCompatibleDC(self._hdc_screen)
        self._bits = ctypes.c_void_p()
        self._hbmp = gdi32.CreateDIBSection(self._hdc_screen, ctypes.byref(bmi),
                                            DIB_RGB_COLORS, ctypes.byref(self._bits), None, 0)
        if not self._hbmp:
            raise OSError("CreateDIBSection failed", ctypes.get_last_error())
        self._old_bmp = gdi32.SelectObject(self._hdc_mem, self._hbmp)
        n = w * h * 4
        self._cbuf = ctypes.cast(self._bits, ctypes.POINTER(ctypes.c_ubyte * n)).contents
        flat = np.frombuffer(self._cbuf, dtype=np.uint8)
        self.flat = flat.reshape(h * w, 4)
        self.buffer = self.flat.reshape(h, w, 4)
        self.u32 = self.flat.reshape(-1).view(np.uint32)
        self.w, self.h = w, h
        self._stride = np.int32(w)

    def resize_if_needed(self):
        x, y, w, h = virtual_screen()
        if (x, y, w, h) != (self.x, self.y, self.w, self.h):
            self.x, self.y = x, y
            self._alloc_bitmap(w, h)
            self.apply_z_mode(self.z_mode)   # 位置/层级随新尺寸一起重算
            return True
        return False

    def present(self):
        # pptDst 的坐标系**跟着窗口走**：顶层窗是屏幕坐标，而 WS_CHILD 子窗口是
        # 父窗口客户区坐标。桌面层的窗口挂在 WorkerW 下，若这里仍传屏幕坐标
        # self.x/self.y，窗口会被再挪一个"父客户区原点"的距离，而且是每帧都挪
        # —— 实测在"副屏在主屏左侧"的布局里（虚拟屏原点 x=-1920），桌面层窗口
        # 稳定停在屏幕 (-3840, 0)，主屏上完全看不到桌面层特效。
        # 所以统一减掉 _origin 折算到当前坐标系。
        ox, oy = self._origin
        pt_dst = POINT(self.x - ox, self.y - oy)
        pt_src = POINT(0, 0)
        size = SIZE(self.w, self.h)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        if not user32.UpdateLayeredWindow(self.hwnd, self._hdc_screen, ctypes.byref(pt_dst),
                                          ctypes.byref(size), self._hdc_mem, ctypes.byref(pt_src),
                                          0, ctypes.byref(blend), ULW_ALPHA):
            raise OSError("UpdateLayeredWindow failed", ctypes.get_last_error())

    def pump(self):
        msg = wt.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), self.hwnd, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def set_panel_hwnd(self, hwnd):
        """登记控制面板 hwnd（面板创建 / 重建时调用）。

        两个用途，都不是可选的：
        1. 从"谁盖住了我"的判定里排除 —— 面板浮在特效之上是预期状态；
        2. 抢顶时的插入锚点（插到面板正下方，既复位又不遮住设置界面）——
           但只在"面板自己是置顶窗、且确实压得住对手"时才用它，见 _anchor。
        """
        v = hwnd_int(hwnd)
        self.panel_hwnd = v or None

    @staticmethod
    def _is_occluder(h):
        """该窗口是否构成"真的盖住了我"。

        不能只看 IsWindowVisible：桌面上常驻一堆"可见但零面积"的系统与驱动
        辅助窗（ForegroundStaging / IME / 触摸板 SynTP / 缩略图 helper），它们
        就挂在 topmost 组里、位于我们之上，却什么也不画。早先版本用"链上第一个
        可见窗口"判定 -> 每 0.5s 都判成被盖 -> 每 0.5s 抢一次顶。故要求：
        可见 + 有实际尺寸 + 类名不在 shell 组件黑名单里。
        """
        if not user32.IsWindowVisible(h):
            return False
        r = RECT()
        if not user32.GetWindowRect(h, ctypes.byref(r)):
            return False
        if (r.right - r.left) < _OCCLUDER_MIN_PX or \
                (r.bottom - r.top) < _OCCLUDER_MIN_PX:
            return False
        buf = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(h, buf, 128)
        # 自家的另一层（全屏层 / 鼠标层）当然压在自己上面，不算"被外人盖住"：
        # 否则两层会互相判定被盖、各自每 0.5s 抢一次顶，表现为周期性闪烁。
        return buf.value not in _NON_OCCLUDERS and buf.value != _CLASS_NAME

    def _covered_by(self):
        """返回压在我们之上、被判为遮挡物的**第一个**窗口 hwnd（没有则 0）。

        沿 z 序向上逐个检查（GW_HWNDPREV）：我们是 WS_EX_TOPMOST，链上的窗口
        必然同属 topmost 组。

        关于控制面板：**2026-10-19 起面板不再是置顶窗**（普通窗口，与其他
        软件一致），而我们是 topmost —— 两者根本不在同一个 z 序组，这条链
        上溯**永远碰不到它**，下面那个"排除面板"的判断因此成了死代码。
        保留它只为兼容"面板被改回置顶"的旧配置路径，不删是因为它在那种情况
        下依然是必要的：面板压住特效是预期状态，不该被判成故障去抢顶。

        返回 hwnd 而不是 bool 的原因：抢顶时要判断"面板是不是比这个对手高"
        （见 _anchor）——拿面板当锚点只有在面板确实压得住对手时才成立。
        """
        if not self.hwnd:
            return 0
        h = user32.GetWindow(self.hwnd, GW_HWNDPREV)
        guard = 0
        while h and guard < _CHAIN_GUARD:
            guard += 1
            if not (self.panel_hwnd and hwnd_int(h) == self.panel_hwnd):
                if self._is_occluder(h):
                    return hwnd_int(h)
            h = user32.GetWindow(h, GW_HWNDPREV)
        return 0

    def _covered(self):
        """本窗口是否被"别人的"可见置顶窗口盖住（_covered_by 的布尔形式）。"""
        return bool(self._covered_by())

    def _anchor(self, blocker=0):
        """抢顶时的插入锚点。

        Win32 语义（实测确认，含"插到非置顶窗之后会丢掉 WS_EX_TOPMOST"一条）：
            SetWindowPos(A, B) 把 A 插到 **B 之下** —— B 在 A 之上。

        **当前实况（2026-10-19 起）：面板已不是置顶窗**（fx_ui 去掉了
        `-topmost` 与每秒抢顶，面板就是普通窗口），所以下面第一道关
        `_is_topmost_window(p)` 恒为假、**永远返回 HWND_TOPMOST** —— 抢顶
        一律插到置顶组顶部，不拿面板当锚点。这也正是想要的：特效层是 topmost，
        天然压在普通窗口（面板）之上，"面板上的点击特效可见"不再需要任何
        临时提升机制。

        保留面板锚点分支只为兼容"面板被改回置顶"的旧路径，它本身的坑必须
        记住：
        * after = 面板 是"插到面板正下方"，能复位又不压掉设置界面；
        * 但只在面板确实比对手高时成立 —— Citrix 那类十几 Hz 重新置顶的远控
          全屏窗很容易把面板压在下面，那时拿面板当锚点就是把它的位置当天花板，
          每次抢顶都只是把自己插回面板之下，**永远回不到对手之上**
          （实测：锚点=面板 -> 被盖采样 100%，抢顶 78 次全白费）；
        * 更硬的约束：锚点自己不是置顶窗时，SetWindowPos 会把我们的
          WS_EX_TOPMOST 一起清掉，之后任何普通窗口都能盖住特效，
          而 keep_on_top 每次"抢顶"只是又插回同一个非置顶锚点之后 ——
          实测连抢 30 次仍丢失。所以锚点资格必须先验。
        """
        p = self.panel_hwnd
        if not p or not _is_topmost_window(p):
            return HWND_TOPMOST
        # 注意 _is_above(hwnd, other) 问的是"other 在 hwnd 之上"。
        # 我们要确认的是"面板压得住对手"，也就是面板在对手**之上**：
        #   _is_above(blocker, p) == True  -> 面板在上，可以拿它当锚点
        #   False                          -> 面板自己都被压住了，它当不了天花板
        if blocker and not _is_above(blocker, p):
            return HWND_TOPMOST
        return HWND(p)

    def _chain_sig(self):
        """我们之上最近 `_SIG_DEPTH` 个窗口的 hwnd（z 序自上而下）。

        只做 `GetWindow`（~0.7µs/次），**不读矩形也不读类名** —— 这是"每帧自检"
        得以便宜的关键。它只用来判断"链变没变"：变了才去做昂贵的全量检查。
        """
        out = []
        h = user32.GetWindow(self.hwnd, GW_HWNDPREV)
        n = 0
        while h and n < _SIG_DEPTH:
            out.append(hwnd_int(h))
            h = user32.GetWindow(h, GW_HWNDPREV)
            n += 1
        return tuple(out)

    def keep_on_top(self, min_interval=_RAISE_MIN_INTERVAL):
        """把叠加层钉回最上层 —— 但只在真的被盖住时才动手。

        窗口创建时虽带 WS_EX_TOPMOST，但只要用户点开一个同样置顶的应用
        （全屏视频 / 画中画 / 悬浮窗 / Citrix 之类的远程桌面全屏窗），对方被
        激活后就会压过我们。topmost 不会因为普通窗口激活而丢失，所以"点击后
        特效跑到后面"几乎都是被"别的置顶窗口"盖住了。

        关键：**不能无条件周期调用**。控制面板自己也在做置顶保活，若 overlay
        无条件抢顶，就会和面板的抢顶互相翻转 z 序 —— 表现为"每隔几秒画面闪
        一下"。所以先判定是否真被盖，未被盖直接返回。

        调用频率是**每帧**（主循环就这么调），所以两个"别太贵"的闸门：

        * **链签名短路**：链上最近的窗口没变、且距上次全量检查不到
          `_FULL_CHECK_INTERVAL` —— 直接返回。全量检查要读每个窗口的矩形与类名
          （74µs 起，渲染循环里因 GIL 争用可到 340µs），而读签名只要 0.7µs/次。
          被盖住的关键事件（对方被抬到我们头上）必然改变签名，所以短路不会
          让反应变慢：链一变当帧就查。
        * **抢顶限流** `min_interval`：真正的 `SetWindowPos` 之间至少隔这么久，
          防止抢不过时退化成每帧重排 z 序。

        顺序是刻意的：判"被盖"必须发生在抢顶限流之前，否则"没有遮挡时一次
        SetWindowPos 都不许发"这条不变量就变成"被限流挡住了"，测试会失效。
        """
        if not self.hwnd or self.z_mode != LAYER_TOPMOST:
            return False
        # 临时"骑在面板之上"期间（面板空白处的点击特效播放中）**整段跳过
        # 抢顶**。不跳过的话会自相矛盾地把自己打回去：被提升后本窗口排在
        # 面板之上，_anchor 的资格检查恰好判"面板压得住任何对手"，锚点就
        # 变成面板，每帧 SetWindowPos 把自己插回面板之下 —— 提升只活一帧，
        # 面板上永远看不到文字/表情（实测：raise 后立刻测仍是"面板之下"）。
        if self._ride_above_panel:
            return False
        # 置顶位自愈：一旦 WS_EX_TOPMOST 掉了，这个窗口就被任何普通窗口盖住，
        # 而链签名/遮挡判定的前提（"我们之上的窗口同属 topmost 组"）也跟着失效。
        # 掉的原因就是上面那条 —— 曾经把锚点插到了非置顶窗（例如从未被置顶过的
        # 控制面板）之后。读一次扩展样式只要 ~0.3µs，每帧做也划算。
        if not _is_topmost_window(self.hwnd):
            self.apply_z_mode(LAYER_TOPMOST)
            self._last_sig = None
            return True
        now = time.monotonic()
        sig = self._chain_sig()
        if sig == self._last_sig and now - self._last_full_check < _FULL_CHECK_INTERVAL:
            return False
        self._last_sig = sig
        self._last_full_check = now
        blocker = self._covered_by()
        if not blocker:
            return False
        if min_interval > 0.0:
            if now - self._last_raise < min_interval:
                return False
            self._last_raise = now
        # 锚点怎么选见 _anchor：面板只在"它压得住对手"时才配当锚点，否则直接
        # 插到置顶组顶部。NOCOPYBITS 避免系统去保存/恢复被遮挡位图 —— 那会在
        # 重排瞬间翻出一帧无效内容。
        user32.SetWindowPos(self.hwnd, self._anchor(blocker), 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
                            | SWP_NOCOPYBITS | SWP_NOOWNERZORDER)
        # 抢顶会改变链（我们从别人下面挪到了上面），把签名作废，
        # 下一帧必定重新全量检查一次 —— 否则会拿着旧签名跳过一整轮，
        # 万一同一个对手立刻又抬上来，我们就要等满 _FULL_CHECK_INTERVAL 才发现。
        self._last_sig = None
        return True

    # ------------------------------------------------------------ 显示层级
    def apply_z_mode(self, mode):
        """施加显示层级：LAYER_TOPMOST 最上层 / LAYER_DESKTOP 仅桌面图层。

        桌面层优先挂到壁纸层（WorkerW 的子窗口）；挂不上就降级为"取消置顶 +
        沉到 z 序最底"—— 两者对用户的观感一致（任何窗口都能盖住特效），
        但降级方案保住了多显示器覆盖，所以是可接受的兜底。
        """
        mode = LAYER_DESKTOP if mode == LAYER_DESKTOP else LAYER_TOPMOST
        self.z_mode = mode
        if not self.hwnd:
            return mode
        if mode == LAYER_DESKTOP:
            # 先摘掉置顶：从"最上层"切过来时 WS_EX_TOPMOST 还挂着，带着它挂
            # 到桌面层会被忽略（桌面层窗口不该是 topmost），也容易被后续抢顶
            # 逻辑误判。HWND_NOTOPMOST 会把这个扩展样式位清掉。
            user32.SetWindowPos(self.hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
                                | SWP_NOCOPYBITS | SWP_NOOWNERZORDER)
            self._desktop_fallback = not self._attach_desktop()
            if self._desktop_fallback:
                self._detach_parent()
                user32.SetWindowPos(self.hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
                                    | SWP_NOCOPYBITS | SWP_NOOWNERZORDER)
        else:
            self._desktop_fallback = False
            # 先记下"切过来之前我们是不是置顶窗"：从桌面层（WorkerW 的 WS_CHILD
            # 子窗口）切回来时，沿 GW_HWNDPREV 得到的是一条子窗口链，拿它当"被谁
            # 盖住"毫无意义，会把随便一个桌面图标窗口当成对手。
            was_topmost = _is_topmost_window(self.hwnd)
            self._detach_parent()
            # 锚点同样要过 _anchor 的资格检查：用户点了「最上层」却发现特效还在
            # 对手之下，最常见的原因就是这里又把窗口插回了"已经被对手压住的面板"
            # 之下。
            blocker = self._covered_by() if was_topmost else 0
            user32.SetWindowPos(self.hwnd, self._anchor(blocker),
                                self.x, self.y, self.w, self.h,
                                SWP_NOACTIVATE | SWP_NOCOPYBITS
                                | SWP_NOOWNERZORDER | SWP_SHOWWINDOW)
        return mode

    def _rebuild_layered_surface(self):
        """重装 WS_EX_LAYERED，迫使系统重建分层表面。

        **SetParent 会把分层表面弄死**（真机实测，2026-09-15）：
        把分层窗口挂成 WorkerW 的子窗口（或摘回来）之后，UpdateLayeredWindow
        依旧返回成功，IsWindowVisible / 屏幕矩形 / WS_EX_TOPMOST 全部正常，
        但窗口**再也不出现在屏幕上** —— 用户看到的就是"切到桌面层后特效消失，
        切回最上层也恢复不了"。把 WS_EX_LAYERED 摘掉再装回去，系统会重建分层
        表面，画面立刻回来（实测：0/6 → 6/6 采样点可见）。

        挂载 / 解挂之后都必须走一遍：两个方向都是 SetParent，同一条根因。

        **装回样式后必须立刻重新上屏**（真机实测，2026-09-15）：重新加上
        WS_EX_LAYERED 会把分层属性**重置**，窗口在下次 UpdateLayeredWindow 之前
        不再显示任何内容。而本方法是在渲染循环第 3 步被调用的，下一次常规上屏
        在第 5 步 —— 中间隔着 keep_on_top 与整个 scene.step（真实水波下可达
        几十毫秒），窗口就白着那么久，用户看到"切换图层时特效闪一下"。
        实测（子窗口态）：摘装后不补上屏 → 屏幕读回壁纸色 0x97401c（画面没了）；
        补一次上屏 → 立刻恢复。
        DIB 里仍是上一帧的画面，所以这里补上屏等于"多显示一帧旧内容"，
        代价为零、观感连续；本帧的新内容随后在第 5 步照常覆盖。
        """
        ex = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, ex & ~WS_EX_LAYERED)
        user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, ex)
        try:
            self.present()
        except Exception:
            pass          # 上屏失败交给主循环的错误处理，别把层级切换也带崩

    def _attach_desktop(self):
        """挂到桌面壁纸层。任一前提不满足就返回 False（由调用方降级）：

        1. 找得到承载壁纸的 WorkerW；
        2. 它的矩形**覆盖整个虚拟屏** —— 多显示器下每块屏一个 WorkerW，
           只挂住主屏那个会让副屏的特效整片消失，宁可降级也不能挂。
        """
        workerw = _find_wallpaper_workerw()
        if not workerw:
            return False
        r = RECT()
        if not user32.GetWindowRect(HWND(workerw), ctypes.byref(r)):
            return False
        if not (r.left <= self.x and r.top <= self.y
                and r.right >= self.x + self.w and r.bottom >= self.y + self.h):
            return False
        # 必须先改成 WS_CHILD：SetParent 不会自动改样式，而 WS_POPUP 的窗口
        # 在系统眼里仍是"顶层窗"，GetParent 返回的是 owner（恒为 0）——
        # 父子关系形同虚设，还可能被当成独立顶层窗重新参与 z 序竞争。
        style = user32.GetWindowLongW(self.hwnd, GWL_STYLE)
        # 取不到样式（窗口已失效）时 style 是 0，**绝不能拿它做后续的写回** ——
        # 那会把窗口的全部样式抹成 0（连 WS_VISIBLE 都没了）。下面两条回滚
        # 分支都要用这个值，所以在本函数入口就挡掉。
        if not style:
            return False
        user32.SetWindowLongW(self.hwnd, GWL_STYLE,
                              (style & ~WS_POPUP_STYLE) | WS_CHILD)
        # 回读校验，而不是看 SetLastError：SetWindowLong 成功时并不保证把
        # last error 清零，而"样式本来就已经是 WS_CHILD"（重复施加层级时）
        # 不会触发任何内部动作，残留的旧错误码会被当成失败 —— 结果是明明
        # 挂得上却静默降级成"沉底窗口"。回读才是可靠的判据。
        if not (user32.GetWindowLongW(self.hwnd, GWL_STYLE) & WS_CHILD):
            # 回滚样式再降级，与下面 SetParent 失败那条分支保持对称。
            # 不回滚就会留下"没有父亲却有 WS_CHILD"的窗口：系统按孤儿子窗
            # 处理它（不上屏、不参与正常 z 序），而且**不可自愈** ——
            # _parented 仍是 False，_detach_parent() 开头就直接返回，
            # 此后没有任何路径会来修它。
            # 此处还没调过 SetParent，分层表面未受影响，所以不必
            # _rebuild_layered_surface()；_origin 也还是旧值，无需复位。
            user32.SetWindowLongW(self.hwnd, GWL_STYLE, style)
            return False
        # SetParent 的返回值是"原来的父亲"，顶层窗时就是 NULL，无法据此判断成败，
        # 因此直接回读 GetParent 校验；不成立就回滚样式，别留下半吊子状态。
        user32.SetParent(self.hwnd, HWND(workerw))
        if hwnd_int(user32.GetParent(self.hwnd)) != workerw:
            user32.SetParent(self.hwnd, None)
            user32.SetWindowLongW(self.hwnd, GWL_STYLE, style)
            # 已经摘回顶层窗了，父子状态与坐标系必须同步复位：_rebuild_layered_surface
            # 会补一次上屏，若 _origin 还留着上一次挂载时的父客户区原点，
            # 那次上屏就会把窗口按错误的坐标系再挪一段。
            self._parented = False
            self._origin = (0, 0)
            self._rebuild_layered_surface()   # 这一步的 SetParent 也会废掉分层表面
            return False
        self._parented = True
        # 子窗口坐标相对父窗口**客户区**：WorkerW 的客户区原点通常就是虚拟屏
        # 原点，但不是所有系统都如此，按 ClientToScreen 精确折算。折算结果存进
        # _origin —— present() 每帧重新定位时必须用同一套坐标系（见其注释）。
        self._origin = _client_origin(workerw)
        user32.SetWindowPos(self.hwnd, None,
                            self.x - self._origin[0], self.y - self._origin[1],
                            self.w, self.h,
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW
                            | SWP_NOCOPYBITS | SWP_NOOWNERZORDER)
        # SetParent 之后必须重建分层表面，否则窗口再也不上屏（见该方法注释）
        self._rebuild_layered_surface()
        return True

    def _detach_parent(self):
        """从壁纸层退出来，回到独立顶层窗口（屏幕坐标）。"""
        if not self.hwnd or not self._parented:
            return
        user32.SetParent(self.hwnd, None)
        # 与挂载时对称：清掉 WS_CHILD、改回 WS_POPUP，否则会留下一个
        # "没有父亲却有 WS_CHILD"的窗口，系统会把它当孤儿子窗处理。
        style = user32.GetWindowLongW(self.hwnd, GWL_STYLE)
        user32.SetWindowLongW(self.hwnd, GWL_STYLE,
                              (style & ~WS_CHILD) | WS_POPUP_STYLE)
        self._parented = False
        self._origin = (0, 0)           # 回到顶层窗，本地原点即屏幕原点
        user32.SetWindowPos(self.hwnd, None, self.x, self.y, self.w, self.h,
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW
                            | SWP_NOCOPYBITS | SWP_NOOWNERZORDER)
        # 与挂载对称：这里的 SetParent 同样会废掉分层表面（见该方法注释）
        self._rebuild_layered_surface()

    def is_above(self, other_hwnd):
        """本窗口是否真的位于 other_hwnd **之上**（沿 z 序实查，不靠猜）。

        骑乘期间每帧都要问一次：控制面板自己有 1s 一次的置顶保活
        （fx_ui._raise_top -> SetWindowPos(HWND_TOPMOST)），会把面板顶到
        我们之上，把点击特效重新压回面板底下。实测提升只活了 ~0.3s 就是
        被它打下去的。
        """
        if not self.hwnd or not other_hwnd:
            return False
        target = hwnd_int(other_hwnd)
        h = user32.GetWindow(HWND(self.hwnd), GW_HWNDPREV)
        n = 0
        while h and n < _CHAIN_GUARD:
            if hwnd_int(h) == target:
                return True
            h = user32.GetWindow(h, GW_HWNDPREV)
            n += 1
        return False

    def keep_riding_panel(self, panel_hwnd):
        """骑乘期间的自愈：确认自己仍在面板之上，掉了就重新提升。

        代价是一次沿 GW_HWNDPREV 的短链查找（~0.7µs/级），每帧可承受。
        返回 True = 本次真的补了一次 SetWindowPos。
        """
        if not self._ride_above_panel or not panel_hwnd:
            return False
        if self.is_above(panel_hwnd):
            return False
        self._last_sig = None
        return self.raise_topmost()

    def is_riding_panel(self):
        """当前是否处在"临时骑在控制面板之上"的状态（点击特效播放期间）。

        给 _restack_overlays 用：那段时间里不能纠正两层先后，否则那次
        SetWindowPos 会把正在播的点击特效压回面板之下。
        """
        return bool(self._ride_above_panel)

    def place_above(self, other_hwnd):
        """把本窗口插到另一个窗口的**正上方**（只在最上层模式下有意义）。

        两层都在最上层时，钉顶保活只把"被盖住的那层"插回置顶组顶部 ——
        反复几次后两层的先后可能翻过来（雨盖住水波）。调用方在每次层级/
        钉顶动作之后用它统一纠正一次。

        **实现要点**：win32 里 SetWindowPos(A, B) 是把 A 插到 **B 之下**
        （B 在 A 之上），所以"插到 other 之上"不能直接把 other 当插入点 ——
        那正好是反的。要拿 other 的**上一个**窗口当插入点；other 已经在最顶时
        用 HWND_TOPMOST。早先的实现直接传 other_hwnd，方向是反的：本意是
        "鼠标层浮在全屏层之上"，实际被按在全屏层之下（两层都是 per-pixel alpha
        的透明窗，肉眼看不出来，所以一直没暴露，但相对顺序确实是错的，
        也让"谁盖住了我"这条链更难看懂）。
        """
        if not self.hwnd or not other_hwnd:
            return False
        above_other = user32.GetWindow(HWND(other_hwnd), GW_HWNDPREV)
        after = HWND(above_other) if above_other else HWND_TOPMOST
        return bool(user32.SetWindowPos(
            self.hwnd, after, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOCOPYBITS
            | SWP_NOOWNERZORDER))

    def raise_topmost(self):
        """把本窗口插到**置顶组最顶**（插入点 HWND_TOPMOST）。

        "骑到面板之上播放点击特效"必须用这个，不能用 place_above ——
        place_above 拿"面板之上的窗口"当插入点，而实测确认：SetWindowPos
        插到**非置顶窗**之后会连 WS_EX_TOPMOST 一起清掉（见 _anchor 注释），
        于是下一帧 keep_on_top 的自愈逻辑又把窗口插回锚点（面板）之下，
        提升当场被打回 —— 表现为"文字只在面板下透出一两帧的模糊影子"。

        同时置位 _ride_above_panel：让 keep_on_top 在特效播放期间不再抢顶，
        否则"提升 → 被判定被面板盖住 → 锚点=面板 → 插回面板之下"会每帧
        自我否定，提升只活一帧（keep_on_top 里对同一标志有对应检查）。
        """
        if not self.hwnd:
            return False
        self._ride_above_panel = True
        return bool(user32.SetWindowPos(
            self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOCOPYBITS
            | SWP_NOOWNERZORDER))

    def leave_panel_ride(self, panel_hwnd):
        """结束"骑在面板之上"：降回面板之下，并恢复抢顶保活。

        插入点用面板本身（SetWindowPos(A, B) 把 A 插到 B **之下**），
        恢复常态"面板浮在特效之上"。置顶位自愈交给下一帧 keep_on_top。
        """
        self._ride_above_panel = False
        if not self.hwnd or not panel_hwnd:
            return False
        self._last_sig = None
        return bool(user32.SetWindowPos(
            self.hwnd, HWND(panel_hwnd), 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOCOPYBITS
            | SWP_NOOWNERZORDER))

    def place_below(self, other_hwnd):
        """把本窗口插到另一个窗口的**正下方**（place_above 的逆操作）。

        用途：面板空白处的点击特效播放期间，鼠标层被 place_above 临时提到
        面板之上（否则文字/表情全被面板挡住，见 desktop_fx 渲染循环），播放
        结束用这个降回来，恢复"面板浮在特效之上"的常态设计。

        Win32 语义：SetWindowPos(A, B) 把 A 插到 **B 之下**，所以 other 本身
        就是插入点 —— 和 place_above 正好相反（那里必须拿 other 的上一个窗口
        当插入点，直接传 other 方向是反的）。
        """
        if not self.hwnd or not other_hwnd:
            return False
        return bool(user32.SetWindowPos(
            self.hwnd, HWND(other_hwnd), 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOCOPYBITS
            | SWP_NOOWNERZORDER))

    def is_desktop_fallback(self):
        """桌面层级是否退成了"沉底窗口"（拿不到 WorkerW 时）。供诊断/测试用。"""
        return bool(self._desktop_fallback)

    def ensure_alive(self):
        """窗口是否还活着；死了就地重建并重新施加层级。

        桌面层是 WorkerW 的**子窗口**，资源管理器重启会连它一起销毁：IsWindow
        变假、画面定格。返回 True 表示重建过，调用方需重新读取宽高。
        """
        if self.hwnd and user32.IsWindow(self.hwnd):
            return False
        self._create_window()
        self._alloc_bitmap(self.w, self.h)
        self.apply_z_mode(self.z_mode)
        return True

    def destroy(self):
        if self._hdc_mem and self._old_bmp:
            gdi32.SelectObject(self._hdc_mem, self._old_bmp)
            self._old_bmp = None
        if self._hbmp:
            gdi32.DeleteObject(self._hbmp)
            self._hbmp = None
        if self._hdc_mem:
            gdi32.DeleteDC(self._hdc_mem)
            self._hdc_mem = None
        if self._hdc_screen:
            user32.ReleaseDC(None, self._hdc_screen)
            self._hdc_screen = None
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        self._parented = False
        self.buffer = None
        self.flat = None
        self.u32 = None
