# -*- coding: utf-8 -*-
"""诊断：程序到底在不在跑、托盘窗口存不存在、是哪些进程占着单例锁。"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

u32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.EnumWindows.restype = wt.BOOL
u32.GetClassNameW.argtypes = [wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
u32.GetClassNameW.restype = ctypes.c_int
u32.GetWindowTextW.argtypes = [wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
u32.GetWindowTextW.restype = ctypes.c_int
u32.IsWindowVisible.argtypes = [wt.HWND]
u32.IsWindowVisible.restype = wt.BOOL
u32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
u32.FindWindowW.restype = wt.HWND

TARGETS = ("DesktopFX", "Tray")
found = []


@ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
def _enum(hwnd, _):
    buf = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(hwnd, buf, 256)
    cls = buf.value
    u32.GetWindowTextW(hwnd, buf, 256)
    title = buf.value
    if any(t.lower() in cls.lower() or t.lower() in title.lower()
           for t in TARGETS):
        found.append((hwnd, cls, title, bool(u32.IsWindowVisible(hwnd))))
    return True


print("=" * 64)
print("运行状态诊断")
print("=" * 64)

print("\n[1] FindWindowW 直接查托盘窗口类")
for name in ("DesktopFXTrayCls",):
    h = u32.FindWindowW(name, None)
    print("    类 %-20s -> %s" % (name, ("hwnd=0x%X" % h) if h else "找不到"))

print("\n[2] 枚举所有顶层窗口（类名或标题含 DesktopFX / Tray）")
u32.EnumWindows(_enum, 0)
if found:
    for hwnd, cls, title, vis in found:
        print("    hwnd=0x%-10X 可见=%-5s 类=%-24s 标题=%s"
              % (hwnd, vis, cls, title or "(空)"))
else:
    print("    一个都没找到")

print("\n[3] python / pythonw 进程")
try:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter "
         "\"Name='pythonw.exe' OR Name='python.exe'\" | "
         "ForEach-Object { '{0}|{1}|{2}' -f $_.ProcessId, $_.CreationDate, "
         "$_.CommandLine }"],
        capture_output=True, text=True, timeout=30)
    lines = [l for l in (out.stdout or "").splitlines() if l.strip()]
    if lines:
        for l in lines:
            parts = l.split("|", 2)
            pid = parts[0]
            created = parts[1] if len(parts) > 1 else "?"
            cmd = parts[2] if len(parts) > 2 else "?"
            print("    PID %-8s 启动于 %s" % (pid, created))
            print("        %s" % (cmd[:150],))
    else:
        print("    没有找到 python / pythonw 进程")
    if out.stderr.strip():
        print("    (stderr) %s" % out.stderr.strip()[:200])
except Exception as e:
    print("    查询失败: %r" % (e,))

print("\n[4] 任务栏（explorer）是否在运行")
try:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-Process explorer -ErrorAction SilentlyContinue | "
         "Measure-Object).Count"],
        capture_output=True, text=True, timeout=30)
    n = (out.stdout or "").strip()
    print("    explorer 进程数: %s%s" % (n, "   <-- 资源管理器没在跑！"
                                        if n == "0" else ""))
except Exception as e:
    print("    查询失败: %r" % (e,))

print()
sys.exit(0)
