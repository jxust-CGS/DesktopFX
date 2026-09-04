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

ok = True


def kill_strays():
    """Best-effort: make sure no DesktopFX instance holds the mutex."""
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

    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)
    n = 0
    try:
        pe = PE32()
        pe.dwSize = ctypes.sizeof(pe)
        if k32.Process32FirstW(snap, ctypes.byref(pe)):
            while True:
                if pe.szExeFile.lower() == "pythonw.exe":
                    h = k32.OpenProcess(0x0001, False, pe.th32ProcessID)
                    if h:
                        k32.TerminateProcess(h, 1)
                        k32.CloseHandle(h)
                        n += 1
                if not k32.Process32NextW(snap, ctypes.byref(pe)):
                    break
    finally:
        k32.CloseHandle(snap)
    return n


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
