# -*- coding: utf-8 -*-
"""探测：能不能在纯 ctypes 下读到**系统正在播放的音频**（音乐律动的前提）。

两条路都试：
  A. IAudioMeterInformation —— 直接问系统"当前峰值电平"，最省事。
     COM 接口：IMMDeviceEnumerator → GetDefaultAudioEndpoint(eRender)
     → IMMDevice::Activate(IAudioMeterInformation) → GetPeakValue /
     GetChannelsPeakValues。
  B. IAudioClient loopback —— 把系统输出**回环**成 PCM 自己算频谱，
     能分频段（低频鼓点/中频人声/高频镲片），但要维护采集线程。

判据：能不能拿到非零的峰值。若系统此刻在放音乐，峰值应明显大于 0；
若没放音乐，应接近 0 —— **能区分"读到 0"和"读不到"**才是真跑通。

用法：python tools/diag/audio_probe.py [秒数]
"""
import ctypes
import sys
import time
from ctypes import wintypes as wt

ole32 = ctypes.WinDLL("ole32", use_last_error=True)
ole32.CoCreateInstance.restype = ctypes.c_long
ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD,
                                    ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
ole32.CoInitializeEx.restype = ctypes.c_long
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wt.DWORD]

CLSCTX_ALL = 23
E_RENDER = 0
E_MULTIMEDIA = 1
COINIT_MULTITHREADED = 0


_GUID_KEEP = []


def guid(s):
    """'XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX' -> GUID 指针。

    ⚠️ 三个坑都踩过：
    1. 不能用 `(c_ubyte * 16)` 逐字节赋值 —— ctypes 数组不支持 item
       assignment（TypeError）。要用 `from_buffer_copy` 一次拷进去。
    2. **不能按字符对顺序直接排字节**：GUID 在内存里的布局是
       DWORD Data1 / WORD Data2 / WORD Data3 / BYTE Data4[8]，前三段是
       **小端**多字节字段。按字符对顺序排等于把 Data1 当大端存，
       CoCreateInstance 会返回 CLASS_E_CLASSNOTAVAILABLE(0x80040154)。
       必须按字段用 struct.pack('<IHH', ...) 拼。
    3. **返回的指针必须有生命周期**：buf 是局部变量，函数返回后被 GC，
       调用方拿到的就是野指针 —— Activate 会回 E_NOINTERFACE(0x80004002)
       （看起来像"接口不支持"，其实是 IID 已经被回收了）。
       所以要把 buf 挂到模块级列表上保活。
    """
    import struct
    a = s.replace("-", "")
    data1 = int(a[0:8], 16)
    data2 = int(a[8:12], 16)
    data3 = int(a[12:16], 16)
    data4 = bytes.fromhex(a[16:32])
    raw = struct.pack("<IHH", data1, data2, data3) + data4
    buf = (ctypes.c_ubyte * 16).from_buffer_copy(raw)
    _GUID_KEEP.append(buf)          # 保活，别让 GC 回收
    return ctypes.cast(ctypes.pointer(buf), ctypes.c_void_p)


CLSID_MMDeviceEnumerator = guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
# ⚠️ IID 尾段极易记错：真实的 IAudioMeterInformation 是
#    C02216F6-8C67-4B5B-9D00-D008E73E0064
# （查 Windows SDK：um/endpointvolume.idl 的 uuid(...) 那一行）。
# 记成 "...-9D4D-8BF6B8FBF9E6" 会让 Activate 返回 E_NOINTERFACE(0x80004002)，
# 看起来像"系统不支持这个接口"，其实是 IID 写错了。
IID_IAudioMeterInformation = guid("C02216F6-8C67-4B5B-9D00-D008E73E0064")


def vcall(obj, index, restype, argtypes, *args):
    """按 COM vtable 下标调用。"""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0]
    fn = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))[index]
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(fn)(obj, *args)


def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
    hr = ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
    print("CoInitializeEx -> 0x%08x" % (hr & 0xFFFFFFFF))

    enum = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(CLSID_MMDeviceEnumerator, None, CLSCTX_ALL,
                                IID_IMMDeviceEnumerator,
                                ctypes.byref(enum))
    print("CoCreateInstance(IMMDeviceEnumerator) -> 0x%08x" % (hr & 0xFFFFFFFF))
    if hr < 0 or not enum:
        print("❌ 拿不到设备枚举器")
        return 1

    # IMMDeviceEnumerator::GetDefaultAudioEndpoint(eRender, eMultimedia, &dev)
    dev = ctypes.c_void_p()
    hr = vcall(enum, 4, ctypes.c_long,
               [wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_void_p)],
               E_RENDER, E_MULTIMEDIA, ctypes.byref(dev))
    print("GetDefaultAudioEndpoint -> 0x%08x  dev=%s" % (hr & 0xFFFFFFFF,
                                                     bool(dev)))
    if hr < 0 or not dev:
        print("❌ 没有默认渲染设备")
        return 1

    # IMMDevice::Activate(IID_IAudioMeterInformation, ...)
    meter = ctypes.c_void_p()
    hr = vcall(dev, 3, ctypes.c_long,
               [ctypes.c_void_p, wt.DWORD, ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p)],
               IID_IAudioMeterInformation, CLSCTX_ALL, None,
               ctypes.byref(meter))
    print("IMMDevice::Activate(IAudioMeterInformation) -> 0x%08x"
          % (hr & 0xFFFFFFFF))
    if hr < 0 or not meter:
        print("❌ 拿不到电平表接口")
        return 1

    # IAudioMeterInformation vtable（对着 SDK endpointvolume.h 核过）：
    #   0 QueryInterface / 1 AddRef / 2 Release
    #   3 GetPeakValue / 4 GetMeteringChannelCount / 5 GetChannelsPeakValues
    #   6 QueryHardwareSupport / 7 GetMeteringFrequency
    # ⚠️ 索引错位一位就会读到别的函数的返回值，表现为"峰值恒 0"而不报错。
    n_ch = wt.DWORD()
    hr = vcall(meter, 4, ctypes.c_long, [ctypes.POINTER(wt.DWORD)],
               ctypes.byref(n_ch))
    print("GetMeteringChannelCount -> 0x%08x  声道数=%d"
          % (hr & 0xFFFFFFFF, n_ch.value))
    if hr < 0 or n_ch.value == 0:
        print("❌ 声道数为 0")
        return 1

    # 采样：峰值 + 各声道峰值
    n = n_ch.value
    peaks = (ctypes.c_float * n)()
    print("\n采样 %.1f 秒（每 0.2s 一次）" % dur)
    print("  t(s)   peak    各声道")
    t0 = time.time()
    samples = []
    while time.time() - t0 < dur:
        peak = ctypes.c_float()
        hr1 = vcall(meter, 3, ctypes.c_long, [ctypes.POINTER(ctypes.c_float)],
                    ctypes.byref(peak))
        hr2 = vcall(meter, 5, ctypes.c_long,
                    [wt.DWORD, ctypes.POINTER(ctypes.c_float)],
                    n, peaks)
        vals = [round(peaks[i], 4) for i in range(n)]
        samples.append(peak.value)
        print("  %4.1f  %.5f  %s" % (time.time() - t0, peak.value, vals))
        time.sleep(0.2)

    mx = max(samples) if samples else 0.0
    nz = sum(1 for s in samples if s > 0.001)
    print("\n峰值最大=%.5f  非零采样=%d/%d" % (mx, nz, len(samples)))
    if nz == 0:
        print("判读: 全程 0 —— 链路可能是通的但**此刻没有声音**；"
              "放首歌再跑一次即可区分")
        print("      （建议对照：让系统播放音乐，然后重跑本脚本）")
    else:
        print("判读: ✅ 读到音频电平，链路可用（数字随音乐变化）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
