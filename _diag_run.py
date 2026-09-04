# -*- coding: utf-8 -*-
"""干净启动 -> 抓前后两帧 -> 差分判断雨是否在动 + 检测蓝青像素。"""
import os, sys, time, subprocess, ctypes, ctypes.wintypes as wt
import numpy as np
from PIL import ImageGrab

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_PY = r"C:\Users\hspcadmin\.workbuddy\binaries\python\envs\desktop_fx\Scripts\python.exe"
SCRIPT = os.path.join(ROOT, "desktop_fx.py")
OLAY_CLS = "DesktopFXOverlayCls"

u32 = ctypes.WinDLL("user32", use_last_error=True)

print("== 干净启动 ==")
env = dict(os.environ)
env["DESKTOP_FX_AUTOKILL"] = "9000"
p = subprocess.Popen([VENV_PY, SCRIPT], cwd=ROOT, env=env,
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     text=True, bufsize=1)
time.sleep(2.5)

olay = []
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
buf = ctypes.create_unicode_buffer(256)
def cb(hwnd, _l):
    if u32.GetClassNameW(hwnd, buf, 256) and buf.value == OLAY_CLS:
        ex = u32.GetWindowLongW(hwnd, -20)
        vis = u32.IsWindowVisible(hwnd)
        r = wt.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(r))
        olay.append((hwnd, vis, ex, (r.left, r.top, r.right, r.bottom)))
    return True
u32.EnumWindows(WNDENUMPROC(cb), 0)
print("overlay 窗口:", [(hex(h), 'vis' if v else 'hid', hex(e), rect) for h,v,e,rect in olay])

A = ImageGrab.grab()
A.save(os.path.join(ROOT, "_diag_A.png"))
time.sleep(0.35)
B = ImageGrab.grab()
B.save(os.path.join(ROOT, "_diag_B.png"))

a = np.asarray(A.convert("RGB")).astype(np.int16)
b = np.asarray(B.convert("RGB")).astype(np.int16)
diff = np.abs(a - b)
changed = int((diff.sum(axis=2) > 24).sum())
total = a.shape[0]*a.shape[1]
print(f"两帧间变化的像素(帧差>24): {changed} / {total}  ({100.0*changed/total:.2f}%)")

# 蓝青雨丝检测（在 B 上）
R, G, Bc = b[:,:,0].astype(np.int32), b[:,:,1].astype(np.int32), b[:,:,2].astype(np.int32)
mask = (Bc - R > 35) & (Bc > 90) & (G - R > 5) & (R < 200)
cnt = int(mask.sum())
print(f"偏蓝青像素(疑似雨/涟漪): {cnt} / {total}  ({100.0*cnt/total:.2f}%)")

# 差分里偏蓝青的像素（说明这些蓝青在动 = 真正的雨）
dm = (diff.sum(axis=2) > 24)
dm_blue = int((dm & (mask)).sum())
print(f"  其中在帧差里也变化的(=正在运动的蓝青): {dm_blue}")

print("截图 A/B 已保存: _diag_A.png _diag_B.png")
try:
    out, _ = p.communicate(timeout=8)
    print("== stdout/stderr (尾部) ==\n", (out or "")[-1500:])
except subprocess.TimeoutExpired:
    print("(程序仍在运行，AUTOKILL 未触发，手动结束)")
    p.terminate()
