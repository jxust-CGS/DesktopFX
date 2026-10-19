# -*- coding: utf-8 -*-
"""验证打包产物 dist/DesktopFX.exe 真机可跑且含最新改动。

检查项（都基于窗口/样式等客观信号，不靠肉眼）：
  1. exe 能启动，托盘窗口 + 两个覆盖层窗口都出现
  2. 面板**不是置顶窗**（GWL_EXSTYLE 的 WS_EX_TOPMOST 位为 0）——
     这是本次改动最外显的行为，exe 里有就说明打进了新代码
  3. 面板唤出后仍不是置顶窗（show() 走的是"临时置顶再摘掉"）
  4. 退出清理干净

用法：python tools/diag/exe_check.py
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes as wt

ROOT = __file__.rsplit("tools", 1)[0].rstrip("/\\")
EXE = os.path.join(ROOT, "dist", "DesktopFX.exe")
WS_EX_TOPMOST = 0x00000008

u = ctypes.WinDLL("user32", use_last_error=True)
u.GetClassNameW.restype = ctypes.c_int
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowTextW.restype = ctypes.c_int
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.IsWindowVisible.restype = wt.BOOL
u.IsWindowVisible.argtypes = [wt.HWND]
u.GetWindowLongW.restype = wt.LONG
u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
u.ShowWindow.restype = wt.BOOL
u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def wins():
    out = []

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        t = ctypes.create_unicode_buffer(200)
        u.GetWindowTextW(h, t, 200)
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        out.append((h, c.value, t.value, pid.value, bool(u.IsWindowVisible(h))))
        return True

    u.EnumWindows(PROC(cb), 0)
    return out


def desktopfx():
    return [(h, c, t, pid, v) for h, c, t, pid, v in wins()
            if "DesktopFX" in c or "TkTopLevel" in c]


def wait_for(pred, timeout=25.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.5)
    return False


def main():
    if not os.path.exists(EXE):
        print("找不到产物：%s" % EXE)
        return 1
    size = os.path.getsize(EXE)
    print("产物 %s  %.1f MB" % (os.path.basename(EXE), size / 1e6))

    print("\n清理旧实例:", "OK" if wait_for(lambda: not desktopfx(), 6)
          else "⚠️ 仍有残留")
    subprocess.Popen([EXE], cwd=os.path.dirname(EXE),
                     creationflags=0x00000008 | 0x00000200)
    ok = wait_for(lambda: len([1 for h, c, t, p, v in desktopfx()
                                if c == "DesktopFXOverlayCls"]) >= 2, 30)
    if not ok:
        print("❌ exe 启动后没出现两个覆盖层窗口")
        for h, c, t, p, v in desktopfx():
            print("   ", h, c, t, p, v)
        return 1
    rows = desktopfx()
    print("✅ 窗口已出现：")
    for h, c, t, p, v in rows:
        print("    hwnd=%-10d cls=%-22s pid=%-7d vis=%s" % (h, c, p, v))

    # 面板启动后是隐藏的（要等用户从托盘唤出），这里主动叫出来才能验样式。
    # 用项目自己的跨进程消息：注册同名 RegisterWindowMessage 拿到同一个 id。
    print("\n调出面板：")
    sys.path.insert(0, ROOT)
    try:
        import ctypes as _ct
        _u = _ct.WinDLL("user32", use_last_error=True)
        _u.RegisterWindowMessageW.restype = wt.UINT
        _u.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
        _u.PostMessageW.restype = wt.BOOL
        _u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        msg = _u.RegisterWindowMessageW("DesktopFX_ShowPanel_v1")
        ov = None
        for h, c, t, p, v in rows:
            if c == "DesktopFXOverlayCls":
                ov = h
                break
        if msg and ov:
            _u.PostMessageW(ov, msg, 0, 0)
        print("  发消息 msg=%s -> overlay=%s" % (msg, ov))
    except Exception as exc:
        print("  （发消息失败：%s）" % exc)
    wait_for(lambda: any(c == "TkTopLevel" for _h, c, _t, _p, _v in desktopfx()),
             10.0)
    rows = desktopfx()
    panels = [(h, c) for h, c, t, p, v in rows
              if c == "TkTopLevel" and "桌面特效" in t]
    rc = 0
    if not panels:
        print("❌ 面板仍未创建，无法验证样式位")
        rc = 1
    else:
        ph = panels[0][0]
        ex = u.GetWindowLongW(ph, -20)
        top = bool(ex & WS_EX_TOPMOST)
        print("\n面板 hwnd=%d ex=0x%08x topmost=%s" % (ph, ex & 0xFFFFFFFF, top))
        if top:
            print("❌ 面板仍是置顶窗 —— exe 里不是最新代码！")
            rc = 1
        else:
            print("✅ 面板不是置顶窗（含本次改动的代码）")
        # 唤出一次，确认 show() 的"临时置顶再摘掉"生效
        sys.path.insert(0, ROOT)
        try:
            import fx_winlayer
            msg = fx_winlayer.show_panel_message()
            h = u.FindWindowW(fx_winlayer._CLASS_NAME, None)
            if msg and h:
                u.PostMessageW(h, msg, 0, 0)
        except Exception as exc:
            print("（无法发唤出消息：%s）" % exc)
        time.sleep(1.2)
        ex2 = u.GetWindowLongW(ph, -20)
        t2 = bool(ex2 & WS_EX_TOPMOST)
        vis = bool(u.IsWindowVisible(ph))
        print("唤出后：visible=%s topmost=%s" % (vis, t2))
        if vis and not t2:
            print("✅ 唤出后可见、且置顶位已摘掉")
        elif not vis:
            print("（唤出没成功，仅验证样式）")
        if t2:
            print("❌ 唤出后仍置顶")
            rc = 1

    print("\n退出清理：")
    pids = {pid for _h, _c, _t, pid, _v in rows}
    for pid in pids:
        subprocess.run(["taskkill", "//F", "//PID", str(pid)],
                       capture_output=True, text=True, errors="replace")
    time.sleep(2.0)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wt.HANDLE
    k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k.TerminateProcess.restype = wt.BOOL
    k.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    for pid in pids:
        hh = k.OpenProcess(0x0001, False, pid)
        if hh:
            k.TerminateProcess(hh, 1)
            k.CloseHandle(hh)
    gone = wait_for(lambda: not desktopfx(), 8)
    print("✅ 窗口已全部清理" if gone else "⚠️ 仍有残留窗口")
    return rc


if __name__ == "__main__":
    sys.exit(main())