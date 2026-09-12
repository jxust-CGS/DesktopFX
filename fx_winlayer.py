# -*- coding: utf-8 -*-
"""Win32 per-pixel-alpha 分层覆盖窗口。依赖 _win32 预设的函数签名。"""
import ctypes
import ctypes.wintypes as wt

import numpy as np

from _win32 import (user32, gdi32, kernel32, HANDLE, HWND, HDC, HBITMAP,
                    WPARAM, LPARAM, LRESULT, BOOL, UINT, DWORD, INT, RECT,
                    hwnd_int)

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
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
# 不保存/恢复被遮挡处的位图：本窗口内容 100% 由 UpdateLayeredWindow 提供，
# 让系统去 copy bits 反而会在 z 序变动时翻出无效内容（可见的一帧闪烁）。
SWP_NOCOPYBITS = 0x0100
SWP_NOOWNERZORDER = 0x0200
# GetWindow 的 z 序方向：返回"位于该窗口之上"的邻窗
GW_HWNDPREV = 3

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


class Overlay:
    def __init__(self):
        self.hwnd = None
        self._hdc_screen = None
        self._hdc_mem = None
        self._hbmp = None
        self._old_bmp = None
        self._bits = ctypes.c_void_p()
        self._cbuf = None
        self.x = self.y = 0
        self.w = self.h = 1
        self.buffer = None
        self.flat = None
        self.u32 = None
        self._stride = np.int32(1)
        # 控制面板 hwnd。面板是我们自己的窗口，必须从"谁盖住了我"的判定里排除，
        # 否则 overlay 会跟面板无限互抢 z 序（见 keep_on_top）。
        self.panel_hwnd = None

    def create(self):
        _register_class()
        show_panel_message()  # 必须在窗口创建前注册，wndproc 才知道要拦哪个号
        x, y, w, h = virtual_screen()
        self.x, self.y, self.w, self.h = x, y, w, h
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            _CLASS_NAME, "DesktopFX Overlay", WS_POPUP,
            x, y, w, h, None, None, kernel32.GetModuleHandleW(None), None)
        if not self.hwnd:
            raise OSError("CreateWindowExW failed", ctypes.get_last_error())
        # 必须有这一步：CreateWindowExW 不带 WS_VISIBLE 时窗口默认隐藏，
        # UpdateLayeredWindow 只会更新位图，不会让它出现在桌面上。
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        if not user32.IsWindowVisible(self.hwnd):
            raise OSError("overlay window is not visible after ShowWindow")
        self._alloc_bitmap(w, h)
        return self

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
            return True
        return False

    def present(self):
        pt_dst = POINT(self.x, self.y)
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
        """登记控制面板 hwnd（面板创建 / 重建时调用，供 keep_on_top 排除）。"""
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
        return buf.value not in _NON_OCCLUDERS

    def _covered(self):
        """本窗口是否被"别人的"可见置顶窗口盖住。

        沿 z 序向上逐个检查（GW_HWNDPREV）：我们是 WS_EX_TOPMOST，链上的窗口
        必然同属 topmost 组，因此链上出现任何"非自家"的真实窗口即视为被盖。
        控制面板不算"别人"—— 面板浮在特效之上是预期状态，不是故障。
        """
        if not self.hwnd:
            return False
        h = user32.GetWindow(self.hwnd, GW_HWNDPREV)
        guard = 0
        while h and guard < 64:
            guard += 1
            if not (self.panel_hwnd and hwnd_int(h) == self.panel_hwnd):
                if self._is_occluder(h):
                    return True
            h = user32.GetWindow(h, GW_HWNDPREV)
        return False

    def keep_on_top(self):
        """把叠加层钉回最上层 —— 但只在真的被盖住时才动手。

        窗口创建时虽带 WS_EX_TOPMOST，但只要用户点开一个同样置顶的应用
        （全屏视频 / 画中画 / 悬浮窗 / 部分游戏），对方被激活后就会压过我们。
        topmost 不会因为普通窗口激活而丢失，所以"点击后特效跑到后面"几乎
        都是被"别的置顶窗口"盖住了。

        关键：**不能无条件周期调用**。控制面板自己也在做置顶保活，若 overlay
        每 0.5s 无条件抢一次顶，就会和面板每 2s 的抢顶互相翻转 z 序 —— 表现
        为"每隔几秒画面闪一下"。所以先判定是否真被盖，未被盖直接返回。
        """
        if not self.hwnd:
            return False
        if not self._covered():
            return False
        # 插到面板之下（两者同属 topmost 组）：既复位又不把面板压掉；
        # 面板不存在时直接插到 topmost 组顶部。NOCOPYBITS 避免系统去
        # 保存/恢复被遮挡位图 —— 那会在重排瞬间翻出一帧无效内容。
        after = HWND(self.panel_hwnd) if self.panel_hwnd else HWND_TOPMOST
        user32.SetWindowPos(self.hwnd, after, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
                            | SWP_NOCOPYBITS | SWP_NOOWNERZORDER)
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
        self.buffer = None
        self.flat = None
        self.u32 = None
