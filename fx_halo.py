# -*- coding: utf-8 -*-
"""鼠标光环：跟随光标的圆环，随**系统正在播放的音频**律动。

两部分：
  1. `AudioLevel` —— 读系统输出设备的实时电平（纯 ctypes 调 COM，零新依赖）
  2. `HaloField`  —— 光环绘制（多种样式，全代码绘制）

为什么必须读"系统音频"而不是程序自己的播放器：律动的意义在于跟随**用户
正在听的音乐**。程序自己放的点击音效当然也能测到电平，但那不是用户要的。

电平怎么来的（`IAudioMeterInformation`）：
    IMMDeviceEnumerator -> GetDefaultAudioEndpoint(eRender, eMultimedia)
        -> IMMDevice::Activate(IAudioMeterInformation) -> GetPeakValue
系统直接给出 0~1 的峰值，比自己抓 PCM 再算 RMS 便宜得多；代价是拿不到
频谱（分不了低频鼓点与高频镲片），所以"律动"是整体强弱而非分频段。

⚠️ COM 的三个坑（都踩过）：
  1. GUID 在内存里是 `DWORD Data1 / WORD Data2 / WORD Data3 / BYTE Data4[8]`，
     前三段是**小端**多字节字段 —— 按字符对顺序排字节会让
     CoCreateInstance 返回 CLASS_E_CLASSNOTAVAILABLE(0x80040154)。
  2. 构造 GUID 的缓冲区**必须有生命周期** —— 局部变量被 GC 后指针成野指针，
     Activate 会回 E_NOINTERFACE(0x80004002)，看着像"系统不支持"。
  3. `IAudioMeterInformation` 的真实 IID 是
     `C02216F6-8C67-4B5B-9D00-D008E73E0064`（Windows SDK
     um/endpointvolume.idl）。尾段极易记错。
     vtable: 3 GetPeakValue / 4 GetMeteringChannelCount / 5 GetChannelsPeakValues

COM 对象有 apartment 归属，**只能在创建它的线程里调用**，所以采集线程自己
建 COM、自己采，与渲染线程之间只传一个 float。
"""
import ctypes
import ctypes.wintypes as wt
import math
import threading
import time

import numpy as np

from fx_image import hex_to_bgr, _premul_pack

__all__ = ["AudioLevel", "HaloField", "HALO_STYLES", "HALO_STYLE_DEFAULT",
           "halo_style_names", "norm_halo_style"]

# ---------------------------------------------------------------- COM 基础设施

_ole32 = ctypes.WinDLL("ole32", use_last_error=True)
_ole32.CoCreateInstance.restype = ctypes.c_long
_ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD,
                                     ctypes.c_void_p, ctypes.c_void_p]
_ole32.CoInitializeEx.restype = ctypes.c_long
_ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wt.DWORD]
_ole32.CoUninitialize.restype = None
_ole32.CoUninitialize.argtypes = []

_CLSCTX_ALL = 23
_E_RENDER = 0
_E_MULTIMEDIA = 1
_COINIT_MULTITHREADED = 0

# GUID 缓冲区要保活：局部变量被 GC 后指针就是野指针（见模块 docstring）
_GUID_KEEP = []


def _guid(s):
    """'XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX' -> GUID 指针。

    Data1/Data2/Data3 是**小端**多字节字段，必须按字段拼，不能按字符对顺序
    直接排字节（那样 CoCreateInstance 会回 0x80040154）。
    """
    import struct
    a = s.replace("-", "")
    raw = (struct.pack("<IHH", int(a[0:8], 16), int(a[8:12], 16),
                        int(a[12:16], 16))
           + bytes.fromhex(a[16:32]))
    buf = (ctypes.c_ubyte * 16).from_buffer_copy(raw)
    _GUID_KEEP.append(buf)
    return ctypes.cast(ctypes.pointer(buf), ctypes.c_void_p)


_CLSID_MMDevice_ENUMERATOR = _guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
_IID_IMMDEVICEENUMERATOR = _guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
_IID_IAUDIOMETERINFORMATION = _guid("C02216F6-8C67-4B5B-9D00-D008E73E0064")


def _vcall(obj, index, restype, argtypes, *args):
    """按 COM vtable 下标调用。索引错位一位就会读到别的函数的返回值，
    表现为"数值恒 0"却不报错 —— 对照 SDK 头文件核过。"""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0]
    fn = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))[index]
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(fn)(obj, *args)


# ---------------------------------------------------------------- 音频电平

class AudioLevel:
    """系统输出设备的实时电平（0~1），带自适应归一化与包络平滑。

    对外只暴露 `level()` 一个 float，采集细节全在后台线程里。

    三件事决定了这个值"看起来像不像律动"：
    1. **归一化**：不同音量下峰值差一个数量级（实测 0.01~0.10），固定阈值
       会导致"轻音乐完全不动、重音乐常年爆表"。所以维护一个**自适应上限**
       （慢速衰减的 running max）与固定地板，映射到 0~1。
    2. **包络**：原始峰值是瞬时值，会在 0.02 与 0.09 之间乱跳。上升要快
       （鼓点立刻响应），下降要慢（光环不能疯狂闪烁）—— 双时间常数。
    3. **平滑系数与帧率无关**：按 dt 换算指数系数，60fps 与 144fps 的观感
       必须一致。
    """

    def __init__(self, interval=0.03):
        self.interval = max(0.01, float(interval))
        self._lock = threading.Lock()
        self._level = 0.0          # 平滑后电平 0~1
        self._raw = 0.0            # 最近一次原始峰值
        self._ceil = 0.06          # 自适应上限（running max，慢速衰减）
        self._floor = 0.0015       # 静音底噪
        self._ok = False           # COM 链路是否可用
        self._last_error = ""      # 最近一次失败的原因（供诊断）
        self._started = False
        self._stop = threading.Event()
        self._thread = None
        self._sensitivity = 1.0

    # -------------------------------------------------- 对外接口
    def start(self):
        """起后台采集线程（幂等）。"""
        if self._started:
            return
        self._started = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="fx-audio",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=1.5)
        self._thread = None
        self._started = False

    def set_sensitivity(self, s):
        """灵敏度（0.2~3.0），越大越容易"跳起来"。"""
        with self._lock:
            self._sensitivity = max(0.05, float(s))

    def level(self):
        """当前平滑电平 0~1。"""
        with self._lock:
            return self._level

    def available(self):
        """音频采集链路是否可用（没声卡/权限问题会是 False）。"""
        with self._lock:
            return self._ok

    def last_error(self):
        """最近一次失败的原因（空 = 没有失败过）。"""
        with self._lock:
            return self._last_error

    # -------------------------------------------------- 后台线程
    def _run(self):
        meter = None
        chan = 1
        peaks = None
        try:
            _ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
        except Exception:
            pass
        try:
            while not self._stop.is_set():
                if meter is None:
                    meter, chan = self._open_meter()
                    if meter is None:
                        # 没有渲染设备 / 权限不足：退避重试，别把 CPU 烧了
                        with self._lock:
                            self._ok = False
                        self._stop.wait(2.0)
                        continue
                    peaks = (ctypes.c_float * chan)()
                    with self._lock:
                        self._ok = True
                raw = self._read_peak(meter, chan, peaks)
                if raw is None:
                    meter = None          # 设备变了（拔耳机/换声卡）-> 重建
                    continue
                self._push(raw)
                self._stop.wait(self.interval)
        finally:
            try:
                _ole32.CoUninitialize()
            except Exception:
                pass

    def _open_meter(self):
        """建 IMMDeviceEnumerator -> 默认渲染设备 -> 电平表。

        ⚠️ **不要用一个大 except 把所有异常吞掉**：编程错误（比如变量名写错
        触发 NameError）会被伪装成"这台机器没有音频设备"，症状是电平恒 0
        而日志里什么都没有 —— 这个坑踩过一次。所以这里区分两种情况：
        COM 返回的错误码 = 设备不可用（正常情况，静默重试）；
        Python 异常 = 我们的代码有错（记下来，别装没事）。
        """
        enum = ctypes.c_void_p()
        try:
            hr = _ole32.CoCreateInstance(_CLSID_MMDevice_ENUMERATOR, None,
                                         _CLSCTX_ALL, _IID_IMMDEVICEENUMERATOR,
                                         ctypes.byref(enum))
            if hr < 0 or not enum:
                return None, 0
            dev = ctypes.c_void_p()
            hr = _vcall(enum, 4, ctypes.c_long,
                        [wt.DWORD, wt.DWORD, ctypes.c_void_p],
                        _E_RENDER, _E_MULTIMEDIA, ctypes.byref(dev))
            if hr < 0 or not dev:
                return None, 0
            meter = ctypes.c_void_p()
            hr = _vcall(dev, 3, ctypes.c_long,
                        [ctypes.c_void_p, wt.DWORD, ctypes.c_void_p,
                         ctypes.c_void_p],
                        _IID_IAUDIOMETERINFORMATION, _CLSCTX_ALL, None,
                        ctypes.byref(meter))
            if hr < 0 or not meter:
                return None, 0
            n = wt.DWORD()
            hr = _vcall(meter, 4, ctypes.c_long, [ctypes.POINTER(wt.DWORD)],
                        ctypes.byref(n))
            if hr < 0 or n.value == 0:
                return None, 0
            return meter, n.value
        except Exception as exc:
            # 这不是"设备不存在"，是我们的代码错了 —— 记下来
            self._last_error = "%s: %s" % (type(exc).__name__, exc)
            return None, 0

    def _read_peak(self, meter, chan, peaks):
        """读一次峰值（各声道取最大）。失败返回 None（设备失效）。"""
        try:
            hr = _vcall(meter, 5, ctypes.c_long,
                        [wt.DWORD, ctypes.POINTER(ctypes.c_float)],
                        chan, peaks)
            if hr < 0:
                return None
            v = 0.0
            for i in range(chan):
                p = peaks[i]
                if p > v:
                    v = p
            return float(v)
        except Exception:
            return None

    def _push(self, raw):
        """原始峰值 -> 归一化 -> 包络。"""
        with self._lock:
            self._raw = raw
            # 自适应上限：跟着峰值走，但衰减很慢（响过一次之后不能立刻掉下来，
            # 否则一段轻声的鼓点会被当成"极响"）
            if raw > self._ceil:
                self._ceil = raw
            else:
                self._ceil = max(0.04, self._ceil * 0.985)
            span = max(1e-4, self._ceil - self._floor)
            norm = (raw - self._floor) / span
            if norm < 0.0:
                norm = 0.0
            elif norm > 1.0:
                norm = 1.0
            norm *= self._sensitivity
            if norm > 1.0:
                norm = 1.0
            # 双时间常数包络：上升快（0.45），下降慢（0.06）
            if norm > self._level:
                self._level += (norm - self._level) * 0.45
            else:
                self._level += (norm - self._level) * 0.06


# ---------------------------------------------------------------- 光环样式

HALO_STYLE_DEFAULT = "呼吸圆环"

# 样式名 -> 内部标识。名字是中文（界面直接用），标识给代码用。
HALO_STYLES = {
    "呼吸圆环": "breath",
    "双环脉冲": "double",
    "彩虹光环": "rainbow",
    "辐条光环": "spokes",
    "星芒光环": "star",
    "粒子光环": "particles",
}

HALO_STYLE_ORDER = ["呼吸圆环", "双环脉冲", "彩虹光环", "辐条光环",
                    "星芒光环", "粒子光环"]

# 每个样式要多大包围盒（相对基础半径的倍数）。按样式给而不是一律 2.6 倍：
# 包围盒越小，逐像素运算越少（彩虹样式的 HSV→RGB 尤其吃这个）。
_HALO_SPAN = {
    "breath": 1.22, "double": 1.22, "rainbow": 1.28,
    "spokes": 1.42, "star": 2.20, "particles": 1.55,
}
# 哪些样式需要**角度**（arctan2 约 1.1ms/帧，不要白算）
_HALO_NEEDS_ANG = {"rainbow": True, "spokes": True, "star": True}


def halo_style_names():
    """界面用的样式名列表（顺序固定）。"""
    return list(HALO_STYLE_ORDER)


def norm_halo_style(name):
    """把配置里的样式值归一化成合法样式名（认标识，认旧名，认空）。"""
    if not name:
        return HALO_STYLE_DEFAULT
    s = str(name)
    if s in HALO_STYLES:
        return s
    for k, v in HALO_STYLES.items():
        if s == v:
            return k
    return HALO_STYLE_DEFAULT


# ---------------------------------------------------------------- 光环特效

class HaloField:
    """跟随光标的圆环，随音频电平律动。画在**鼠标层**。

    性能：只在自己的包围盒内逐像素算（numpy 向量化），不扫全屏。
    所有样式共享同一套"到中心的距离/角度"网格，只有"目标半径、宽度、
    颜色"不同 —— 换样式只是换几个参数，不换算法。

    样式一览：
        呼吸圆环  单环，半径随电平脉动
        双环脉冲  内外双环，一涨一落
        彩虹光环  颜色沿角度流转，随电平增亮
        辐条光环  圆周上一圈短线，长度随电平
        星芒光环  从中心放射的射线，随电平伸长并缓慢自转
        粒子光环  环上散布的粒子，随电平抖动散开
    """

    def __init__(self, w, h, seed=61):
        self.w, self.h = w, h
        self.cx = float(w) * 0.5
        self.cy = float(h) * 0.5
        self._rng = np.random.default_rng(seed)
        self._style = HALO_STYLE_DEFAULT
        self._rgb = (120.0, 200.0, 255.0)
        self._last_color = None
        self._base_r = 46.0
        self._level = 0.0
        self._phase = 0.0
        # 粒子光环的粒子（角度、基准半径、抖动相位）
        self._parts = self._make_parts(64)
        self._part_offsets = np.zeros((64, 3), dtype=np.float32)
        # 彩虹用的 256 级色环（构造时算一次；每帧现算 HSV 要 11.6ms）
        self._hue_lut = self._make_hue_lut(256)

    # -------------------------------------------------- 基础
    def _make_parts(self, n):
        ang = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
        # 基准半径收得紧一点（0.94~1.06），粒子才连成一个"环"而不是散开
        rad = self._rng.uniform(0.94, 1.06, size=n).astype(np.float32)
        pha = self._rng.uniform(0.0, 2.0 * math.pi, size=n).astype(np.float32)
        return ang.astype(np.float32), rad, pha

    @staticmethod
    def _make_hue_lut(n=256):
        """预计算 n 级均匀色相的 RGB 表（行 = (R, G, B)，0~1）。

        彩虹环每帧要按角度取色。现算 HSV→RGB 要 6 次 np.where 铺满包围盒
        （实测 11.6ms/帧）—— 改成查表后只要一次索引（~1ms）。
        256 级色阶肉眼看不出色带。
        """
        hue = np.arange(n, dtype=np.float32) / n
        h6 = hue * 6.0
        c = np.ones_like(h6)
        x = 1.0 - np.abs(h6 % 2.0 - 1.0)
        idx = h6.astype(np.int32) % 6
        lut = np.zeros((n, 3), dtype=np.float32)
        comps = ((c, x, np.zeros_like(x)), (x, c, np.zeros_like(x)),
                 (np.zeros_like(x), c, x), (np.zeros_like(x), x, c),
                 (x, np.zeros_like(x), c), (c, np.zeros_like(x), x))
        for i, comp in enumerate(comps):
            sel = (idx == i)
            for k in range(3):
                lut[:, k] = np.where(sel, comp[k], lut[:, k])
        return lut

    def resize(self, w, h):
        self.w, self.h = w, h

    def set_cursor(self, x, y):
        self.cx = float(x)
        self.cy = float(y)

    def set_style(self, name):
        s = norm_halo_style(name)
        if s != self._style:
            self._style = s

    def set_color(self, hex_str):
        if hex_str == self._last_color:
            return
        self._last_color = hex_str
        bgr = hex_to_bgr(hex_str)
        # ⚠️ 存成 **(b, g, r)** 顺序 —— `_premul_pack(b, g, r, a)` 的参数就是
        # BGR（打包成 0xAARRGGBB，低字节是蓝）。存成 RGB 会让红蓝互换
        # （浅蓝变成橙色），而这种错误在深色背景上几乎看不出来。
        self._rgb = (float(bgr[0]), float(bgr[1]), float(bgr[2]))

    def set_size(self, r):
        self._base_r = max(18.0, float(r))

    def set_level(self, v):
        """喂入电平 0~1（由 Scene 从 AudioLevel 取）。"""
        v = float(v)
        if v < 0.0:
            v = 0.0
        elif v > 1.0:
            v = 1.0
        self._level = v

    def active(self):
        """是否需要画（电平为 0 时也要画 —— 静默时是一圈很淡的环）。"""
        return True

    # -------------------------------------------------- 绘制
    def draw(self, u32, w, h, cfg, dt=0.0):
        """在鼠标层缓冲上画光环。"""
        self._phase += float(dt)
        style = self._style
        sid = HALO_STYLES[style]
        r0 = self._base_r * (1.0 + 0.55 * self._level)
        # 包围盒按样式给（`_HALO_SPAN`）—— 越小越省逐像素运算
        span = r0 * _HALO_SPAN.get(sid, 1.3) + 12.0
        x0 = int(max(0, np.floor(self.cx - span)))
        x1 = int(min(w, np.ceil(self.cx + span)))
        y0 = int(max(0, np.floor(self.cy - span)))
        y1 = int(min(h, np.ceil(self.cy + span)))
        if x1 <= x0 or y1 <= y0:
            return
        ys = np.arange(y0, y1, dtype=np.float32)[:, None]
        xs = np.arange(x0, x1, dtype=np.float32)[None, :]
        dx = xs - self.cx
        dy = ys - self.cy
        d = np.sqrt(dx * dx + dy * dy)
        # 角度只有三种样式要用 —— arctan2 约 1.1ms/帧，不需要的别算
        ang = (np.arctan2(dy, dx)
               if _HALO_NEEDS_ANG.get(sid) else None)

        fn = getattr(self, "_draw_" + sid)
        fn(u32, w, h, xs, ys, d, ang, r0)

    # ---- 各样式 ----
    def _blend(self, u32, w, h, xs, ys, mask, rgb, alpha_scale=1.0):
        """把 mask（0~1 的覆盖率）与 rgb 颜色按预乘 alpha 混进缓冲。

        预乘的意义：半透明的环叠在别的特效上时，颜色不会因为叠加而发灰。

        `rgb` 必须是 **(b, g, r)** 顺序 —— `_premul_pack(b, g, r, a)` 打包成
        0xAARRGGBB（低字节是蓝）。传 RGB 进去会红蓝互换。
        """
        if alpha_scale <= 0.0:
            return
        a = np.clip(mask * float(alpha_scale), 0.0, 1.0)
        a_c = np.rint(a * 255.0).astype(np.uint32)
        if not a_c.any():
            return
        cb, cg, cr = rgb
        packed = _premul_pack(
            np.rint(cb * a).astype(np.uint32),
            np.rint(cg * a).astype(np.uint32),
            np.rint(cr * a).astype(np.uint32), a_c)
        grid = ys.astype(np.int32) * np.int32(w) + xs.astype(np.int32)
        u32[grid.reshape(-1)] = packed.reshape(-1)

    def _ring_mask(self, d, radius, width):
        """圆环的覆盖率：中心线处 1，边缘处 0（软边）。"""
        half = max(0.5, width * 0.5)
        t = np.abs(d - radius) / half
        t = np.clip(t, 0.0, 1.0)
        return (1.0 - t) * (1.0 - t)

    def _draw_breath(self, u32, w, h, xs, ys, d, ang, r0):
        mask = self._ring_mask(d, r0, 3.2 + 2.0 * self._level)
        self._blend(u32, w, h, xs, ys, mask, self._rgb, 0.55 + 0.45 * self._level)

    def _draw_double(self, u32, w, h, xs, ys, d, ang, r0):
        m1 = self._ring_mask(d, r0, 3.0)
        # 内环与外环反相：一涨一落，像声波的两道涟漪
        m2 = self._ring_mask(d, r0 * 0.62, 2.4)
        inv = 1.0 - self._level
        mask = np.maximum(m1 * (0.35 + 0.65 * self._level),
                          m2 * (0.30 + 0.55 * inv))
        self._blend(u32, w, h, xs, ys, mask, self._rgb, 0.75)

    def _draw_rainbow(self, u32, w, h, xs, ys, d, ang, r0):
        mask = self._ring_mask(d, r0, 4.0 + 2.5 * self._level)
        if not mask.any():
            return
        # 颜色沿角度流转。⚠️ **必须查表**：直接按 HSV 现算要 6 次 np.where
        # 铺满整张包围盒（实测 11.6ms/帧，单它一个就吃掉大半帧预算）。
        # 预计算 256 级色环 + 索引取值，视觉上 256 级看不出色阶。
        idx = ((ang + self._phase * 0.8) / (2.0 * math.pi) % 1.0
               * 256.0).astype(np.int32) & 255
        rgb = self._hue_lut[idx]                # (ny, nx, 3)
        a = np.clip(mask * (0.5 + 0.5 * self._level), 0.0, 1.0)
        a_c = np.rint(a * 255.0).astype(np.uint32)
        # `_premul_pack` 要 (b, g, r) —— rgb 的索引是 R,G,B，要交换
        packed = _premul_pack(
            (rgb[..., 2] * a * 255.0).astype(np.uint32),
            (rgb[..., 1] * a * 255.0).astype(np.uint32),
            (rgb[..., 0] * a * 255.0).astype(np.uint32), a_c)
        grid = ys.astype(np.int32) * np.int32(w) + xs.astype(np.int32)
        u32[grid.reshape(-1)] = packed.reshape(-1)

    def _draw_spokes(self, u32, w, h, xs, ys, d, ang, r0):
        n = 24
        seg = (ang + math.pi) / (2.0 * math.pi) * n
        # floor 可能正好等于 n（角度在边界上）—— 必须夹住，否则查表越界
        idx = np.clip(np.floor(seg).astype(np.int32), 0, n - 1)
        frac = seg - idx
        # 每根辐条长度随电平变化（用角度序号做伪随机，错开避免整齐划一）。
        # ⚠️ wob 只依赖**整数序号**，所以查表即可 —— 全网格算 sin 要好几毫秒
        # （星芒样式的大包围盒下尤其明显）。
        wob = self._wob_lut(24, 1.7, 3.0)[idx]
        ln = r0 * (0.35 + 0.75 * self._level * (0.55 + 0.45 * wob))
        # 沿半径方向的软边
        half = max(1.5, r0 * 0.10)
        radial = np.clip((ln - d) / half + 1.0, 0.0, 1.0)
        # 横向：辐条中线亮，两侧暗（frac 距 0.5 越远越暗）
        lateral = np.clip(1.0 - np.abs(frac - 0.5) * 2.0, 0.0, 1.0)
        mask = radial * lateral
        mask = np.where(d > r0 * 1.35, 0.0, mask)
        self._blend(u32, w, h, xs, ys, mask, self._rgb, 0.55 + 0.4 * self._level)

    def _draw_star(self, u32, w, h, xs, ys, d, ang, r0):
        n = 12
        seg = (ang + math.pi) / (2.0 * math.pi) * n
        idx = np.clip(np.floor(seg).astype(np.int32), 0, n - 1)
        frac = seg - idx
        wob = self._wob_lut(12, 2.3, 1.5)[idx]
        ln = r0 * (0.8 + 1.35 * self._level) * (0.6 + 0.4 * (wob * 0.5 + 0.5))
        half = max(1.2, r0 * 0.06)
        radial = np.clip((ln - d) / half + 1.0, 0.0, 1.0)
        lateral = np.clip(1.0 - np.abs(frac - 0.5) * 2.4, 0.0, 1.0)
        mask = radial * lateral
        self._blend(u32, w, h, xs, ys, mask, self._rgb, 0.6 + 0.4 * self._level)

    def _wob_lut(self, n, k, speed):
        """n 个辐条的伪随机抖动系数（查表用）。

        只依赖**整数序号**，所以按序号预计算 n 个值即可 —— 免去在全网格
        （星芒样式的包围盒有三十万像素）上算 sin。
        """
        phase = self._phase * speed
        return np.sin(np.arange(n, dtype=np.float32) * k + phase)

    def _draw_particles(self, u32, w, h, xs, ys, d, ang, r0):
        angs, rads, phas = self._parts
        n = len(angs)
        # 粒子半径随电平"炸开"
        spread = 1.0 + 0.45 * self._level
        pr = r0 * rads * spread
        # 每个粒子在自己的角度上"抖动"
        jitter = np.sin(phas + self._phase * 4.0) * (0.10 + 0.22 * self._level)
        pa = angs + jitter * 0.5
        pxs = self.cx + np.cos(pa) * pr          # (n,)
        pys = self.cy + np.sin(pa) * pr          # (n,)
        # 每个粒子的亮度随自己的相位与电平 —— 基础亮度给足：静音时也要看得见
        # 一圈（否则"没有声音"看起来像"坏了"）
        bright = 0.55 + 0.45 * (np.sin(phas * 2.3 + self._phase * 6.0)
                                * 0.5 + 0.5)
        size = 2.2 + 3.0 * self._level
        # ⚠️ **性能**：逐粒子算整格距离场是 64 × (ny×nx) 次运算（每帧好几
        # 毫秒，白白吃掉一帧预算）。改成**只在粒子周围的小方框内算** ——
        # 总代价降到 64 × (2·size+1)² ≈ 几千次，几乎免费。
        acc = np.zeros((ys.shape[0], xs.shape[1]), dtype=np.float32)
        half = max(size, 0.5) + 1.0
        for i in range(n):
            ix0 = int(max(0, np.floor(pxs[i] - half)))
            ix1 = int(min(xs.shape[1], np.ceil(pxs[i] + half) + 1))
            iy0 = int(max(0, np.floor(pys[i] - half)))
            iy1 = int(min(ys.shape[0], np.ceil(pys[i] + half) + 1))
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            gx = xs[0, ix0:ix1][None, :]        # (1, w)
            gy = ys[iy0:iy1, 0][:, None]        # (h, 1)
            ddx = (gx - pxs[i]) ** 2 + (gy - pys[i]) ** 2
            m = np.clip(1.0 - np.sqrt(ddx) / max(size, 0.5), 0.0, 1.0) ** 2
            sub = acc[iy0:iy1, ix0:ix1]
            np.maximum(sub, m * bright[i], out=sub)
        self._blend(u32, w, h, xs, ys, acc, self._rgb, 0.55 + 0.45 * self._level)
