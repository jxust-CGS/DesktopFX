# -*- coding: utf-8 -*-
"""定位：为什么 AudioLevel 在后台线程里读到 0，而同线程脚本能读到。

对照两种执行方式，逐个 COM 步骤打印返回码：
  A. 主线程直接建 COM（已知能通）
  B. 后台线程建 COM（AudioLevel 用的方式）

差别若在 CoInitializeEx / apartment 上，B 的某一步会返回非 0。
"""
import ctypes
import ctypes.wintypes as wt
import sys
import threading
import time

sys.path.insert(0, __file__.rsplit("tools", 1)[0].rstrip("/\\"))

import fx_halo as fh  # noqa: E402


def probe(tag):
    """按 AudioLevel 的同一套步骤跑一遍，逐步打印。"""
    hr = fh._ole32.CoInitializeEx(None, fh._COINIT_MULTITHREADED)
    print("  [%s] CoInitializeEx -> 0x%08x%s"
          % (tag, hr & 0xFFFFFFFF,
             "（S_FALSE 表示本线程已初始化过）" if hr == 1 else ""))
    enum = ctypes.c_void_p()
    hr = fh._ole32.CoCreateInstance(fh._CLSID_MMDevice_ENUMERATOR, None,
                                    fh._CLSCTX_ALL, fh._IID_IMMDEVICEENUMERATOR,
                                    ctypes.byref(enum))
    print("  [%s] CoCreateInstance -> 0x%08x enum=%s"
          % (tag, hr & 0xFFFFFFFF, bool(enum)))
    if hr < 0 or not enum:
        return
    dev = ctypes.c_void_p()
    hr = fh._vcall(enum, 4, ctypes.c_long,
                   [wt.DWORD, wt.DWORD, ctypes.c_void_p],
                   fh._E_RENDER, fh._E_MULTIMEDIA, ctypes.byref(dev))
    print("  [%s] GetDefaultAudioEndpoint -> 0x%08x dev=%s"
          % (tag, hr & 0xFFFFFFFF, bool(dev)))
    if hr < 0 or not dev:
        return
    meter = ctypes.c_void_p()
    hr = fh._vcall(dev, 3, ctypes.c_long,
                   [ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, ctypes.c_void_p],
                   fh._IID_IAUDIOMETERINFORMATION, fh._CLSCTX_ALL, None,
                   ctypes.byref(meter))
    print("  [%s] Activate(meter) -> 0x%08x meter=%s"
          % (tag, hr & 0xFFFFFFFF, bool(meter)))
    if hr < 0 or not meter:
        return
    n = wt.DWORD()
    hr = fh._vcall(meter, 4, ctypes.c_long, [ctypes.POINTER(wt.DWORD)],
                   ctypes.byref(n))
    print("  [%s] GetMeteringChannelCount -> 0x%08x n=%d"
          % (tag, hr & 0xFFFFFFFF, n.value))
    if hr < 0 or n.value == 0:
        return
    peaks = (ctypes.c_float * n.value)()
    for i in range(3):
        time.sleep(0.2)
        hr = fh._vcall(meter, 5, ctypes.c_long,
                       [wt.DWORD, ctypes.POINTER(ctypes.c_float)],
                       n.value, peaks)
        pk = ctypes.c_float()
        hr2 = fh._vcall(meter, 3, ctypes.c_long,
                        [ctypes.POINTER(ctypes.c_float)], ctypes.byref(pk))
        print("  [%s] peaks hr=0x%08x %s | GetPeakValue hr=0x%08x %.5f"
              % (tag, hr & 0xFFFFFFFF,
                 [round(peaks[k], 5) for k in range(n.value)],
                 hr2 & 0xFFFFFFFF, pk.value))
    fh._ole32.CoUninitialize()


def main():
    print("A. 主线程：")
    probe("main")

    print("\nB. 后台线程（AudioLevel 的做法）：")
    th = threading.Thread(target=lambda: probe("thr"), daemon=True)
    th.start()
    th.join(6)

    print("\nC. 直接用 AudioLevel 类：")
    al = fh.AudioLevel()
    al.start()
    time.sleep(2.0)
    print("   available=%s  level=%.5f  raw=%.5f  ceil=%.5f"
          % (al.available(), al.level(), al._raw, al._ceil))
    al.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
