"""Verify the double-click entry point (启动桌面特效.bat) end to end.

Everything here is pure ctypes so it has no dependency on PowerShell's
process lifetime (PowerShell kills its child tree when a command finishes,
which produced false "pythonw did not start" results in earlier tests).

Checks
  1. bat file hygiene : ASCII-only + CRLF line endings (both required by cmd.exe)
  2. launch           : os.startfile() == what Windows Explorer does on double-click
  3. alive            : pythonw.exe process appears and survives
  4. actually drawing : the overlay window (class DesktopFXOverlayCls) exists
  5. single instance  : a second launch must not create a second instance
                        （注意：一个实例现在有**两个**覆盖窗口 ——
                          全屏特效层 + 鼠标特效层，判据是"数量没变"）
"""
import ctypes
import glob
import os
import re
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
OVERLAY_CLASS = "DesktopFXOverlayCls"
TRAY_CLASS = "DesktopFXTrayCls"

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)

# ---------------------------------------------------------------- process enum
k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
k32.Process32FirstW.restype = wintypes.BOOL
k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
k32.Process32NextW.restype = wintypes.BOOL
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.OpenProcess.restype = wintypes.HANDLE
k32.TerminateProcess.argtypes = [wintypes.HANDLE, ctypes.c_uint]
k32.TerminateProcess.restype = wintypes.BOOL
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.CloseHandle.restype = wintypes.BOOL

MAX_PATH = 260
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


def list_procs(name_filter=None):
    """Return [(pid, exe_name)] for all processes (optionally name-filtered)."""
    out = []
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (None, 0, -1) or snap == ctypes.c_void_p(-1).value:
        return out
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(pe)
        if k32.Process32FirstW(snap, ctypes.byref(pe)):
            while True:
                nm = pe.szExeFile
                if name_filter is None or nm.lower() == name_filter.lower():
                    out.append((pe.th32ProcessID, nm))
                if not k32.Process32NextW(snap, ctypes.byref(pe)):
                    break
    finally:
        k32.CloseHandle(snap)
    return out


def kill_all_pythonw():
    n = 0
    for pid, _nm in list_procs("pythonw.exe"):
        h = k32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if h:
            if k32.TerminateProcess(h, 1):
                n += 1
            k32.CloseHandle(h)
    return n


# ----------------------------------------------------------------- window enum
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
u32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
u32.EnumWindows.restype = wintypes.BOOL
u32.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
u32.GetClassNameW.restype = ctypes.c_int
u32.IsWindowVisible.argtypes = [wintypes.HWND]
u32.IsWindowVisible.restype = wintypes.BOOL


def count_windows_by_class(cls):
    found = []

    def cb(hwnd, _l):
        buf = ctypes.create_unicode_buffer(256)
        if u32.GetClassNameW(hwnd, buf, 256) and buf.value == cls:
            found.append(hwnd)
        return True

    u32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


def count_overlay_windows():
    return count_windows_by_class(OVERLAY_CLASS)


def visible_overlay_windows():
    """返回实际可见的覆盖层窗口。

    仅检查 class 存在是不够的：CreateWindowExW 没给 WS_VISIBLE 的话窗口会
    默认隐藏，UpdateLayeredWindow 只更新位图但用户什么都看不到。
    """
    return [h for h in count_windows_by_class(OVERLAY_CLASS)
            if u32.IsWindowVisible(h)]


# ------------------------------------------------------------------------ main
def check_bat_file(path):
    raw = open(path, "rb").read()
    crlf = raw.count(b"\r\n")
    bare_lf = raw.count(b"\n") - crlf
    non_ascii = sum(1 for b in raw if b > 127)
    bom = raw[:3] == b"\xef\xbb\xbf"

    print("== 1. file hygiene ==")
    print(f"  path          : {path}")
    print(f"  size          : {len(raw)} bytes")
    print(f"  line endings  : CRLF={crlf}  bare-LF={bare_lf}")
    print(f"  non-ASCII     : {non_ascii}")
    print(f"  UTF-8 BOM     : {bom}")

    ok = True
    if bare_lf:
        print("  [FAIL] bare LF present -> cmd.exe cannot resolve goto labels")
        ok = False
    else:
        print("  [OK] pure CRLF")
    if non_ascii:
        print("  [FAIL] non-ASCII bytes -> mangled by the ANSI codepage")
        ok = False
    else:
        print("  [OK] ASCII only")
    if bom:
        print("  [FAIL] UTF-8 BOM confuses cmd.exe")
        ok = False

    # analyse only real code - cmd ignores rem / :: comment lines, and the
    # header comment deliberately contains words like "goto LABEL" and URLs.
    text = raw.decode("ascii", "replace")
    code_lines = [ln for ln in text.splitlines()
                  if not ln.strip().lower().startswith(("rem", "::"))]
    code = "\n".join(code_lines)

    # Git Bash's MSYS layer rewrites backslashes to forward slashes in
    # anything that looks like an absolute path. Catch a leaked "C://...".
    # The lookbehind skips URLs (the "s://" of "https://" follows a letter).
    drive_paths = re.findall(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\"'\s]*", code)
    fwd = [p for p in drive_paths if "/" in p]
    print(f"  drive paths found : {len(drive_paths)}")
    if fwd:
        print(f"  [FAIL] forward slashes leaked in: {fwd[:3]}")
        ok = False
    else:
        print("  [OK] all drive paths use backslashes")

    # every hardcoded absolute path must actually exist on this machine
    missing = [p for p in sorted(set(drive_paths)) if not os.path.exists(p)]
    if missing:
        print("  [FAIL] hardcoded paths that do not exist:")
        for p in missing:
            print(f"         {p}")
        ok = False
    else:
        print(f"  [OK] all {len(set(drive_paths))} hardcoded paths exist")

    # every goto target must have a matching label
    gotos = set(re.findall(r"goto\s+([A-Za-z0-9_]+)", code))
    labels = set(re.findall(r"^:([A-Za-z0-9_]+)", code, re.M))
    dangling = gotos - labels
    if dangling:
        print(f"  [FAIL] goto targets with no label: {sorted(dangling)}")
        ok = False
    else:
        print(f"  [OK] all {len(gotos)} goto targets resolve to a label")

    print()
    return ok


def main():
    ok = True
    bats = glob.glob(os.path.join(HERE, "*.bat"))
    bats = [b for b in bats if not os.path.basename(b).startswith("_")]
    if not bats:
        print("FAIL: no launcher .bat found")
        return 1
    bat = bats[0]

    if not check_bat_file(bat):
        ok = False

    print("== 2. launch (ShellExecute / double-click equivalent) ==")
    killed = kill_all_pythonw()
    print(f"  pre-clean: terminated {killed} stray pythonw.exe")
    time.sleep(0.6)
    assert not list_procs("pythonw.exe"), "stray pythonw still alive"

    os.startfile(bat)  # exactly what Explorer does on double-click
    print("  os.startfile() called")

    t = 0.0
    for step in (1.5, 1.5, 2.0, 3.0):
        time.sleep(step)
        t += step
        procs = list_procs("pythonw.exe")
        print(f"  t~{t:>4.1f}s  pythonw count = {len(procs)}")

    procs = list_procs("pythonw.exe")
    if not procs:
        print("  [FAIL] no pythonw.exe running after launch")
        ok = False
    else:
        print(f"  [OK] app is running ({len(procs)} pythonw.exe; "
              f"venv launcher + real interpreter = 2 is expected)")

    print()
    print("== 3. overlay actually rendered ==")
    wins = count_overlay_windows()
    # 一个实例建**两个**覆盖窗口：全屏特效层 + 鼠标特效层。少一个说明第二层
    # 没建起来（渲染线程会因此直接挂掉），不能只判"有没有窗口"。
    if len(wins) >= 2:
        print(f"  [OK] 覆盖层齐全：{len(wins)} 个窗口（全屏特效层 + 鼠标特效层）"
              f"  首个 hwnd=0x{wins[0]:x}")
    elif wins:
        print(f"  [FAIL] 只有 {len(wins)} 个覆盖窗口（预期 2 个：全屏层 + 鼠标层）")
        ok = False
    else:
        print(f"  [FAIL] no window of class {OVERLAY_CLASS}")
        ok = False

    print()
    print("== 3a. overlay visible on screen ==")
    vis = visible_overlay_windows()
    if vis:
        print(f"  [OK] overlay is visible (hwnd=0x{vis[0]:x}) — effect reaches desktop")
    else:
        print(f"  [FAIL] overlay window exists but is NOT visible — "
              f"window was never ShowWindow'd, so nothing appears on desktop")
        ok = False

    print()
    print("== 3b. tray subsystem alive ==")
    # 托盘窗口曾经**根本创建不出来**：_proc 里 lparam & 0xFFFFFFFF 把 WM_NCCREATE
    # 的 CREATESTRUCT 指针截成 32 位，CreateWindowExW 直接返回 NULL。后果是托盘
    # 图标、右键菜单、Ctrl+Alt+R 全废，而失败完全静默。这里必须实机卡住。
    tray = count_windows_by_class(TRAY_CLASS)
    if tray:
        print(f"  [OK] tray host window present (class={TRAY_CLASS}, "
              f"hwnd=0x{tray[0]:x})")
    else:
        print(f"  [FAIL] no window of class {TRAY_CLASS} -> "
              f"tray icon / menu / hotkey are all dead")
        ok = False

    print()
    print("== 4. single-instance guard ==")
    before_wins = len(wins)
    before_procs = len(procs)
    os.startfile(bat)
    time.sleep(4.0)
    wins2 = count_overlay_windows()
    n2 = len(list_procs("pythonw.exe"))
    if len(wins2) > before_wins or n2 > before_procs:
        print(f"  [FAIL] second launch created another instance "
              f"(overlay {before_wins}->{len(wins2)}, pythonw {before_procs}->{n2})")
        ok = False
    else:
        print(f"  [OK] second double-click added nothing "
              f"(overlay {len(wins2)}, pythonw {n2})")

    print()
    print("== cleanup ==")
    print(f"  terminated {kill_all_pythonw()} pythonw.exe")

    print()
    print("== LAUNCHER VERIFIED ==" if ok else "== LAUNCHER FAILED ==")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
