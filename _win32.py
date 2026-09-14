# -*- coding: utf-8 -*-
"""集中修复 ctypes 函数签名：
- 把 wintypes 缺失的 handle 别名补成 c_void_p
- 凡是 64-bit handle 的 Win32 函数都显式声明 argtypes / restype，
  避免 ctypes 默认按 c_int 转换造成 overflow
"""
import ctypes
import ctypes.wintypes as wt

HANDLE = c_void_p = ctypes.c_void_p
HDC = HBITMAP = HBRUSH = HICON = HCURSOR = HFONT = HPEN = HRGN = ctypes.c_void_p
HWND = HMENU = HINSTANCE = HMODULE = ctypes.c_void_p

# 补齐 Python 3.13+ 移除的 wintypes 别名
for _name in ("HICON", "HCURSOR", "HMODULE", "HMONITOR", "HBRUSH", "HINSTANCE",
              "HWND", "HDC", "HBITMAP", "HMENU", "HANDLE", "HPEN", "HRGN", "HFONT"):
    if not hasattr(wt, _name):
        setattr(wt, _name, ctypes.c_void_p)

# LRPC/result types: 64-bit signed for LRESULT/LPARAM, 64-bit unsigned for WPARAM
LRESULT = LPARAM = ctypes.c_longlong
WPARAM  = ctypes.c_size_t    # unsigned pointer-sized
MSG     = wt.MSG
UINT    = ctypes.c_uint
DWORD   = ctypes.c_ulong
WORD    = ctypes.c_ushort
BYTE    = ctypes.c_ubyte
LONG    = ctypes.c_long
BOOL    = ctypes.c_int
BYTE_   = ctypes.c_ubyte
INT     = ctypes.c_int
SHORT   = ctypes.c_short

RECT = wt.RECT


def hwnd_int(h):
    """把 Win32 窗口句柄统一取成 int（NULL/无效 -> 0）。

    必须走这里，不要直接 `int(h)`。ctypes 里同一个"句柄"有两种形态：
      - Win32 函数**返回值**声明为 c_void_p 时，ctypes 会转成 int（或 NULL 时的
        None）—— 这时 int(h) 没问题；
      - 而**手动构造**的 HWND(x) 是 c_void_p 实例，它没有 __int__，int() 会退化
        成"把 8 字节 buffer 当字节串解析"并抛 ValueError，
        报错形如 `invalid literal for int() with base 10: b'\\xe8\\x03...'`。
    两种形态混用时就是一颗地雷，故统一走本函数。
    """
    v = getattr(h, "value", h)
    try:
        return int(v) if v else 0
    except (TypeError, ValueError):
        return 0


# ----------  Acrylic / rounded corners helpers (Win10 1803+ / Win11)
# 注意：SetWindowCompositionAttribute 是未公开 API，但 Windows 已稳定支持多年。
class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_uint),
        ("AccentFlags", ctypes.c_uint),
        ("GradientColor", ctypes.c_uint),  # AABBGGRR
        ("AnimationId", ctypes.c_uint),
    ]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),       # WCA_ACCENT_POLICY = 19
        ("Data", ctypes.c_void_p),         # ACCENT_POLICY*（void* 避免类型/强转歧义）
        ("SizeOfData", ctypes.c_size_t),
    ]


WCA_ACCENT_POLICY = 19
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
ACCENT_ENABLE_BLURBEHIND = 3

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DEFAULT = 0
DWMWCP_DONOTROUND = 1
DWMWCP_ROUND = 2
DWMWCP_ROUNDSMALL = 3


def _bgra(abgr):
    """将 RGBA 元组转成 AABBGGRR 整数（供 ACCENT_POLICY.GradientColor）。"""
    r, g, b, a = abgr
    return (a << 24) | (b << 16) | (g << 8) | r


user32, gdi32, shell32, kernel32 = (None, None, None, None)


def _load_dwm():
    try:
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        dwm.DwmSetWindowAttribute.argtypes = [HWND, ctypes.c_uint,
                                              ctypes.c_void_p, ctypes.c_uint]
        return dwm
    except Exception:
        return None


def set_window_rounded(hwnd, preference=DWMWCP_ROUND):
    """调用 DWM 设置窗口圆角；Win11 生效，Win10 旧版本会静默失败。"""
    dwm = _load_dwm()
    if dwm is None or not hwnd:
        return False
    val = ctypes.c_uint(preference)
    hr = dwm.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                                   ctypes.byref(val), ctypes.sizeof(val))
    return hr == 0


def set_window_acrylic(hwnd, rgba=(22, 24, 30, 200)):
    """为无边框窗口启用亚克力模糊背景。rgba 为 tint 颜色。"""
    if user32 is None or not hwnd:
        return False
    try:
        fn = user32.SetWindowCompositionAttribute
    except AttributeError:
        return False
    fn.restype = ctypes.c_long
    fn.argtypes = [HWND, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
    policy = ACCENT_POLICY(ACCENT_ENABLE_ACRYLICBLURBEHIND, 0,
                           _bgra(rgba), 0)
    data = WINDOWCOMPOSITIONATTRIBDATA(WCA_ACCENT_POLICY,
                                       ctypes.cast(ctypes.byref(policy),
                                                   ctypes.c_void_p),
                                       ctypes.sizeof(policy))
    return fn(hwnd, ctypes.byref(data)) == 0


def patch_libs():
    """为所有用到的 Win32 函数预设 argtypes / restype。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # ---------- GDI
    gdi32.SelectObject.restype = HANDLE
    gdi32.SelectObject.argtypes = [HANDLE, HANDLE]
    gdi32.CreateCompatibleDC.restype = HDC
    gdi32.CreateCompatibleDC.argtypes = [HDC]
    gdi32.DeleteObject.restype = BOOL
    gdi32.DeleteObject.argtypes = [HANDLE]
    gdi32.DeleteDC.restype = BOOL
    gdi32.DeleteDC.argtypes = [HDC]
    gdi32.GetStockObject.restype = HANDLE
    gdi32.GetStockObject.argtypes = [INT]
    gdi32.CreateDIBSection.restype = HBITMAP
    gdi32.CreateDIBSection.argtypes = [HDC, ctypes.c_void_p, UINT,
                                       ctypes.c_void_p, HANDLE, DWORD]
    # GetPixel 返回 COLORREF(0x00BBGGRR)，失败给 CLR_INVALID=0xFFFFFFFF
    gdi32.GetPixel.restype = UINT
    gdi32.GetPixel.argtypes = [HDC, INT, INT]
    # ---------- USER32
    user32.GetSystemMetrics.restype = INT
    user32.GetSystemMetrics.argtypes = [INT]
    # 从 .ico 文件加载图标：比手搓 DIBSection 稳得多，且能拿到多尺寸（高 DPI 友好）
    user32.LoadImageW.restype = HANDLE
    user32.LoadImageW.argtypes = [HINSTANCE, ctypes.c_wchar_p, UINT,
                                  INT, INT, UINT]
    user32.GetDC.restype = HDC
    user32.GetDC.argtypes = [HWND]
    user32.ReleaseDC.restype = INT
    user32.ReleaseDC.argtypes = [HWND, HDC]
    user32.RegisterClassExW.restype = WORD
    user32.RegisterClassExW.argtypes = [ctypes.c_void_p]
    user32.CreateWindowExW.restype = HWND
    user32.CreateWindowExW.argtypes = [DWORD, ctypes.c_wchar_p, ctypes.c_wchar_p, DWORD,
                                       INT, INT, INT, INT,
                                       HWND, HMENU, HINSTANCE, ctypes.c_void_p]
    user32.UpdateLayeredWindow.restype = BOOL
    user32.UpdateLayeredWindow.argtypes = [HWND, HDC, ctypes.c_void_p, ctypes.c_void_p,
                                           HDC, ctypes.c_void_p, DWORD,
                                           ctypes.c_void_p, DWORD]
    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [HWND, UINT, WPARAM, LPARAM]
    user32.DestroyWindow.restype = BOOL
    user32.DestroyWindow.argtypes = [HWND]
    user32.PeekMessageW.restype = BOOL
    user32.PeekMessageW.argtypes = [ctypes.c_void_p, HWND, UINT, UINT, UINT]
    user32.TranslateMessage.restype = BOOL
    user32.TranslateMessage.argtypes = [ctypes.c_void_p]
    user32.DispatchMessageW.restype = LRESULT
    user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
    user32.PostMessageW.restype = BOOL
    user32.PostMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
    user32.SendMessageW.restype = LRESULT
    user32.SendMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
    # 广播消息用（TaskbarCreated 等）。SMTO_ABORTIFHUNG 避免被卡死窗口拖住。
    user32.SendMessageTimeoutW.restype = LRESULT
    user32.SendMessageTimeoutW.argtypes = [HWND, UINT, WPARAM, LPARAM, UINT,
                                           UINT, ctypes.POINTER(ctypes.c_size_t)]
    user32.IsWindow.restype = BOOL
    user32.IsWindow.argtypes = [HWND]
    user32.PostQuitMessage.restype = ctypes.c_void_p
    user32.PostQuitMessage.argtypes = [INT]
    user32.GetMessageW.restype = INT
    user32.GetMessageW.argtypes = [ctypes.c_void_p, HWND, UINT, UINT]
    user32.RegisterHotKey.restype = BOOL
    user32.RegisterHotKey.argtypes = [HWND, INT, UINT, UINT]
    user32.UnregisterHotKey.restype = BOOL
    user32.UnregisterHotKey.argtypes = [HWND, INT]
    user32.CreateIconIndirect.restype = HICON
    user32.CreateIconIndirect.argtypes = [ctypes.c_void_p]
    user32.DestroyIcon.restype = BOOL
    user32.DestroyIcon.argtypes = [HICON]
    user32.CreatePopupMenu.restype = HMENU
    user32.CreatePopupMenu.argtypes = []
    user32.AppendMenuW.restype = BOOL
    user32.AppendMenuW.argtypes = [HMENU, UINT, UINT, ctypes.c_wchar_p]
    # 返回类型必须是 UINT：配合 TPM_RETURNCMD 时它返回被选中的菜单项 id，
    # 若声明成 BOOL 高 32 位会被截断、取不到真正的 id。
    user32.TrackPopupMenu.restype = UINT
    user32.TrackPopupMenu.argtypes = [HMENU, UINT, INT, INT, INT, HWND, ctypes.c_void_p]
    user32.DestroyMenu.restype = BOOL
    user32.DestroyMenu.argtypes = [HMENU]
    user32.SetForegroundWindow.restype = BOOL
    user32.SetForegroundWindow.argtypes = [HWND]
    user32.GetCursorPos.restype = BOOL
    user32.GetCursorPos.argtypes = [ctypes.c_void_p]
    user32.GetAsyncKeyState.restype = SHORT
    user32.GetAsyncKeyState.argtypes = [INT]
    user32.SetWindowPos.restype = BOOL
    user32.SetWindowPos.argtypes = [HWND, HWND, INT, INT, INT, INT, UINT]
    user32.GetParent.restype = HWND
    user32.GetParent.argtypes = [HWND]
    user32.IsWindowVisible.restype = BOOL
    user32.IsWindowVisible.argtypes = [HWND]
    # z 序遍历：配合 GW_HWNDPREV 判断"本窗口是否被别的置顶窗口盖住"。
    # 不声明 restype 的话，64 位 handle 会被 ctypes 截成 c_int 丢掉高位。
    user32.GetWindow.restype = HWND
    user32.GetWindow.argtypes = [HWND, UINT]
    user32.GetClassNameW.restype = INT
    user32.GetClassNameW.argtypes = [HWND, ctypes.c_wchar_p, INT]
    user32.ShowWindow.restype = BOOL
    user32.ShowWindow.argtypes = [HWND, INT]
    user32.GetWindowRect.restype = BOOL
    user32.GetWindowRect.argtypes = [HWND, ctypes.c_void_p]
    user32.RegisterWindowMessageW.restype = UINT
    user32.RegisterWindowMessageW.argtypes = [ctypes.c_wchar_p]
    user32.FindWindowW.restype = HWND
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    # 桌面壁纸层（WorkerW）挂载用：找子窗口 / 枚举顶层窗 / 认父
    user32.FindWindowExW.restype = HWND
    user32.FindWindowExW.argtypes = [HWND, HWND, ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.SetParent.restype = HWND
    user32.SetParent.argtypes = [HWND, HWND]
    # 窗口样式读写：挂到桌面壁纸层要把 WS_POPUP 换成 WS_CHILD，
    # 否则 SetParent 只改 owner 语意、GetParent 依旧返回 0。
    user32.GetWindowLongW.restype = LONG
    user32.GetWindowLongW.argtypes = [HWND, INT]
    user32.SetWindowLongW.restype = LONG
    user32.SetWindowLongW.argtypes = [HWND, INT, LONG]
    user32.EnumWindows.restype = BOOL
    user32.EnumWindows.argtypes = [ctypes.c_void_p, LPARAM]
    # ---------- SHELL32
    shell32.Shell_NotifyIconW.restype = BOOL
    shell32.Shell_NotifyIconW.argtypes = [DWORD, ctypes.c_void_p]
    # ---------- KERNEL32
    kernel32.GetModuleHandleW.restype = HMODULE
    kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = HANDLE
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, BOOL, ctypes.c_wchar_p]
    kernel32.CloseHandle.restype = BOOL
    kernel32.CloseHandle.argtypes = [HANDLE]
    return user32, gdi32, shell32, kernel32


user32, gdi32, shell32, kernel32 = patch_libs()


def screen_pixel(x, y):
    """读屏幕上某点的颜色，返回 (r, g, b)；读不到返回 None。

    - `GetDC(NULL)` 拿到的是**整个虚拟桌面**的 DC（所有显示器都在内），
      坐标与 `GetCursorPos` 同一套物理像素（进程声明了 PER_MONITOR_AWARE_V2）。
    - 返回值是 COLORREF = `0x00BBGGRR`，**BGR 序** —— 按 RGB 解会把红蓝弄反。
    - 这是"屏幕取色器"的底层：读的是 DWM 合成后**真正显示在屏幕上**的颜色，
      所以覆盖层、壁纸、任何窗口的颜色都能取到。
    """
    if user32 is None or gdi32 is None:
        return None
    hdc = user32.GetDC(None)
    if not hdc:
        return None
    try:
        v = int(gdi32.GetPixel(hdc, int(x), int(y)))
    except Exception:
        return None
    finally:
        user32.ReleaseDC(None, hdc)
    if v == 0xFFFFFFFF:
        return None
    return (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)
