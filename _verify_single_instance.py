"""Verify the single-instance guard.

A. mutex semantics : two real OS processes call _acquire_singleton();
                     the first must win, the second must be refused.
B. wake-up message : the "show panel" cross-process message must reach the
                     already-running instance's overlay window procedure and
                     set the flag that the UI thread polls.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
sys.path.insert(0, HERE)

from desktop_fx import SINGLETON_MUTEX as MUTEX_NAME   # noqa: E402 单一事实来源

ok = True


def kill_strays(timeout=6.0, quiet=1.0):
    """Best-effort: make sure no DesktopFX instance holds the mutex.

    只 terminate 一轮是不够的：紧挨着跑的上一个脚本（如 _verify_error_reporting）
    派生的 pythonw 可能还没 start 完 —— 第一轮扫描时它还不存在，杀掉的目标里
    没有它，随后它才起来并抢先占住互斥体，本脚本的"第一个进程"就变成 REFUSED，
    整个用例假失败。所以要求"连续 quiet 秒都扫不到候选"才算清干净。
    """
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                               wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD)]
    k32.QueryFullProcessImageNameW.restype = wintypes.BOOL

    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    def _pythonw_pids():
        snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)
        pids = []
        try:
            pe = PE32()
            pe.dwSize = ctypes.sizeof(pe)
            if k32.Process32FirstW(snap, ctypes.byref(pe)):
                while True:
                    if pe.szExeFile.lower() == "pythonw.exe":
                        pids.append(pe.th32ProcessID)
                    if not k32.Process32NextW(snap, ctypes.byref(pe)):
                        break
        finally:
            k32.CloseHandle(snap)
        return pids

    def _is_desktopfx(pid):
        """只认 DesktopFX 自己的 pythonw —— 见下方安全说明。"""
        h = k32.OpenProcess(0x1000, False, pid)     # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            n = wintypes.DWORD(520)
            buf = ctypes.create_unicode_buffer(520)
            if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                return False
            path = buf.value.lower()
            # 项目 venv 或 %LOCALAPPDATA%\DesktopFX\venv 两种分发形态都覆盖到
            return "desktop_fx" in path or "desktopfx" in path
        finally:
            k32.CloseHandle(h)

    def _mutex_held():
        """DesktopFX 的互斥体当前是否被占（=真的有实例在跑）。"""
        k32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k32.OpenMutexW.restype = wintypes.HANDLE
        h = k32.OpenMutexW(0x00100000, False, MUTEX_NAME)   # SYNCHRONIZE
        if not h:
            return False
        k32.CloseHandle(h)
        return True

    def _kill(pids):
        for pid in pids:                 # pid 只记一次，避免重复计数
            killed.add(pid)
            h = k32.OpenProcess(0x0001, False, pid)
            if h:
                k32.TerminateProcess(h, 1)
                k32.CloseHandle(h)

    # 第一阶段：只清 DesktopFX 自己的 pythonw（venv 路径带 desktop_fx）。
    # 这一阶段是安全的：不碰用户其它 pythonw 程序。
    # 为什么不能"扫到空就返回"：上一个脚本派生的 pythonw 可能此刻还没 start 完，
    # 空扫描发生在它出现之前。要求**连续 quiet 秒都没有候选**才算干净。
    killed = set()
    quiet_since = None
    deadline = time.time() + timeout
    while True:
        pids = [p for p in _pythonw_pids()
                if p not in killed and _is_desktopfx(p)]
        if pids:
            _kill(pids)
            quiet_since = None
        else:
            quiet_since = quiet_since or time.time()
            if time.time() - quiet_since >= quiet:
                return len(killed)
        if time.time() >= deadline:
            break
        time.sleep(0.15)

    # 第二阶段（罕见）：窄筛选清完互斥体仍被占 —— 说明占用者是个"路径里不带
    # desktopfx 的 pythonw"（例如某台机器直接用系统 python 跑 desktop_fx.py）。
    # 此时才扩大到所有 pythonw，并**明确告知**可能误伤。
    if _mutex_held():
        rest = [p for p in _pythonw_pids() if p not in killed]
        if rest:
            print(f"  [warn] 互斥体仍被占用，扩大到终止 {len(rest)} 个 pythonw 进程"
                  f"（可能包含非 DesktopFX 程序）")
            _kill(rest)
    return len(killed)


# ------------------------------------------------------------------ A. mutex
print("== A. mutex semantics across real processes ==")
print(f"  pre-clean: terminated {kill_strays()} stray pythonw.exe")
time.sleep(0.6)

probe = os.path.join(HERE, "_probe_singleton.py")
with open(probe, "w", encoding="utf-8") as f:
    f.write(
        "import os, sys, time\n"
        "sys.path.insert(0, r'%s')\n"
        "import desktop_fx\n"
        "got = desktop_fx._acquire_singleton()\n"
        "print('GOT' if got else 'REFUSED')\n"
        "time.sleep(2.0) if got else None\n" % HERE
    )

p1 = subprocess.Popen([PY, probe], stdout=subprocess.PIPE, text=True)
time.sleep(1.2)                       # let it grab the mutex and stay alive
p2 = subprocess.Popen([PY, probe], stdout=subprocess.PIPE, text=True)
out2 = p2.communicate(timeout=30)[0].strip()
out1 = p1.communicate(timeout=30)[0].strip()

print(f"  first  process : {out1}")
print(f"  second process : {out2}")
if out1 == "GOT" and out2 == "REFUSED":
    print("  [OK] mutex correctly refuses the second instance")
else:
    print("  [FAIL] mutex did not behave as expected")
    ok = False

# after the first process exits, the mutex must be released again
time.sleep(0.4)
p3 = subprocess.Popen([PY, probe], stdout=subprocess.PIPE, text=True)
out3 = p3.communicate(timeout=30)[0].strip()
print(f"  after first exits, a new process : {out3}")
if out3 == "GOT":
    print("  [OK] mutex released on process exit (no permanent lockout)")
else:
    print("  [FAIL] mutex stuck - the app could never start again")
    ok = False
os.remove(probe)

# ---------------------------------------------------------- B. wake-up message
print()
print("== B. cross-process 'show panel' message ==")
from _win32 import user32  # noqa: E402
import fx_winlayer  # noqa: E402

msg = fx_winlayer.show_panel_message()
print(f"  registered message id : {msg}")
if not msg:
    print("  [FAIL] RegisterWindowMessageW returned 0")
    ok = False
else:
    ov = fx_winlayer.Overlay().create()
    try:
        fx_winlayer._show_panel_flag = False
        sent = user32.PostMessageW(ov.hwnd, msg, 0, 0)
        print(f"  PostMessageW -> {sent}")
        if not sent:
            print("  [FAIL] PostMessageW failed")
            ok = False
        else:
            time.sleep(0.15)
            ov.pump()
            got = fx_winlayer.take_show_panel_request()
            again = fx_winlayer.take_show_panel_request()
            print(f"  take_show_panel_request() 1st={got}  2nd={again}")
            if got and not again:
                print("  [OK] message delivered and consumed exactly once")
            else:
                print("  [FAIL] flag not set, or not consumed")
                ok = False
    finally:
        ov.destroy()

print()
print("== SINGLE-INSTANCE VERIFIED ==" if ok else "== SINGLE-INSTANCE FAILED ==")
sys.exit(0 if ok else 1)
