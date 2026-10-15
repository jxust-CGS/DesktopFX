# -*- coding: utf-8 -*-
"""系统托盘图标（纯 ctypes 原生；默认使用 WorkBuddy 品牌图标 assets/tray_icon.png，
缺失时回退到自绘云雨图标，PIL 可选）。"""
import ctypes
import ctypes.wintypes as wt
import os

import numpy as np

from _win32 import (user32, gdi32, kernel32, shell32, HANDLE, HWND, HMENU,
                    HICON, HINSTANCE, HBRUSH, WPARAM, LRESULT, BOOL, UINT, INT,
                    DWORD)
from _paths import app_root, res_path

# 用户数据目录（exe/项目目录）；图标本身走 res_path() 以兼容 PyInstaller 打包
APP_DIR = app_root()

WM_USER = 0x0400
WM_TRAY = WM_USER + 1
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_HOTKEY = 0x0312
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_CONTEXTMENU = 0x007B

NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIM_ADD = 0x00
NIM_MODIFY = 0x01
NIM_DELETE = 0x02
NIM_SETVERSION = 0x04
NOTIFYICON_VERSION_4 = 4

MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
TPM_LEFTALIGN = 0x0000
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100     # 让 TrackPopupMenu 返回被选中的项 id
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
HOTKEY_ID = 0x5252
IDM_PANEL = 1001
IDM_PAUSE = 1002
IDM_QUIT = 1003
BI_RGB = 0
DIB_RGB_COLORS = 0
WS_EX_TOOLWINDOW = 0x00000080     # 不在 Alt+Tab 里出现
# ---- 从 .ico 文件加载标准图标
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
SM_CXSMICON = 49                # 系统小图标尺寸（托盘用的就是这个档）
SM_CYSMICON = 50


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


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
                ("cbWndExtra", ctypes.c_int), ("hInstance", HINSTANCE),
                ("hIcon", HICON), ("hCursor", HANDLE), ("hbrBackground", HBRUSH),
                ("lpszMenuName", ctypes.c_wchar_p), ("lpszClassName", ctypes.c_wchar_p),
                ("hIconSm", HICON)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", BOOL), ("xHotspot", DWORD), ("yHotspot", DWORD),
                ("hbmMask", HANDLE), ("hbmColor", HANDLE)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", DWORD), ("hWnd", HWND), ("uID", UINT), ("uFlags", UINT),
                ("uCallbackMessage", UINT), ("hIcon", HICON), ("szTip", ctypes.c_wchar * 128),
                ("dwState", DWORD), ("dwStateMask", DWORD), ("szInfo", ctypes.c_wchar * 256),
                ("uVersion", UINT), ("szInfoTitle", ctypes.c_wchar * 64),
                ("dwInfoFlags", DWORD), ("guidItem", ctypes.c_ubyte * 16), ("hBalloonIcon", HICON)]


# ------------------------------------------------------------------ 图标
def _icon_rgba(size=32):
    """加载 WorkBuddy 品牌图标（assets/tray_icon.png），缩放为指定尺寸，返回 BGRA 像素
    （DIB 要求的字节序），供 build_icon 转 HICON、_verify_icon_preview 落盘预览。

    文件缺失 / 损坏 / 无 PIL 时回退到自绘云雨图标，保证托盘永远有图标。
    """
    path = res_path(os.path.join("assets", "tray_icon.png"))
    try:
        from PIL import Image
        im = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
        arr = np.asarray(im).astype(np.uint8)
        # PIL 是 RGBA；DIB 要 BGRA，对调 R/B 通道
        return arr[..., [2, 1, 0, 3]].copy()
    except Exception:
        return _cloud_glyph_rgba(size)


def _cloud_glyph_rgba(size=32):
    """回退图标：自绘云 + 雨 + 水波（不依赖 PIL）。"""
    n = size
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    e = ((xx - 16.0) / 11.5) ** 2 + ((yy - 10.5) / 6.2) ** 2
    cloud = np.clip((1.0 - e) * 6.0, 0.0, 1.0) * 0.98
    rain = np.zeros((n, n), np.float32)
    for x0, y0, ln in ((8.5, 18.0, 11.0), (16.0, 20.0, 10.0), (23.5, 18.0, 11.0)):
        d = xx - (x0 + (yy - y0) * 0.45)
        band = np.clip(1.45 - np.abs(d), 0.0, 1.0)
        vert = np.clip(yy - (y0 - 1.0), 0.0, 1.0) * np.clip((y0 + ln - yy) / 3.0, 0.0, 1.0)
        rain = np.maximum(rain, band * vert)
    arc = np.clip(1.3 - np.abs(np.sqrt((xx - 16.0) ** 2 + ((yy - 27.0) * 2.1) ** 2) - 9.0), 0.0, 1.0)
    arc *= np.clip((yy - 24.0) / 2.0, 0.0, 1.0)
    # 托盘图标需要足够不透明度，复杂的壁纸/透明任务栏才能看清。
    # 直接用形状 mask 作为 alpha，不额外压暗。
    alpha = np.clip(np.maximum(np.maximum(cloud, rain), arc), 0.0, 1.0)
    cr = np.clip(cloud * 205 + rain * 95 + arc * 130, 0, 255)
    cg = np.clip(cloud * 232 + rain * 208 + arc * 232, 0, 255)
    cb = np.clip(cloud * 245 + rain * 255 + arc * 255, 0, 255)
    w = alpha / np.maximum(cloud + rain + arc, 1e-6)
    cr = np.clip(cr * w, 0, 255)
    cg = np.clip(cg * w, 0, 255)
    cb = np.clip(cb * w, 0, 255)
    pix = np.zeros((n, n, 4), np.uint8)
    pix[:, :, 0] = np.rint(cb).astype(np.uint8)
    pix[:, :, 1] = np.rint(cg).astype(np.uint8)
    pix[:, :, 2] = np.rint(cr).astype(np.uint8)
    pix[:, :, 3] = np.rint(alpha * 255.0).astype(np.uint8)
    return pix


def _load_ico_icon():
    """从 assets/tray_icon.ico 用系统 API 加载图标。

    这是托盘图标的**首选路径**：.ico 内含 16/32/48/64/128/256 多档尺寸，
    Windows 会按托盘实际大小和高 DPI 缩放自动挑最清晰的一档。相比手搓
    DIBSection + CreateIconIndirect，它不依赖 GDI 创建 DC/DIB 能否成功，
    在受限会话里也稳得多。失败返回 None，由 build_icon 回退。
    """
    path = res_path(os.path.join("assets", "tray_icon.ico"))
    if not os.path.exists(path):
        return None
    try:
        cx = user32.GetSystemMetrics(SM_CXSMICON) or 16
        cy = user32.GetSystemMetrics(SM_CYSMICON) or 16
        h = user32.LoadImageW(None, path, IMAGE_ICON, cx, cy, LR_LOADFROMFILE)
        return h if h else None
    except Exception:
        return None


def build_icon(size=32):
    # 优先走标准 .ico 加载；拿不到再自己画。
    h = _load_ico_icon()
    if h:
        return h

    n = size
    pix = _icon_rgba(n)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = n
    bmi.bmiHeader.biHeight = -n
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB
    # 用内存 DC（不依赖真实屏幕 DC），在 pythonw / 无显示器 / 某些远程会话里
    # 比 GetDC(None) 更稳定。CreateDIBSection 需要的只是一张 DC 的句柄做参照。
    hdc = gdi32.CreateCompatibleDC(None)
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), DIB_RGB_COLORS,
                                  ctypes.byref(bits), None, 0)
    gdi32.DeleteDC(hdc)
    if not hbmp:
        return None
    buf = ctypes.cast(bits, ctypes.POINTER(ctypes.c_ubyte * (n * n * 4))).contents
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(n, n, 4)
    arr[:] = pix
    ii = ICONINFO()
    ii.fIcon = True
    ii.hbmMask = None
    ii.hbmColor = hbmp
    return user32.CreateIconIndirect(ctypes.byref(ii))


# ------------------------------------------------------------------ 托盘
class TrayHost:
    #: 窗口类名。注意 RegisterClassExW 是**进程级**的：同名注册第二次会失败，
    #: 而 CreateWindowExW 照常成功 —— 但用的是第一次注册的窗口过程（指向第一个
    #: 实例）。一个进程只跑一个托盘时无所谓；测试里要并发多个实例就必须换名字，
    #: 否则第二个实例的窗口消息会被第一个实例截走。
    CLS_NAME = "DesktopFXTrayCls"

    def __init__(self, queue, title="桌面特效", cls_name=None):
        self.queue = queue
        self.title = title
        self.cls_name = cls_name or self.CLS_NAME
        self.hwnd = None
        self.hicon = None
        self._wndproc = None
        # 任务栏重建广播。explorer.exe 崩溃 / Windows 更新 / 用户手动重启资源管理器
        # 时，资源管理器会重建任务栏并广播这条消息。不监听它，托盘图标就永久消失：
        # 程序还在跑、雨还在下，但用户既打不开面板也没法退出，只能去任务管理器。
        # 开机自启场景同样依赖它 —— 若本进程比 explorer 先起来，首次 NIM_ADD
        # 会失败（任务栏还不存在），等收到这条广播再补挂即可。
        self.wm_taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self.icon_added = False

    def _add_icon(self):
        """（重新）挂上托盘图标。返回是否成功。"""
        if not self.hwnd:
            return False
        if self.hicon is None:
            self.hicon = build_icon(32)
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.hicon if self.hicon else HICON(0)
        nid.szTip = self.title[:127]
        ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        if ok:
            shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(nid))
        self.icon_added = ok
        return ok

    def _proc(self, hwnd, msg, wparam, lparam):
        # 注意：**不要**在这里写 lparam = int(lparam) & 0xFFFFFFFF。
        # lparam 在多数消息里是 64 位指针（WM_NCCREATE 的 CREATESTRUCT 就是），
        # 截成 32 位后再原样传给 DefWindowProcW 会让窗口创建直接失败：
        # WM_NCCREATE 返回 0 -> CreateWindowExW 返回 NULL -> 托盘窗口根本不存在，
        # 于是图标、右键菜单、热键全废，而失败是静默的，日志里一点痕迹都没有。
        # 需要取低位时（下面 WM_TRAY 判断鼠标按键）用局部变量 lp。
        if msg == self.wm_taskbar_created and self.wm_taskbar_created:
            # 任务栏重建：旧图标已被资源管理器丢弃，必须重新 NIM_ADD。
            # 注意这里不能用 NIM_MODIFY —— 图标对我们还在，但对新的任务栏不存在。
            self._add_icon()
            return 0
        if msg == WM_TRAY:
            lp = int(lparam) & 0xFFFFFFFF
            if lp in (WM_RBUTTONUP, WM_CONTEXTMENU, 0x0204):
                self._popup()
            elif lp in (WM_LBUTTONDBLCLK, WM_LBUTTONUP):
                self.queue.put(("panel", None))
            return 0
        if msg == WM_COMMAND:
            low = int(wparam) & 0xFFFF
            self._dispatch_menu(low)
            return 0
        if msg == WM_HOTKEY:
            self.queue.put(("pause", None))
            return 0
        if msg in (WM_DESTROY, WM_CLOSE):
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _dispatch_menu(self, cmd):
        """把菜单项 id 翻译成主程序事件。 WM_COMMAND 与 TrackPopupMenu 的
        返回值两条路径都汇总到这里，保证只有一处决定"点了某项该干啥"。"""
        if cmd == IDM_PANEL:
            self.queue.put(("panel", None))
        elif cmd == IDM_PAUSE:
            self.queue.put(("pause", None))
        elif cmd == IDM_QUIT:
            self.queue.put(("quit", None))

    def _popup(self):
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, IDM_PANEL, "显示 / 隐藏面板")
        user32.AppendMenuW(menu, MF_STRING, IDM_PAUSE, "暂停 / 继续    Ctrl+Alt+R")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, IDM_QUIT, "退出")
        user32.SetForegroundWindow(self.hwnd)
        # 必须用 TPM_RETURNCMD 才能拿到被选中的项 id；只给 TPM_NONOTIFY 会抑制
        # WM_COMMAND，而 TrackPopupMenu 自身又不返回 id -> 点了菜单毫无反应。
        # 两者一起给：返回 id 且不再发 WM_COMMAND，避免双触发。
        res = user32.TrackPopupMenu(
            menu, TPM_LEFTALIGN | TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
            pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)   # 关闭菜单的 modal 循环
        user32.DestroyMenu(menu)
        if res:
            self._dispatch_menu(int(res))

    def run(self):
        self._wndproc = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, ctypes.c_longlong)(self._proc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = self.cls_name
        user32.RegisterClassExW(ctypes.byref(wc))

        # 必须是**隐藏的顶层窗口**，不能用 HWND_MESSAGE（message-only 窗口）。
        # 实测（见 _probe_broadcast.py）：message-only 窗口收不到 HWND_BROADCAST
        # 广播，于是 explorer 重建任务栏时发的 TaskbarCreated 永远到不了这里，
        # 托盘图标就再也回不来。隐藏顶层窗口能正常收到广播；不设 WS_VISIBLE
        # 就不会出现在任务栏和 Alt+Tab 里，WS_EX_TOOLWINDOW 再兜一层底。
        self.hwnd = user32.CreateWindowExW(
            WS_EX_TOOLWINDOW, self.cls_name, "DesktopFXTray", 0,
            0, 0, 0, 0, HWND(0), None, kernel32.GetModuleHandleW(None), None)
        if not self.hwnd:
            # 托盘是控制面板关闭后唯一的退出途径，窗口建不起来必须让用户知道，
            # 而不是让线程静默结束、用户发现"图标没了但雨还在下"。
            self.queue.put(("tray-failed", None))
            return

        # 首次挂载可能失败：开机自启时本进程可能早于 explorer 创建任务栏。
        # 失败不用慌，等 TaskbarCreated 广播到来时会再挂一次。
        self._add_icon()

        # 热键注册失败（Ctrl+Alt+R 被别的程序占用）不能静默 —— 用户按了没反应
        # 会以为程序坏了。失败时告知主程序，由它落 error.log。
        if not user32.RegisterHotKey(self.hwnd, HOTKEY_ID,
                                     MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("R")):
            self.queue.put(("hotkey-failed", None))

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def stop(self):
        if self.hwnd:
            try:
                user32.UnregisterHotKey(self.hwnd, HOTKEY_ID)
            except Exception:
                pass
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self.hwnd
            nid.uID = 1
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            self.icon_added = False
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        if self.hicon:
            try:
                user32.DestroyIcon(self.hicon)
            except Exception:
                pass
            self.hicon = None
