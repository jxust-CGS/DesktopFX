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
    "三条线": "triple",
    "折线圆环": "poly",
    "赛博风": "cyber",
}

HALO_STYLE_ORDER = ["呼吸圆环", "双环脉冲", "彩虹光环", "辐条光环",
                    "星芒光环", "粒子光环",
                    "三条线", "折线圆环", "赛博风"]

# 每个样式要多大包围盒（相对基础半径的倍数）。按样式给而不是一律 2.6 倍：
# 包围盒越小，逐像素运算越少（彩虹样式的 HSV→RGB 尤其吃这个）。
#
# ⚠️ 只对**稠密样式**有意义：稀疏样式（见 `_HALO_SPARSE`）根本不建包围盒。
_HALO_SPAN = {
    "breath": 1.22, "double": 1.22, "rainbow": 1.28,
    "particles": 1.55,
}
# 哪些样式需要**角度**（arctan2 约 1.1ms/帧，不要白算）
_HALO_NEEDS_ANG = {"rainbow": True}

# 哪些样式走**稀疏描边**路径（不建包围盒、不逐像素算掩码）。
#
# 这些样式本质是"从圆心向外的一堆笔画"（辐条、星芒、三条线、折线、赛博），
# 用极坐标把每条笔画的像素参数化出来即可，不必遍历包围盒里的空白。
# 代价从 O(包围盒面积 ∝ 半径²) 降到 O(笔画总长 ∝ 半径) ——
# 这正是"波动幅度能放大 3 倍"的前提。
#
# ⚠️ 稀疏路径只写**有墨的像素**，不会像旧的整块写回那样把包围盒里
# alpha=0 的像素也写成 0 —— 那会把同层（水波）的像素擦掉一块。
_HALO_SPARSE = {"breath", "double", "rainbow", "particles",
                "spokes", "star", "triple", "poly", "cyber"}

# 半径上限。波动幅度调大时半径会涨，而**包围盒面积按平方涨** ——
# 不设上限的话，极端组合（一圈多粗 + 幅度拉满 + 音乐正响）能把一帧
# 拖到几百毫秒。
#
# 320 是权衡值：1080p 屏的半高是 540，光环半径 320 已经相当夸张；
# 而"大小 160 + 幅度 1.65"的组合理论半径是 424 —— 会被这个上限削掉
# 一小段（幅度不到 3 倍）。这是**有意的**：再大的光环既难看又抢戏。
MAX_HALO_R = 320.0

# 波动幅度滑块的范围（相对基准半径的百分比）。
# 0.55 是原版的写死系数 —— 保留为默认值，老配置升级后观感不变；
# 上限 1.65 正好是它的 3 倍（"波动幅度调节范围增大 3 倍"）。
HALO_SWING_MIN = 0.0
HALO_SWING_MAX = 1.65
HALO_SWING_DEFAULT = 0.55


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
        self._swing = HALO_SWING_DEFAULT
        # 稀疏描边的光斑半径（像素）：每个采样点摊成这么大一片，
        # 相邻采样点的光斑才能叠成连续的线
        self._spread = 1.2
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

    def set_swing(self, v):
        """波动幅度：音乐最响时半径相对基准的涨落比例（0~1.65）。

        半径 = 基准 × (1 + 幅度 × 电平)。幅度 0.55 是原版写死的值，
        1.65 是它的 3 倍 —— 也就是"波动幅度调节范围增大 3 倍"。
        """
        self._swing = max(HALO_SWING_MIN,
                          min(HALO_SWING_MAX, float(v)))

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
        # 半径 = 基准 × (1 + 波动幅度 × 电平)
        r0 = self._base_r * (1.0 + self._swing * self._level)
        if r0 > MAX_HALO_R:
            r0 = MAX_HALO_R
        fn = getattr(self, "_draw_" + sid)
        if sid in _HALO_SPARSE:
            # 稀疏样式：不建包围盒、不逐像素算掩码，直接按笔画画。
            # 代价只随"笔画总长"增长，与包围盒面积（∝ 半径²）无关。
            fn(u32, w, h, None, None, None, None, r0)
            return
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

        fn(u32, w, h, xs, ys, d, ang, r0)

    # ---- 各样式 ----
    # -------------------------------------------------- 稀疏描边（快路径）
    #
    # 为什么需要它：旧画法在**整个包围盒**内逐像素算 mask。包围盒面积与
    # 半径的平方成正比，于是「半径随音乐涨落」会把每帧代价按平方放大 ——
    # 实测星芒光环在半径 250 时已经要 90ms/帧（60fps 预算只有 16.7ms）。
    # 而"波动幅度调节范围增大 3 倍"恰恰要让半径涨到那么大。
    #
    # 稀疏画法换个思路：**先算出真正有墨的像素在哪，只写这些像素**。
    # 放射状样式（辐条/星芒/折线/赛博）本质上是一堆"从圆心向外的笔画"，
    # 每笔只占很窄的一条 —— 用极坐标把每笔参数化出来，笔的像素就能直接
    # 算出来，不必遍历包围盒里其余的空白。
    # 代价从 O(包围盒面积) 降到 O(笔画总长)，后者只随半径线性增长。
    #
    # ⚠️ 附带修掉一个旧画法的隐患：`_blend` 是**整块包围盒写回**，连
    # alpha=0 的像素也会被写成 0（= 全透明）。光环画在水波之后，于是
    # 包围盒内那圈水波会被"擦"掉一块。稀疏画法只写有墨的像素，天然
    # 不会误伤同层的其它特效。

    def _stroke(self, u32, w, h, r, th0, th1, width, rgb,
                alpha=1.0, taper=0.0, color_fn=None):
        """画一段**径向笔画**：半径 r 处、角度 th0~th1 的一条弧。

        这是稀疏描边的最小积木 —— 环、辐条、折线、赛博风全都是若干条
        这样的弧拼出来的。

        参数
            r        —— 笔画所在的半径
            th0, th1 —— 角度范围（弧度）
            width    —— 笔画厚度（像素，沿**径向**量）
            taper    —— 端点收细比例（0 = 不收细，1 = 两端收成尖）
            alpha    —— 整体不透明度
            color_fn —— 可选：按角度取色 (角度, 半径) -> (b, g, r)，
                        用于彩虹光环那种颜色随角度流转的样式

        几何要点：一条弧是"半径固定为 r、角度从 th0 走到 th1"的曲线，
        它的**厚度在径向上**（内圈到外圈 width 像素宽）。所以到笔画的
        距离 = |当前半径 - r|，切向那 1 像素弧长只用来**采样密度**，
        不参与距离计算。

        采样密度按弧长来：角步长 = 1 像素弧长对应的角度 = 1/r。
        半径大时步长自动变小，弧画得连续；半径小时步长变大，省算力。
        """
        if width <= 0.0 or alpha <= 0.0:
            return
        dth = abs(th1 - th0)
        if dth < 1e-6:
            return
        half_w = width * 0.5
        # 角步长 = 1 像素弧长 / 半径（保证采样点间距约 1 像素）。
        #
        # 但**不必真的每像素一个采样点**：光斑半径有 1.2 像素，采样点间距
        # 放宽到 1.6 像素时光斑仍然连成线，而采样量降到 6 成。半径越大
        # 越明显（彩虹样式在半径 320 时采样点是 2010 个）。
        step_px = 1.6
        dth_step = step_px / max(r, 1.0)
        n = max(3, int(math.ceil(dth / dth_step)) + 1)
        tt = np.linspace(th0, th1, n, dtype=np.float32)
        # 径向窗口：笔画厚度 + 各 1 像素软边
        rr = np.arange(r - half_w - 1.0, r + half_w + 1.0,
                       dtype=np.float32)
        R = rr[:, None]
        T = tt[None, :]
        px = self.cx + R * np.cos(T)
        py = self.cy + R * np.sin(T)
        # 到笔画的距离 = 径向偏移（笔画的厚度就是径向的）。
        # ⚠️ 必须与 T 广播成 (径向格数, 采样点数) —— 只写 np.abs(R - r)
        # 得到的是 (径向格数, 1)，整条弧会塌成一个点。
        dist = np.abs(R - r) * np.ones_like(T)
        mask = np.clip(1.0 - (dist - half_w) / max(0.75, width * 0.35),
                       0.0, 1.0)
        if taper > 0.0:
            # 沿弧长方向两端收细（只影响端点，不影响中段亮度）
            mid = (th0 + th1) * 0.5
            u = np.abs((T - mid) / max(dth * 0.5, 1e-6))
            mask = mask * np.clip(1.0 - (u ** 2) * taper, 0.0, 1.0)
        # 角度要展平成与 px 同形状（px 是 (径向格数, 采样点数)）
        self._paint(u32, w, h, px, py, mask, rgb, alpha,
                    color_fn=color_fn,
                    angles=np.broadcast_to(T, px.shape))

    def _paint(self, u32, w, h, px, py, mask, rgb, alpha=1.0,
               color_fn=None, angles=None):
        """把一小块 mask 画进缓冲（**只写有墨的像素**）。

        `px`/`py`/`mask` 形状相同，是这一小块自己的坐标系。

        ⚠️ 稀疏描边的像素是**采样点**，每个采样点只代表"这里该有多浓"，
        不是"这里只有一个像素"。所以要把每个采样点摊成一小片（半径
        `spread` 像素的软斑），相邻采样点的光斑自然叠成连续的线 ——
        只取整成 1 个像素的话，线会变成断断续续的虚线（采样间距 1 像素
        时必然如此）。

        `color_fn` 给定时按**每个采样点的角度**取色（彩虹光环用），
        此时 `angles` 必须与 `px` 同形状。`color_fn(角度, 半径)` 返回
        (n, 3) 的 (r, g, b)。
        """
        if alpha <= 0.0:
            return
        a = np.clip(mask * float(alpha), 0.0, 1.0)
        idx = np.nonzero(a > 0.002)
        n_sel = len(idx[0])
        if n_sel == 0:
            return
        a_sel = a[idx]
        sx = px[idx]
        sy = py[idx]
        spread = max(1.0, float(self._spread))
        cb, cg, cr = rgb
        # 逐采样点的颜色（没给 color_fn 就用统一色）
        if color_fn is not None and angles is not None:
            # angles 与 px 同形状 —— 用**二维**索引取值
            # （idx 是 (行, 列) 元组，不能用在展平数组上）
            ang_sel = np.asarray(angles, dtype=np.float32)[idx]
            rad_sel = np.sqrt((sx - self.cx) ** 2 + (sy - self.cy) ** 2)
            cols = color_fn(ang_sel, rad_sel)          # (n_sel, 3) r,g,b
        else:
            cols = None
        # 光斑偏移网格：显式的 (K, K)，K = 2*k+1
        k = int(spread)
        off = np.arange(-k, k + 1, dtype=np.float32)
        ox = np.repeat(off[None, :], off.size, axis=0)      # (K, K)
        oy = np.repeat(off[:, None], off.size, axis=1)      # (K, K)
        dist = np.sqrt(ox * ox + oy * oy)
        w_blob = np.clip(1.0 - (dist - spread * 0.5) / max(0.6, spread * 0.5),
                         0.0, 1.0).ravel()      # (K*K,)
        # 每个采样点摊成 (K*K,) 个像素
        ix = np.rint(sx[:, None] + ox.ravel()[None, :]).astype(np.int32).ravel()
        iy = np.rint(sy[:, None] + oy.ravel()[None, :]).astype(np.int32).ravel()
        wv = (a_sel[:, None] * w_blob[None, :]).ravel()
        # 每个像素记着它来自哪个采样点（取颜色要用）
        src = np.repeat(np.arange(n_sel, dtype=np.int32),
                        ox.size)
        ok = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h) & (wv > 0.002)
        if not ok.any():
            return
        ix = ix[ok]
        iy = iy[ok]
        wv = wv[ok]
        src = src[ok]
        # 同一像素可能被多个采样点覆盖 —— 取**最大权重**（累加会过曝成白）。
        #
        # ⚠️ 两条走过的弯路，别再走：
        #  1. `argsort` + `unique` 去重：O(n log n)，n 是"采样点 × 光斑
        #     面积"，大半径下几十万 —— 实测占掉一半帧预算。
        #  2. "局部直方图"（按 flat 的 min..max 开临时数组）：跨越大半个
        #     屏幕时临时数组有两百万元素 —— 实测星芒从 8ms 涨到 198ms。
        #
        # 现在的做法：`np.unique` 拿到唯一像素与反查表（unique 内部虽排序，
        # 但 n 只与墨迹成正比，比 argsort 那版少一次全量排序），
        # 再用 `np.maximum.at` 聚合。
        flat = iy.astype(np.int64) * np.int64(w) + ix.astype(np.int64)
        uniq, inv = np.unique(flat, return_inverse=True)
        wmax = np.zeros(len(uniq), dtype=np.float32)
        np.maximum.at(wmax, inv, wv)
        if cols is not None:
            # 颜色按**采样点**聚合，不按像素 —— 光斑的每个像素继承它所属
            # 采样点的颜色。同一根弧上颜色几乎相同，按采样点聚合省掉
            # (像素数 × 3) 的临时数组（彩虹样式下这一项就要好几 MB）。
            #
            # 去重后每个像素要一个"代表采样点"：取它最后出现的那个
            # （同一像素被多个光斑覆盖时，颜色取谁都能看）。
            rep = np.zeros(len(uniq), dtype=np.int32)
            np.maximum.at(rep, inv, src)
            cb_v = cols[rep, 2]
            cg_v = cols[rep, 1]
            cr_v = cols[rep, 0]
        else:
            cb_v = cb
            cg_v = cg
            cr_v = cr
        a_c = np.rint(wmax * 255.0).astype(np.uint32)
        packed = _premul_pack(
            np.rint(cb_v * wmax).astype(np.uint32),
            np.rint(cg_v * wmax).astype(np.uint32),
            np.rint(cr_v * wmax).astype(np.uint32), a_c)
        u32[uniq] = packed

    def _draw_breath(self, u32, w, h, xs, ys, d, ang, r0):
        """呼吸圆环：一圈细线，随电平涨落（半径变大、线也变粗变亮）。"""
        self._stroke(u32, w, h, r0, 0.0, 2.0 * math.pi,
                     3.2 + 2.0 * self._level, self._rgb,
                     0.55 + 0.45 * self._level)

    def _draw_double(self, u32, w, h, xs, ys, d, ang, r0):
        """双环脉冲：内外两环反相 —— 一涨一落，像声波的两道涟漪。"""
        inv = 1.0 - self._level
        self._stroke(u32, w, h, r0, 0.0, 2.0 * math.pi, 3.0,
                     self._rgb, 0.35 + 0.65 * self._level)
        self._stroke(u32, w, h, r0 * 0.62, 0.0, 2.0 * math.pi, 2.4,
                     self._rgb, 0.30 + 0.55 * inv)

    def _draw_rainbow(self, u32, w, h, xs, ys, d, ang, r0):
        """彩虹光环：颜色沿角度流转的圆环。

        ⚠️ **必须查表**：直接按 HSV 现算要 6 次 np.where 铺满整张包围盒
        （实测 11.6ms/帧，单它一个就吃掉大半帧预算）。预计算 256 级
        色环 + 索引取值，视觉上 256 级看不出色阶。
        """
        def color_fn(th, rr):
            idx = ((th + self._phase * 0.8) / (2.0 * math.pi) % 1.0
                   * 256.0).astype(np.int32) & 255
            return self._hue_lut[idx] * 255.0     # (n, 3) 的 r,g,b

        self._stroke(u32, w, h, r0, 0.0, 2.0 * math.pi,
                     4.0 + 2.5 * self._level, self._rgb,
                     0.5 + 0.5 * self._level, color_fn=color_fn)

    def _draw_spokes(self, u32, w, h, xs, ys, d, ang, r0):
        """辐条光环（**空心**）：一圈短弧段，中间是空的。

        为什么改成空心：实心辐条是一根根从圆心射出的**实线楔形**，在鼠标
        指针周围堆成一坨，视觉上把光标"埋"住了 —— 想点准一个按钮得先
        猜光标在哪。空心版本只画弧的**两条边**（像仪表盘的刻度框），
        中心留空，指针始终清清楚楚。
        """
        n = 24
        seg_w = 2.0 * math.pi / n
        # 24 根辐条，每根画成"口"字形（两条径向边 + 一道外弧）
        for i in range(n):
            th = i * seg_w + self._phase * 0.15
            # 每根辐条长度错开，避免整齐划一
            wob = 0.5 + 0.5 * math.sin(i * 1.7 + self._phase * 3.0)
            ln = r0 * (0.55 + 0.55 * self._level * (0.6 + 0.4 * wob))
            # 辐条占据的角度宽度：窄一点才不会挤成一团
            hw = seg_w * (0.16 + 0.10 * self._level)
            al = 0.45 + 0.35 * self._level
            # 两条径向边（从内圈到外圈）
            self._stroke(u32, w, h, (ln + r0 * 0.42) * 0.5,
                         th - hw, th - hw, 1.6, self._rgb, al)
            self._stroke(u32, w, h, (ln + r0 * 0.42) * 0.5,
                         th + hw, th + hw, 1.6, self._rgb, al)
            # 外端那一道弧
            self._stroke(u32, w, h, ln, th - hw, th + hw,
                         1.6, self._rgb, al)

    def _draw_star(self, u32, w, h, xs, ys, d, ang, r0):
        """星芒光环（**空心**）：放射状的芒线，只画线不画面。

        旧版是从圆心射出的实心三角，尖端在圆心 —— 正好压在鼠标指针上。
        现在改成：每道芒是一条**细长的描边三角形**（两条斜边 + 一道底边），
        中间镂空，且整体从圆心往外让开一段距离，光标所在处完全干净。
        """
        n = 12
        seg_w = 2.0 * math.pi / n
        for i in range(n):
            th = i * seg_w + self._phase * 0.22
            wob = 0.5 + 0.5 * math.sin(i * 2.3 + self._phase * 1.5)
            ln = r0 * (0.95 + 0.55 * self._level) * (0.68 + 0.32 * wob)
            # 内端离圆心留出空档 —— 光标就待在那儿
            r_in = r0 * 0.34
            hw = seg_w * 0.20
            al = 0.55 + 0.35 * self._level
            # 两条斜边：从内端到外端，角度从 th±hw 收到 th
            for sgn in (-1.0, 1.0):
                self._ray(u32, w, h, r_in, ln, th, th + sgn * hw,
                          1.7, self._rgb, al)
            # 外端封口
            self._stroke(u32, w, h, ln, th - hw, th + hw,
                         1.7, self._rgb, al)

    def _ray(self, u32, w, h, r0, r1, th0, th1, width, rgb, alpha=1.0):
        """一条**斜的**描边：从 (r0, th0) 连到 (r1, th1) 的直线笔画。

        极坐标下两点连线就是一条直线段。按参数 t 线性插值出这条线上的
        点，再按**到线段的距离**算覆盖率 —— 只在这条线自己的小窗口里算。

        实现上直接用**笛卡尔坐标**做"点到线段"的距离：把每个采样点投影
        到线段上，取最近点的距离。全程只在这条线附近的几百个点上运算，
        与包围盒面积无关。
        """
        if alpha <= 0.0 or width <= 0.0:
            return
        # 采样密度按线段长度（像素）来
        ax = self.cx + r0 * math.cos(th0)
        ay = self.cy + r0 * math.sin(th0)
        bx = self.cx + r1 * math.cos(th1)
        by = self.cy + r1 * math.sin(th1)
        seg_len = math.hypot(bx - ax, by - ay)
        if seg_len < 1e-3:
            return
        n = max(3, int(seg_len * 1.2) + 2)
        t = np.linspace(0.0, 1.0, n, dtype=np.float32)
        px = ax + (bx - ax) * t
        py = ay + (by - ay) * t
        # 到线段的距离（对每个采样点，取最近点的距离）
        vx, vy = bx - ax, by - ay
        ll = vx * vx + vy * vy
        wx = px - ax
        wy = py - ay
        u = np.clip((wx * vx + wy * vy) / ll, 0.0, 1.0)
        dxp = wx - u * vx
        dyp = wy - u * vy
        dist = np.sqrt(dxp * dxp + dyp * dyp)
        half_w = width * 0.5
        mask = np.clip(1.0 - (dist - half_w) / max(0.75, width * 0.4),
                       0.0, 1.0)
        self._paint(u32, w, h, px, py, mask, rgb, alpha)

    def _wob_lut(self, n, k, speed):
        """n 个辐条的伪随机抖动系数（查表用）。

        只依赖**整数序号**，所以按序号预计算 n 个值即可 —— 免去在全网格
        （星芒样式的包围盒有三十万像素）上算 sin。
        """
        phase = self._phase * speed
        return np.sin(np.arange(n, dtype=np.float32) * k + phase)

    def _draw_particles(self, u32, w, h, xs, ys, d, ang, r0):
        """粒子光环：环上一圈粒子，随电平抖动散开。

        每个粒子就是**一个光斑** —— 稀疏画法里最自然的一种：直接把
        粒子的位置当作采样点，光斑半径随电平变大。
        """
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
        # 每个粒子画成一个软圆斑（mask 用径向衰减）
        off = np.arange(-int(size) - 1, int(size) + 2, dtype=np.float32)
        ox = np.repeat(off[None, :], off.size, axis=0)      # (K, K)
        oy = np.repeat(off[:, None], off.size, axis=1)      # (K, K)
        dist = np.sqrt(ox * ox + oy * oy)
        blob = np.clip(1.0 - dist / max(size, 0.5), 0.0, 1.0) ** 2
        for i in range(n):
            px = pxs[i] + ox.ravel()[None, :]
            py = pys[i] + oy.ravel()[None, :]
            m = (blob.ravel()[None, :] * bright[i]).ravel()
            self._paint(u32, w, h, px, py, m.reshape(1, -1),
                        self._rgb, 0.55 + 0.45 * self._level)

    # ---- 新增样式 ----
    def _draw_triple(self, u32, w, h, xs, ys, d, ang, r0):
        """三条线：同心三条细弧，错相位起伏，像音频均衡器的三根柱。

        电平越高，三条线之间的间距越大（从"挤在一起"散开）——
        视觉上就是"三根柱子随音乐跳动"。
        """
        spread = 0.10 + 0.16 * self._level
        for k, (rr, al) in enumerate(((0.72, 0.85), (0.88, 0.70), (1.04, 0.55))):
            # 每条线各自的相位错开 -> 三条线不同步地起伏
            ph = self._phase * 2.4 + k * 2.1
            r = r0 * rr * (1.0 + spread * math.sin(ph))
            # 每条线只画一段（留缺口），像仪表盘的刻度
            gap = 0.28
            start = self._phase * 0.35 + k * 2.0
            self._stroke(u32, w, h, r, start, start + 2.0 * math.pi - gap,
                         2.0, self._rgb, al * (0.55 + 0.4 * self._level))

    def _draw_poly(self, u32, w, h, xs, ys, d, ang, r0):
        """折线圆环：多边形边框（而不是圆），随音乐缩放并缓慢自转。

        极坐标下"多边形的一条边"就是一条**斜的直线段**（从顶点到顶点），
        用 `_ray` 画直线段即可 —— 这正是折线圆环比圆环更"硬朗"的原因。
        """
        n = 7
        r = r0 * (0.72 + 0.30 * self._level)
        rot = self._phase * 0.25
        al = 0.55 + 0.4 * self._level
        for i in range(n):
            th0 = rot + i * 2.0 * math.pi / n
            th1 = rot + (i + 1) * 2.0 * math.pi / n
            # 顶点半径错开一点 -> 是不规则多边形（更有折线感）
            rv = r * (1.0 + 0.10 * math.sin(i * 2.1 + self._phase * 1.4))
            self._ray(u32, w, h, r, rv, th0, th1, 2.2, self._rgb, al)

    def _draw_cyber(self, u32, w, h, xs, ys, d, ang, r0):
        """赛博风：霓虹分段环 + 扫描线 + 数据块。

        三层叠加，全是稀疏描边：
          1. 外圈：断续的霓虹段（赛博朋克的招牌）
          2. 内圈：一道快速旋转的扫描弧
          3. 零散"数据块"：沿圆周分布的小短线
        """
        al = 0.6 + 0.4 * self._level
        # 1) 断续霓虹段
        n_seg = 14
        seg_w = 2.0 * math.pi / n_seg
        for i in range(n_seg):
            th = i * seg_w + self._phase * 0.3
            duty = 0.45 + 0.35 * math.sin(i * 1.3 + self._phase * 2.0)
            r = r0 * (1.0 + 0.05 * math.sin(i * 0.9 + self._phase * 3.0))
            self._stroke(u32, w, h, r, th, th + seg_w * duty,
                         2.4, self._rgb, al)
        # 2) 旋转扫描弧
        sweep = self._phase * 1.6
        self._stroke(u32, w, h, r0 * 0.82, sweep - 0.5, sweep + 0.5,
                     3.0, self._rgb, al)
        self._stroke(u32, w, h, r0 * 0.82, sweep - 0.5, sweep + 0.5,
                     1.2, self._rgb, al * 0.7)
        # 3) 数据块：沿内圈的小短线
        n_tick = 30
        for i in range(n_tick):
            th = i * 2.0 * math.pi / n_tick + self._phase * 0.2
            ln = 0.06 + 0.05 * (0.5 + 0.5 * math.sin(i * 2.7 + self._phase * 5.0))
            self._stroke(u32, w, h, r0 * (0.55 + ln),
                         th - 0.012, th + 0.012, 1.5, self._rgb,
                         al * (0.35 + 0.5 * (0.5 + 0.5 * math.sin(i * 1.9))))
