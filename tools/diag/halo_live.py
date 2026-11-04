# -*- coding: utf-8 -*-
"""真机验证：开着光环的 DesktopFX，屏幕上真的会随音乐出现光环。

做法：启动实例 → 把配置里 halo_enabled 打开 → 把光标**停在屏幕某处不动**
→ 抓屏，看那一块有没有画出光环；再对比"电平高"与"电平低"两种画面。

⚠️ 难点：光标位置由 SendInput 控制，但**系统真实光标**才是渲染依据
（MouseProbe 读 GetCursorPos）。所以脚本必须真的把光标移到目标点，
并且在抓屏时**别再动它**。

判据：
  1. 光标附近区域在"开着光环"时比"关着光环"多出亮像素（光环画出来了）
  2. 亮像素集中在光标周围（不在屏幕别处）
  3. 光环会**随时间变化**（律动）—— 两次抓屏之间有差异

用法：python tools/diag/halo_live.py
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes as wt

import numpy as np

ROOT = __file__.rsplit("tools", 1)[0].rstrip("/\\")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
PY_EXE = r"C:\Users\hspcadmin\.pyenv\pyenv-win\versions\3.11.9\python.exe"

u = ctypes.WinDLL("user32", use_last_error=True)
g = ctypes.WinDLL("gdi32", use_last_error=True)
u.GetClassNameW.restype = ctypes.c_int
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowTextW.restype = ctypes.c_int
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.IsWindowVisible.restype = wt.BOOL
u.IsWindowVisible.argtypes = [wt.HWND]
u.SetCursorPos.restype = wt.BOOL
u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
u.GetCursorPos.restype = wt.BOOL
u.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
g.BitBlt.restype = wt.BOOL
g.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                     ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
g.CreateCompatibleDC.restype = wt.HDC
g.CreateCompatibleDC.argtypes = [wt.HDC]
g.CreateCompatibleBitmap.restype = wt.HBITMAP
g.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
g.SelectObject.restype = wt.HGDIOBJ
g.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
g.GetDIBits.restype = ctypes.c_int
g.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, wt.LPVOID,
                        wt.LPVOID, wt.UINT]
g.DeleteObject.restype = wt.BOOL
g.DeleteObject.argtypes = [wt.HGDIOBJ]
g.DeleteDC.restype = wt.BOOL
g.DeleteDC.argtypes = [wt.HDC]
u.GetDC.restype = wt.HDC
u.GetDC.argtypes = [wt.HWND]
u.ReleaseDC.restype = ctypes.c_int
u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def grab(x0, y0, w, h):
    hdc = u.GetDC(None)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    g.BitBlt(mdc, 0, 0, w, h, hdc, x0, y0, 0x00CC0020)
    bi = _BIH()
    ctypes.memset(ctypes.byref(bi), 0, ctypes.sizeof(bi))
    bi.biSize, bi.biWidth, bi.biHeight = ctypes.sizeof(bi), w, -h
    bi.biPlanes, bi.biBitCount = 1, 32
    buf = (ctypes.c_char * (w * h * 4))()
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    arr = np.frombuffer(bytes(buf), dtype=np.uint8).reshape(h, w, 4)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(None, hdc)
    return arr[:, :, :3].copy()


def fx_windows():
    out = []

    def cb(h, _):
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        if "DesktopFX" in c.value:
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            out.append((h, c.value, pid.value))
        return True

    u.EnumWindows(PROC(cb), 0)
    return out


def kill_existing():
    pids = {pid for _h, _c, pid in fx_windows()}
    for pid in pids:
        subprocess.run(["taskkill", "//F", "//PID", str(pid)],
                       capture_output=True, text=True, errors="replace")
    time.sleep(1.0)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wt.HANDLE
    k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k.TerminateProcess.restype = wt.BOOL
    k.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    for pid in pids:
        h = k.OpenProcess(0x0001, False, pid)
        if h:
            k.TerminateProcess(h, 1)
            k.CloseHandle(h)
    for _ in range(12):
        time.sleep(0.4)
        if not fx_windows():
            return
    return


def set_cfg(**kw):
    """改配置（走项目自己的原子写）。"""
    from _paths import atomic_write_json
    p = os.path.join(ROOT, "config.json")
    with open(p, "r", encoding="utf-8") as f:
        cfg = json_load(f)
    cfg.update(kw)
    atomic_write_json(p, cfg)


def json_load(f):
    import json
    return json.load(f)


def find_desktop_spot():
    """找一块**真正的桌面空白**（WindowFromPoint 命中 Progman/WorkerW）。

    ⚠️ 不能随便挑坐标：主屏上可能有 MobaXterm / 浏览器这类**白底窗口**
    盖着，抓到的会是它们的白色内容区（实测最亮=255、均值=237，全白），
    什么特效都测不出来。所以要先用 WindowFromPoint 确认命中桌面。
    """
    for gy in range(200, 1100, 40):
        for gx in range(200, 1800, 40):
            h = u.WindowFromPoint(wt.POINT(gx, gy))
            if not h:
                continue
            c = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(h, c, 256)
            if c.value in ("Progman", "WorkerW", "SysListView32"):
                return gx, gy
    return 700, 500


def spawn_instance():
    log = open(os.path.join(ROOT, "probe_boot.log"), "w", encoding="utf-8")
    subprocess.Popen([PY_EXE, "desktop_fx.py"], cwd=ROOT, stdout=log,
                     stderr=subprocess.STDOUT,
                     creationflags=0x00000008 | 0x00000200)
    for _ in range(30):
        time.sleep(0.5)
        if len([1 for _h, c, _p in fx_windows()
                if c == "DesktopFXOverlayCls"]) >= 2:
            break
    time.sleep(3.0)


def shoot(x, y, n=6, gap=0.4):
    """连抓 n 帧，返回 (帧间最大差异, 各帧最亮值, 各帧均值)"""
    r = 140
    x0, y0 = x - r, y - r
    frames = []
    for _ in range(n):
        frames.append(grab(x0, y0, 2 * r, 2 * r))
        time.sleep(gap)
    diffs = [int(np.abs(frames[i].astype(int) - frames[i - 1].astype(int)).sum())
             for i in range(1, len(frames))]
    return max(diffs), [int(f.max()) for f in frames], \
        [round(float(f.mean()), 1) for f in frames]


def keep_awake():
    """阻止系统/显示器休眠，并唤醒已休眠的屏幕。

    ⚠️ 长时间无人操作时显示器会休眠，此时 **BitBlt 抓屏全黑**（实测整屏
    最亮=0），探测脚本会得出"没有特效"的假结论。要先把屏幕唤醒：
    SetThreadExecutionState(ES_CONTINUOUS|ES_DISPLAY_REQUIRED) 保持常亮，
    再用一次极小的鼠标移动当作输入事件把屏幕叫醒。
    """
    ES_CONTINUOUS = 0x80000000
    ES_DISPLAY_REQUIRED = 0x00000002
    ES_SYSTEM_REQUIRED = 0x00000001
    try:
        u.SetThreadExecutionState.restype = wt.DWORD
        u.SetThreadExecutionState.argtypes = [wt.DWORD]
        u.SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED
                                  | ES_SYSTEM_REQUIRED)
    except Exception:
        pass
    # 极小的鼠标移动（1 像素）—— 足以当作输入事件唤醒屏幕，又不会明显挪光标
    class _MI(ctypes.Structure):
        _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                    ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                    ("dwExtraInfo", ctypes.c_void_p)]

    class _II(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("mi", _MI)]

    arr = (_II * 1)()
    arr[0].type = 0
    arr[0].mi.dx, arr[0].mi.dy = 1, 0
    arr[0].mi.dwFlags = 0x0001          # MOUSEEVENTF_MOVE
    u.SendInput(1, arr, ctypes.sizeof(_II))


def play_test_tone(loop=True):
    """循环播放一段内置音效，让系统输出设备有信号。

    为什么需要它：**没人放音乐时电平恒 0，光环静止**（这是正确行为），
    于是"帧间无差异"既可能是"坏了"也可能是"没声音"——两回事分不开。
    自己播一段声音就把这个混淆因素消掉了：开着光环 + 有声音 → 会动；
    关着光环 + 同样有声音 → 不动。

    ⚠️ 必须是**有起伏**的信号：用 winsound 循环播放同一个音效时，每个采样
    窗口读到的峰值都一样（实测电平恒为 0.8178），光环半径恒定 → 画面不动 →
    还是测不出律动。所以这里**脉冲播放**（每隔 0.3s 放一次短音效），电平才会
    一高一低地起伏。

    用 winsound（异步），音量走系统音量，用内置的短音效。
    """
    import threading
    import winsound
    cands = []
    sfx_dir = os.path.join(ROOT, "assets", "sfx")
    if os.path.isdir(sfx_dir):
        for f in sorted(os.listdir(sfx_dir)):
            if f.lower().endswith(".wav"):
                cands.append(os.path.join(sfx_dir, f))
    if not cands:
        return None
    # 挑一个短的（起止清晰，脉冲之间有静音间隙）
    path = min(cands, key=lambda p: os.path.getsize(p))
    state = {"stop": False}

    def pulse():
        while not state["stop"]:
            try:
                winsound.PlaySound(path, winsound.SND_FILENAME
                                   | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            except Exception:
                pass
            time.sleep(0.3)

    th = threading.Thread(target=pulse, daemon=True)
    th.start()
    state["thread"] = th
    _TONE_STATE.append(state)
    return path


_TONE_STATE = []


def stop_test_tone():
    import winsound
    for st in _TONE_STATE:
        st["stop"] = True
    _TONE_STATE.clear()
    try:
        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass


def main():
    keep_awake()
    print("清理旧实例")
    kill_existing()
    base = dict(rain_enabled=False, snow_enabled=False, petal_enabled=False,
                meteor_enabled=False, spotlight_enabled=False,
                paint_enabled=False, wave_enabled=False,
                win_ripple_enabled=False, ripple_enabled=False,
                pet_enabled=False, halo_style="呼吸圆环",
                halo_color="#7EC8FF", halo_size=60.0, halo_sensitivity=1.0)

    cx, cy = find_desktop_spot()
    print("桌面空白点：(%d,%d)" % (cx, cy))

    # --- A. 开着光环（并播一段音效制造电平）---
    set_cfg(halo_enabled=True, **base)
    spawn_instance()
    u.SetCursorPos(cx, cy)
    time.sleep(1.5)
    pt = wt.POINT()
    u.GetCursorPos(ctypes.byref(pt))
    print("光标实际在 (%d,%d)" % (pt.x, pt.y))
    tone = play_test_tone()
    print("播放测试音效：%s" % (os.path.basename(tone) if tone else "（无）"))
    time.sleep(1.5)
    d_on, maxs_on, means_on = shoot(pt.x, pt.y)
    stop_test_tone()
    print("开着光环：帧间最大差异=%d  各帧最亮=%s" % (d_on, maxs_on))

    # --- B. 关掉光环（必须重启实例：配置不热读），同样播音效 ---
    print("\n关掉光环并重启实例（对照组）…")
    kill_existing()
    set_cfg(halo_enabled=False, **base)
    spawn_instance()
    u.SetCursorPos(cx, cy)
    time.sleep(1.5)
    u.GetCursorPos(ctypes.byref(pt))
    tone = play_test_tone()
    print("播放测试音效：%s" % (os.path.basename(tone) if tone else "（无）"))
    time.sleep(1.5)
    d_off, maxs_off, means_off = shoot(pt.x, pt.y)
    stop_test_tone()
    print("关着光环：帧间最大差异=%d  各帧最亮=%s" % (d_off, maxs_off))

    print("\n开着：帧间差异=%d / 关着：%d（两组都在放声音）" % (d_on, d_off))
    ok = d_on > max(d_off * 2, 3000)
    print("结论：%s" % ("✅ 光环在屏幕上随音乐律动（有声音时会动，关掉就不动）"
                        if ok else "❌ 没检测到光环"))
    kill_existing()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
