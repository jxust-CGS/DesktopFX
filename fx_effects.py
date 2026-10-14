# -*- coding: utf-8 -*-
"""
特效引擎：下雨 + 鼠标涟漪 + 落地水花 + 闪电。

所有绘制都直接写入 Overlay 暴露的 uint32 视图（0xAARRGGBB，小端 BGRA，
RGB 需预乘 alpha），整层再由 Windows 以 per-pixel alpha 合成到桌面，
因此这里只需"画上去"，半透明混合由系统完成。
"""
import colorsys
import ctypes
import ctypes.wintypes as wt
import math
import os
import random
import threading
import time

import numpy as np

from _win32 import user32, HANDLE, INT, SHORT

# 宠物跟随的卡通形象用 PIL 画（超采样 + LANCZOS，与 fx_ui._render_aa 同一套
# 先例）：三角耳朵 / 多段尾巴 / 细胡须这类形状用绘图原语表达要干净得多。
# 形状全部来自代码，不引入任何图片资源 —— 也就不牵扯第三方形象授权。
try:
    from PIL import Image as _PImage
    from PIL import ImageDraw as _PDraw
    from PIL import ImageFont as _PImageFont
    _LANCZOS = _PImage.Resampling.LANCZOS
    _BICUBIC = _PImage.Resampling.BICUBIC
except Exception:                       # pragma: no cover - 打包环境必有 PIL
    _PImage = None
    _PDraw = None
    _PImageFont = None
    _LANCZOS = None
    _BICUBIC = None

# ---- Win32 窗口枚举（仅 Windows 下可用；非 Windows 仅跳过，不报错）----
try:
    _WinU32 = user32
    _EnumWindows = _WinU32.EnumWindows
    _EnumWindows.restype = wt.BOOL
    _EnumWindows.argtypes = (
        ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM)
    _IsIconic = _WinU32.IsIconic
    _IsIconic.restype = wt.BOOL
    _IsIconic.argtypes = (wt.HWND,)
    _GetWindowRect = _WinU32.GetWindowRect
    _GetForegroundWindow = _WinU32.GetForegroundWindow
    _GetForegroundWindow.restype = wt.HWND
    _GetClassNameW = _WinU32.GetClassNameW
    _GetClassNameW.restype = wt.INT
    _GetClassNameW.argtypes = (wt.HWND, ctypes.c_wchar_p, wt.INT)
    _WIN32_OK = True
except Exception:
    _WIN32_OK = False

TAU = np.float32(2.0 * np.pi)

# 涟漪的同心环（半径比, 亮度比）与径向层（像素偏移, 亮度比）
RING_RATIO = np.array([1.0, 0.78, 0.55], dtype=np.float32)
RING_ALPHA = np.array([1.0, 0.45, 0.20], dtype=np.float32)
LAYER_OFF = np.array([-1.0, 0.0, 1.0], dtype=np.float32)
LAYER_ALPHA = np.array([0.45, 1.0, 0.45], dtype=np.float32)


# ------------------------------------------------------------------ 工具
def hex_to_bgr(s):
    s = s.lstrip("#").strip()
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    try:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        r, g, b = 191, 227, 255
    return np.array([b, g, r], dtype=np.float32)


def _premul_pack(b, g, r, a):
    """把 4 个 uint32 通道打包成 0xAARRGGBB。"""
    return b | (g << np.uint32(8)) | (r << np.uint32(16)) | (a << np.uint32(24))


def _over_blend(dst, sub, a, sc):
    """把**预乘**精灵 `sub` 以覆盖率 `a` 叠加到 `dst` 上（原地写回 dst 子区域）。

    这是真正的 over 混合，而不是本文件其它特效惯用的"非零覆盖"：

        dst = src + dst·(1 − src_a)

    缓冲本身就是预乘格式，所以 over 不必再除 alpha。**有羽化边缘的精灵必须走
    这里** —— 覆盖写法会把半透明边缘像素写成"实心但偏暗"，叠在涟漪 / 水面上
    就是一圈脏边。不透明粒子（雨滴、雪花的实心核）用覆盖就够了。

    参数：
        dst  目标：`u32.reshape(h, w)[y0:y1, x0:x1]` 的切片视图
        sub  精灵的对应子区域（预乘 RGBA float32，0~1）
        a    每像素覆盖率 = 精灵 alpha × 整体不透明度
        sc   常量缩放 = 255 × 整体不透明度（把预乘色转回 0~255 整数）
    """
    inv = 1.0 - a
    d = dst.astype(np.uint32)
    dr = ((d >> np.uint32(16)) & np.uint32(0xFF)).astype(np.float32)
    dg = ((d >> np.uint32(8)) & np.uint32(0xFF)).astype(np.float32)
    db = (d & np.uint32(0xFF)).astype(np.float32)
    da = ((d >> np.uint32(24)) & np.uint32(0xFF)).astype(np.float32)

    nr = sub[:, :, 0] * sc + dr * inv
    ng = sub[:, :, 1] * sc + dg * inv
    nb = sub[:, :, 2] * sc + db * inv
    na = a * 255.0 + da * inv      # a 已含不透明度

    out = (np.clip(nb, 0.0, 255.0).astype(np.uint32)
           | (np.clip(ng, 0.0, 255.0).astype(np.uint32) << np.uint32(8))
           | (np.clip(nr, 0.0, 255.0).astype(np.uint32) << np.uint32(16))
           | (np.clip(na, 0.0, 255.0).astype(np.uint32) << np.uint32(24)))
    dst[...] = out


def _smooth5(a, k, passes=1):
    """5 点拉普拉斯平滑（原地）。2D / 3D 数组都适用：
    3D 时最后一维是通道，各通道独立平滑。

    `c += k*(邻居均值 - c)` 里 RHS 会先整体算完再累加，所以重叠读写安全。
    """
    k = np.float32(k)
    for _ in range(passes):
        c = a[1:-1, 1:-1]
        c += k * (0.25 * (a[:-2, 1:-1] + a[2:, 1:-1]
                          + a[1:-1, :-2] + a[1:-1, 2:]) - c)


def _rs_table(g, f):
    """整数倍双线性放大的取样表：把长度 g 的轴放大 f 倍。

    返回 (i0, i1, w)，于是 `out = a[i0] + (a[i1] - a[i0]) * w` 就是双线性插值。
    权值以 f 为周期重复，所以整张表只在建网格时算一次，每帧只做两次 take。

    为什么必须有这一步：上屏是**最近邻**采样，模拟网格（短边 ~110、格子 ~10px）
    直接铺到屏幕上，每个格子就是一块 10px 的硬方块 —— 满屏"方格纸"。
    参考实例（WebGL Fluid）的染料纹理是 1024 宽（约半个屏宽）所以丝滑；CPU 上把
    模拟网格提到那个量级要付出 20 倍平流开销，不划算。改成先在**模拟网格上**做
    双线性放大到"显示网格"，再由最近邻铺屏：插值后的曲面是连续的，硬边消失，
    残留的只是格子边界处的一阶不连续（肉眼几乎不可见）。
    """
    t = np.arange(g * f, dtype=np.float32)
    u = (t + 0.5) / float(f) - 0.5          # 目标像素中心 -> 源坐标
    i0 = np.floor(u)
    np.clip(i0, 0.0, float(g - 1), out=i0)
    i1 = np.minimum(i0 + 1.0, float(g - 1))
    w = np.clip(u - i0, 0.0, 1.0)
    return (i0.astype(np.int32), i1.astype(np.int32), w.astype(np.float32))


def _hsv_to_rgb(h, s, v):
    """向量化 HSV->RGB（h/s/v ∈ [0,1]，任意同形状数组）。返回 (r, g, b)。"""
    h = np.mod(h, 1.0) * 6.0
    i = np.floor(h).astype(np.int32)
    f = h - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    i = np.mod(i, 6)
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return r, g, b


# ------------------------------------------------------------------ 雨
class RainField:
    def __init__(self, w, h, seed=7):
        self.rng = np.random.default_rng(seed)
        self.w, self.h = w, h
        self.n = 0
        self.x = np.zeros(0, np.float32)
        self.y = np.zeros(0, np.float32)
        self.depth = np.zeros(0, np.float32)     # 景深：越大越近、越快、越亮、越粗
        self.vary = np.zeros(0, np.float32)      # 个体随机扰动
        self._speed = np.zeros(0, np.float32)
        self._windx = np.zeros(0, np.float32)
        self._length = np.zeros(0, np.float32)
        self._rgb = (191.0, 227.0, 255.0)
        self.set_amount(240, w, h)

    def resize(self, w, h):
        self.w, self.h = w, h

    def _init_arrays(self, n, start=0, end=None):
        if end is None:
            end = n
        rng = self.rng
        cnt = end - start
        if cnt <= 0:
            return
        self.x[start:end] = rng.random(cnt, dtype=np.float32) * self.w
        self.y[start:end] = rng.random(cnt, dtype=np.float32) * self.h
        self.depth[start:end] = rng.uniform(0.32, 1.45, cnt).astype(np.float32)
        self.vary[start:end] = rng.uniform(0.78, 1.26, cnt).astype(np.float32)

    def set_amount(self, n, w=None, h=None):
        if w:
            self.w = w
        if h:
            self.h = h
        n = int(max(0, min(1500, n)))
        if n == self.n:
            return
        if n > self.n:
            x, y, d, v = self.x, self.y, self.depth, self.vary
            self.x = np.zeros(n, np.float32); self.x[:len(x)] = x
            self.y = np.zeros(n, np.float32); self.y[:len(y)] = y
            self.depth = np.zeros(n, np.float32); self.depth[:len(d)] = d
            self.vary = np.zeros(n, np.float32); self.vary[:len(v)] = v
            self._init_arrays(n, start=len(x))
        else:
            self.x = self.x[:n].copy()
            self.y = self.y[:n].copy()
            self.depth = self.depth[:n].copy()
            self.vary = self.vary[:n].copy()
        self.n = n

    def update(self, dt, cfg):
        """推进雨滴，返回本帧落到底边的 x 坐标数组（用于水花）。"""
        if self.n == 0:
            return np.zeros(0, np.float32)

        rng = self.rng
        speed = np.float32(cfg["rain_speed"]) * self.depth * self.vary
        windx = np.float32(cfg["wind"] * 340.0) * self.depth

        self.y += speed * dt
        self.x += windx * dt

        length = np.clip(speed * np.float32(0.030 * cfg["rain_length"]), 4.0, 170.0)

        # 落到底边 -> 记录水花位置并回到顶部
        hit = self.y >= self.h
        splash_x = self.x[hit].copy()
        if hit.any():
            k = int(hit.sum())
            self.y[hit] = -rng.random(k, dtype=np.float32) * self.h * 0.35 - length[hit] * 0.5
            self.x[hit] = rng.random(k, dtype=np.float32) * self.w
            self.depth[hit] = rng.uniform(0.32, 1.45, k).astype(np.float32)
            self.vary[hit] = rng.uniform(0.78, 1.26, k).astype(np.float32)

        # 左右出界 -> 环绕
        span = self.w + 120.0
        self.x = np.where(self.x < -60.0, self.x + span, self.x)
        self.x = np.where(self.x > self.w + 60.0, self.x - span, self.x)

        self._speed = speed
        self._windx = windx
        self._length = length
        return splash_x

    def draw(self, u32, w, h, cfg):
        if self.n == 0:
            return
        speed, windx, length = self._speed, self._windx, self._length

        mag = np.sqrt(speed * speed + windx * windx) + np.float32(1e-6)
        dirx = (windx / mag).astype(np.float32)
        diry = (speed / mag).astype(np.float32)

        rows_n = int(length.max()) + 2
        k = np.arange(rows_n, dtype=np.float32)                      # (R,)

        ry = self.y[:, None] - k[None, :] * diry[:, None]
        cx_ = self.x[:, None] - k[None, :] * dirx[:, None]
        valid = k[None, :] <= length[:, None]                        # (N,R)

        prof = np.clip(1.0 - k[None, :] / np.maximum(length[:, None], 1e-3), 0.0, 1.0)
        prof = (prof ** 1.2).astype(np.float32)                      # 尾部稍亮
        alpha = (self.depth[:, None] * self.vary[:, None] * prof
                 * np.float32(0.92 * cfg["rain_opacity"]))           # (N,R)

        keep = alpha > np.float32(0.012)
        np.logical_and(keep, valid, out=keep)

        iy = np.rint(ry[keep]).astype(np.int32)
        ix = np.rint(cx_[keep]).astype(np.int32)
        af = alpha[keep]

        m = (iy >= 0) & (iy < h) & (ix >= 0) & (ix < w)
        iy, ix, af = iy[m], ix[m], af[m]
        if iy.size == 0:
            return

        colr, colg, colb = self._rgb
        a_chan = np.rint(af * 255.0).astype(np.uint32)
        pb = np.rint(colb * af).astype(np.uint32)
        pg = np.rint(colg * af).astype(np.uint32)
        pr = np.rint(colr * af).astype(np.uint32)
        packed = _premul_pack(pb, pg, pr, a_chan)

        stride = np.int32(w)
        u32[iy * stride + ix] = packed

        # 近景雨滴加粗一列，制造层次
        thick = af > np.float32(0.30)
        if thick.any():
            ix2 = ix[thick] + 1
            iy2 = iy[thick]
            ok = ix2 < w
            if ok.any():
                af2 = af[thick][ok] * np.float32(0.55)
                a2 = np.rint(af2 * 255.0).astype(np.uint32)
                u32[iy2[ok] * stride + ix2[ok]] = _premul_pack(
                    np.rint(colb * af2).astype(np.uint32),
                    np.rint(colg * af2).astype(np.uint32),
                    np.rint(colr * af2).astype(np.uint32), a2)

    def set_color(self, hex_str):
        bgr = hex_to_bgr(hex_str)
        self._rgb = (float(bgr[2]), float(bgr[1]), float(bgr[0]))


# ------------------------------------------------------------------ 涟漪
class RippleField:
    CAP = 120

    def __init__(self):
        self.cx = np.zeros(self.CAP, np.float32)
        self.cy = np.zeros(self.CAP, np.float32)
        self.r0 = np.zeros(self.CAP, np.float32)
        self.rmax = np.zeros(self.CAP, np.float32)
        self.age = np.zeros(self.CAP, np.float32)
        self.life = np.ones(self.CAP, np.float32)
        self.strength = np.zeros(self.CAP, np.float32)
        self.squash = np.ones(self.CAP, np.float32)     # 纵向压扁，模拟透视
        self.col = np.zeros((self.CAP, 3), np.float32)  # BGR
        self.active = np.zeros(self.CAP, dtype=bool)

    def spawn(self, cx, cy, rmax, life, strength, color_bgr, r0=2.0, squash=1.0):
        free = np.flatnonzero(~self.active)
        if free.size:
            i = int(free[0])
        else:
            i = int(np.argmax(self.age / np.maximum(self.life, 1e-3)))
        self.cx[i] = cx
        self.cy[i] = cy
        self.r0[i] = r0
        self.rmax[i] = rmax
        self.age[i] = 0.0
        self.life[i] = life
        self.strength[i] = strength
        self.squash[i] = squash
        self.col[i] = color_bgr
        self.active[i] = True

    def update(self, dt):
        if not self.active.any():
            return
        self.age += dt
        self.active &= self.age < self.life

    def draw(self, u32, w, h):
        idxs = np.flatnonzero(self.active)
        if idxs.size == 0:
            return
        for i in idxs:
            p = float(self.age[i] / max(self.life[i], 1e-3))
            if p >= 1.0:
                continue
            grow = 1.0 - (1.0 - p) ** 2.0
            r = self.r0[i] + (self.rmax[i] - self.r0[i]) * grow
            a = self.strength[i] * ((1.0 - p) ** 1.7)
            if a < 0.012 or r <= 1.0:
                continue

            m = int(max(40, min(720, r * 4.2)))
            ang = np.linspace(0.0, TAU, m, endpoint=False, dtype=np.float32)
            ca = np.cos(ang)
            sa = np.sin(ang) * self.squash[i]

            # 形状统一为 (环数, 层数, 采样点数)
            rad = np.maximum(r * RING_RATIO[:, None, None] + LAYER_OFF[None, :, None], 0.0)
            al = a * RING_ALPHA[:, None, None] * LAYER_ALPHA[None, :, None]

            px = self.cx[i] + rad * ca[None, None, :]
            py = self.cy[i] + rad * sa[None, None, :]

            ix = np.rint(px.reshape(-1)).astype(np.int32)
            iy = np.rint(py.reshape(-1)).astype(np.int32)
            af = np.broadcast_to(al, (RING_RATIO.size, LAYER_OFF.size, m)).reshape(-1)

            ok = (iy >= 0) & (iy < h) & (ix >= 0) & (ix < w) & (af > 0.012)
            if not ok.any():
                continue
            ix, iy, af = ix[ok], iy[ok], af[ok]

            cb, cg, cr = self.col[i]
            a_chan = np.rint(af * 255.0).astype(np.uint32)
            packed = _premul_pack(
                np.rint(cb * af).astype(np.uint32),
                np.rint(cg * af).astype(np.uint32),
                np.rint(cr * af).astype(np.uint32), a_chan)
            u32[iy * np.int32(w) + ix] = packed


# ------------------------------------------------------------------ 真实水波
class WaterRippleField:
    """真实水波：二维波动方程（高度场 + 速度场双缓冲），画成迎光/背光的亮暗条纹。

    和 `RippleField`（解析彩色圆环）是**两个完全独立的特效**，可以同时开启对比。

    物理照抄参考壁纸 `workshop/431960/1431073750`（jQuery Ripples / sirxemic）的着色器：

        drop   : h += (0.5 - cos(clamp(1 - d/r, 0, 1) * PI) * 0.5) * strength
        update : g += (avg4(h) - h) * K;    g *= damp;    h += g

    这两行是**真的波**：向外传播、相互干涉、撞到边界反射，而且能撑好几秒
    （参考实测波前 108 px/s，4 秒才传遍屏幕；阻尼 0.995/步 ≈ 0.74/秒）。

    参考的第三段（采样背景图做 UV 折射）我们**做不了** —— 透明 overlay 拿不到
    桌面像素（`WaveField` 就是因为这个被删的）。改成把波面坡度当法线、按固定
    光向做迎光提亮 / 背光压暗，再叠一道浪尖高光。对任何桌面底色都成立。

    ### 两条与参考不同的设计（都是为了让"旋钮"变成物理量）

    1. **仿真频率固定 60Hz，波速靠拉普拉斯系数 K 调**。
       这个差分格式的相速度是 `c = sqrt(K)` 格/步，所以
       `K = (波速 / (60 × 格边长))²`。好处是无论网格多粗，波**每帧只推进
       `c` 格（约 2~3px）**，永远不会出现"格子太粗、波一帧跳 6px"的踏步感。
       （一开始按 `hz = 波速/格边长` 做，粗网格要降到 18~24Hz，波会有明显抖动。）
       `c` 必须 ≤ `_C_MAX`，超过这个差分格式会发散 —— 波速旋钮因此有上限。
    2. **阻尼按秒折算**：`damp_step = 每秒保留比例 ** (1/60)`。
       直接写每步常数的话，改分辨率会连波寿命一起改掉。
    """

    _CELL_TARGET = 560.0            # 长边目标格数（成本预算）
    _CELL_MIN, _CELL_MAX = 2.4, 5.0
    _HZ = 60.0                      # 固定仿真频率（与参考一致；帧率靠累加器解耦）
    _C_MAX = 1.25                   # 每步位移上限（格）；这个差分格式 K<=2 才稳定，留余量
    _MAX_STEPS_PER_FRAME = 3        # 卡顿时不追帧，避免雪崩
    _SPEED_REF = 180.0              # 默认波速 px/s（相速度；实测可见波前 ≈ 0.6× 它）
    _PERSIST_DEF = 0.45             # 默认衰减档位（≈ 每秒保留 0.72，与参考的 0.74 相当）
    _DAMP_MIN, _DAMP_MAX = 0.55, 0.93   # 每秒的振幅保留比例
    _EPS = 3.0e-4                   # "这里还有波"的高度阈值
    _STRENGTH_REF = 0.08            # 默认落点强度（参考：移动 0.002 / 点击 0.15）
    _H_MAX = 8.0                    # |h| / |g| 上限，纯粹防跑飞 —— 正常用不到
                                    # （2.0 会被连续拖动顶到，钳位把波峰削平）
    _IDLE_CHECK = 24                # 每多少步做一次"场上还有没有波"的全场扫描

    # ---- 上屏着色（见 draw / shade）----
    _KNEE_PER_PX = 5.2e-4           # 软饱和拐点：每像素高度变化多少算"满对比度"
    _KS_DEF = 0.45                  # 迎光提亮峰值
    _KD_DEF = 0.28                  # 背光压暗峰值
    _KSP_DEF = 0.45                 # 浪尖高光峰值
    _LIGHT_DEF = 225.0              # 光照来自左上（0=右 90=下 180=左 270=上）
    _OPACITY_REF = 0.14             # 兜底浓度
    _OPACITY_MAX = 0.20             # **硬上限**：用户要求"浓度限制在 20% 以内"。
                                    # 面板滑块的上限也是它，改这里要同步 fx_ui。
                                    # 它是一层叠在桌面上的半透明条纹，浓度就是
                                    # "桌面被盖住多少"，0.20 已经能看清波纹了。
    _BRIGHT_E = 1.50                # 迎光侧的亮度曲线指数（用 sqrt 实现，见 draw）
    _RENDER_EPS = 0.03              # 判定"这一格要画"的幅度阈值
    _LUT_N = 8192                   # "dep -> 预乘像素"查表的量化级数（见 _shade_lut）
    _LUT_HALF = (_LUT_N - 1) * 0.5  # dep ∈ [-1,1] -> 表索引的缩放
    _BLOCK_PX = 2.2                 # 显示网格的格子边长上限（px）
    _UP_MAX = 2                     # 双线性放大倍数上限
    _PAD = 3                        # 绘制包围盒往外留的格数

    # ---- 活塞扰动（窗口在水面上移动 / 缩放 / 出现 / 离开）----
    # 窗口是水面上的障碍物：它**不动时水面是平的**，一移动才把水挤开。所以注入量
    # 正比于边的**法向速度**，而不是"窗口在不在"。下面几个常数只影响量级：
    _PUSH_V_REF = 900.0             # 参考速度（像素/秒）：拖动窗口的典型速度
    _PUSH_AMP = 0.14                # 参考速度下，窗外那道浪的峰值
    _PUSH_W_DEF = 44.0              # 边缘扰动带的**完整宽度**（像素，只铺窗外那一侧）
    _PUSH_W_MIN = 1.6               # 扰动带不得窄于这么多个格 —— 再窄网格表示不出软包
    _DOME_AMP = 0.10                # 最小化 / 恢复时整块水面抬升或塌陷的幅度

    # `_push_band` 单向化时用的"每步位移"系数：g 每步增量是 `k·(四邻平均 − h)`，而
    # `四邻平均 − h = 0.25·∇²h`；对照 `h[n+1] = 2h[n] − h[n−1] + c²∇²h` 得 `c = 0.5√k`。
    _PUSH_C_COEF = 0.5
    # 包的两端是否归零。窗内是平水（0），若包在窗缘处直接取峰值，那里就有一个
    # **空间跳变**，它本身会向内辐射一片波；改成两端归零的包，窗缘处与静止水面
    # 连续，向内就没了这个源。代价是浪峰从窗缘外移 `B/2`（约 22px）。
    _PUSH_EDGE_ZERO = True

    # ---------------------------------------------------------- 生命周期
    def __init__(self, w, h, seed=0):
        self._rng = np.random.RandomState(seed)
        self._t = 0.0
        self._tint = (1.0, 1.0, 1.0)      # 高光染色 **(b, g, r)**，归一化到 0~1，默认纯白
        self._lut = None                  # 着色+打包的查表（与分辨率无关，见 _shade_lut）
        self._lut_key = None
        self.resize(w, h)

    def resize(self, w, h):
        """网格跟着分辨率走，但长边格数压在 `_CELL_TARGET` 附近 —— 成本有上界。"""
        self.w, self.h = int(w), int(h)
        self.cell = float(np.clip(max(w, h) / self._CELL_TARGET,
                                  self._CELL_MIN, self._CELL_MAX))
        self.gw = max(4, int(round(w / self.cell)))
        self.gh = max(4, int(round(h / self.cell)))
        self.height = np.zeros((self.gh, self.gw), np.float32)
        self.vel = np.zeros((self.gh, self.gw), np.float32)
        self._acc = 0.0
        self._alive = False
        self._since_check = 0
        self.k = self._coef(self._SPEED_REF)
        self.damp = self._damp_step(self._PERSIST_DEF)
        # 显示网格 = 模拟网格 × up。上屏是**最近邻**采样，模拟格 2.4~5px 直接铺屏
        # 就是一块块硬方块 —— 水波画的是**高频亮暗条纹**，方块化比颜料盘更扎眼。
        # 先在模拟网格上做双线性放大到"显示网格"（格 ≤ 1.6px），硬边就消失了。
        self._up = int(np.clip(np.ceil(self.cell / self._BLOCK_PX), 1, self._UP_MAX))
        self._rsy = _rs_table(self.gh, self._up)
        self._rsx = _rs_table(self.gw, self._up)

    def reset(self):
        self.height[:] = 0.0
        self.vel[:] = 0.0
        self._acc = 0.0
        self._alive = False
        self._since_check = 0

    # ---------------------------------------------------------- 参数换算
    def _coef(self, speed):
        """波速(px/s) -> 拉普拉斯系数 K。相速度 c = sqrt(K) 格/步，所以 K = c²。"""
        c = float(speed) / (self._HZ * self.cell)
        c = float(np.clip(c, 0.02, self._C_MAX))
        return c * c

    def _damp_step(self, persist):
        """wave_persist 0..1 -> 每秒保留比例 -> 每步保留比例。"""
        p = float(np.clip(persist, 0.0, 1.0))
        per_sec = self._DAMP_MIN + (self._DAMP_MAX - self._DAMP_MIN) * p
        return float(per_sec ** (1.0 / self._HZ))

    # ---------------------------------------------------------- 落点
    def drop(self, cx, cy, radius_px, strength):
        """一个落点：余弦软包（与参考一致 —— 缓升缓降，没有硬边，不会激起网格噪声）。"""
        if strength == 0.0 or radius_px <= 0.0:
            return False
        c = self.cell
        gx, gy = float(cx) / c, float(cy) / c
        spr = max(float(radius_px) / c, 0.75)
        x0 = max(0, int(np.floor(gx - spr)))
        x1 = min(self.gw, int(np.ceil(gx + spr)) + 1)
        y0 = max(0, int(np.floor(gy - spr)))
        y1 = min(self.gh, int(np.ceil(gy + spr)) + 1)
        if x1 <= x0 or y1 <= y0:
            return False
        ys = np.arange(y0, y1, dtype=np.float32)[:, None] - np.float32(gy)
        xs = np.arange(x0, x1, dtype=np.float32)[None, :] - np.float32(gx)
        d = np.sqrt(ys * ys + xs * xs)
        t = np.clip(1.0 - d / np.float32(spr), 0.0, 1.0)
        self.height[y0:y1, x0:x1] += (0.5 - np.cos(t * np.pi) * 0.5) * np.float32(strength)
        self._alive = True
        return True

    def splash(self, cx, cy, cfg, big=False):
        """按 cfg 落一个点。big=True 用于点击（半径 ×1.5、强度 ×2.2，与参考的 0.002/0.15 同比例）。"""
        r = float(cfg.get("wave_size", 22.0)) * (1.5 if big else 1.0)
        s = float(cfg.get("wave_strength", self._STRENGTH_REF)) * (2.2 if big else 1.0)
        return self.drop(cx, cy, r, s)

    # ---------------------------------------------------------- 活塞（窗口）
    def push_rect(self, rect, edge_v, strength, dt, width_px=0.0):
        """把矩形窗口当活塞：四条边各按**自身的法向速度**把水推开 / 抽回。

        `edge_v = (v左, v右, v上, v下)`，是四条边各自的移动速度（像素/秒，正向
        沿 +x / +y）。四条边的外法向是固定的（左 −x、右 +x、上 −y、下 +y），
        于是各边的注入量就是 `v·n`：

            左 −vx0    右 +vx1    上 −vy0    下 +vy1

        **这一个式子同时覆盖平移与改尺寸**：向右拖 = 左边塌陷 + 右边起浪（船尾的
        空腔、船首的推水）；向右拉宽 = 右边推进；向右缩窄 = 右边抽回。它们本来
        就是同一个物理量的不同取值，不必写成分支。

        ### 每帧注入多少：既跟速度走，又不依赖帧率

        设扰动带宽度 `B`（像素，**只往窗外那一侧铺**，默认 44 ≈ 旧写法两侧相加）。
        边扫过某个格点时，它在 `B/(v·dt)` 帧里一直覆盖这个格点，于是逐帧注入量取

            amp_frame = A · clip(v / V_REF) · (v·dt) / (2B)

        累积到扫完之后是 `A · clip(v / V_REF) / π` —— 与帧率**无关**，且快的拖动
        起浪更大（真实船波的弗劳德数也是这个方向），超过 V_REF 后饱和。若简单地
        "每帧注入一个常数"，30 帧/秒的机器上边在带里停留的帧数减半，浪就莫名弱
        一半。

        **为什么带只铺单侧、却仍取 44 宽**：带太窄会在快速拖动时"漏格"——边缘
        一帧跨 30px，而带只有 22px，中间就留下一条没被扫到的缝，浪变成虚线。
        旧写法两侧各 22、加起来正好 44；现在整条 44 都铺在窗外，空间覆盖不变。

        **为什么是 A/π 而不是 2A/π**：包只铺单侧（`∫₀¹cos(t·π/2)dt = 2/π`），
        而注入又是**单向**的（见 `_push_band`）。两者正好互相抵消 —— 旧写法是
        "两侧对称的静位移包、随后一分为二"，向外那半的峰值同样是 A/π。所以用户
        看到的浪**强度分毫不差**，只是窗口里侧不再有那半截内向的波。

        调用方**必须把带骑在"这一帧行程的中点"上**（见 `Scene._win_perturb`）：
        取端点等于用左端点法做积分，帧率越低每帧跨得越远、注入越被高估。
        """
        x0, y0, x1, y1 = (float(v) for v in rect)
        if x1 <= x0 or y1 <= y0:
            return False
        c = self.cell
        band = max(float(width_px) if width_px > 0.0 else self._PUSH_W_DEF,
                   self._PUSH_W_MIN * c)
        # 屏幕外的部分不用注入；先把矩形收进网格范围（大窗口常常只露出一部分）
        x0 = float(np.clip(x0, 0.0, self.w))
        x1 = float(np.clip(x1, 0.0, self.w))
        y0 = float(np.clip(y0, 0.0, self.h))
        y1 = float(np.clip(y1, 0.0, self.h))
        if x1 <= x0 or y1 <= y0:
            return False
        dt = max(float(dt), 1.0 / 240.0)
        vx0, vx1, vy0, vy1 = (float(v) for v in edge_v)
        # 分母 2B：包只铺单侧、扫过时的积分是 `2B/π`（旧写法两侧各 B/2、积分也是
        # `2B/π`），于是这里每帧注入的峰值与旧写法一致；差别只在"不再一分为二"，
        # 所以窗外那道浪的高度与从前**完全一样**，少掉的是钻进窗口里的另一半。
        scale = float(strength) * self._PUSH_AMP / (2.0 * band) * dt
        hit = False
        # out 是这条边的**外侧方向**，也就是波该去的方向：左 −1、右 +1、上 −1、下 +1。
        # 隔壁那条边永远在另一侧，所以"推出去的水"不会泼到窗口里。
        for axis, coord, lo, hi, vn, out in ((0, x0, y0, y1, -vx0, -1),
                                             (0, x1, y0, y1, vx1, +1),
                                             (1, y0, x0, x1, -vy0, -1),
                                             (1, y1, x0, x1, vy1, +1)):
            sp = float(np.clip(abs(vn) / self._PUSH_V_REF, 0.0, 1.0))
            if sp <= 0.0:
                continue
            # 面积守恒那一项：这一帧边只扫过 vn·dt，注入量按扫过的比例摊。
            amp = scale * sp * vn
            if abs(amp) < 1e-7:
                continue
            if self._push_band(axis, coord, lo, hi, amp, band, out):
                hit = True
        if hit:
            self._alive = True
        return hit

    def _push_band(self, axis, coord, lo, hi, amp, band, out_sign=1):
        """沿一条直线边注入一条软包带：**只铺窗口外侧**，而且是**单向行波**。

        `out_sign` = 这条边的外侧方向（= 波要去的方向）：左/上为 −1，右/下为 +1。

        ### 一、只铺外侧，且两端归零

        包从边缘起、向**外**铺满 `band`，窗口里侧一点不写。窗内那些格（格心落在
        `coord` 内侧）直接排除掉，而不是被 `clip` 夹成 0 —— 夹出来的 0 会让
        `cos(0) = 1`，在窗口里反手糊上一个峰值。

        包形默认**两端归零**（`sin`，峰值落在窗外 `band/2` 处）。这一点比"只铺
        外侧"还要紧：窗内是平水，包若在窗缘处直接取峰值，那里就留下一个**空间
        跳变**，它自己会向内辐射一片波 —— 实测里侧漏量 128%（比外侧还多），
        改成两端归零后立刻掉到 8.5%。代价只是浪峰从窗缘外移约 22px，看着更像
        船首前方那道弓形波。`_PUSH_EDGE_ZERO=False` 可切回"窗缘即峰值"，两种
        形状的积分完全相同（见 `test_band_shapes_agree_on_area`）。

        ### 二、单向：静止位移包会一分为二

        只铺外侧**不够**。往波场里丢一个静止的位移包，它必然分裂成左右各半的反向
        行波（这是波动方程的通解形状，跟包摆在哪儿无关）—— 另一半照样钻进窗口里，
        看着还是"内外同时起波"。要整包朝外走，得连**速度场**一起给：

        设注入的位移包 `f(i)`（i 是法向格索引）。右行波满足 `h[i](n+1) = h[i−c](n)`，
        `c` = 每步走过的格数，泰勒展开就是

            g = h[i−c] − h[i] ≈ −c · ∂f/∂i

        于是同时写 `height += f` 与 `vel += −c·out_sign·∂f/∂i`，注入的就是一列
        朝外走的行波，窗口里侧保持平水。对负的塌陷包（船尾空腔）同样成立：它也是
        朝外走的，只是相位反半个波。

        **`c` 是 `0.5·√k` 而不是 `√k`**：本格式的 `g` 每步增量是 `k·(四邻平均 − h)`，
        而 `四邻平均 − h = 0.25·∇²h`；对照 `h[n+1] = 2h[n] − h[n−1] + c²∇²h` 得
        `c² = 0.25k`。写成 `√k` 会让注入的"伴随速度"大一倍，波非但不单向、还比
        真实传播快一倍地泄漏回窗口里（实测里侧漏量 74%）。

        ### 三、切向收口

        切向 `[lo, hi]` 内恒为 1，两端各 `band` 内收口，角上才不会切出一条硬边。
        用软包带而不是沿边撒一串落点：落点间距必然大于波半径，波前会变成一串珠子；
        带既连成一条，又处处一阶连续（硬边会激起网格噪声，见 `test_no_grid_artifact`）。
        """
        c = self.cell
        s = 1 if out_sign >= 0 else -1
        lim_a, lim_b = (self.gw, self.gh) if axis == 0 else (self.gh, self.gw)
        # 只在外侧那 band 里：s=+1 -> [coord, coord+band]，s=-1 -> [coord-band, coord]
        na, nb = (coord, coord + band) if s > 0 else (coord - band, coord)
        a0 = max(0, int(np.floor(na / c)))
        a1 = min(lim_a, int(np.ceil(nb / c)) + 1)
        b0 = max(0, int(np.floor((lo - band) / c)))
        b1 = min(lim_b, int(np.ceil((hi + band) / c)) + 1)
        if a1 <= a0 or b1 <= b0:
            return False
        bd = np.float32(band)
        cf = np.float32(c)
        half_pi = np.float32(np.pi * 0.5)
        # **两个轴都先换成像素再比** —— `coord` / `lo` / `hi` 都是像素，格索引
        # 直接拿去减会差出一个 `cell` 的倍数（曾经漏了这一下，整条带恒为 0）。
        # 格中心 = idx·cell + cell/2。
        pa_px = (np.arange(a0, a1, dtype=np.float32) + np.float32(0.5)) * cf
        # 向外为正：t<0 是窗口里侧、t>1 是带外，两处都不注入（水只往外跑）
        t = (pa_px - np.float32(coord)) * np.float32(s) / bd
        inside = (t >= 0.0) & (t <= 1.0)
        t = np.clip(t, 0.0, 1.0)
        if self._PUSH_EDGE_ZERO:
            # 两端归零的包：窗缘处与静止的水面连续（值与法向斜率都不跳），
            # 不会向内辐射；峰值落在窗外 `band/2` 处（≈ 船首前方那道弓形波）。
            pa = np.sin(t * np.pi)
            dpa = np.cos(t * np.pi) * np.float32(np.pi * s * c / band)
        else:
            # 窗缘处直接取峰值的包：积分与上面完全相同，但窗缘有一个空间跳变。
            pa = np.cos(t * half_pi)
            dpa = -np.sin(t * half_pi) * half_pi * np.float32(s * c / band)
        pa[~inside] = 0.0
        dpa[~inside] = 0.0
        # 切向：到 [lo, hi] 的距离 / band（区间内为 0 -> 包值为 1）
        pb = (np.arange(b0, b1, dtype=np.float32) + np.float32(0.5)) * cf
        pb = np.maximum(np.maximum(np.float32(lo) - pb, pb - np.float32(hi)),
                        0.0) / bd
        np.clip(pb, 0.0, 1.0, out=pb)
        pb = np.cos(pb * half_pi)
        # 每步位移（格）= `_PUSH_C_COEF·√k`，见上面第二条说明。
        c_step = np.float32(self._PUSH_C_COEF * np.sqrt(max(self.k, 0.0)))
        a_s = np.float32(amp)
        g_s = (-c_step * np.float32(s) * a_s)            # 单向化：vel += −c·out·∂f/∂i
        if axis == 0:      # 竖直边：法向是 x，所以 outer 的行是 y、列是 x
            self.height[b0:b1, a0:a1] += a_s * np.multiply.outer(pb, pa)
            self.vel[b0:b1, a0:a1] += g_s * np.multiply.outer(pb, dpa)
        else:              # 水平边：法向是 y，行正是 y
            self.height[a0:a1, b0:b1] += a_s * np.multiply.outer(pa, pb)
            self.vel[a0:a1, b0:b1] += g_s * np.multiply.outer(dpa, pb)
        return True

    def dome_rect(self, rect, amp):
        """整块矩形当作水面上的一"包"：中心最高、四边归零（amp 可正可负）。

        最小化 = 窗口离开水面，水从四周合拢填掉它占的位置（amp < 0：水面塌陷，
        边缘的坡同时把波往里、往外推，看着就是"水合拢"）；恢复 = 窗口重新压回
        水面（amp > 0：把水向外推开）。同一几何形状的两个相位，波形差半个波，
        "离开"和"压回"一眼能分辨。

        用**可分离**的升余弦包（沿 x、y 各一条 cos 曲线相乘）：形状是圆角的包、
        没有硬边，成本只有一次外积。整块矩形（而不是只做一圈边）是因为窗口离开
        水面时，水的运动发生在它**留下的整个空洞**上。
        """
        amp = float(amp)
        if abs(amp) < 1e-7:
            return False
        c = self.cell
        x0, y0, x1, y1 = (float(v) for v in rect)
        x0 = float(np.clip(x0, 0.0, self.w))
        x1 = float(np.clip(x1, 0.0, self.w))
        y0 = float(np.clip(y0, 0.0, self.h))
        y1 = float(np.clip(y1, 0.0, self.h))
        if x1 - x0 < c or y1 - y0 < c:
            return False
        ix0 = max(0, int(np.floor(x0 / c)))
        ix1 = min(self.gw, int(np.ceil(x1 / c)))
        iy0 = max(0, int(np.floor(y0 / c)))
        iy1 = min(self.gh, int(np.ceil(y1 / c)))
        if ix1 <= ix0 or iy1 <= iy0:
            return False
        hw = max(0.5 * (x1 - x0), 0.5 * c)
        hh = max(0.5 * (y1 - y0), 0.5 * c)
        half_pi = np.float32(np.pi * 0.5)
        f = np.float32(c)
        px = ((np.arange(ix0, ix1, dtype=np.float32) + np.float32(0.5)) * f
              - np.float32((x0 + x1) * 0.5)) / np.float32(hw)
        py = ((np.arange(iy0, iy1, dtype=np.float32) + np.float32(0.5)) * f
              - np.float32((y0 + y1) * 0.5)) / np.float32(hh)
        np.clip(px, -1.0, 1.0, out=px)
        np.clip(py, -1.0, 1.0, out=py)
        px = np.cos(px * half_pi)
        py = np.cos(py * half_pi)
        self.height[iy0:iy1, ix0:ix1] += np.float32(amp) * np.multiply.outer(py, px)
        self._alive = True
        return True

    # ---------------------------------------------------------- 推进
    def step(self, k, damp):
        """一步波动方程。边界格恒为 0，等于一堵墙 —— 波撞上去会反射（参考也是反射）。"""
        h, g = self.height, self.vel
        if h.shape[0] < 3 or h.shape[1] < 3:
            return
        avg = (h[:-2, 1:-1] + h[2:, 1:-1] + h[1:-1, :-2] + h[1:-1, 2:]) * np.float32(0.25)
        g[1:-1, 1:-1] += (avg - h[1:-1, 1:-1]) * np.float32(k)
        g *= np.float32(damp)
        h += g
        np.clip(h, -self._H_MAX, self._H_MAX, out=h)
        np.clip(g, -self._H_MAX, self._H_MAX, out=g)

    def update(self, dt, cfg):
        """按累加器推进若干步。返回本帧实际推进的步数（0 = 这帧没动）。"""
        dt = float(dt)
        self._t += dt
        if not self._alive:
            # 空场：不推进也不累加，免得闲置时攒下一堆待补的步
            self._acc = 0.0
            return 0

        self.k = self._coef(cfg.get("wave_speed", self._SPEED_REF))
        self.damp = self._damp_step(cfg.get("wave_persist", self._PERSIST_DEF))
        step_dt = 1.0 / self._HZ
        self._acc += dt
        n = 0
        while self._acc >= step_dt and n < self._MAX_STEPS_PER_FRAME:
            self._acc -= step_dt
            self.step(self.k, self.damp)
            n += 1
        if n >= self._MAX_STEPS_PER_FRAME:
            self._acc = 0.0            # 卡顿时不追帧
        if n:
            # "场上还有没有波"要扫全场，比一步波动方程还贵，所以隔一段才判一次。
            self._since_check += n
            if self._since_check >= self._IDLE_CHECK:
                self._since_check = 0
                if self.active_box() is None:
                    self.reset()
        return n

    # ---------------------------------------------------------- 上屏
    def shade(self, cfg, box=None):
        """算出明暗调制量 `dep ∈ [-1, 1]`：正 = 迎光（提亮），负 = 背光（压暗）。

        对比度照抄参考的 `|offset| = |grad| / sqrt(|grad|² + delta²)` —— 一条**软饱和**
        曲线：坡度小于拐点时按比例减弱、大于拐点后封顶到 1。**不能直接拿坡度当亮度**，
        否则波一衰减整片就糊掉（实测 2 秒后峰值只剩 0.0036，而参考那时还很清楚）；
        也不能简单地把梯度归一化，那样连噪声都会被放大成满对比度条纹。

        拐点取 `_KNEE_PER_PX × 格边长`，即"每像素高度变化 5.2e-4"——与参考的
        `delta = 1/res`（res=800、格 2.4px）等价，所以两边观感的浓淡是对齐的。

        **化简**：原式 `amp × (∇h/|∇h| · L)` 里的 `|∇h|` 会约掉 ——

            |∇h|/√(|∇h|²+k²) × (∇h·L)/|∇h|  ==  (∇h·L) / √(|∇h|²+k²)

        于是不用算 `|∇h|`、不用 `where` 兜零除、也不会有 0/0 的 NaN（分母恒 ≥ k > 0）。
        实测这一步就省掉近一半的运算量。

        `box` 给定时只算那一块（带一圈邻居供差分），这是上屏路径的用法 ——
        波只占屏幕一小片时，成本随波的大小走而不是随屏幕走。
        """
        ang = np.radians(float(cfg.get("wave_light", self._LIGHT_DEF)))
        # **取负**：`wave_light` 说的是"光从屏幕哪个方向打过来"，而
        # `dep = (∇h·L)` 的 L 是"指向光源"的向量。实测不取负的话，把光设成
        # 从右边来，亮部反而出现在左边 —— 滑条会完全名不副实。
        lx, ly = np.float32(-np.cos(ang)), np.float32(-np.sin(ang))
        knee2 = np.float32((self._KNEE_PER_PX * self.cell) ** 2)
        if box is None:
            hx, hy = self.slope()
        else:
            hx, hy = self.slope(box)
        return (hx * lx + hy * ly) / np.sqrt(hx * hx + hy * hy + knee2)

    def draw(self, u32, w, h, cfg, solo=False):
        """把波面画成**迎光提亮 / 背光压暗**的亮暗条纹。

        参考的第三段是 `tex(背景, uv + offset * perturbance)` —— 真折射。我们拿不到
        桌面像素，改成把坡度方向当法线的水平分量：迎光的一侧提亮、背光的一侧压暗，
        再在浪尖叠一道高光。亮 = 白色预乘像素（把桌面提亮），暗 = 黑色预乘像素
        （把桌面压暗）——`UpdateLayeredWindow` 走的是 ULW_ALPHA + 预乘，
        两者都成立（聚光灯的全屏压暗早就在用这个能力）。

        `solo=True` 表示**本帧鼠标层上只有水波**（聚光灯 / 颜料盘 / 涟漪都没出图）。
        调用方若不能确定，一律用默认的 False —— 那只是慢一点，不会画错。
        """
        if not self._alive:
            return
        op = float(np.clip(cfg.get("wave_opacity", self._OPACITY_REF),
                          0.0, self._OPACITY_MAX))
        if op <= 0.0:
            return

        # 用"场上还有没有波"的范围当绘制范围：着色只在有波的地方非零，
        # pad 留 _PAD 格（波前每帧推进 ~1 格，不会跑出这个余量）。
        box = self.active_box(pad=self._PAD, thr=self._render_h())
        if box is None:
            return
        ry0, rx0, ry1, rx1 = box

        # 双线性要邻居，所以 dep 块比输出范围各多一圈。**必须真的多取到那一圈**，
        # 不能在块边缘用 clamp 顶替 —— 否则"裁剪版"与"整块网格版"会在边缘处
        # 用不同的值插值，差出几个 alpha 台阶（无损性测试会当场抓到）。
        ey0, ex0 = max(0, ry0 - 1), max(0, rx0 - 1)
        ey1, ex1 = min(self.gh, ry1 + 1), min(self.gw, rx1 + 1)
        dep = self.shade(cfg, box=(ey0, ex0, ey1, ex1))
        if float(np.abs(dep).max()) <= self._RENDER_EPS:
            return

        # 模拟网格 -> 显示网格（双线性）。插值的是 **dep 这个标量场** 而不是
        # 打包后的像素：`_premul_pack` 出来的 uint32 是位域，做加权平均会把
        # 通道串位。也不能先算 bright/dark 再插值 —— 那两条曲线带指数，
        # 先插值再映射才是"把波面形状平滑化"，物理上也更自然。
        f = self._up
        if f > 1:
            sy = slice(ry0 * f, ry1 * f)
            sx = slice(rx0 * f, rx1 * f)
            i0y = self._rsy[0][sy] - ey0
            i1y = self._rsy[1][sy] - ey0
            wy = self._rsy[2][sy]
            i0x = self._rsx[0][sx] - ex0
            i1x = self._rsx[1][sx] - ex0
            wx = self._rsx[2][sx]
            # **先 x 后 y**：numpy 的高级索引把被索引轴放到最内层，反过来得到的是
            # Fortran 连续数组，后续运算会走转置慢路径（颜料盘的 14.3ms -> 1.1ms）。
            #
            # 混合一律走 `out=` 原地：`X + (Y - X) * w` 这种写法会连开三个同尺寸临时
            # 数组，实测 **原地比临时快 2.4 倍**（9.2ms -> 3.8ms @1.4M 元素）。
            t = np.take(dep, i0x, axis=1)
            mid = np.take(dep, i1x, axis=1)
            np.subtract(mid, t, out=mid)
            np.multiply(mid, wx, out=mid)
            np.add(t, mid, out=t)
            dep = np.take(t, i0y, axis=0)
            mid = np.take(t, i1y, axis=0)
            np.subtract(mid, dep, out=mid)
            np.multiply(mid, wy[:, None], out=mid)
            np.add(dep, mid, out=dep)
        qh, qw = dep.shape
        k_s = float(np.clip(cfg.get("wave_contrast", self._KS_DEF), 0.0, 1.5))
        k_d = float(np.clip(cfg.get("wave_shading", self._KD_DEF), 0.0, 1.0))
        k_sp = float(np.clip(cfg.get("wave_highlight", self._KSP_DEF), 0.0, 1.5))

        # **着色与打包合成一次查表**：这两步的结果只取决于 `dep` 一个标量（k 与
        # 浓度都是本帧的常量），于是可以预先算成一张 8192 级的表，运行时只剩
        # "量化 + 查表"三步。原来那条二十多趟数组运算的流水线实测 16.9 ms
        # @52.7 万格（打包一项就 11.5 ms，是全帧最贵的地方），现在约 2 ms。
        # 误差见 `_shade_lut` 的推导：不到 0.02 个字节。
        lut = self._shade_lut(k_s, k_d, k_sp, op)
        idx = dep + np.float32(1.0)
        idx *= np.float32(self._LUT_HALF)
        idx += np.float32(0.5)              # +0.5 再取整 = 四舍五入
        np.clip(idx, 0.0, np.float32(self._LUT_N - 1), out=idx)
        small = lut[idx.astype(np.int32)]

        # 上屏：先 x 后 y（结果才是 C 连续，见 PaintField 的踩坑），
        # 且**只覆盖非零像素**，别把鼠标层上别人画好的东西整片抹掉。
        # `solo=True` 时（本帧鼠标层只有水波）缓冲区是空的，改成整片直写：
        # 实测 3.3M 像素下"掩码 + 按掩码写"要 7.0 ms，直写只要 1.0 ms。
        py0 = max(0, int(np.floor(ry0 * self.cell)))
        px0 = max(0, int(np.floor(rx0 * self.cell)))
        py1 = min(int(h), int(np.ceil(ry1 * self.cell)))
        px1 = min(int(w), int(np.ceil(rx1 * self.cell)))
        if py1 <= py0 or px1 <= px0:
            return
        # 屏幕像素 -> 显示格：取**最近的**显示格（round 不是 floor）。
        # 显示格 T 覆盖屏幕区间 [T·cell/f, (T+1)·cell/f)、中心在 (T+0.5)·cell/f，
        # 所以 T = round((py+0.5)·f/cell − 0.5)。用 floor 会归到区间左端，
        # 凭空叠上半格固定偏移（颜料盘踩过：+2.6px @1080p）。
        iy = np.rint((np.arange(py0, py1, dtype=np.float32) + 0.5)
                     * np.float32(f / self.cell) - 0.5).astype(np.int32) - ry0 * f
        ix = np.rint((np.arange(px0, px1, dtype=np.float32) + 0.5)
                     * np.float32(f / self.cell) - 0.5).astype(np.int32) - rx0 * f
        np.clip(iy, 0, qh - 1, out=iy)
        np.clip(ix, 0, qw - 1, out=ix)
        big = small[:, ix][iy]
        dst = u32.reshape(int(h), int(w))[py0:py1, px0:px1]
        if solo:
            dst[:] = big
        else:
            np.copyto(dst, big, where=big != 0)

    def _shade_lut(self, k_s, k_d, k_sp, op):
        """"明暗量 -> 预乘像素"的查表（8 千级）。参数或染色一变就重建。

        整条着色链的输入只有 `dep` 一个标量，输出也只有那一个像素，所以它可以
        完全预计算 —— 这是把上屏成本从"二十多趟数组运算"降到"量化 + 查表"的
        根据。原式逐条抄在下面（**改动这里必须同步改 draw 的说明**）：

            pos  = clip(dep, 0, 1)          neg = clip(-dep, 0, 1) = pos - dep
            bright = clip(pos^1.5·k_s + pos⁴·k_sp, 0, 1)
            alpha  = clip(bright + neg·k_d, 0, 1) · 255·op
            rgb    = bright · 255·op · 染色

        `pos^1.5` 用 `pos·sqrt(pos)`、`pos⁴` 用 `(pos²)²` —— float32 的 `np.power`
        实测 25ns/元素，是全流程最贵的一项，现在只在建表时对 8192 个点算一次。

        **量化误差**：dep 被分成 `_LUT_N` 级，单级宽 `2/(N-1)`。输出对 dep 的斜率
        最陡处在迎光支的 pos=1 附近，约 `(1.5·k_s + 4·k_sp)·255·浓度`；面板把
        `k_s/k_sp` 限在 1.5、浓度限在 0.20，故斜率 ≤ 2.5·255·0.20 ≈ 128 字节/单位，
        乘以级宽 2.4e-4 得 **0.03 个字节** —— 连一个量化台阶都不到，肉眼无差。

        **染色顺序是 (b, g, r)**：`_premul_pack` 收的就是 bgr，存成 rgb 会红蓝互换
        （第一版就是这么错的：设 #FF0000 画出来是蓝的）。
        """
        key = (k_s, k_d, k_sp, op, self._tint)
        if key == self._lut_key:
            return self._lut
        d = np.linspace(-1.0, 1.0, self._LUT_N, dtype=np.float32)
        pos = np.clip(d, 0.0, 1.0)
        neg = pos - d
        # `pos = clip(dep,0,1)`、`neg = clip(-dep,0,1)` 二者必有一个是 0，
        # 所以 `neg == pos - dep` —— 省掉一次 clip。
        sq = np.sqrt(pos)
        sq *= pos                              # pos^1.5
        p2 = pos * pos
        p2 *= p2                               # pos^4
        sq *= np.float32(k_s)
        p2 *= np.float32(k_sp)
        np.add(sq, p2, out=sq)
        np.clip(sq, 0.0, 1.0, out=sq)          # sq = bright
        neg *= np.float32(k_d)                 # neg = dark
        # dark 用完即弃，直接拿它当 (bright + dark) 的容器 —— 两者互斥。
        np.add(sq, neg, out=neg)
        neg *= np.float32(255.0 * op)
        np.clip(neg, 0.0, 255.0, out=neg)      # neg = alpha
        sq *= np.float32(255.0 * op)           # sq = 提亮量 0~255
        cb, cg, cr = self._tint
        self._lut = _premul_pack(
            (sq * np.float32(cb) + np.float32(0.5)).astype(np.uint32),
            (sq * np.float32(cg) + np.float32(0.5)).astype(np.uint32),
            (sq * np.float32(cr) + np.float32(0.5)).astype(np.uint32),
            (neg + np.float32(0.5)).astype(np.uint32))
        self._lut_key = key
        return self._lut

    def slope(self, box=None):
        """**前向差分**坡度（每格）。和参考的 cross(dy, dx) 用的是同一对量。

        边界格留 0 —— 那里没有"外面的格子"，坡度当 0 正好等于"没有倾斜、不着色"。
        `box` 给定时只算 `(y0, x0, y1, x1)` 那一块，并且会多取一圈邻居，
        免得块的边界被当成"外面的格子"而留着 0（那样会在波里面切出一道假的黑边）。
        """
        h = self.height
        if box is None:
            hx = np.zeros_like(h)
            hy = np.zeros_like(h)
            hx[:, :-1] = h[:, 1:] - h[:, :-1]
            hy[:-1, :] = h[1:, :] - h[:-1, :]
            return hx, hy
        y0, x0, y1, x1 = box
        py0, px0 = max(0, y0 - 1), max(0, x0 - 1)
        py1, px1 = min(self.gh, y1 + 1), min(self.gw, x1 + 1)
        sl = h[py0:py1, px0:px1]
        fx = np.zeros_like(sl)
        fy = np.zeros_like(sl)
        fx[:, :-1] = sl[:, 1:] - sl[:, :-1]
        fy[:-1, :] = sl[1:, :] - sl[:-1, :]
        oy, ox = y0 - py0, x0 - px0
        return (fx[oy:oy + (y1 - y0), ox:ox + (x1 - x0)].copy(),
                fy[oy:oy + (y1 - y0), ox:ox + (x1 - x0)].copy())

    def set_color(self, hex_str):
        """高光的染色（暗面恒为黑，压暗本来就该是无彩的）。"""
        bb, gg, rr = hex_to_bgr(hex_str)          # hex_to_bgr 给的是 0~255
        # 保持 **BGR** 存（与 _premul_pack 的入参一致），别顺手改成 RGB
        self._tint = (float(bb) / 255.0, float(gg) / 255.0, float(rr) / 255.0)
        return self._tint

    # ---------------------------------------------------------- 查询
    def _render_h(self):
        """绘制包围盒该用的高度阈值。

        **不能直接用 `_EPS`**：包围盒是按"还有没有波"（高度）划的，可上屏画的是
        **坡度**。两个都低于阈值的相邻格之间照样能有 `2 × 阈值` 的高度差，换算成
        明暗就是 `2 × 阈值 / 拐点` —— 拿 `_EPS=3e-4` 去算会到 0.33，远大于
        `_RENDER_EPS=0.03`，于是盒子外面**有可见的波被裁掉**。
        反解一下：想让漏掉的明暗都 < `_RENDER_EPS`，高度阈值就得取
        `_RENDER_EPS × 拐点 / 2`。
        """
        return self._RENDER_EPS * (self._KNEE_PER_PX * self.cell) * 0.5

    def active_box(self, pad=3, thr=None):
        """还有波的格范围 `(y0, x0, y1, x1)`；空场返回 None。

        `thr` 默认用 `_EPS`（判"活没活着"）；上屏要用 `_render_h()`，见那里的说明。
        """
        if thr is None:
            thr = self._EPS
        m = (np.abs(self.height) > thr) | (np.abs(self.vel) > thr)
        rows = np.flatnonzero(m.any(axis=1))
        if rows.size == 0:
            return None
        cols = np.flatnonzero(m.any(axis=0))
        if cols.size == 0:
            return None
        return (max(0, int(rows[0]) - pad), max(0, int(cols[0]) - pad),
                min(self.gh, int(rows[-1]) + 1 + pad),
                min(self.gw, int(cols[-1]) + 1 + pad))

    def energy(self):
        """场上总能量（h² + g²）。测试与调参靠它。"""
        return float((self.height.astype(np.float64) ** 2).sum()
                     + (self.vel.astype(np.float64) ** 2).sum())


# ------------------------------------------------------------------ 闪电
class Lightning:
    """随机闪电（**已停用**：用户要求去掉这个功能）。

    类与 `_verify_smoke` / `_verify_stability` 里针对它的测试都保留，但不再接进
    Scene / DEFAULTS / fx_ui —— 与 `CursorTrailField` 同样的处理方式：
    功能下线但代码不删，将来想恢复只需重新接线。
    """

    def __init__(self, seed=11):
        self.rng = np.random.default_rng(seed)
        self.next_in = self.rng.uniform(6.0, 20.0)
        self.flash = []          # [(剩余时间, 强度)]
        self.level = 0.0

    def update(self, dt, enabled, w, h):
        if not enabled:
            self.flash.clear()
            self.level = 0.0
            return 0.0
        self.next_in -= dt
        if self.next_in <= 0:
            self.next_in = self.rng.uniform(7.0, 26.0)
            base = self.rng.uniform(0.10, 0.26)
            self.flash = [(0.09, base), (0.05, 0.0), (0.13, base * 0.75), (0.30, 0.0)]
        level = 0.0
        while self.flash:
            self.flash[0] = (self.flash[0][0] - dt, self.flash[0][1])
            if self.flash[0][0] <= 0:
                self.flash.pop(0)
                continue
            level = self.flash[0][1]
            break
        self.level = level
        return level

    def draw(self, u32, w, h):
        lv = self.level
        if lv <= 0.004:
            return
        a = np.uint32(min(255, int(lv * 255)))
        packed = _premul_pack(np.uint32(215), np.uint32(232), np.uint32(255), a)
        u32[:] = packed


# ------------------------------------------------------------------ 鼠标
class MouseProbe:
    VK_LBUTTON = 0x01
    VK_RBUTTON = 0x02

    def __init__(self):
        self.pt = wt.POINT()
        self.x = 0
        self.y = 0
        self.prev_x = 0
        self.prev_y = 0
        self._lb = False
        self._rb = False
        self.acc = 0.0
        self.clicked = 0        # 1=左键 2=右键
        self.speed = 0.0
        self.mx = self.x
        self.my = self.y
        # 关键：先把 x/y 设为当前光标位置，再 _probe 让 prev=x，
        # 避免首帧算出"从 (0,0) 到光标"的幽灵位移 → 凭空炸涟漪。
        user32.GetCursorPos(ctypes.byref(self.pt))
        self.x = self.prev_x = int(self.pt.x)
        self.y = self.prev_y = int(self.pt.y)
        self.mx, self.my = self.x, self.y

    def _probe(self):
        user32.GetCursorPos(ctypes.byref(self.pt))
        self.prev_x, self.prev_y = self.x, self.y
        self.x, self.y = int(self.pt.x), int(self.pt.y)

    def update(self, dt, ov_x, ov_y):
        """折算到覆盖层坐标系（虚拟屏幕左上角为原点）。"""
        self._probe()
        dx = self.x - self.prev_x
        dy = self.y - self.prev_y
        dist = (dx * dx + dy * dy) ** 0.5
        self.speed = dist / max(dt, 1e-4)

        lb = bool(user32.GetAsyncKeyState(self.VK_LBUTTON) & 0x8000)
        rb = bool(user32.GetAsyncKeyState(self.VK_RBUTTON) & 0x8000)
        self.clicked = 0
        if lb and not self._lb:
            self.clicked = 1
        elif rb and not self._rb:
            self.clicked = 2
        self._lb, self._rb = lb, rb

        self.mx = self.x - ov_x
        self.my = self.y - ov_y
        self.acc += dist
        return dist


# ------------------------------------------------------------------ 窗口探针
# 覆盖层自身的窗口类名，枚举时跳过，避免对自己的矩形触发水波
_OVERLAY_CLASS = "DesktopFXOverlayCls"


class WindowProbe:
    """枚举桌面窗口并跨帧跟踪，输出"窗口动了"的事件流。

    两种节奏
    --------
    * **每帧**：只对**热点窗口**（最近动过的那几个 + 当前前台窗口）各读一次
      `GetWindowRect`。一次内核调用几微秒，所以拖动 / 缩放中的窗口能拿到逐帧
      精确的矩形；
    * **10Hz**：全量 `EnumWindows`，负责发现"谁开始动了"，以及最小化 / 恢复 /
      出现 / 消失。全量枚举要几百微秒，不能每帧做。

    为什么非要逐帧：事件只报"窗口现在在哪"，调用方还得靠**位移 ÷ 时间**算出
    窗口的速度，而水面被推开的幅度正比于这个速度（见 `WaterRippleField.push_rect`）。
    只按 10Hz 采样的话，拖动中的窗口每帧差 100 像素，速度既测不准、注入又只在
    每 6 帧来一串 —— 看着就是一顿一顿的。

    事件（rect 都已折算到覆盖层坐标系）
    ----------------------------------
        ("change",   旧矩形, 新矩形, 经过的时间)   移动 / 缩放，逐帧
        ("minimize", 最小化前的矩形, 经过的时间)
        ("restore",  恢复后的矩形,   经过的时间)

    矩形**变了**就报 change，不区分"平移"还是"改尺寸"：调用方拿到前后两条边
    就能算出四条边各自的速度，平移与缩放本来就是同一件事（见 push_rect）。

    新建窗口（各种弹窗、气泡、提示框）**刻意不报** —— 桌面上冒一个气泡就掀
    一片浪只会吵闹。

    仅在 Windows 下真正工作（依赖 user32.EnumWindows）；非 Windows 环境
    update 安全返回空列表，不影响其它特效。
    """

    _HOT_TTL = 0.7          # 窗口动过之后，保持"逐帧跟踪"多久（秒）

    def __init__(self, throttle=0.1):
        self._throttle = float(throttle)
        self._acc = 1.0                      # 首次立即扫一次
        self.windows = []                    # 可见窗口矩形（覆盖层坐标系）
        self.active = None                   # 活动窗口矩形 或 None
        self.ov_x = 0
        self.ov_y = 0
        self._prev = {}                      # hwnd -> ((x0,y0,x1,y1), minimized)
        self._hot = {}                       # hwnd -> 还要逐帧跟踪多少秒
        self._vs_w = 0
        self._vs_h = 0
        if _WIN32_OK:
            try:
                self._vs_w = int(user32.GetSystemMetrics(78))   # SM_CXVIRTUALSCREEN
                self._vs_h = int(user32.GetSystemMetrics(79))   # SM_CYVIRTUALSCREEN
            except Exception:
                self._vs_w = self._vs_h = 0

    def set_origin(self, x, y):
        self.ov_x, self.ov_y = x, y

    def _enumerate(self):
        """返回 {hwnd: ((x0,y0,x1,y1), minimized)}（虚拟屏幕坐标）。"""
        result = {}
        if not _WIN32_OK:
            return result
        rect = wt.RECT()
        buf = ctypes.create_unicode_buffer(256)

        def cb(hwnd, lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            if not _GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            x0, y0, x1, y1 = rect.left, rect.top, rect.right, rect.bottom
            w = x1 - x0
            h = y1 - y0
            if w <= 0 or h <= 0:
                return True
            _GetClassNameW(hwnd, buf, 256)
            cls = buf.value
            if cls in ("Progman", "WorkerW", "Shell_TrayWnd", _OVERLAY_CLASS):
                return True
            # 整个虚拟屏幕的矩形（覆盖层 / 真实桌面）跳过
            if (self._vs_w and self._vs_h and x0 <= 0 and y0 <= 0
                    and w >= self._vs_w - 4 and h >= self._vs_h - 4):
                return True
            minimized = bool(_IsIconic(hwnd))
            # hwnd 的 restype 是 c_void_p，NULL 会被转成 None，直接 int() 会抛
            result[int(hwnd) if hwnd else 0] = ((x0, y0, x1, y1), minimized)
            return True

        try:
            _EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
        except Exception:
            return {}
        return result

    def _diff(self, prev, cur, dt):
        """纯函数：对比两批快照，输出事件（rect 已折算到覆盖层坐标系）。

        四条边的位移之和 ≤ 1 像素的变化不算（有些窗口会自己抖 1 像素；那点速度
        只会往水里注入约 0.01 的位移，却会让"水面一直活着"、鼠标层每帧白上屏）。
        """
        events = []
        for hwnd, (rect, minimized) in cur.items():
            p = prev.get(hwnd)
            if p is None:
                continue                        # 新建窗口：不报（弹窗会刷屏）
            prect, pmin = p
            if minimized and not pmin:
                events.append(("minimize", self._to_local(prect), dt))
            elif not minimized and pmin:
                events.append(("restore", self._to_local(rect), dt))
            elif not minimized and not pmin and (
                    abs(rect[0] - prect[0]) + abs(rect[1] - prect[1])
                    + abs(rect[2] - prect[2]) + abs(rect[3] - prect[3])) > 1:
                events.append(("change", self._to_local(prect),
                               self._to_local(rect), dt))
        return events

    def reset(self):
        """清掉全部跟踪状态。

        特效关掉时调用：否则重新打开后，第一次全量扫描会把"上次关掉之前的位置"
        当成参照，算出一个跨越整个停用期的巨大位移（速度 → 直接顶到上限），
        无端炸出一片浪。
        """
        self._prev = {}
        self._hot = {}
        self._acc = 1.0

    def update(self, dt):
        """每帧调用，返回本帧的窗口事件列表。"""
        dt = float(dt)
        if not _WIN32_OK:
            return []
        events = self._track(dt)               # 先逐帧精测，再全量扫描
        self._acc += dt
        if self._acc >= self._throttle:
            scan_dt, self._acc = self._acc, 0.0
            try:
                cur = self._enumerate()
            except Exception:
                cur = {}
            if cur:
                events.extend(self._scan(cur, scan_dt))
        return events

    def _track(self, dt):
        """逐帧精测热点窗口。只读那几个 hwnd，成本与窗口总数无关。"""
        if not self._hot:
            return []
        out = []
        rect = wt.RECT()
        for hwnd in list(self._hot):
            ttl = self._hot[hwnd] - dt
            if ttl <= 0.0:
                del self._hot[hwnd]
                continue
            self._hot[hwnd] = ttl
            if not _GetWindowRect(hwnd, ctypes.byref(rect)):
                del self._hot[hwnd]
                continue
            if _IsIconic(hwnd):
                continue                        # 最小化 / 恢复交给全量扫描
            now = (rect.left, rect.top, rect.right, rect.bottom)
            p = self._prev.get(hwnd)
            if p is None or p[1] or p[0] == now:
                continue
            out.append(("change", self._to_local(p[0]), self._to_local(now), dt))
            self._prev[hwnd] = (now, False)
            self._hot[hwnd] = self._HOT_TTL     # 还在动，继续跟
        return out

    def _scan(self, cur, dt):
        """10Hz 全量扫描：找出新动过的窗口、最小化 / 恢复，并刷新热点集合。"""
        events = self._diff(self._prev, cur, dt)
        for hwnd, (rect, mini) in cur.items():
            p = self._prev.get(hwnd)
            if p is not None and not mini and rect != p[0]:
                self._hot[hwnd] = self._HOT_TTL     # 刚发现它在动 -> 转逐帧
        self._prev = cur
        for h in [k for k in self._hot if k not in cur]:
            del self._hot[h]
        # 更新可见窗口列表与活动窗口（覆盖层坐标系）
        self.windows = [self._to_local(r) for (r, m) in cur.values() if not m]
        # GetForegroundWindow() 在"焦点在桌面"或切换窗口的瞬间会返回 NULL，
        # 而 restype 是 c_void_p —— ctypes 会把 NULL 变成 None。直接 int(None)
        # 会抛 TypeError 并每帧打穿渲染循环（见 error.log 的 render: 条目）。
        fg = _GetForegroundWindow() if _WIN32_OK else None
        fg = int(fg) if fg else None
        act = cur.get(fg)
        self.active = self._to_local(act[0]) if (act and not act[1]) else None
        if act and not act[1]:
            # 前台窗口恒为热点：用户刚开始拖的那一刻不必等下一次全量扫描，
            # 否则起步的 0.1 秒是"死"的 —— 人已经在拖，水面还没反应。
            self._hot[fg] = self._HOT_TTL
        return events

    def _to_local(self, rect):
        x0, y0, x1, y1 = rect
        return (x0 - self.ov_x, y0 - self.ov_y, x1 - self.ov_x, y1 - self.ov_y)


# ------------------------------------------------------------------ 雪
class SnowField:
    """飘雪：近垂直下落 + 轻微左右摆动，绘制为柔边圆点（非雨丝）。

    与 RainField 保持同一套接口（set_amount / set_color / update / draw /
    resize），scene 层可以像对待雨一样对待它。雪花不做落地溅水，
    update 永远返回空数组。所有运算都是 numpy 向量化，且对 alpha 做裁剪，
    绝不会产生 NaN / Inf（_verify_snow 会专门盯这个）。
    """

    K = 5                        # 基准柔边圆点半径（核边长 2K+1，中档 + size=1.0）
    K_MAX = 20                   # 核半径上限：snow_size 拉满时不至于糊成一团
    # 深度分档（depth 越大 = 越近：更快、更亮、也该更大）与对应的核半径倍率。
    # 分档而非连续是为了让 draw 保持向量化的批量盖章：每档一次，共三次。
    TIER_EDGES = (0.75, 1.15)
    TIER_SCALES = (0.55, 1.0, 1.55)

    KEEP = 0.012                 # 权重阈值：低于它的格子直接不盖章
    # --- 辉光（廉价 bloom）---
    # 只给"近档 + 偏亮"的粒子加一层大半径低 alpha 光晕。这不是省事，而是
    # 真实 bloom 的做法：只有超过亮度阈值的像素才溢出光晕。远档又小又暗，
    # 给它加光晕纯属浪费像素；近档也只有 vary>=1 的一半粒子发光，成本可控。
    GLOW_TIER = 2                # 参与辉光的档位（0=远 1=中 2=近）
    GLOW_VARY = 1.0              # 参与辉光的亮度门槛（vary 越大越亮）
    GLOW_SCALE = 1.8             # 光晕半径上限 = 实心核半径 × 该倍率（glow=1 时）
    GLOW_SCALE_MIN = 1.45        # 光晕半径下限倍率（glow→0+ 时），保证还能看见
    GLOW_POWER = 1.25            # 光晕权重衰减指数（越小越"广"，外圈越亮）
    GLOW_GAIN = 0.60             # 光晕亮度增益：a_halo = a × snow_glow × 该值

    def __init__(self, w, h, seed=9):
        self.rng = np.random.default_rng(seed)
        self.w, self.h = w, h
        self._t = 0.0
        # 柔边圆点核按半径缓存（1D，长度 2K+1）：近大远小需要多种半径，
        # 而 snow_size 可变，所以按需生成而不是构造时固定一套。
        self._kern = {}
        self._rgb = (235.0, 240.0, 255.0)   # 默认淡蓝白
        self.n = 0
        self.x = np.zeros(0, np.float32)
        self.y = np.zeros(0, np.float32)
        self.depth = np.zeros(0, np.float32)
        self.vary = np.zeros(0, np.float32)
        self.phase = np.zeros(0, np.float32)
        self.sway_amp = np.zeros(0, np.float32)
        self.sway_freq = np.zeros(0, np.float32)
        # 亮度脉动：每个粒子有独立的频率与相位，整片雪才不会"一起呼吸"
        self.twk_freq = np.zeros(0, np.float32)
        self.twk_phase = np.zeros(0, np.float32)
        self._speed = np.zeros(0, np.float32)
        self.set_amount(240, w, h)

    def resize(self, w, h):
        self.w, self.h = w, h

    def _kernel(self, k_rad, power=1.5):
        """取（并缓存）真·圆形柔边核，返回三个等长展平数组 (dx, dy, 权重)。

        权重用**径向距离** `clip(1 - r/(K+0.5), 0, 1)^power`，等值线是圆，雪花
        边缘才圆润；若用可分离积 `ker(dx)*ker(dy)` 等值线偏菱形、近方形。
        power 越大衰减越软：实心核 1.5，辉光光晕 GLOW_POWER。
        权重 <= KEEP 的格子预先裁掉，省下盖章时的无效像素。
        """
        key = (k_rad, power)
        got = self._kern.get(key)
        if got is not None:
            return got
        k = np.arange(-k_rad, k_rad + 1, dtype=np.int32)
        m = k.size
        dxs = np.tile(k, m)             # 快变：x 偏移
        dys = np.repeat(k, m)           # 慢变：y 偏移
        rr = np.sqrt(dxs.astype(np.float32) ** 2 + dys.astype(np.float32) ** 2)
        w = np.clip(1.0 - rr / (k_rad + 0.5), 0.0, 1.0).astype(np.float32) ** power
        keep = w > self.KEEP
        got = (dxs[keep], dys[keep], w[keep])
        self._kern[key] = got
        return got

    def _kernel_glow(self, k_rad, kr_core, amp):
        """辉光复合核：权重 = max(实心核, amp × 光晕核)。

        为什么不"多画一遍光晕"：stamp 是覆盖写，没有混合。光晕先画会被随后
        的实心核覆盖掉一圈（核心边缘留一道**暗环**）；光晕后画又会把核心抹暗。
        改成在权重上取 max（两项同乘该粒子的 alpha，所以可以直接比权重），
        每个像素只写一次、径向亮度单调递减，天生没有这两种伪影，也没有额外
        的一遍开销。
        """
        key = ("g", k_rad, kr_core, round(float(amp), 4))
        got = self._kern.get(key)
        if got is not None:
            return got
        k = np.arange(-k_rad, k_rad + 1, dtype=np.int32)
        m = k.size
        dxs = np.tile(k, m)
        dys = np.repeat(k, m)
        rr = np.sqrt(dxs.astype(np.float32) ** 2 + dys.astype(np.float32) ** 2)
        core = np.clip(1.0 - rr / (kr_core + 0.5), 0.0, 1.0).astype(np.float32) ** 1.5
        halo = np.clip(1.0 - rr / (k_rad + 0.5), 0.0, 1.0).astype(np.float32) ** self.GLOW_POWER
        w = np.maximum(core, halo * np.float32(amp))
        keep = w > self.KEEP
        got = (dxs[keep], dys[keep], w[keep])
        self._kern[key] = got
        return got

    def _init_arrays(self, n, start=0, end=None):
        if end is None:
            end = n
        cnt = end - start
        if cnt <= 0:
            return
        rng = self.rng
        self.x[start:end] = rng.random(cnt, dtype=np.float32) * self.w
        self.y[start:end] = rng.random(cnt, dtype=np.float32) * self.h
        self.depth[start:end] = rng.uniform(0.35, 1.5, cnt).astype(np.float32)
        self.vary[start:end] = rng.uniform(0.85, 1.15, cnt).astype(np.float32)
        self.phase[start:end] = rng.uniform(0.0, TAU, cnt).astype(np.float32)
        self.sway_amp[start:end] = rng.uniform(8.0, 26.0, cnt).astype(np.float32)
        self.sway_freq[start:end] = rng.uniform(0.5, 1.4, cnt).astype(np.float32)
        self.twk_freq[start:end] = rng.uniform(0.45, 1.8, cnt).astype(np.float32)
        self.twk_phase[start:end] = rng.uniform(0.0, TAU, cnt).astype(np.float32)

    def set_amount(self, n, w=None, h=None):
        if w:
            self.w = w
        if h:
            self.h = h
        n = int(max(0, min(1500, n)))
        if n == self.n:
            return
        if n > self.n:
            # 所有逐粒子数组一起增长，保留旧数据、初始化新增部分
            for name in ("x", "y", "depth", "vary", "phase", "sway_amp",
                         "sway_freq", "twk_freq", "twk_phase"):
                old = getattr(self, name)
                new = np.zeros(n, np.float32)
                new[:len(old)] = old
                setattr(self, name, new)
            self._init_arrays(n, start=self.n)
        else:
            for name in ("x", "y", "depth", "vary", "phase", "sway_amp",
                         "sway_freq", "twk_freq", "twk_phase"):
                setattr(self, name, getattr(self, name)[:n].copy())
        self.n = n

    def update(self, dt, cfg):
        """推进雪花，返回空数组（雪不溅水）。"""
        if self.n == 0:
            return np.zeros(0, np.float32)

        self._t += dt
        speed = np.float32(cfg["snow_speed"]) * self.depth * self.vary
        # 风速与「横向漂移」是两个独立旋钮：
        #   snow_drift 按 depth 放大 —— 近处的斜得更狠（透视倾斜）；
        #   snow_wind  整场同向的均匀气流，并叠加两个慢正弦当阵风，
        #              风忽大忽小，而不是一条笔直的斜线。
        wind = float(cfg.get("snow_wind", 0.0))
        if wind:
            t = self._t
            gust = (0.65 + 0.35 * np.sin(TAU * 0.07 * t)
                    + 0.12 * np.sin(TAU * 0.23 * t + 1.7))
            wind = np.float32(wind * gust)
        windx = np.float32(cfg.get("snow_drift", 0.0) * 60.0) * self.depth
        # 横向摆动：把 sin 当作时变速度叠加，制造左右轻飘
        sway = self.sway_amp * np.sin(self._t * self.sway_freq + self.phase)

        self.y += speed * dt
        self.x += (windx + wind + sway) * dt

        # 落到底边 -> 回到顶部重置
        hit = self.y >= self.h
        if hit.any():
            k = int(hit.sum())
            self.y[hit] = -self.rng.random(k, dtype=np.float32) * self.h * 0.25
            self.x[hit] = self.rng.random(k, dtype=np.float32) * self.w
            self.depth[hit] = self.rng.uniform(0.35, 1.5, k).astype(np.float32)
            self.vary[hit] = self.rng.uniform(0.85, 1.15, k).astype(np.float32)
            self.phase[hit] = self.rng.uniform(0.0, TAU, k).astype(np.float32)
            self.sway_amp[hit] = self.rng.uniform(8.0, 26.0, k).astype(np.float32)
            self.sway_freq[hit] = self.rng.uniform(0.5, 1.4, k).astype(np.float32)
            self.twk_freq[hit] = self.rng.uniform(0.45, 1.8, k).astype(np.float32)
            self.twk_phase[hit] = self.rng.uniform(0.0, TAU, k).astype(np.float32)

        # 左右出界 -> 环绕
        span = self.w + 120.0
        self.x = np.where(self.x < -60.0, self.x + span, self.x)
        self.x = np.where(self.x > self.w + 60.0, self.x - span, self.x)

        self._speed = speed
        return np.zeros(0, np.float32)

    def _stamp(self, u32, w, h, sel, ix0, iy0, kdx, kdy, kw, alpha, tint):
        """把选中的粒子按 (dx, dy, 权重) 足迹盖到 u32 上（覆盖写）。

        tint = (colr, colg, colb)。af 先按 KEEP 裁一遍再算索引，越界再裁一遍，
        两轮过滤都能省掉大量下标运算 —— draw 是全项目最热的路径。
        """
        a = alpha[sel]
        af = (a[:, None] * kw[None, :]).reshape(-1)
        m = af > self.KEEP
        if not m.any():
            return
        af = af[m]
        wx = (ix0[sel, None] + kdx[None, :]).reshape(-1)[m]
        wy = (iy0[sel, None] + kdy[None, :]).reshape(-1)[m]
        m = (wx >= 0) & (wx < w) & (wy >= 0) & (wy < h)
        if not m.any():
            return
        wx, wy, af = wx[m], wy[m], af[m]
        colr, colg, colb = tint
        a_c = np.rint(af * 255.0).astype(np.uint32)
        packed = _premul_pack(
            np.rint(colb * af).astype(np.uint32),
            np.rint(colg * af).astype(np.uint32),
            np.rint(colr * af).astype(np.uint32), a_c)
        u32[wy * np.int32(w) + wx] = packed

    def draw(self, u32, w, h, cfg):
        if self.n == 0:
            return
        base = float(cfg.get("snow_size", 1.0))
        glow = float(cfg.get("snow_glow", 0.0))
        twk = float(cfg.get("snow_twinkle", 0.0))
        colr, colg, colb = self._rgb
        # alpha 必须裁到 1.0：depth(<=1.5) × vary(<=1.15) × opacity 可达 1.55，
        # 打包时左移 24 位会溢出回绕，导致最亮的雪花中心反而变暗甚至全透明。
        alpha = self.depth * self.vary * np.float32(cfg["snow_opacity"] * 0.9)
        if twk > 0.0:
            # 亮度脉动只做"变暗"不做"变亮"：峰值亮度不变，所以不会把本就接近
            # 饱和的近雪推过 1.0 被裁平 —— 那样脉动会在一半周期里彻底消失。
            ph = TAU * self.twk_freq * np.float32(self._t) + self.twk_phase
            alpha = alpha * (1.0 - twk * (0.5 - 0.5 * np.sin(ph)))
        np.clip(alpha, 0.0, 1.0, out=alpha)
        ix0 = np.rint(self.x).astype(np.int32)
        iy0 = np.rint(self.y).astype(np.int32)

        # 近大远小：按 depth 分三档给不同核半径（透视感），再按「远 -> 近」
        # 依次盖章 —— stamp 是覆盖写，后画的近雪压住先画的远雪，等价画家算法。
        lv = np.digitize(self.depth, self.TIER_EDGES)
        krad = [int(min(self.K_MAX, max(1, round(self.K * ks * base))))
                for ks in self.TIER_SCALES]
        tint = (colr, colg, colb)

        for tier in range(len(self.TIER_SCALES)):
            sel = lv == tier
            if not sel.any():
                continue
            if glow > 0.0 and tier == self.GLOW_TIER:
                # 近档拆成"发光的一半 / 不发光的一半"：偏亮的粒子用复合核
                # （实心核 ∪ 光晕），其余照旧。等价于真实 bloom 的亮度阈值。
                # 光晕半径也随辉光强度增长：低辉光只付小光晕的钱，拉满才铺开
                # 最大的一圈（成本随滑条单调，不白花）。
                kr = int(min(self.K_MAX * 2, max(2, round(
                    krad[tier] * (self.GLOW_SCALE_MIN
                                  + (self.GLOW_SCALE - self.GLOW_SCALE_MIN)
                                  * glow)))))
                core_k = self._kernel(krad[tier])
                glow_k = self._kernel_glow(kr, krad[tier], glow * self.GLOW_GAIN)
                hot = self.vary >= self.GLOW_VARY
                for sub, (kdx, kdy, kw) in ((sel & ~hot, core_k),
                                            (sel & hot, glow_k)):
                    if sub.any():
                        self._stamp(u32, w, h, sub, ix0, iy0,
                                    kdx, kdy, kw, alpha, tint)
                continue
            kdx, kdy, kw = self._kernel(krad[tier])
            self._stamp(u32, w, h, sel, ix0, iy0, kdx, kdy, kw, alpha, tint)

    def set_color(self, hex_str):
        bgr = hex_to_bgr(hex_str)
        self._rgb = (float(bgr[2]), float(bgr[1]), float(bgr[0]))


# ------------------------------------------------------------------ 落樱
class PetalField:
    """落樱：樱花瓣边翻面边自旋地飘落。

    形状与观感取自 WebGL Sakura（Wallpaper Engine 888689688）：
      * 花瓣 = 椭圆把 |x| 与 y 再转 15° 后、按 x 正负取**不同横向半径**
        —— 左右不对称就把椭圆切成"顶端带凹口"的花瓣，而不是一颗蛋
      * 一个端点偏饱和粉、另一端接近白（参考里由 grady 调制 g/b 通道），
        中脉再压深一点；翻到侧面时漫反射+高光让整片泛白一闪
      * 三轴连续旋转 + 透视投影（近大远小）

    在 CPU 逐像素 overlay 上的取舍：不可能每帧给每个粒子重采样一张旋转过的
    精灵图，所以把「形状」预烘成**核表**，档位 = 自旋角(ANGLE_N) × 翻面(6)。
    关键收益：档位只影响**建表成本**，不影响**每帧成本** —— 每帧只做一次
    "按档取核 + 批量盖章"。所以档位可以给得比较细来压制转角跳变。
    核表按半径缓存，`petal_flutter` / `petal_spin` 都不触发重建。

    与 SnowField 同一套接口（set_amount / set_color / resize / update / draw），
    scene 层可以像对待雪一样对待它。
    """

    K = 6                        # 花瓣半长基准（中档 + size=1.0）
    K_MAX = 16                   # 半长上限：petal_size 拉满时不至于糊成一片
    TIER_EDGES = (0.75, 1.15)    # 深度分档（与雪同构：越近更大更亮）
    TIER_SCALES = (0.5, 1.0, 1.65)
    ANGLE_N = 32                 # 自旋角档位（11.25°/档）
    # 翻面档位：|cos| 量化后的"可见宽度"。刻意固定成常数，这样核表能按半径
    # 缓存 —— petal_flutter 只改变落在各档的概率，不需要重建表。
    SQ_LEVELS = (0.26, 0.44, 0.63, 0.80, 0.92, 1.0)
    KEEP = 0.012                 # 权重阈值：低于它的格子不盖章

    # 参考 shader 里的形状常数（原样搬过来，未做任何"艺术加工"）
    _SN = 0.28819045102521       # sin 15°
    _CS = 0.965925826289068      # cos 15°
    _OX, _OY = 0.0325, 0.012     # 椭圆中心
    _RX_L, _RX_R = 0.18, 0.29    # 左半 / 右半横向半径（不等 -> 凹口）
    _RY = 0.48

    _ARR = ("x", "y", "depth", "vary", "phase", "sway_amp", "sway_freq",
            "rot", "spin", "tumble", "tumble_freq")

    def __init__(self, w, h, seed=23):
        self.rng = np.random.default_rng(seed)
        self.w, self.h = w, h
        self._t = 0.0
        self._kt = {}                       # r_px -> (kdx, kdy, kw, kmix)
        self._maxr = None                   # 形状外接半径缓存
        # 翻面档位分界（档位中点）。类体里不能写推导式引用类变量，
        # 所以在 __init__ 里算。
        self._BND = np.array(
            [(self.SQ_LEVELS[i] + self.SQ_LEVELS[i + 1]) * 0.5
             for i in range(len(self.SQ_LEVELS) - 1)], dtype=np.float32)
        self._body = (255.0, 157.0, 182.0)   # 花瓣主体：饱和粉
        self._edge = (255.0, 240.0, 244.0)   # 花瓣边缘：近白
        self.n = 0
        for name in self._ARR:
            setattr(self, name, np.zeros(0, np.float32))
        self.set_amount(160, w, h)

    def resize(self, w, h):
        self.w, self.h = w, h

    # ------------------------------------------------------------ 核表
    def _shape_dist(self, u, v):
        """花瓣的"半径函数"：< 1 在花瓣内。u/v 是花瓣自身坐标。

        原样搬参考 shader 的做法：先把 (|u|, v) 再转 15°，再按 fx 的正负取
        **不同**的横向半径（左 0.18 / 右 0.29）。左右不对称正是"顶端凹口"的来源
        —— 少了这一步就只是个椭圆，看着像蛋不像花瓣。
        """
        ax = np.abs(u)
        # GLSL 的 mat2(a,b,c,d) 是**列主序**，`v * M` 展开是
        #   fx = cs*ax - sn*v ,  fy = sn*ax + cs*v
        # 我最初按行向量矩阵写成了 (cs*ax + sn*v, -sn*ax + cs*v) —— 相当于转了
        # -15°，凹口因此落到"饱和端"（参考里凹口在**苍白端**）：
        # 缝线 fx=0 只在 v>0 侧可达，那一侧用的是窄椭圆 rx=0.18，于是花瓣在
        # 苍白端被掐出一道凹口，饱和端则是饱满圆头。
        fx = np.float32(self._CS) * ax - np.float32(self._SN) * v
        fy = np.float32(self._SN) * ax + np.float32(self._CS) * v
        rx = np.where(fx < 0.0, np.float32(self._RX_L), np.float32(self._RX_R))
        return np.sqrt(((fx - self._OX) / rx) ** 2
                       + ((fy - self._OY) / self._RY) ** 2)

    def _max_radius(self):
        """花瓣形状离原点的最远距离（花瓣自身单位）。

        自旋角任意，所以 sprite 网格必须能装下"转一圈"的花瓣 —— 用形状的外接圆
        半径定网格，而不是拍脑袋写 [-1,1]²：后者有 3/4 的格子永远算不到花瓣，
        白白让建表成本翻两番。
        """
        if self._maxr is not None:
            return self._maxr
        n = 512
        lin = np.linspace(-1.0, 1.0, n, dtype=np.float32)
        uu, vv = np.meshgrid(lin, lin, indexing="ij")
        inside = self._shape_dist(uu, vv) < 1.0
        rr = np.sqrt(uu ** 2 + vv ** 2)
        self._maxr = float(rr[inside].max()) if inside.any() else 0.6
        return self._maxr

    def _ktab(self, r_px):
        """烘一张核表 (kdx, kdy, kw, kmix)：每档一行，全长 padding 到最宽档。

        r_px = 花瓣**半长**（像素）。参考 shader 里花瓣半长为 0.48（形状单位），
        所以 1 形状单位 = r_px / 0.48 像素。

        padding 出来的格子权重为 0，盖章时被 KEEP 过滤 —— 这是"变长核批量化"
        的关键：只有长度统一，才能把 n 个粒子×各自档位 一次性铺成 (n, L)
        做全向量化盖章，既不用在 Python 里按档分组，也不用逐粒子循环。
        """
        got = self._kt.get(r_px)
        if got is not None:
            return got
        R = int(max(1, r_px))
        scale = np.float32(0.48 / R)                 # 形状单位 -> 像素
        g = int(np.ceil(self._max_radius() / float(scale)))
        k = np.arange(-g, g + 1, dtype=np.float32)
        m = int(k.size)
        dxs = np.tile(k, m)                          # 快变：x 偏移
        dys = np.repeat(k, m)                        # 慢变：y 偏移
        cx = (dxs * scale)[None, :]                  # (1, N) 形状单位
        cy = (dys * scale)[None, :]

        ang = (np.arange(self.ANGLE_N, dtype=np.float32)
               * np.float32(TAU / self.ANGLE_N))[:, None]
        ca, sa = np.cos(ang), np.sin(ang)
        # 自旋：把屏幕偏移转进花瓣自身坐标（与 shader 的 PointCoord 同为 y 向下）
        u = ca * cx + sa * cy                        # (A, N) 花瓣横向
        v = -sa * cx + ca * cy                       # (A, N) 花瓣长轴
        sq = np.array(self.SQ_LEVELS, dtype=np.float32)[None, :, None]
        uu = u[:, None, :]                           # (A, S, N)
        vv = v[:, None, :] / sq                      # 翻面 = 沿长轴压扁

        wgt = np.clip(1.0 - self._shape_dist(uu, vv), 0.0, 1.0) ** 0.45

        # 混色权重 pale：0 = 主体（饱和粉），1 = 边缘（近白）。
        # 参考 shader 里 grady / 叶脉都用**花瓣自身坐标**（coord.x / coord.y），
        # 不是屏幕坐标 —— 所以这里用 u/v，否则花瓣一转渐变就跟着屏幕转，露馅。
        pale = np.clip(v * 0.5 + 0.5, 0.0, 1.0) ** 0.35
        pale = pale[:, None, :] * (1.0 - 0.35 * np.exp(-(uu / 0.22) ** 2))
        # 翻到侧面时泛白一闪（参考里 diffuse + specular 的效果）
        pale = np.clip(pale + (1.0 - sq) * 0.35, 0.0, 1.0)

        V = int(self.ANGLE_N) * len(self.SQ_LEVELS)
        w2 = np.ascontiguousarray(wgt).reshape(V, m * m)
        p2 = np.ascontiguousarray(pale).reshape(V, m * m)
        keep = w2 > self.KEEP
        cnt = keep.sum(axis=1)
        L = int(cnt.max()) if cnt.size else 0
        rows = np.arange(V)[:, None]
        if L <= 0:
            z = np.zeros((V, 0), np.int32)
            tab = (z, z.copy(), np.zeros((V, 0), np.float32),
                   np.zeros((V, 0), np.float32))
            self._kt[r_px] = tab
            return tab
        # 稳定排序把保留格排到行首（False=保留），裁到 L 列；行内不足 L 的
        # 位置权重置 0，盖章时被 KEEP 过滤。
        order = np.argsort(~keep, axis=1, kind="stable")[:, :L]
        sel = keep[rows, order]
        tab = (dxs[order].astype(np.int32),
               dys[order].astype(np.int32),
               np.where(sel, w2[rows, order], 0.0).astype(np.float32),
               np.where(sel, p2[rows, order], 0.0).astype(np.float32))
        self._kt[r_px] = tab
        return tab

    # ------------------------------------------------------------ 粒子
    def _init_arrays(self, n, start=0, end=None):
        if end is None:
            end = n
        cnt = end - start
        if cnt <= 0:
            return
        rng = self.rng
        self.x[start:end] = rng.random(cnt, dtype=np.float32) * self.w
        self.y[start:end] = rng.random(cnt, dtype=np.float32) * self.h
        self.depth[start:end] = rng.uniform(0.35, 1.5, cnt).astype(np.float32)
        self.vary[start:end] = rng.uniform(0.80, 1.20, cnt).astype(np.float32)
        self.phase[start:end] = rng.uniform(0.0, TAU, cnt).astype(np.float32)
        self.sway_amp[start:end] = rng.uniform(10.0, 34.0, cnt).astype(np.float32)
        self.sway_freq[start:end] = rng.uniform(0.35, 1.10, cnt).astype(np.float32)
        self.rot[start:end] = rng.uniform(0.0, TAU, cnt).astype(np.float32)
        self.spin[start:end] = (rng.uniform(0.45, 1.50, cnt).astype(np.float32)
                                * np.where(rng.random(cnt) < 0.5, -1.0, 1.0))
        self.tumble[start:end] = rng.uniform(0.0, TAU, cnt).astype(np.float32)
        self.tumble_freq[start:end] = rng.uniform(0.5, 1.5, cnt).astype(np.float32)

    def set_amount(self, n, w=None, h=None):
        if w:
            self.w = w
        if h:
            self.h = h
        n = int(max(0, min(800, n)))
        if n == self.n:
            return
        if n > self.n:
            for name in self._ARR:
                old = getattr(self, name)
                new = np.zeros(n, np.float32)
                new[:len(old)] = old
                setattr(self, name, new)
            self._init_arrays(n, start=self.n)
        else:
            for name in self._ARR:
                setattr(self, name, getattr(self, name)[:n].copy())
        self.n = n

    def _respawn(self, hit):
        """落到底边 / 飘出侧边后重新投放（保持"永远有花瓣在飘"）。"""
        k = int(hit.sum())
        if k <= 0:
            return
        rng = self.rng
        self.y[hit] = -rng.random(k, dtype=np.float32) * self.h * 0.3
        self.x[hit] = rng.random(k, dtype=np.float32) * self.w
        self.depth[hit] = rng.uniform(0.35, 1.5, k).astype(np.float32)
        self.vary[hit] = rng.uniform(0.80, 1.20, k).astype(np.float32)
        self.phase[hit] = rng.uniform(0.0, TAU, k).astype(np.float32)
        self.sway_amp[hit] = rng.uniform(10.0, 34.0, k).astype(np.float32)
        self.sway_freq[hit] = rng.uniform(0.35, 1.10, k).astype(np.float32)
        self.rot[hit] = rng.uniform(0.0, TAU, k).astype(np.float32)
        self.spin[hit] = (rng.uniform(0.45, 1.50, k).astype(np.float32)
                          * np.where(rng.random(k) < 0.5, -1.0, 1.0))
        self.tumble[hit] = rng.uniform(0.0, TAU, k).astype(np.float32)
        self.tumble_freq[hit] = rng.uniform(0.5, 1.5, k).astype(np.float32)

    def update(self, dt, cfg):
        """推进花瓣，返回空数组（落樱不溅水）。"""
        if self.n == 0:
            return np.zeros(0, np.float32)
        self._t += dt
        speed = np.float32(cfg["petal_speed"]) * self.depth * self.vary
        # 与雪同构：petal_drift 按 depth 放大（透视倾斜），petal_wind 是整场
        # 同向气流 + 双正弦阵风。两者独立。
        wind = float(cfg.get("petal_wind", 0.0))
        if wind:
            t = self._t
            gust = (0.65 + 0.35 * np.sin(TAU * 0.05 * t)
                    + 0.12 * np.sin(TAU * 0.19 * t + 1.7))
            wind = np.float32(wind * gust)
        windx = np.float32(cfg.get("petal_drift", 0.0) * 60.0) * self.depth
        # 花瓣比雪飘得"活"：左右摆幅更大、频率更低，像被风一下下托着走
        sway = self.sway_amp * np.sin(self._t * self.sway_freq + self.phase)

        self.y += speed * dt
        self.x += (windx + wind + sway) * dt

        spin = np.float32(cfg.get("petal_spin", 1.0))
        self.rot += self.spin * spin * np.float32(dt)
        self.rot %= TAU                       # 及时取模，避免长时间漂移掉精度
        self.tumble += self.tumble_freq * np.float32(dt)

        self._respawn(self.y >= self.h)
        span = self.w + 160.0
        self.x = np.where(self.x < -80.0, self.x + span, self.x)
        self.x = np.where(self.x > self.w + 80.0, self.x - span, self.x)
        return np.zeros(0, np.float32)

    # ------------------------------------------------------------ 绘制
    def _stamp(self, u32, w, h, sel, ix0, iy0, kdx, kdy, kw, kmix,
               alpha, body, edge):
        """批量盖章。kdx/kdy/kw/kmix 都是 (n, L)：n 个粒子各自的核已经按档取好。

        与雪的单核版本相比只多了一路 kmix（双色混）—— 因为花瓣有"一端饱和粉、
        一端近白"的渐变，单色 tint 会把它压成一张平涂色块。
        """
        a = alpha[sel]
        af = (a[:, None] * kw).reshape(-1)
        m = af > self.KEEP
        if not m.any():
            return
        wx = (ix0[sel, None] + kdx).reshape(-1)[m]
        wy = (iy0[sel, None] + kdy).reshape(-1)[m]
        af = af[m]
        mi = kmix.reshape(-1)[m]
        m = (wx >= 0) & (wx < w) & (wy >= 0) & (wy < h)
        if not m.any():
            return
        wx, wy, af, mi = wx[m], wy[m], af[m], mi[m]
        # 注意：body / edge 存的是 **(r, g, b)**，而 _premul_pack 收的是 (b, g, r)。
        # 这里曾经把解包顺序写成 (b, g, r)，红蓝直接互换 —— 花瓣渲染成蓝紫色，
        # 而"抽层差分""点亮面积"这类断言全都照样通过（颜色错了不等于没画上去）。
        # 是"花瓣贡献的像素必须偏暖 R>B"这条断言把它抓出来的。
        br0, bg0, bb0 = body
        br1, bg1, bb1 = edge
        cb = np.float32(bb0) + (np.float32(bb1) - np.float32(bb0)) * mi
        cg = np.float32(bg0) + (np.float32(bg1) - np.float32(bg0)) * mi
        cr = np.float32(br0) + (np.float32(br1) - np.float32(br0)) * mi
        a_c = np.rint(af * 255.0).astype(np.uint32)
        packed = _premul_pack(
            np.rint(cb * af).astype(np.uint32),
            np.rint(cg * af).astype(np.uint32),
            np.rint(cr * af).astype(np.uint32), a_c)
        u32[wy * np.int32(w) + wx] = packed

    def draw(self, u32, w, h, cfg):
        if self.n == 0:
            return
        base = float(cfg.get("petal_size", 1.0))
        flutter = float(np.clip(cfg.get("petal_flutter", 0.0), 0.0, 1.0))
        # alpha 必须 clip：depth(<=1.5) × vary(<=1.2) × opacity 可达 1.8，
        # 打包时左移 24 位会溢出回绕，最亮的花瓣反而变暗/消失（雪踩过这个坑）。
        alpha = self.depth * self.vary * np.float32(cfg["petal_opacity"])
        np.clip(alpha, 0.0, 1.0, out=alpha)
        ix0 = np.rint(self.x).astype(np.int32)
        iy0 = np.rint(self.y).astype(np.int32)

        lvl = np.digitize(self.depth, self.TIER_EDGES)
        krad = [int(min(self.K_MAX, max(1, round(self.K * ks * base))))
                for ks in self.TIER_SCALES]

        # 档位索引 = 自旋角档 × 翻面档
        ab = np.floor(self.rot * np.float32(self.ANGLE_N / TAU)
                      ).astype(np.int32) % self.ANGLE_N
        sqc = 1.0 - flutter * (1.0 - np.abs(np.cos(self.tumble)))
        sb = np.searchsorted(self._BND, sqc.astype(np.float32)).astype(np.int32)
        var = ab * len(self.SQ_LEVELS) + sb

        body, edge = self._body, self._edge
        # 远 -> 近 依次盖章：stamp 是覆盖写，后画的近花瓣压住远的（画家算法）
        for tier in range(len(self.TIER_SCALES)):
            sel = lvl == tier
            if not sel.any():
                continue
            kdx, kdy, kw, kmix = self._ktab(krad[tier])
            v = var[sel]
            self._stamp(u32, w, h, sel, ix0, iy0,
                        kdx[v], kdy[v], kw[v], kmix[v], alpha, body, edge)

    def set_color(self, hex_str):
        """花瓣主体色（参考里的饱和粉那一端）。"""
        bgr = hex_to_bgr(hex_str)
        self._body = (float(bgr[2]), float(bgr[1]), float(bgr[0]))

    def set_edge_color(self, hex_str):
        """花瓣边缘 / 翻面泛白那一端的颜色。"""
        bgr = hex_to_bgr(hex_str)
        self._edge = (float(bgr[2]), float(bgr[1]), float(bgr[0]))


# ------------------------------------------------------------------ 流星雨
class MeteorField:
    """流星雨：斜向高速掠过的"亮头 + 长尾"彗星。

    观感取自 Wallpaper Engine 场景 2016635516「流星夜」里的 Shooting star：
      * 本质是**粒子 + ropetrail 渲染器**：emitter rate 0.2/s、lifetime 3s、
        maxcount 16 —— 同一时刻只有一两颗，很克制；velocityrandom 指向 +x、
        gravity (0,-40,0)、drag 0.1 -> 斜向右下、越走越慢的细长轨迹
      * material 是 additive 混合的圆形贴图（particle/drop），另有一颗
        size 100~150 的 sprite 光晕子发射器跟着头走 -> 头部带 bloom
      * 场景底下还铺了一张星空底图（models/流星夜.json）

    在 CPU 逐像素 overlay 上的三个取舍：

    1. **不逐颗流星做线段栅格化**。那需要给每颗流星开一块 (L×W) 的临时画布，
       1920×1080 下十几颗就是百万像素级 numpy 开销。改成「把轨迹采样成一串
       同半径圆点」，把所有流星的所有采样点拼成一个 (n·K, L) 的盖章任务，
       一次向量化搞定 —— 与雪花/落樱同一套路子。
    2. **采样间距必须小于直径**，否则拖尾断成一串虚线。所以 K 是按
       「轨迹长度 / 半径」算出来的，不是拍脑袋的常数（见 `_SPAN`）。
    3. **头部辉光必须烘进同一张核**（`max(实心核, amp×光晕核)`），不能当独立
       的一遍画：stamp 是覆盖写、没有混合，先画会被随后的实心核压出一圈暗环，
       后画又会把最亮的头芯抹暗。雪花的 `snow_glow` 踩过同一个坑。

    颜色存的是 **BGR**（`hex_to_bgr` 的原始顺序），直接喂给 `_premul_pack`，
    少一次来回换序 —— 落樱那边就是在这里把红蓝弄反过。

    与其它特效同一套接口（set_amount / resize / update / draw）。
    """

    R_BASE = 2.8                 # 头部实心半径基准（像素，meteor_size=1.0）
    GLOW_SCALE = 3.8             # 光晕半径 / 实心半径
    _SPAN = 1.2                  # 采样间距 = 半径 × 该值（<2 才不断线）
    _BURN_MIN = 0.40             # 燃尽距离 / 「出生点到背风边」的距离，下限
    _BURN_MAX = 1.35             # 同上的上限；>1 = 允许飞过屏幕边再燃尽（见 _spawn）
    _KMIN, _KMAX = 12, 512
    _KEEP = 0.006
    # 圆盘核内**平坦区**占比：权重在 rr <= (1-PLATEAU)·(r+0.5) 内恒为 1，
    # 之后才衰减到 0。这是"圆盘链不变成串珠"的关键 —— 盖章是覆盖写不是求和，
    # 相邻圆盘之间只会取到"较晚画的"那一个的权重；如果核是尖顶（纯衰减），
    # 两圆心连线的中点只能拿到 w(spacing/2)，实测会形成 ~2 倍的亮度波纹，
    # 一条彗尾看上去就是一串珠子。有了平坦区，中点落在两个圆盘的平台内 -> 恒 1。
    # 前提是平台要重叠：2·(1-PLATEAU)(r+0.5) >= _SPAN·r，对 r>=0.84 成立。
    PLATEAU = 0.42
    _ARR = ("x", "y", "vx", "vy", "t", "life", "spd", "blen", "bright")

    def __init__(self, w, h, seed=31):
        self.rng = np.random.default_rng(seed)
        self.w, self.h = w, h
        self._t = 0.0
        self._acc = 0.0
        self._want = 0
        self._life_ema = 0.0                  # 最近生成那批的平均寿命（自校正用）
        self._kt = {}
        self._hc = (255.0, 255.0, 255.0)      # 头：#FFFFFF (b, g, r)
        self._tc = (240.0, 184.0, 95.0)       # 尾：#5FB8F0 (b, g, r)
        self.n = 0
        for name in self._ARR:
            setattr(self, name, np.zeros(0, np.float32))
        # 星空（可选层，独立 rng，避免被流星的随机数消费影响排布）
        self._sx = np.zeros(0, np.float32)
        self._sy = np.zeros(0, np.float32)
        self._su = np.zeros(0, np.float32)
        self._sph = np.zeros(0, np.float32)
        self._sfr = np.zeros(0, np.float32)
        self._s_den = -1.0
        self._s_w = self._s_h = -1
        self.set_amount(14, w, h)

    def resize(self, w, h):
        self.w, self.h = w, h
        self._s_w = self._s_h = -1            # 星空按新尺寸重排
        self._life_ema = 0.0                  # 燃尽距离随屏幕走，平均寿命要重估

    # ------------------------------------------------------------ 核
    def _ktab_round(self, r_px, glow=0.0):
        """圆形核（可选叠加辉光），返回展平的 (kdx, kdy, kw)。

        实心部分用「平坦区 + 边缘衰减」（见 PLATEAU 的注释）——圆盘链要连成
        一条**亮度均匀**的带，而不是一串珠子。辉光在这里就和实心核**合并**
        成一张复合核（逐格取 max），所以盖章时只有一遍 —— 见类注释第 3 条。
        """
        key = (int(round(float(r_px) * 4.0)), round(float(glow), 3))
        got = self._kt.get(key)
        if got is not None:
            return got
        r = float(max(0.7, min(26.0, r_px)))
        edge = r + 0.5
        gr = r * float(self.GLOW_SCALE)
        g = int(np.ceil((gr if glow > 0.0 else r) + 1.0))
        k = np.arange(-g, g + 1, dtype=np.float32)
        m = int(k.size)
        dxs = np.tile(k, m)
        dys = np.repeat(k, m)
        rr = np.sqrt(dxs * dxs + dys * dys)
        wgt = np.clip((1.0 - rr / edge) / self.PLATEAU, 0.0, 1.0) ** 0.9
        if glow > 0.0:
            # 参考场景的 bloom（strength 2.0 / threshold 0.65）在 CPU 上的等效：
            # 峰值 0.78、半径 3.8× 实心半径、近似二次衰减 —— 太弱的话滑条
            # 推满也只多 5% 像素，用户根本看不出这个旋钮存在。
            halo = np.clip(1.0 - rr / (gr + 0.5), 0.0, 1.0) ** 1.9 * (0.78 * glow)
            wgt = np.maximum(wgt, halo)
        keep = wgt > self._KEEP
        tab = (dxs[keep].astype(np.int32), dys[keep].astype(np.int32),
               wgt[keep].astype(np.float32))
        self._kt[key] = tab
        return tab

    # ------------------------------------------------------------ 粒子
    def set_amount(self, n, w=None, h=None):
        """目标**同时在屏**的流星数。真正的数量由 update 按速率渐入渐出，
        拖滑条时不会瞬间冒出一屏流星。"""
        if w:
            self.w = w
        if h:
            self.h = h
        self._want = int(max(0, min(400, n)))

    def _spawn(self, k, cfg):
        if k <= 0:
            return
        rng = self.rng
        w, h = self.w, self.h
        old = self.n
        tot = old + k
        for name in self._ARR:
            a = getattr(self, name)
            b = np.zeros(tot, np.float32)
            b[:old] = a
            setattr(self, name, b)

        speed = max(1.0, float(cfg.get("meteor_speed", 880.0)))
        ang = float(np.clip(cfg.get("meteor_angle", 34.0), -80.0, 80.0))
        rad = np.radians(ang)
        dx, dy = float(np.sin(rad)), float(np.cos(rad))
        length = max(20.0, float(cfg.get("meteor_length", 380.0)))

        # 出生点 = 「燃尽点」沿 -d 方向退开 lead。燃尽点在整个屏幕内均匀取，
        # 于是**每颗流星的头部都必然从屏幕内部扫过**（不会在边角擦一下就走）。
        # 曾经用过"屏幕外一条垂直于 d 的幕布"来投放：矩形屏幕在该幕布切线上
        # 只有一个切点，幕布两端离屏幕半个对角线那么远，而生平只有几百像素的
        # 位移根本走不回来 —— 结果 12 颗流星一颗都没进屏（点亮 0 像素）。
        # 出生点直接取屏幕的**迎风边界**（顶边 / 左右边，按射线通量加权）：
        #   * 顶边通量 ∝ w·|dy|，侧边通量 ∝ h·|dx| —— 角度越平越偏向侧向进场
        #   * 位置沿该边均匀取（均匀通量 = 均匀覆盖）
        # 这样流星一出生就在屏幕边缘，整段寿命都看得见。曾经用"燃尽点在屏内
        # 均匀取、再往上游退 lead"的模型：lead 最大到 2.1 倍尾长，一半寿命
        # 花在屏外飞 —— 滑条写 14 实际只有七八颗在屏上，单颗测试更是连跑 70
        # 帧都还没进场（"什么都没画出来"）。
        ax, ay = abs(dx), abs(dy)
        f_top = w * ay
        f_side = h * ax
        top = rng.random(k) < (f_top / max(1e-6, f_top + f_side))
        ex = np.where(top, rng.uniform(0.0, w, k),
                      (0.0 if dx >= 0.0 else float(w)))
        ey = np.where(top, 0.0, rng.uniform(0.0, h, k))

        blen = (np.float32(length) * rng.uniform(0.70, 1.35, k)).astype(np.float32)
        # 燃尽距离 = 从出生点沿 d 一路飞到**背风边**（离开屏幕）的距离，再乘一个
        # [_BURN_MIN, _BURN_MAX] 的系数。
        #
        # 曾经取「尾长 × 1.3~2.3」—— 那是**与屏幕尺寸无关**的长度，于是屏幕越高
        # 流星越够不着底：1200 高的屏上平均只飞 580px（48% 高度），本机
        # 3840×1200 十段瞬时密度是 3.1万/3.2万/3.1万/2.7万/2.3万/1.8万/1.3万/
        # 7.9千/3.5千/7.9百（像素/行）—— 最密/最疏 41×，从 40% 高度起断崖式
        # 掉下去，看着就是"只有上半屏有流星"。
        #
        # 上限取 **1.35 而不是 1.0** 是量出来的：`draw` 的生灭包络让流星在最后
        # 30% 寿命里渐隐，行程若是"正好到屏边"，它在离屏边还有三成路程时就淡没了，
        # 底部照样是空的（实测上限取 1.0 时最密/最疏仍是 41×、底部十段只有
        # 789 像素/行）。让它多飞一截、在屏幕外才燃尽，淡出正好落在屏边，尾巴
        # 继续把底部刷上：最密/最疏 41× -> 3.6×，底部十段 789 -> 7358，而
        # **总像素数基本不变** —— 只是把原来堆在顶部的流星摊到了整屏。
        dxf, dyf = float(dx), float(dy)
        if dxf > 0.0:
            bx = (np.float32(w) - ex) / np.float32(dxf)
        elif dxf < 0.0:
            bx = ex / np.float32(-dxf)
        else:
            bx = np.full(k, np.float32(np.inf), np.float32)
        if dyf > 0.0:
            by = (np.float32(h) - ey) / np.float32(dyf)
        elif dyf < 0.0:
            by = ey / np.float32(-dyf)
        else:
            by = np.full(k, np.float32(np.inf), np.float32)
        exit_d = np.maximum(np.minimum(bx, by), np.float32(1.0))
        travel = exit_d * rng.uniform(self._BURN_MIN, self._BURN_MAX, k).astype(np.float32)
        # 出生点贴着背风边时（左边缘靠下那种）行程会短到"一闪就没"——给个下限，
        # 宁可让它飞出屏幕也不要一次眨眼（尾部全靠 life 内长出来）。
        travel = np.maximum(travel, np.float32(0.55) * blen).astype(np.float32)

        vary = rng.uniform(0.80, 1.25, k)
        sp = (np.float32(speed) * vary).astype(np.float32)

        sl = slice(old, tot)
        self.x[sl] = ex.astype(np.float32)
        self.y[sl] = ey.astype(np.float32)
        self.vx[sl] = dx * sp
        self.vy[sl] = dy * sp
        self.t[sl] = 0.0
        self.spd[sl] = sp
        # 尾长是**几何长度**（拖尾最多这么长，与寿命无关）；
        # 寿命 = 燃尽行程 / 速度 —— 行程由屏幕几何定，见上面的 exit_d。
        self.blen[sl] = blen
        self.life[sl] = np.maximum(0.10, travel / np.maximum(sp, np.float32(1.0))
                                   ).astype(np.float32)
        self.bright[sl] = rng.uniform(0.55, 1.0, k).astype(np.float32)
        self.n = tot
        # 记下这批的实际寿命，供生成速率自校正（见 `_mean_life`）。
        m = float(np.mean(self.life[sl]))
        if m > 0.0:
            self._life_ema = (m if self._life_ema <= 0.0
                              else 0.92 * self._life_ema + 0.08 * m)

    def _mean_life(self, cfg):
        """生成速率用的平均寿命：最近实际生成那批的指数平均。

        还没生成过（刚构造 / 刚 resize）时给个量级初值 —— 一个屏高 / 速度，
        也就是"从迎风边飞到背风边"的时长。它只影响最初几帧的生成速率，
        之后 EMA 会把它拉到实测值。
        """
        if self._life_ema > 0.0:
            return self._life_ema
        sp = max(1.0, float(cfg.get("meteor_speed", 880.0)))
        return max(0.15, float(self.h) / sp)

    def update(self, dt, cfg):
        """推进流星（返回空数组：流星不溅水）。"""
        dt = float(dt)
        self._t += dt
        if self.n:
            self.x += self.vx * dt
            self.y += self.vy * dt
            self.t += dt
            m = 2.0 * max(self.w, self.h)
            dead = ((self.t >= self.life) | (self.x < -m) | (self.x > self.w + m)
                    | (self.y < -m) | (self.y > self.h + m))
            if dead.any():
                keep = ~dead
                for name in self._ARR:
                    setattr(self, name, getattr(self, name)[keep])
                self.n = int(keep.sum())

        want = int(max(0, min(400, self._want)))
        if want <= 0:
            return np.zeros(0, np.float32)
        # 生成速率 = 目标颗数 / 平均寿命。平均寿命取**最近实际生成那批**的指数
        # 平均（见 `_mean_life`），而不是解析式：解析式要同时依赖屏幕长宽比、
        # 飞行角度与 _BURN_MIN，改任何一个都会系统性偏多/偏少 —— 早先漏掉尾长
        # 抖动系数，滑条写 14 实际稳态 25 颗。用实测值自校正，稳态颗数会自动
        # 收敛到 want（速率 × 实际平均寿命 = want）。
        self._acc += (want / self._mean_life(cfg)) * dt
        k = int(self._acc)
        if k > 0:
            self._acc -= k
            k = min(k, max(4, want), max(0, 400 - self.n))
            if k:
                self._spawn(k, cfg)
        return np.zeros(0, np.float32)

    # ------------------------------------------------------------ 绘制
    def _stamp(self, u32, w, h, px, py, kdx, kdy, kw, alpha, mixarr, c0, c1,
               keep=None):
        """批量盖章。px/py/alpha 是 (P,) 的展平点集，mixarr 是每点的混色权重
        （None = 全用 c0）。c0 / c1 是 **(b, g, r)** 三元组。"""
        if keep is None:
            keep = self._KEEP
        ax = (px[:, None] + kdx[None, :]).reshape(-1)
        ay = (py[:, None] + kdy[None, :]).reshape(-1)
        av = (alpha[:, None] * kw[None, :]).reshape(-1)
        mi = (np.zeros(av.size, np.float32) if mixarr is None
              else np.repeat(mixarr, kdx.size))
        m = av > keep
        if not m.any():
            return
        ax, ay, av, mi = ax[m], ay[m], av[m], mi[m]
        m = (ax >= 0) & (ax < w) & (ay >= 0) & (ay < h)
        if not m.any():
            return
        ax, ay, av, mi = ax[m], ay[m], av[m], mi[m]
        # c0 / c1 已经是 (b, g, r)，与 _premul_pack 的入参顺序一致
        b0, g0, r0 = c0
        b1, g1, r1 = c1
        cb = np.float32(b0) + (np.float32(b1) - np.float32(b0)) * mi
        cg = np.float32(g0) + (np.float32(g1) - np.float32(g0)) * mi
        cr = np.float32(r0) + (np.float32(r1) - np.float32(r0)) * mi
        a_c = np.rint(av * 255.0).astype(np.uint32)
        packed = _premul_pack(np.rint(cb * av).astype(np.uint32),
                              np.rint(cg * av).astype(np.uint32),
                              np.rint(cr * av).astype(np.uint32), a_c)
        u32[ay * np.int32(w) + ax] = packed

    def _ensure_stars(self, w, h, den):
        if (self._s_den == den and self._s_w == w and self._s_h == h):
            return
        rng = np.random.default_rng(20240912)
        cnt = int(min(6000, round(den * (w * h) / 9000.0)))
        self._sx = rng.uniform(0.0, w, cnt).astype(np.float32)
        self._sy = rng.uniform(0.0, h, cnt).astype(np.float32)
        self._su = rng.random(cnt).astype(np.float32)
        self._sph = rng.uniform(0.0, float(TAU), cnt).astype(np.float32)
        self._sfr = rng.uniform(0.35, 1.60, cnt).astype(np.float32)
        self._s_den, self._s_w, self._s_h = den, w, h

    def _draw_stars(self, u32, w, h, den, cfg):
        """星空：先画（随后的流星会自然地盖住星点）。"""
        self._ensure_stars(w, h, den)
        if self._sx.size == 0:
            return
        op = float(np.clip(cfg.get("meteor_opacity", 0.9), 0.0, 1.0))
        tw = 0.5 + 0.5 * np.sin(self._t * (TAU * self._sfr) + self._sph)
        mag = 0.18 + 0.82 * self._su ** 2.6      # 暗星多亮星少
        a = mag * (0.30 + 0.70 * tw) * op * 0.95
        np.clip(a, 0.0, 1.0, out=a)
        kdx, kdy, kw = self._ktab_round(1.2)
        self._stamp(u32, w, h, np.rint(self._sx).astype(np.int32),
                    np.rint(self._sy).astype(np.int32), kdx, kdy, kw,
                    a.astype(np.float32), None, self._hc, self._hc, keep=0.004)

    def draw(self, u32, w, h, cfg):
        den = float(cfg.get("meteor_stars", 0.0))
        if den > 0.0:
            self._draw_stars(u32, w, h, den, cfg)
        if self.n == 0:
            return

        op = float(np.clip(cfg.get("meteor_opacity", 0.9), 0.0, 1.0))
        size = float(np.clip(cfg.get("meteor_size", 1.0), 0.3, 4.0))
        r_head = float(min(26.0, max(0.8, self.R_BASE * size)))

        # 采样数按「最长轨迹 / 采样间距」推：间距 = 半径 × _SPAN < 直径，
        # 相邻圆盘必然重叠 -> 连成一条带，不会断成虚线。
        # 轨迹长度取**当前**长度（尾巴要一点点长出来），不是 blen 上限。
        sp = self.spd + np.float32(1e-6)
        cur = np.minimum(self.blen, sp * self.t)
        # 采样数上限所隐含的「最长可连续渲染尾长」：超过就截短，
        # 宁可短一点也不断成虚线（极细 + 极长的组合才会碰到）。
        cur = np.minimum(cur, np.float32(self._KMAX * self._SPAN * r_head))
        ks = int(np.clip(np.ceil(float(cur.max()) / (self._SPAN * r_head)),
                         self._KMIN, self._KMAX))
        s = np.linspace(1.0, 0.0, ks, dtype=np.float32)      # 1=尾端, 0=头部
        ux = (self.vx / (np.sqrt(self.vx * self.vx + self.vy * self.vy)
                         + np.float32(1e-6)))[:, None]
        uy = (self.vy / (np.sqrt(self.vx * self.vx + self.vy * self.vy)
                         + np.float32(1e-6)))[:, None]
        blc = cur[:, None]
        px = np.rint(self.x[:, None] - ux * blc * s[None, :]).astype(np.int32)
        py = np.rint(self.y[:, None] - uy * blc * s[None, :]).astype(np.int32)

        # 生灭包络：亮起 ~6%、最后 30% 渐隐 —— 否则整条尾巴会在 t=life
        # 那一帧**整条同时消失**（rope trail 没有火焰残影，一撤就穿帮）。
        tt = self.t / np.maximum(self.life, np.float32(1e-3))
        env = (np.clip(tt / 0.06, 0.0, 1.0)
               * np.clip((1.0 - tt) / 0.30, 0.0, 1.0)).astype(np.float32)

        # 头部 12% 保持满亮（亮头），之后按 1.55 次幂向尾部收
        prof = np.clip((1.0 - s) / 0.88, 0.0, 1.0) ** 1.55
        alp = (self.bright * env)[:, None] * op * prof[None, :]
        np.clip(alp, 0.0, 1.0, out=alp)
        # 混色：头部近亮白、55% 之后全用尾色。
        # 必须铺成 (n, ks) —— 混色只沿轨迹变化、与是哪颗流星无关，
        # 但盖章收的是展平点集，长度得跟 px 对齐（这里踩过一次索引对不上的坑）。
        mix = np.tile(np.clip(s / 0.55, 0.0, 1.0), (self.n, 1))

        kdx, kdy, kw = self._ktab_round(r_head)
        # 尾 -> 头 依次盖章（stamp 是覆盖写，后画的亮头压住暗尾）
        self._stamp(u32, w, h, px.reshape(-1), py.reshape(-1),
                    kdx, kdy, kw, alp.reshape(-1).astype(np.float32),
                    mix.astype(np.float32), self._hc, self._tc)

        glow = float(np.clip(cfg.get("meteor_glow", 0.0), 0.0, 1.0))
        if glow > 0.0:
            gdx, gdy, gw = self._ktab_round(r_head, glow)
            ga = np.clip(self.bright * env * op, 0.0, 1.0).astype(np.float32)
            self._stamp(u32, w, h, np.rint(self.x).astype(np.int32),
                        np.rint(self.y).astype(np.int32), gdx, gdy, gw, ga,
                        None, self._hc, self._hc)

    def set_color(self, hex_str):
        """头部颜色（参考里是 additive 的纯白热点）。"""
        self._hc = tuple(float(v) for v in hex_to_bgr(hex_str))

    def set_tail_color(self, hex_str):
        """拖尾颜色（越靠尾越接近这个色）。"""
        self._tc = tuple(float(v) for v in hex_to_bgr(hex_str))


# ------------------------------------------------------------------ 颜料盘
class PaintField:
    """颜料盘：鼠标搅动的流体颜料（参考 Lively 壁纸 "Fluids"
    = PavelDoGreat 的 WebGL Fluid Simulation，MIT）。

    与"全屏自转的色雾"不同，这里是**交互驱动**的：

    1. **指针移动 = 搅动**。沿轨迹按间距连续落色，并沿运动方向给流场一记冲量；
       颜料被拖出去之后又被预烘基流卷成涡旋、拉成丝。
    2. **点击 = 扩散**。在落点撒一圈**径向向外**的速度冲量 + 一团颜料，
       颜料被推着向外炸开成一朵。
    3. **空闲 = 全透明**。没有指针输入就不再注入；已有颜料靠基流继续涡旋，
       再按 paint_fade 指数消隐，最后整屏干净。draw 只画"有颜料的包围盒"，
       空闲时几乎零成本 —— 这是它与"自己铺满整屏"的旧版最大的区别。

    参考工程是完整 Navier-Stokes（GPU：228×128 速度场 + 20 次 Jacobi 压力投影
    + bloom + sunrays）。CPU 逐像素照搬不可能 60fps，四个取舍：

    1. **不做压力投影**（省掉 20 次 Jacobi，最大一笔）。改用**预烘 curl-noise
       基流**：由 φ 的旋转梯度得 (u,v) = (∂φ/∂y, −∂φ/∂x)，按构造
       ∂u/∂x + ∂v/∂y ≡ 0，天然无散度。3 条基流各由 5 列随机正弦叠加，只在建
       网格时算一次；每帧按缓慢变化的时间权重混合（0.2ms），流场持续变形。
    2. **网格只到 ~110 短边**（16:9 约 198×110 ≈ 22k 格），染料与速度同分辨率，
       比参考的 1820×1024 粗得多；上屏用最近邻放大。颜料本身是低频软糊状 +
       带辉光，放大后的台阶看不出来。
    3. **上屏放大用 take 链 + 先 x 后 y**（见 draw 里的说明），1080p ≈1.1ms。
    4. **只画包围盒**：颜料稀疏，大部分屏幕常年为空。遮罩赋值只在聚光灯也在场
       时才需要（否则整片覆盖写就够，更快）。

    **单位约定**（很容易搞错）：`_advect` 里是 `sx = xs − u·gw·dt`，`xs` 是格子
    坐标，所以 `u` 的量纲是**屏幕宽/秒**，`v` 是**屏幕高/秒**（格是方的，
    `gw·cell == w`、`gh·cell == h`）。基流 `_FLOW = 0.075` 即"每秒走 7.5% 屏宽"。

    颜色存 **RGB**（渲染时才拆进 `_premul_pack(bb, gb, rb, ab)`）—— 落樱在
    这里把红蓝弄反过。

    与其它特效同一套接口（set_color / resize / update / draw），另外多两个：
    `set_pointer()` 每帧喂光标，`reset()` 关闭时清场。
    """

    # 网格由"总格数上限"反推短边，保证 16:9 / 21:9 / 4K 双屏都不超预算
    _CELLS_MAX = 22000
    _G_MIN, _G_MAX = 64, 144
    _BASIS = 3                   # 预烘基流条数
    _WAVES = 5                   # 每条基流叠加的正弦列数
    _FLOW = 0.075                # 基流速度基准（屏宽/秒），乘 paint_swirl
    _IMP_TAU = 0.22              # 指针/点击冲量场的时间常数（秒）
    _FOLLOW = 4.00               # 稳态下"流场跟随指针"的倍率（见 _pointer 的推导）
                                 # 必须 >>1。=1 只是"跟手同速"，颜料仍然整条拖在身后；
                                 # 实测亮部质心相对鼠标：0.55 -> -146px、1.5 -> -86px、
                                 # 2.5 -> -37px、4.0 -> 0px（600px/s，刷子半径 45px）
    _FADE_MIN, _FADE_MAX = 0.15, 1.10   # 消隐速率 1/s（对应 paint_fade 0~1）
    _BLOCK_PX = 4.0              # 上屏后允许的最大格子边长；显示网格 = 模拟网格 × up
    _HUE_HZ = 0.5                # 色相自转（圈/秒）× paint_hue_spread
    _HUE_PER_SCREEN = 1.1        # 指针划过一屏宽推进的色相（圈）× paint_hue_spread
    _R_NORM = 0.042              # 笔刷半径基准（屏幕短边比例），乘 paint_brush
    _AMP = 0.18                  # 单团峰值浓度基准，乘 paint_flow
    _CLICK_PUSH = 6.00           # 点击径向冲量峰值（屏宽/秒），乘 paint_click
    _DIFF_RATE = 2.5             # 晕染强度基准（1/s），乘 paint_diffusion × dt
    _DYE_CLAMP = 3.0             # 浓度上限，防叠加爆表
    _SPAN = 0.55                 # 轨迹采样间距 = 笔刷半径 × 该值（<1 才连成笔触）
    _MAX_STEPS = 32              # 单帧最多落几团（防高速甩鼠标爆开销）
    _TELEPORT = 0.45             # 单帧位移超屏幕对角线这个比例 -> 视为瞬移，不断笔
    _BBOX_EPS = 1.0e-3           # 包围盒判定阈值（低于它的格子打包后必为 0）

    def __init__(self, w, h, seed=37):
        self.rng = np.random.default_rng(seed)
        self.gh = self.gw = 0
        self.w, self.h = 0, 0
        self._t = 0.0
        self._imp_k = 0.1            # 本帧的冲量注入系数（update 里按 dt 算）
        self._hue = 0.96             # 基准色相（#FF4D6D 的 hsv.h）
        self._sat = 0.70
        self._hue_ph = 0.0           # 色相漂移相位（时间 + 指针行程共同推进）
        self._hue_src = None
        self._om = np.array((0.0105, 0.0172, 0.0251), np.float32)
        self._in = None              # 本帧指针（覆盖层屏幕坐标）
        self._ouse = None            # 上一次被消费的指针（格子坐标）
        self._dirty = False          # 场里有东西时置位（让 reset 幂等）
        self._build(w, h)

    # ------------------------------------------------------------ 网格 / 基流
    def _build(self, w, h):
        ar = (float(w) / float(h)) if h else 1.0
        if not (ar > 0.0):
            ar = 1.0
        long_ar = max(ar, 1.0 / ar)
        g = int(round(np.sqrt(self._CELLS_MAX / long_ar)))
        g = int(np.clip(g, self._G_MIN, self._G_MAX))
        gw = max(8, int(round(g * ar)))
        gh = g
        # 取偶数：辉光要做 2×2 池化 + 整数倍放大
        gw -= gw % 2
        gh -= gh % 2
        self.w, self.h = w, h
        self.gh, self.gw = gh, gw
        # 格尺寸**必须分轴算**：gw/gh 是各自取整（还取偶）出来的，
        # `w/gw` 和 `h/gh` 并不严格相等。1080p 差 0.23%、3840x1200 差 1.4%
        # （3840 宽屏上屏幕右端能差出 ~49px）。
        # 只用 cell 换算 x 的话，颜料会被整体显示在 `px * cellx/celly` 处
        # —— 一个与运动方向无关的**固定横向偏移**（1080p 偏左 2px、
        # 3840x1200 偏左 26px）。人眼在"往左滑"时鼠标会追进颜色区、觉得跟手，
        # 往右滑就被甩开、觉得"滞后"，正是这个固定偏移造成的假象。
        self.cell = float(h) / float(gh)                    # y 向格尺寸
        self.cellx = float(w) / float(gw)                   # x 向格尺寸
        self.r_base = max(1.5, self._R_NORM * float(min(w, h)) / max(self.cell, 1e-6))
        self.dye = np.zeros((gh, gw, 3), np.float32)
        self._dye2 = np.zeros((gh, gw, 3), np.float32)   # 平流双缓冲，免每帧分配
        self._iu = np.zeros((gh, gw), np.float32)
        self._iv = np.zeros((gh, gw), np.float32)
        self._ys = np.arange(gh, dtype=np.float32)[:, None]
        self._xs = np.arange(gw, dtype=np.float32)[None, :]
        # 显示网格 = 模拟网格 × up（up 倍双线性放大后再上屏，见 _rs_table）
        self.up = int(np.clip(np.ceil(self.cell / self._BLOCK_PX), 1, 3))
        dh, dw = gh * self.up, gw * self.up
        self._dh, self._dw = dh, dw
        # 上屏放大用的索引（最近邻）。放大的两步必须"先 x 后 y"，
        # 见 draw() 里的说明。
        #
        # **用四舍五入，不要整除（floor）**：显示格 t 代表的是屏幕区间中心
        # `t*w/dw` 处的值（_rs_table 也是按"目标像素中心 -> 源坐标"取的），
        # floor 会把屏幕 x 归到区间左端，两者口径差半格 —— 实测这个半格带来
        # +2.6px 的固定偏移（1080p），四舍五入后收到 +0.9px，且与 _rs_table 口径一致。
        self._iy = (np.arange(h, dtype=np.int32) * dh + h // 2) // h
        self._ix = (np.arange(w, dtype=np.int32) * dw + w // 2) // w
        self._rsy = _rs_table(gh, self.up)
        self._rsx = _rs_table(gw, self.up)
        self._bu, self._bv = self._make_basis(gh, gw)
        self._phase = self.rng.uniform(0.0, TAU, self._BASIS).astype(np.float32)
        self._t = 0.0
        self._hue_ph = 0.0
        self._in = None
        self._ouse = None
        self._dirty = False

    def _make_basis(self, gh, gw):
        """预烘 _BASIS 条无散度基流，各自把 RMS 归一到 1。

        只在建网格时算一次（~15 次全网格 sin），每帧不再碰三角函数。
        """
        rng = self.rng
        X = np.arange(gw, dtype=np.float32)[None, :] / np.float32(gw)
        Y = np.arange(gh, dtype=np.float32)[:, None] / np.float32(gh)
        bu = np.empty((self._BASIS, gh, gw), np.float32)
        bv = np.empty((self._BASIS, gh, gw), np.float32)
        for k in range(self._BASIS):
            u = np.zeros((gh, gw), np.float32)
            v = np.zeros((gh, gw), np.float32)
            for _ in range(self._WAVES):
                # 频率不取近 0：否则基流退化成整体平移，画面变成"整块飘"
                kx = rng.uniform(0.6, 3.2) * (1.0 if rng.random() < 0.5 else -1.0)
                ky = rng.uniform(0.6, 3.2) * (1.0 if rng.random() < 0.5 else -1.0)
                th = TAU * (np.float32(kx) * X + np.float32(ky) * Y) \
                    + np.float32(rng.uniform(0.0, 2.0 * np.pi))
                s = np.sin(th)
                u += np.float32(TAU * ky) * s       # ∂φ/∂y
                v -= np.float32(TAU * kx) * s       # −∂φ/∂x
            m = float(np.sqrt(np.mean(u * u + v * v))) + 1e-6
            bu[k] = u / m
            bv[k] = v / m
        return bu, bv

    def resize(self, w, h):
        if (w, h) != (self.w, self.h):
            self._build(w, h)

    def reset(self):
        """关闭颜料盘时清场（幂等：本来就没东西就立刻返回）。"""
        if not self._dirty:
            return
        self._dirty = False
        self.dye[:] = 0.0
        self._iu[:] = 0.0
        self._iv[:] = 0.0
        self._ouse = None

    def set_color(self, hex_str):
        """基准色：由它取色相/饱和度，每团的色相再按 paint_hue_spread 抖动。"""
        if hex_str == self._hue_src:
            return
        self._hue_src = hex_str
        b, g, r = (float(x) for x in hex_to_bgr(hex_str))
        hh, ss, _vv = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        self._hue = float(hh)
        # 基准色偏灰时也把饱和度托起来，否则"颜料盘"会发浊
        self._sat = float(min(1.0, max(0.55, ss)))

    def set_pointer(self, x, y, clicked=0):
        """每帧喂入光标（覆盖层屏幕坐标）。

        **特效关闭时也要喂**：`_ouse` 是"上一次被消费的指针"，只在 update()
        里推进；若只在启用时才喂，重新启用后的第一帧会拿着很久以前的旧位置
        算出一条横贯屏幕的笔触（_pointer 里还有一道瞬移保护兜底）。
        """
        self._in = (float(x), float(y), int(clicked))

    # ------------------------------------------------------------ 落笔
    def _stroke_color(self):
        """当前落笔颜色：基准色相 + `_hue_ph` 的**连续**漂移。

        刻意不用"每帧掷一次随机色相"：相邻帧的互补色相叠加起来会互相抵消，
        整片颜料变成灰白噪声，看着是"颜色在抖"而不是"颜色在流动"。改成一个连续
        推进的相位后，一笔拖过去就是一条色相渐变的彩带，停手时颜色也还在缓慢
        转 —— 这才是"颜色飘动"。（参考实例是每 1/10 秒整体换一次满饱和色。）
        """
        r, g, b = _hsv_to_rgb(np.array([self._hue + self._hue_ph], np.float32),
                              np.array([self._sat], np.float32),
                              np.ones(1, np.float32))
        return np.array((float(r[0]), float(g[0]), float(b[0])), np.float32)

    def _splat(self, cx, cy, r, amp, col, push=(0.0, 0.0), radial=0.0):
        """落一团高斯颜料，并（可选）给流场加冲量。

        `push` 是均匀方向的速度（屏宽/秒），`radial` 是以落点为心的向外扩散
        强度（屏宽/秒）。两者互斥，radial 优先。
        """
        gh, gw = self.gh, self.gw
        ext = 2.6 * r
        x0 = int(max(0, np.floor(cx - ext)))
        x1 = int(min(gw, np.ceil(cx + ext) + 1))
        y0 = int(max(0, np.floor(cy - ext)))
        y1 = int(min(gh, np.ceil(cy + ext) + 1))
        if x1 <= x0 or y1 <= y0:
            return
        xs = np.arange(x0, x1, dtype=np.float32)[None, :] - np.float32(cx)
        ys = np.arange(y0, y1, dtype=np.float32)[:, None] - np.float32(cy)
        gg = np.exp(-(xs * xs + ys * ys) / np.float32(2.0 * r * r))
        self.dye[y0:y1, x0:x1] += (gg * np.float32(amp))[..., None] * col[None, None, :]
        if radial != 0.0:
            # 方向必须"饱和"：直接拿 (xs, ys) 当方向的话，落点中心附近的速度 ~0，
            # 而高斯核的质量恰恰集中在中心 —— 整团几乎推不动（实测半径只涨 2 格）。
            # 把半径下限卡在 0.30r，中心以外的质量就都吃得到接近满值的推力。
            inv = np.float32(radial) / np.maximum(
                np.sqrt(xs * xs + ys * ys), np.float32(0.30 * r))
            self._iu[y0:y1, x0:x1] += gg * (xs * inv)
            self._iv[y0:y1, x0:x1] += gg * (ys * inv)
        elif push[0] != 0.0 or push[1] != 0.0:
            # 只用 gg，**不要**再乘 amp：推流强度应当只由指针速度决定，
            # 不该跟着"落色量"变（乘上 amp=0.18 时实际推力只有设计值的 1/5.5，
            # 流场跟不上手，颜料就整条拖在鼠标后面）。
            self._iu[y0:y1, x0:x1] += gg * np.float32(push[0])
            self._iv[y0:y1, x0:x1] += gg * np.float32(push[1])
        self._dirty = True

    def _burst(self, gx, gy, r, flow, cfg):
        """点击 -> 落点炸开一圈**径向向外**的冲量 + 一团颜料（"扩散"）。"""
        click = float(np.clip(cfg.get("paint_click", 1.0), 0.0, 3.0))
        if click <= 0.0:
            return
        self._splat(gx, gy, r * 2.0, self._AMP * 2.4 * flow,
                    self._stroke_color(),
                    radial=self._CLICK_PUSH * click)

    def _pointer(self, dt, cfg):
        """把这一帧的光标轨迹翻译成"落色 + 推流"。

        x 与 y 分别用 `cellx` / `cell` 换算成格 —— 两个轴的格尺寸并不相等
        （见 _build 里的说明），混用会让颜料在屏幕上偏移固定的几个像素。
        """
        ptr = self._in
        if ptr is None:
            return
        px, py, clicked = ptr
        self._in = None                       # 一个位置只消费一次
        gx = px / self.cellx
        gy = py / self.cell
        gw, gh = self.gw, self.gh

        flow = float(np.clip(cfg.get("paint_flow", 1.0), 0.0, 3.0))
        swirl = float(np.clip(cfg.get("paint_swirl", 1.0), 0.0, 3.0))
        r = max(1.2, self.r_base * float(np.clip(cfg.get("paint_brush", 1.0),
                                                 0.1, 4.0)))

        # 点击：先炸（与"是否移动"无关，原地也能扩散）
        if clicked:
            self._burst(gx, gy, r, max(flow, 0.25), cfg)

        if self._ouse is None:
            self._ouse = (gx, gy)
            return
        ox, oy = self._ouse
        self._ouse = (gx, gy)
        dx = gx - ox
        dy = gy - oy
        dist = float(np.hypot(dx, dy))
        if dist < 1e-4 or flow <= 0.0:
            return
        if dist > float(np.hypot(gw, gh)) * self._TELEPORT:
            return                            # 光标瞬移：只重新对位，不拉一条长线

        # 色相随行程推进：拖得越远颜色走得越远 -> 一笔就是一条渐变彩带
        spread = float(np.clip(cfg.get("paint_hue_spread", 0.5), 0.0, 1.0))
        self._hue_ph = (self._hue_ph
                        + (dist / float(gw)) * self._HUE_PER_SCREEN * spread) % 1.0

        # 沿轨迹按间距采样，连成笔触（和流星拖尾同一个套路：间距 < 直径）
        span = max(0.5, r * self._SPAN)
        k = int(min(self._MAX_STEPS, max(1, np.ceil(dist / span))))
        t = np.arange(1, k + 1, dtype=np.float32) / np.float32(k)
        cx = ox + dx * t
        cy = oy + dy * t

        # 推流强度：本帧位移 -> 速度（屏宽或屏高/秒），再乘注入系数。
        # 系数取 `imp_k × FOLLOW`，其中 imp_k = 1 − exp(−dt/tau) 是这一帧冲量场
        # 的衰减比例，于是**稳态冲量 = 指针速度 × FOLLOW**（与帧率无关）。
        # 不要写成"每帧加一个固定量"：冲量场是累加的，固定量会让稳态速度
        # 变成指针速度的几十倍（0.55/0.015 ≈ 37×），颜料瞬间飞出屏幕。
        a = self._imp_k * self._FOLLOW * swirl
        inv_dt = 1.0 / max(dt, 1e-4)
        vx = dx / float(gw) * inv_dt * a
        vy = dy / float(gh) * inv_dt * a
        amp = self._AMP * flow
        col = self._stroke_color()
        for i in range(k):
            self._splat(float(cx[i]), float(cy[i]), r, amp, col, push=(vx, vy))

    # ------------------------------------------------------------ 平流
    def _active_box(self, pad):
        """有颜料的格子范围（含 pad 余量），返回 (y0, y1, x0, x1)；全空则 None。

        颜料是**稀疏**的（鼠标划到哪才有），全网格重算纯属浪费；这是"挂着不费电"
        的前提。亮通道用 maximum 链而不是 `d.max(axis=2)` —— 后者对"最后一维只有
        3 个"走通用 reduce，慢十几倍。
        """
        d = self.dye
        lum = np.maximum(np.maximum(d[..., 0], d[..., 1]), d[..., 2])
        rows = np.flatnonzero(lum.max(axis=1) > self._BBOX_EPS)
        if rows.size == 0:
            return None
        cols = np.flatnonzero(lum.max(axis=0) > self._BBOX_EPS)
        return (max(0, int(rows[0]) - pad), min(self.gh, int(rows[-1]) + 1 + pad),
                max(0, int(cols[0]) - pad), min(self.gw, int(cols[-1]) + 1 + pad))

    def _advect(self, u, v, dt, box=None):
        """半拉格朗日平流：反查 (x − u·dt) 处的染料，双线性插值。

        `box` 限定只算这一块 —— 只画了一小片时没必要全网格重算。
        用 `take` 展平索引而不是 2D fancy-index：同样 4 个角点，RGB 三通道
        一起取，实测比 `d[y0, x0]` 快近一倍。
        """
        gh, gw = self.gh, self.gw
        if box is None:
            box = (0, gh, 0, gw)
        y0, y1, x0, x1 = box
        prev = self.dye
        out = self._dye2
        # box 外必须是 0：上一帧留在那里的数据要丢掉（平流把它搬走了）
        out[:] = 0.0
        if y1 > y0 and x1 > x0:
            ys = np.arange(y0, y1, dtype=np.float32)[:, None]
            xs = np.arange(x0, x1, dtype=np.float32)[None, :]
            sy = np.clip(ys - v[y0:y1, x0:x1] * np.float32(gh * dt), 0.0, gh - 1.001)
            sx = np.clip(xs - u[y0:y1, x0:x1] * np.float32(gw * dt), 0.0, gw - 1.001)
            by = sy.astype(np.int32)
            bx = sx.astype(np.int32)
            # 必须把索引转 float32 再相减：int32 - float32 会被提升成 float64，
            # 后面 4 个 (n,3) 的临时数组会全部翻倍。
            fy = (sy - by.astype(np.float32)).reshape(-1, 1)
            fx = (sx - bx.astype(np.float32)).reshape(-1, 1)
            base = (by * np.int32(gw) + bx).reshape(-1)
            fl = prev.reshape(-1, 3)
            c00 = fl.take(base, axis=0)
            c01 = fl.take(base + 1, axis=0)
            c10 = fl.take(base + gw, axis=0)
            c11 = fl.take(base + gw + 1, axis=0)
            got = (c00 * ((1.0 - fx) * (1.0 - fy))
                   + c01 * (fx * (1.0 - fy))
                   + c10 * ((1.0 - fx) * fy)
                   + c11 * (fx * fy))
            out[y0:y1, x0:x1] = got.reshape(y1 - y0, x1 - x0, 3)
        self.dye, self._dye2 = out, prev

    def update(self, dt, cfg):
        dt = float(min(max(dt, 0.0), 0.1))
        if dt <= 0.0:
            return
        self._t += dt
        gh, gw = self.gh, self.gw
        swirl = float(np.clip(cfg.get("paint_swirl", 1.0), 0.0, 3.0))

        # 1) 色相相位随**时间**缓慢自转：手停着不动，颜色也会慢慢飘
        spread = float(np.clip(cfg.get("paint_hue_spread", 0.5), 0.0, 1.0))
        self._hue_ph = (self._hue_ph + dt * self._HUE_HZ * spread) % 1.0

        # 2) 指针冲量场：先按固定时间常数衰减 + 轻微扩散（把"手"的运动抹开一点）
        decay = float(np.exp(-dt / self._IMP_TAU))
        self._iu *= np.float32(decay)
        self._iv *= np.float32(decay)
        _smooth5(self._iu, 0.22, 1)
        _smooth5(self._iv, 0.22, 1)
        self._imp_k = 1.0 - decay

        # 3) 指针注入（染料 + 冲量）。**必须在平流之前**：放到最后的话本帧
        #    落下的颜料与本帧的冲量都要等下一帧才生效，凭空多出一帧延迟 ——
        #    600px/s 下是 10px、2400px/s 下是 40px 的"落后"，而且是纯延迟。
        self._pointer(dt, cfg)

        # 4) 时间权重混合出本帧的基流（每帧只此一笔，三角函数早已烘好），
        #    再叠加刚注入的指针冲量
        wt = 0.5 + 0.5 * np.sin(TAU * np.float32(self._t) * self._om + self._phase)
        bu, bv = self._bu, self._bv
        u = bu[0] * wt[0]
        v = bv[0] * wt[0]
        for i in range(1, self._BASIS):
            u += bu[i] * wt[i]
            v += bv[i] * wt[i]
        u *= np.float32(swirl * self._FLOW)
        v *= np.float32(swirl * self._FLOW)
        u += self._iu
        v += self._iv

        # 5) 平流 -> 颜料被拉出丝（流体的灵魂，不能省）
        #    只算"有颜料的格子"那块：本帧速度决定了颜料最远能搬多远，那就是余量。
        #    画面干净时 box 为 None -> 整段跳过，空闲帧几乎不花钱。
        pad = 2 + int(np.ceil(max(float(np.abs(u).max()) * gw,
                                  float(np.abs(v).max()) * gh) * dt))
        box = self._active_box(pad)
        if box is not None:
            self._advect(u, v, dt, box)
        # 必须在这里**重新**取一次：_advect 里换成了新的 self.dye（双缓冲换位），
        # 若沿用上面缓存的旧引用，后面的扩散/消隐会全部作用在已经被丢弃的
        # 那一份上 —— 新数组永不衰减，颜料会无限堆积（踩过）。
        dye = self.dye

        # 6) 晕染 + 消隐。两者都必须按 dt 缩放：写成"每帧固定糊一下 / 减一点"
        #    的话，60fps 下等于每秒 60 次，十几秒后颜料摊成整屏均匀色糊。
        diff = float(np.clip(cfg.get("paint_diffusion", 0.35), 0.0, 1.0))
        if diff > 0.0:
            _smooth5(dye, self._DIFF_RATE * diff * dt, 2)
        fade = self._FADE_MIN + self._FADE_MAX * float(
            np.clip(cfg.get("paint_fade", 0.35), 0.0, 1.0))
        dye *= np.float32(np.exp(-fade * dt))
        np.clip(dye, 0.0, self._DYE_CLAMP, out=dye)


    # ------------------------------------------------------------ 绘制
    def _bloom(self, d):
        """大半径辉光：2×2 池化到 1/4 面积再平滑 3 遍，等效半径翻倍且更便宜。"""
        gh, gw = self.gh, self.gw
        # 池化用四条切片相加而不是 .mean(axis=(1,3))：后者在非连续视图上
        # 走通用 reduce，实测 1.63ms，切片写法只要 0.14ms。
        p = (d[0::2, 0::2] + d[0::2, 1::2] + d[1::2, 0::2] + d[1::2, 1::2])
        p *= np.float32(0.25)
        _smooth5(p, 0.62, 3)
        return np.repeat(np.repeat(p, 2, axis=0), 2, axis=1)

    def draw(self, u32, w, h, cfg):
        if (w, h) != (self.w, self.h):
            self.resize(w, h)
        op = float(np.clip(cfg.get("paint_opacity", 0.9), 0.0, 1.0))
        if op <= 0.0 or not self._dirty:
            return
        # 空场必须是免费的：辉光 / 立体感 / 打包全都别做。颜料稀疏，画面上
        # 大部分时间就是这种"鼠标刚停下、颜料已散尽"的状态。
        d0 = self.dye
        if not (np.maximum(np.maximum(d0[..., 0], d0[..., 1]),
                           d0[..., 2]).max() > self._BBOX_EPS):
            self._dirty = False
            return
        glow = float(np.clip(cfg.get("paint_glow", 0.0), 0.0, 1.0))
        shading = float(np.clip(cfg.get("paint_shading", 0.0), 0.0, 1.0))

        d = self.dye
        if glow > 0.0:
            d = d + self._bloom(d) * np.float32(0.9 * glow)

        # 亮通道用 maximum 链而不是 d.max(axis=2)：后者对"最后一维只有 3 个"
        # 走的是通用 reduce，实测 0.86ms，(110,196) 上纯属浪费。
        lum = np.maximum(np.maximum(d[..., 0], d[..., 1]), d[..., 2])
        if shading > 0.0:
            # 用亮度的梯度给颜料加"厚度"：迎光面亮、背光面暗（参考的 SHADING）
            gy = np.zeros_like(lum)
            gx = np.zeros_like(lum)
            gy[1:-1, :] = (lum[2:, :] - lum[:-2, :]) * np.float32(0.5)
            gx[:, 1:-1] = (lum[:, 2:] - lum[:, :-2]) * np.float32(0.5)
            sh = np.clip(1.0 + shading * 7.0 * (gx + gy), 0.35, 1.9).astype(np.float32)
            d = d * sh[..., None]
            lum = lum * sh

        # ---- 只画"有颜料"的包围盒 ----
        # 颜料是稀疏的（鼠标划到哪才有），空闲时这里直接返回，整帧零成本。
        # 阈值 1e-3：打包时 a01*255 要四舍五入到 1 需要 a01 ≥ 0.002，
        # 低于阈值的格子渲染出来必然是 0，裁掉不会丢任何可见像素。
        # 留 2 格余量给双线性（目标格 g 会用源格 g-1）。
        rows = np.flatnonzero(lum.max(axis=1) > self._BBOX_EPS)
        if rows.size == 0:
            return
        cols = np.flatnonzero(lum.max(axis=0) > self._BBOX_EPS)
        gy0 = max(0, int(rows[0]) - 2)
        gy1 = min(self.gh, int(rows[-1]) + 3)
        gx0 = max(0, int(cols[0]) - 2)
        gx1 = min(self.gw, int(cols[-1]) + 3)

        f = self.up
        if f > 1:
            # 模拟网格 -> 显示网格（双线性）。两个轴各取两个抽样点按权值线性
            # 混合；**先 x 后 y** 的理由同下面的上屏放大（结果要保持 C 连续）。
            # 只对包围盒里的子块做 —— 这也让插值开销随颜料面积走。
            sy = slice(gy0 * f, gy1 * f)
            sx = slice(gx0 * f, gx1 * f)
            qh, qw = gy1 - gy0, gx1 - gx0
            i0y = np.clip(self._rsy[0][sy] - gy0, 0, qh - 1)
            i1y = np.clip(self._rsy[1][sy] - gy0, 0, qh - 1)
            wy = self._rsy[2][sy][:, None, None]
            i0x = np.clip(self._rsx[0][sx] - gx0, 0, qw - 1)
            i1x = np.clip(self._rsx[1][sx] - gx0, 0, qw - 1)
            wx = self._rsx[2][sx][None, :, None]
            sub = d[gy0:gy1, gx0:gx1]
            t = np.take(sub, i0x, axis=1)
            t += (np.take(sub, i1x, axis=1) - t) * wx
            p3 = np.take(t, i0y, axis=0)
            p3 += (np.take(t, i1y, axis=0) - p3) * wy
        else:
            p3 = d[gy0:gy1, gx0:gx1]

        # 亮通道直接由插值后的颜色取 max：着色是逐通道同一系数，所以
        # max(插值后的 d) 就是插值后的亮度，还顺带保证 rgb = d/lum ≤ 1。
        lum2 = np.maximum(np.maximum(p3[..., 0], p3[..., 1]), p3[..., 2])
        a01 = np.clip(lum2, 0.0, 1.0) * op
        rgb = p3 / np.maximum(lum2, np.float32(1e-5))[..., None]
        np.clip(rgb, 0.0, 1.0, out=rgb)
        sc = a01 * 255.0
        # +0.5 再 astype 截断 == rint，省掉 4 遍 rint
        ab = (sc + 0.5).astype(np.uint32)
        rb = (rgb[..., 0] * sc + 0.5).astype(np.uint32)
        gb = (rgb[..., 1] * sc + 0.5).astype(np.uint32)
        bb = (rgb[..., 2] * sc + 0.5).astype(np.uint32)
        small = _premul_pack(bb, gb, rb, ab)

        # ---- 显示网格 -> 屏幕（最近邻）----
        # 上屏放大：**必须先 x 后 y**。
        # numpy 的高级索引会把被索引的那个轴放到结果的最内层，于是
        #   `small[iy][:, ix]` -> 结果按 (W, H) 排布 = Fortran 连续，
        # 赋给 C 连续的 dst 会走"转置拷贝"慢路径（1080p 14.3ms）。
        # 换成先 x 后 y，结果就是 C 连续，赋值只要 1.1ms。
        dy0 = int(np.searchsorted(self._iy, gy0 * f, "left"))
        dy1 = int(np.searchsorted(self._iy, gy1 * f - 1, "right"))
        dx0 = int(np.searchsorted(self._ix, gx0 * f, "left"))
        dx1 = int(np.searchsorted(self._ix, gx1 * f - 1, "right"))
        if dy1 <= dy0 or dx1 <= dx0:
            return
        big = np.take(small, self._ix[dx0:dx1] - gx0 * f, axis=1)
        big = big[self._iy[dy0:dy1] - gy0 * f]
        dst = u32.reshape(h, w)[dy0:dy1, dx0:dx1]
        if cfg.get("spotlight_enabled", False):
            # 聚光灯是整屏图层，不能整片覆盖写 —— 只写真有颜料的像素
            np.copyto(dst, big, where=big != 0)
        else:
            dst[:] = big


class CursorTrailField:
    """光标拖尾：复用鼠标坐标（Scene 每帧 feed 当前位置），在环形缓冲里
    记录最近若干帧的落点，绘制成由近及远逐渐淡出的柔边圆点，形成"彗星尾"。

    与 SnowField 共用接口约定（set_amount/set_color/resize/update/draw），
    但多了 feed() —— 因为落点来自外部鼠标，需要 Scene 在 step() 里喂进来。
    所有运算 numpy 向量化，alpha 裁剪，不产生 NaN / Inf。
    """

    def __init__(self, w, h, seed=13):
        self.rng = np.random.default_rng(seed)
        self.w, self.h = w, h
        self._cur_speed = 0.0
        self._rgb = (95.0, 208.0, 255.0)    # 默认青蓝
        self.K = 4
        self._build_kernel(self.K)
        self.max_len = 24
        self._init_buffers(self.max_len)
        self._last_size = None

    def _build_kernel(self, K):
        self.K = max(1, int(round(K)))
        k = np.arange(-self.K, self.K + 1, dtype=np.float32)
        self._ox = k.astype(np.int32)
        self._ker = (np.clip(1.0 - np.abs(k) / (self.K + 0.5), 0.0, 1.0) ** 1.8)

    def _init_buffers(self, n):
        self.max_len = max(1, int(n))
        self.hx = np.zeros(self.max_len, np.float32)
        self.hy = np.zeros(self.max_len, np.float32)
        self.head = 0
        self.count = 0

    def resize(self, w, h):
        self.w, self.h = w, h

    def set_length(self, n):
        """拖尾采样点数；变化时重建缓冲并尽量保留较新样本。"""
        n = int(max(1, min(120, n)))
        if n == self.max_len:
            return
        old_x = self.hx
        old_y = self.hy
        old_head = self.head
        old_count = self.count
        old_cap = len(old_x)
        self._init_buffers(n)
        if old_count:
            take = min(old_count, n)
            src_start = (old_head - take) % old_cap
            for i in range(take):
                idx = (src_start + i) % old_cap
                self.hx[self.head] = old_x[idx]
                self.hy[self.head] = old_y[idx]
                self.head = (self.head + 1) % self.max_len
                self.count = min(self.count + 1, self.max_len)

    def set_size(self, s):
        s = float(s)
        if s == self._last_size:
            return
        self._last_size = s
        self._build_kernel(1.5 + s * 2.0)     # 标量大小 -> 核半径

    def feed(self, x, y, speed=0.0):
        """Scene 每帧调用：把当前光标落点压入环形缓冲。"""
        self._cur_speed = float(speed)
        self.hx[self.head] = float(x)
        self.hy[self.head] = float(y)
        self.head = (self.head + 1) % self.max_len
        if self.count < self.max_len:
            self.count += 1

    def update(self, dt, cfg):
        """拖尾淡出由样本"年龄"（环形索引顺序）体现，无需额外计时。
        dt 暂未使用，保留接口一致性。"""
        return

    def draw(self, u32, w, h, cfg):
        if self.count == 0:
            return
        op = np.float32(cfg.get("trail_opacity", 0.7))
        # 移动越快越亮；静止时仍保留约 40% 可见度
        fade = np.float32(min(1.0, self._cur_speed / 900.0 + 0.4))

        # 由旧到新取出样本
        idxs = ((self.head - self.count + np.arange(self.count)) % self.max_len)
        xs = self.hx[idxs]
        ys = self.hy[idxs]
        rec = np.linspace(0.0, 1.0, self.count, dtype=np.float32)  # 0 旧 -> 1 新

        a = op * rec * fade                                  # (count,)
        af = (a[:, None] * self._ker[None, :]).reshape(-1)   # (count*K2,)

        ix0 = np.rint(xs).astype(np.int32)
        iy0 = np.rint(ys).astype(np.int32)
        wx = (ix0[:, None] + self._ox[None, :]).reshape(-1)
        wy = (iy0[:, None] + self._ox[None, :]).reshape(-1)

        m = (wx >= 0) & (wx < w) & (wy >= 0) & (wy < h) & (af > 0.012)
        wx, wy, af = wx[m], wy[m], af[m]
        if wx.size == 0:
            return

        colr, colg, colb = self._rgb
        a_c = np.rint(af * 255.0).astype(np.uint32)
        packed = _premul_pack(
            np.rint(colb * af).astype(np.uint32),
            np.rint(colg * af).astype(np.uint32),
            np.rint(colr * af).astype(np.uint32), a_c)
        u32[wy * np.int32(w) + wx] = packed

    def set_color(self, hex_str):
        bgr = hex_to_bgr(hex_str)
        self._rgb = (float(bgr[2]), float(bgr[1]), float(bgr[0]))


# ------------------------------------------------------------------ 宠物跟随
# ------------------------------------------------------------------ 宠物跟随
def _pet_rot_rel(pts, ang):
    """把相对坐标点绕原点旋转 ang —— 用于尾巴摆动。

    放在模块级而不是 PetField 的 staticmethod 里：形象绘制函数是 static 的，
    拿不到 self，用模块级函数调用比 `PetField._xxx` 回指类名更直接。
    """
    ca, sa = math.cos(ang), math.sin(ang)
    return [(x * ca - y * sa, x * sa + y * ca) for x, y in pts]


def _pet_leg(base_pt, foot_pt, step, lift_dir=1.0):
    """按步幅算出"抬起的那条腿"的折线端点。

    抬起 = 脚往行进方向偏移、同时离地一点点。两条腿用相反的 lift_dir 传进来，
    就得到交替迈步的效果（后腿往前时前腿往后）。
    """
    bx, by = base_pt
    fx, fy = foot_pt
    lift = max(0.0, step * lift_dir)
    return [(bx, by), (fx + step * 0.062 * lift_dir, fy - lift * 0.050)]


class PetField:
    """宠物跟随：小猫 / 小狗沿着鼠标轨迹跟在后面跑。

    几处值得说的设计：

    * **帧序列预渲染**：卡通形象用 PIL 在 3× 画布上画好、LANCZOS 缩到目标尺寸，
      存成**预乘** RGBA 数组。走路 6 帧（尾巴摆动 + 前后腿交替）、静止 4 帧
      （只摆尾巴、腿站定），另加一张**裁剪过的闭眼覆盖层**做眨眼。
      帧序列而不是"每帧现算骨架"：之后每帧只做贴图，成本与形象复杂度无关；
      而宠物最多 10 只同屏，每只还现算一遍骨骼就太重了。
      画布取 `max(64, 显示尺寸)` 再缩下来 —— 小体型时降采样比直接画 24px 干净。
      形状一律由代码绘制，不引入图片资源，也就不牵扯第三方形象授权。

    * **跟随链**：第一只追鼠标，第二只追第一只……位移走**临界阻尼弹簧**
      （阻尼 c = 2√k）。不用"每帧向目标靠固定比例"的 lerp —— 那种写法的手感
      随帧率变（同一个 follow 值，120 帧的机器跟得明显更紧），而弹簧的解析解
      只依赖 dt，换帧率手感一致。

    * **朝向迟滞**：按水平速度左右翻转，带速度阈值 + 需连续保持一小段时间。
      没有迟滞的话，鼠标原地画小圈会让宠物每帧左右横跳。

    * **静止渐隐 + 现身门槛**：光标停住一小会儿就淡出；而重新出现要等光标
      **真的走了一段**（`pet_min_travel`，量的是净位移）。两处都是为了同一件事：
      宠物不该"摆好姿势等人来" —— 鼠标轻轻一碰就蹦出一队站在原地不动的小猫
      小狗，比不显示更突兀。

    * **沿轨迹现身**：满足门槛时沿**刚走过的那段路径**把队伍铺开，而不是在光标
      身后排成一条直线。它们一出现就已经在轨迹上、立刻顺着轨迹追上来，观感是
      "一直跟在后面、刚显形"，而不是凭空列队站好。路径按**弧长**采样（每 12px
      一个点）：快速划动时一帧能跨几十像素，按帧记会漏掉中间的转折，队伍就会
      切直线穿过拐角。

    * **点击不唤醒**：按钮/右键点一下只是刷新"静止计时"，不会让宠物现身 ——
      光标没动，现身了也只能杵在原地，正是上面要避免的那种状态。

    与其它特效共用 resize/update/draw 的接口约定，另加 `set_pointer()` ——
    目标点与点击事件来自外部鼠标，由 Scene 每帧喂进来。
    """

    KINDS = ("小猫", "小狗")
    MAX_PETS = 10

    # 触发转向的最小水平速度与需保持的时间。阈值给得偏高：宠物慢慢挪动时
    # 应该保持原朝向"侧着身子走"，而不是跟着微小抖动转身。
    _FLIP_SPEED = 55.0          # 像素/秒
    _FLIP_HOLD = 0.07           # 秒
    _SPRITE_MIN = 64            # 精灵分辨率下限
    _SS = 3                     # 精灵超采样倍数
    _WALK_FRAMES = 6            # 走路循环帧数（尾巴摆动 + 前后腿交替）
    _IDLE_FRAMES = 4            # 静止循环帧数（只摆尾巴）
    _STEP_SIZE = 8              # 精灵尺寸按此量化后再缓存（拖滑块时少重建几次）

    # 静止多久开始渐隐 / 淡入淡出各用多久（秒）
    _FADE_IN = 0.26
    _FADE_OUT = 0.42
    # 走路动画的判定速度：低于它就走"静止帧"（腿站定、只摆尾）
    _WALK_SPEED = 26.0

    _BLINK_TIME = 0.11          # 单次眨眼时长
    _BLINK_GAP = (2.2, 6.0)     # 两次眨眼的间隔范围（随机）

    # 光标路径采样（供"沿轨迹现身"用）：按**弧长**每 `_TRAIL_STEP` 像素记一个点。
    # 按帧记会漏掉快速划动时一帧跨过的几十像素，队伍就会切直线穿过拐角。
    _TRAIL_STEP = 12.0
    _TRAIL_MAX = 60             # 最多保留 60 点（约 720 像素的路径）
    _SEAT_GAP = 1.15            # 现身时把间距拉开一点，留出追赶的余地

    def __init__(self, w, h, seed=53):
        self.rng = np.random.default_rng(seed)
        self.w, self.h = w, h
        self._mx = w * 0.5
        self._my = h * 0.5
        # 跟随链状态：位置 / 速度 / 朝向 / 转向候选已保持的时长
        self.px = np.zeros(self.MAX_PETS, np.float32)
        self.py = np.zeros(self.MAX_PETS, np.float32)
        self.vx = np.zeros(self.MAX_PETS, np.float32)
        self.vy = np.zeros(self.MAX_PETS, np.float32)
        self._face = np.ones(self.MAX_PETS, np.float32)     # +1 朝右 / -1 朝左
        self._hold = np.zeros(self.MAX_PETS, np.float32)
        # 每只的走路相位错开一点，否则 10 只会像列队操一样整齐划一
        self._gait_off = self.rng.uniform(0.0, 6.283, self.MAX_PETS)
        # 眨眼：剩余时长 + 距离下次眨眼的倒计时
        self._blink_t = np.zeros(self.MAX_PETS, np.float32)
        self._blink_next = self.rng.uniform(self._BLINK_GAP[0],
                                            self._BLINK_GAP[1], self.MAX_PETS)
        self._n = 0                 # 当前宠物数
        self._placed = False        # 已铺开；未满足现身门槛时恒为 False
        # 现身门槛：量的是**净位移**（从锚点起算），不是累计弧长 —— 鼠标原地
        # 来回抖动能刷出可观的累计弧长，但那显然是"没在走"，不该让宠物蹦出来。
        self._anchor_x = self._mx
        self._anchor_y = self._my
        self._travel = 0.0
        self._trail = []            # 光标最近走出的路径（按弧长采样）
        # 队伍的"身后方向"（单位向量）。**必须是连续的状态量**：停下来时
        # "身后"左右各有一个等距解，只按当场几何判断会让某只挑错一侧，停成
        # 与前一只重叠（实测体型 80 时第 2 只正好叠在第 0 只上）。
        self._dir_x = -1.0
        self._dir_y = 0.0
        self._phase = 0.0           # 步行相位（驱动帧序号与上下浮动）
        self._speed = 0.0
        # 静止渐隐
        self._idle = 0.0            # 光标静止了多久
        self._fade = 1.0            # 当前不透明度倍率
        self._hidden = False        # 已完全隐去（此时冻结物理，等唤醒重新落位）
        # 输入：由 set_pointer 每帧置位，update 消费后清掉
        self._moved = False
        self._clicked = 0
        # 预渲染资源
        self._frames_walk = None
        self._frames_idle = None
        self._blink = None          # (img, dx, dy) 裁剪后的闭眼覆盖层
        self._sprite_key = None
        self._failed = False        # 精灵构建失败过就不要再每帧重试

    # ------------------------------------------------------------ 外部接口
    def set_pointer(self, x, y, clicked=0):
        """Scene 每帧喂当前光标位置与点击事件（覆盖层坐标系）。

        `clicked`：1=左键 2=右键 0=无。位移与点击都在这里记成"待消费"标志，
        由 `update()` 统一处理 —— 一帧里指针可能被喂多次（Scene 里聚光灯 /
        颜料盘 / 宠物各要一次），不能在喂的时候直接改状态。

        点击**不能**让宠物现身（现身只看移动）：光标没动，露面了也只能杵在
        原地，那恰恰是本类要避免的观感。点击只用来刷新"静止计时"，让已经跟
        上来的一队别因为点了一下就提前散场。
        """
        x = float(x)
        y = float(y)
        # 位移阈值 1px：DPI 缩放下光标可能报出亚像素抖动，不该被当成"在动"
        if math.hypot(x - self._mx, y - self._my) > 1.0:
            self._moved = True
            self._push_trail(x, y)
        self._mx, self._my = x, y
        # 净位移（相对本轮锚点）：每次喂指针刷新，门槛在 update 里判
        self._travel = math.hypot(x - self._anchor_x, y - self._anchor_y)
        if clicked:
            self._clicked = int(clicked)

    def _push_trail(self, x, y):
        """把光标路径按**弧长**记下来（每 `_TRAIL_STEP` 像素一个点）。

        首次采样要把起步点一起补上，否则轨迹从第二个采样点才开始，起步那
        一小段就丢了 —— 沿轨迹铺开时最靠后的几只会被外推到错的位置。
        """
        t = self._trail
        if not t:
            t.append((self._mx, self._my))
        lx, ly = t[-1]
        if math.hypot(x - lx, y - ly) >= self._TRAIL_STEP:
            t.append((x, y))
            if len(t) > self._TRAIL_MAX:
                del t[0]

    def _back_point(self, dist):
        """从光标沿**刚走过的路径**往回 `dist` 像素处的坐标。

        路径总长不够时沿末端切线继续往外推：10 只 × 间距 46 需要四百多像素
        的路径，而刚够现身门槛时只有一小段，直接放弃会让后面几只叠在一起。
        连方向都没有（路径不足两点）才退化成水平向左。
        """
        cx, cy = self._mx, self._my
        acc = 0.0
        qx, qy = cx, cy
        for tx, ty in reversed(self._trail):
            seg = math.hypot(tx - qx, ty - qy)
            if seg > 1e-6 and acc + seg >= dist:
                t = (dist - acc) / seg
                return qx + (tx - qx) * t, qy + (ty - qy) * t
            acc += seg
            qx, qy = tx, ty
        extra = dist - acc
        if len(self._trail) >= 2:
            (ax, ay), (bx, by) = self._trail[0], self._trail[1]
            ux, uy = bx - ax, by - ay
            m = math.hypot(ux, uy)
            if m > 1e-6:
                return qx - ux / m * extra, qy - uy / m * extra
        return qx - extra, qy

    def resize(self, w, h):
        self.w, self.h = w, h

    def reset(self):
        """关掉特效时清场：下次打开重新从光标处出现，而不是从上次的位置跑来。

        门槛的锚点也跟着挪到当前光标：关着的时候光标可能已经跑了大半屏，
        不清的话一打开就立刻满足门槛、宠物凭空冒出来。
        """
        self._placed = False
        self._n = 0
        self._anchor_x, self._anchor_y = self._mx, self._my
        self._travel = 0.0
        self._trail.clear()
        self._dir_x, self._dir_y = -1.0, 0.0
        self.vx[:] = 0.0
        self.vy[:] = 0.0
        self._hold[:] = 0.0
        self._blink_t[:] = 0.0
        self._fade = 1.0
        self._hidden = False
        self._idle = 0.0
        self._clicked = 0

    def active(self):
        """这一帧有没有宠物要画（给"空层跳过"用的保守信号）。

        完全隐去时返回 False，让鼠标层这一帧可以被跳过 —— 这是"静止就消失"
        顺带省下来的开销，也是它必须准确的原因：报 False 报错了，隐身中的
        宠物会被冻在屏幕上再也回不来。
        """
        return bool(self._placed and self._n > 0
                    and self._frames_walk is not None
                    and self._fade > 0.01)

    # ------------------------------------------------------------ 每帧
    def update(self, dt, cfg):
        if self._failed:
            return
        # 显式欧拉推进弹簧：dt 过大时数值会发散。Scene 传进来的已经夹过一次，
        # 这里再夹一次，保证本类被单独调用时也稳。
        dt = min(max(float(dt), 0.0), 0.05)
        n = int(np.clip(round(float(cfg.get("pet_count", 2.0))), 1,
                        self.MAX_PETS))
        size = float(np.clip(float(cfg.get("pet_size", 56.0)), 16.0, 160.0))
        follow = float(np.clip(float(cfg.get("pet_follow", 0.55)), 0.0, 1.0))
        spacing = size * 0.82       # 队列间距跟着体型走
        # 0 = 不消失（一直跟着）
        idle_fade = max(0.0, float(cfg.get("pet_idle_fade", 1.6)))
        # 现身门槛（像素净位移，0 = 一有移动就现身）
        min_travel = max(0.0, float(cfg.get("pet_min_travel", 90.0)))

        try:
            self._ensure_sprite(cfg)
        except Exception:
            # 只试一次：失败原因会由渲染线程的保护层记进 error.log，但反复抛
            # 会耗光"连续失败"配额把整个渲染线程熔断掉 —— 一个特效画不出来
            # 不该让雨也停。
            self._failed = True
            raise

        moved = self._moved
        self._moved = False
        clicked = self._clicked
        self._clicked = 0

        # ---------------- 现身门槛
        # 达到门槛就重置锚点（下一轮重新累计）再放行。门槛量的是**净位移**：
        # 原地来回抖动刷出的累计弧长很可观，但那显然是"没在走"，不该让一队
        # 宠物蹦出来。
        # `> 0` 这一半不能省：门槛为 0（"一有移动就现身"）时，光判"数值达标"
        # 会让光标**根本没动**也算过 —— 渐隐完成后同一帧就重新达标、宠物接着
        # 又现身，于是动画在"淡出→淡入"之间无限循环地闪。
        if self._travel > 0.0 and self._travel >= min_travel:
            self._anchor_x, self._anchor_y = self._mx, self._my
            self._travel = 0.0
            armed = True
        else:
            armed = False

        # ---------------- 静止计时
        # 点击只刷新计时：光标没动时点一下不该让宠物现身（现身了也只能杵在
        # 原地），但已经跟上来的一队也不该因为点了一下就提前散场。
        if moved or clicked:
            self._idle = 0.0
        else:
            self._idle += dt

        if not self._placed:
            # 还没露面：得先真的走起来。铺开时沿**刚走过的那段路径**，它们
            # 一出现就已经在轨迹上、立刻顺着轨迹追上来，观感是"一直跟在后面、
            # 刚显形"，而不是在光标旁边列队站好。
            if not armed:
                # 没露面就别留着上次的速度，否则一现身就带着旧惯性窜出去
                self._speed = 0.0
                return
            self._hidden = False
            self._seat_on_trail(n, spacing * self._SEAT_GAP)
            self._n = n
            self._placed = True
            self._fade = 0.0
            self._idle = 0.0
        elif n != self._n:
            # 改数量：按原间距重铺。鼠标正在快速划动时这里有一次瞬移，但换来
            # 的是不用为"新来的从队尾走进来"再养一套状态。
            self._seat_on_trail(n, spacing)
            self._n = n

        # ---------------- 渐隐 / 淡入
        want = 0.0 if (idle_fade > 0.0 and self._idle >= idle_fade) else 1.0
        if self._fade < want:
            self._fade = min(want, self._fade + dt / self._FADE_IN)
        elif self._fade > want:
            self._fade = max(want, self._fade - dt / self._FADE_OUT)
        if want == 0.0 and self._fade <= 0.01:
            # 完全隐去：退回"没露面"并重置锚点 —— 下次得再真的走一段才重新
            # 现身。这正是"不要显示一队不动的宠物在原地等"的另一半。
            self._hidden = True
            self._placed = False
            self._n = 0
            self._anchor_x, self._anchor_y = self._mx, self._my
            self._travel = 0.0
            self._speed = 0.0
            return

        # 隐去期间不推进物理（重新现身时会沿轨迹重铺）
        if self._hidden:
            return

        # 队伍方向：由领头那只更新；它慢下来就**保留最后一次方向**（见 `_dir`
        # 的说明 —— 停下来之后方向不能重新变回"当场几何"那种有歧义的判法）。
        v0 = math.hypot(float(self.vx[0]), float(self.vy[0]))
        if v0 > self._WALK_SPEED:
            self._dir_x = -float(self.vx[0]) / v0
            self._dir_y = -float(self.vy[0]) / v0

        # 刚度：follow=0 时松垮（慢悠悠），follow=1 时紧咬不放。
        # 阻尼取临界值 c = 2√k 并略微过阻尼（×1.03）—— 欠阻尼的宠物会在
        # 光标处来回荡，而过阻尼只是"跟得钝一点"，观感上更像活物。
        k = 14.0 + 190.0 * follow
        c = 2.0 * np.sqrt(k) * 1.03

        for i in range(n):
            if i == 0:
                gx, gy = self._mx, self._my
            else:
                # 目标 = 前一只**身后** spacing 处。用"身后一点"而不是"直接追
                # 前一只的位置"：后者会让整队挤成一坨，因为每只都想贴到同一个点。
                #
                # 身后方向优先取**前一只的运动方向**，只在它几乎不动时才退回
                # "两者连线"。连线方向在这里有个正反馈陷阱：光标掉头时某只可能
                # 正处于前一只的前方，此时连线方向恰好反转，会把"落后"误判成
                # "超前"、反而把它推得更远 —— 队伍就此倒序排开，而且倒序排列
                # 本身也是方程的稳定点，再也回不来。
                ax, ay = float(self.px[i - 1]), float(self.py[i - 1])
                avx, avy = float(self.vx[i - 1]), float(self.vy[i - 1])
                aspd = math.hypot(avx, avy)
                if aspd > self._WALK_SPEED:
                    # 前一只在动：身后 = 它的运动反向。取**局部**方向而不是全局
                    # 队伍方向，队伍才会顺着走过的路径蜿蜒。
                    bx, by = -avx / aspd, -avy / aspd
                else:
                    # 它停下来了：沿用连续状态量 `_dir`。**不能**退回"两者连线"
                    # —— 静止时"身后"左右各有一个等距解，挑错一侧就会停成与
                    # 前一只重叠（实测体型 80 时第 2 只正好叠在第 0 只上，
                    # 而且那个重叠位也是方程的稳定点，进去就出不来）。
                    bx, by = self._dir_x, self._dir_y
                gx = ax + bx * spacing
                gy = ay + by * spacing
            fx = k * (gx - float(self.px[i]))
            fy = k * (gy - float(self.py[i]))
            self.vx[i] += (fx - c * float(self.vx[i])) * dt
            self.vy[i] += (fy - c * float(self.vy[i])) * dt
            self.px[i] += self.vx[i] * dt
            self.py[i] += self.vy[i] * dt

        # 朝向：过阈值且连续保持一小段时间才真的翻
        for i in range(n):
            want = float(self._face[i])
            vx = float(self.vx[i])
            if vx > self._FLIP_SPEED:
                want = 1.0
            elif vx < -self._FLIP_SPEED:
                want = -1.0
            if want != float(self._face[i]):
                self._hold[i] += dt
                if self._hold[i] >= self._FLIP_HOLD:
                    self._face[i] = want
                    self._hold[i] = 0.0
            else:
                self._hold[i] = 0.0

        # ---------------- 眨眼：每只各走各的节奏
        for i in range(n):
            if self._blink_t[i] > 0.0:
                self._blink_t[i] -= dt
            else:
                self._blink_next[i] -= dt
                if self._blink_next[i] <= 0.0:
                    self._blink_t[i] = self._BLINK_TIME
                    self._blink_next[i] = float(self.rng.uniform(
                        self._BLINK_GAP[0], self._BLINK_GAP[1]))

        # 步行相位：走得快摆得快，同时留一个"慢悠悠踱步"的下限
        spd = (float(self.vx[0]) ** 2 + float(self.vy[0]) ** 2) ** 0.5
        self._speed = spd
        self._phase += dt * (2.4 + spd / 70.0)

    # ------------------------------------------------------------ 动画选帧
    def _frame_index(self, i, nf):
        """第 i 只宠物当前该用第几帧。

        每只带一个固定的相位偏移（`_gait_off`），否则 10 只会像列队操一样
        整齐划一地摆尾巴 —— 一眼就能看出是同一张图在动。
        """
        ph = self._phase * 1.15 + float(self._gait_off[i])
        return int(ph) % nf

    def _seat_on_trail(self, n, spacing):
        """把 n 只宠物沿刚走过的路径铺在光标身后（满足门槛现身 / 改数量时用）。

        沿轨迹铺而不是在光标身后拉一条直线：一队宠物显形时应该像是"一直跟在
        后面、刚被看见"，而不是凭空在光标旁边列队站好。朝向按**队伍排列方向**
        定（宠物看着前面那只），而不是一律朝右。
        """
        self.vx[:n] = 0.0
        self.vy[:n] = 0.0
        self._hold[:n] = 0.0
        for i in range(n):
            x, y = self._back_point(spacing * i)
            self.px[i] = x
            self.py[i] = y
        # 队伍方向由**铺开结果**反推：现身那一瞬间速度还是 0，等它涨起来
        # （几帧）之前都得靠这个值，否则队尾会挑错"身后"的一侧。
        if n >= 2:
            dx = float(self.px[1] - self.px[0])
            dy = float(self.py[1] - self.py[0])
            m = math.hypot(dx, dy)
            if m > 1e-6:
                self._dir_x, self._dir_y = dx / m, dy / m
        # 朝向按队伍整体方向定：**队内保持一致**。逐只按"与前一只的横向差"判
        # 会在曲线拐弯处让队内互相打架（同一队里一半朝左一半朝右，很像坏了）；
        # 横向差太小时保持原朝向，免得竖直方向的挪动把整队翻来翻去。
        dx = self._mx - float(self.px[0])
        if abs(dx) > 6.0:
            self._face[:n] = -1.0 if dx < 0.0 else 1.0

    def draw(self, u32, w, h, cfg):
        if not self.active():
            return
        op = (float(np.clip(float(cfg.get("pet_opacity", 1.0)), 0.0, 1.0))
              * self._fade)
        if op <= 0.004:
            return
        moving = self._speed > self._WALK_SPEED
        frames = self._frames_walk if moving else self._frames_idle
        nf = frames.shape[0]
        S = frames.shape[1]
        # 完全平移的宠物看着像贴纸；加一点上下浮动，幅度随速度增长
        amp = S * 0.030
        gait = 0.30 + 0.70 * min(1.0, self._speed / 260.0)

        for i in range(self._n):
            cx = float(self.px[i])
            cy = float(self.py[i]) + float(np.sin(
                self._phase + float(self._gait_off[i]))) * amp * gait
            flip = self._face[i] < 0.0
            img = frames[self._frame_index(i, nf)]
            if flip:
                img = img[:, ::-1]
            self._blit(u32, w, h, img, cx, cy, op)

            # 眨眼：把裁剪过的闭眼覆盖层贴回**同一位置**。
            # 覆盖层按精灵坐标裁剪过，所以翻转时它的横向偏移也要跟着镜像。
            if self._blink_t[i] > 0.0 and self._blink is not None:
                bimg, bdx, bdy = self._blink
                if flip:
                    bdx = S - bdx - bimg.shape[1]
                self._blit(u32, w, h, bimg,
                           cx - S * 0.5 + bdx + bimg.shape[1] * 0.5,
                           cy - S * 0.5 + bdy + bimg.shape[0] * 0.5,
                           op, center=True)

    def _blit(self, u32, w, h, img, cx, cy, op, center=True):
        """把预乘 RGBA 精灵**叠加**到缓冲上（默认以 (cx, cy) 为中心）。

        混合本身走模块级的 `_over_blend`（宠物与点击特效共用同一份实现）。
        这里只负责"把精灵对到屏幕坐标 + 裁掉出屏部分"。

        `center=False` 时 (cx, cy) 是左上角 —— 闭眼覆盖层已经裁剪过，用左上角
        定位比换算中心更直接。
        """
        S = img.shape[0]
        x0 = int(round(cx - S * 0.5)) if center else int(round(cx))
        y0 = int(round(cy - S * 0.5)) if center else int(round(cy))
        # 与屏幕求交（宠物可以走出屏幕边缘，裁掉即可）
        gx0, gy0 = max(0, x0), max(0, y0)
        gx1, gy1 = min(int(w), x0 + S), min(int(h), y0 + S)
        if gx1 <= gx0 or gy1 <= gy0:
            return
        sub = img[gy0 - y0:gy1 - y0, gx0 - x0:gx1 - x0]
        a = sub[:, :, 3] * op
        if not (a > 0.004).any():
            return
        dst = u32.reshape(int(h), int(w))[gy0:gy1, gx0:gx1]
        # 精灵已是预乘色（rgb 内含 alpha），乘 op 就是"整体调不透明度"
        _over_blend(dst, sub, a, 255.0 * op)

    # ------------------------------------------------------------ 精灵
    def _ensure_sprite(self, cfg):
        """按 (种类, 颜色, 尺寸) 重建整套预渲染资源；三者任一变化才重建。

        尺寸**先量化到 `_STEP_SIZE` 的整数倍**再当缓存键：面板滑块是 2px 一档，
        直接拿精确尺寸当键的话，拖动滑块会触发几十次全量重建（每次要画 10 帧
        + 覆盖层 + 4 个表情），拖动就会卡。量化后最多十几次，而 8px 的尺寸差
        在观感上看不出来。
        """
        if _PImage is None:
            raise RuntimeError("宠物跟随需要 Pillow 来绘制卡通形象，"
                               "当前环境没有可用的 PIL")
        kind = str(cfg.get("pet_kind", self.KINDS[0]))
        if kind not in self.KINDS:
            kind = self.KINDS[0]
        color = str(cfg.get("pet_color", "#F2C27B"))
        raw = int(np.clip(round(float(cfg.get("pet_size", 56.0))), 16, 160))
        size = max(self._SPRITE_MIN,
                   int(round(raw / self._STEP_SIZE)) * self._STEP_SIZE)
        key = (kind, color, size)
        if key == self._sprite_key:
            return
        self._frames_walk = self._build_frames(kind, color, size, walk=True)
        self._frames_idle = self._build_frames(kind, color, size, walk=False)
        self._blink = self._build_blink(kind, color, size)
        self._sprite_key = key

    def _build_frames(self, kind, color_hex, size, walk):
        """生成一组动画帧，返回 (F, size, size, 4) 的预乘 RGBA 数组。

        走路帧：尾巴大幅摆动 + 前后腿交替抬起；静止帧：尾巴轻轻晃、腿站定。
        帧数刻意取得少（6 / 4）—— 每帧都是一张完整位图，帧数直接乘进内存与
        重建耗时；而宠物在屏幕上只有几十像素，4~6 帧的循环已经足够连贯。
        """
        f = self._WALK_FRAMES if walk else self._IDLE_FRAMES
        out = []
        for k in range(f):
            ang = 2.0 * math.pi * k / f
            if walk:
                anim = dict(tail=0.46 * math.sin(ang + 0.6),
                            step=math.sin(ang),
                            ear=0.14 * math.sin(2.0 * ang))
            else:
                # 静止时腿不动，只让尾巴慢摆、偶尔抖一下耳朵
                anim = dict(tail=0.22 * math.sin(ang),
                            step=0.0,
                            ear=0.26 * math.sin(2.0 * ang))
            out.append(self._build_sprite(kind, color_hex, size, anim))
        return np.stack(out, axis=0)

    def _build_blink(self, kind, color_hex, size):
        """生成闭眼覆盖层并**裁剪到眼部包围盒**，返回 (img, dx, dy)。

        裁剪是必要的：不裁的话每次眨眼都要在一整张 size×size 上做一遍 over
        混合，成本与贴整只宠物相同；裁完只剩眼睛那一条，开销可以忽略。
        """
        S = max(self._SPRITE_MIN, int(size))
        W = S * self._SS
        img = _PImage.new("RGBA", (W, W), (0, 0, 0, 0))
        dr = _PDraw.Draw(img)
        bgr = hex_to_bgr(color_hex)
        base = (int(bgr[2]), int(bgr[1]), int(bgr[0]))
        dark = tuple(max(0, int(v * 0.34)) for v in base) + (255,)
        lw = max(1, int(round(W * 0.013)))

        # 与形象绘制里同一组眼位（改了那边记得同步这里）
        eyes = ((0.582, 0.376), (0.778, 0.370)) if kind == "小猫" \
            else ((0.596, 0.380), (0.762, 0.373))
        for ex, ey in eyes:
            dr.line([(ex - 0.046) * W, ey * W, ex * W, (ey + 0.012) * W,
                     (ex + 0.046) * W, ey * W],
                    fill=dark, width=max(1, int(lw * 1.5)), joint="curve")

        arr = img.resize((int(size), int(size)), _LANCZOS)
        a = np.asarray(arr).astype(np.float32)[..., 3]
        ys, xs = np.nonzero(a > 0.02)
        if xs.size == 0:
            return None
        dy0, dy1 = int(ys.min()), int(ys.max()) + 1
        dx0, dx1 = int(xs.min()), int(xs.max()) + 1
        crop = np.asarray(arr.crop((dx0, dy0, dx1, dy1))).astype(np.float32)
        crop *= (1.0 / 255.0)
        crop[..., :3] *= crop[..., 3:4]         # 直通 alpha -> 预乘
        return np.ascontiguousarray(crop), dx0, dy0

    def _build_sprite(self, kind, color_hex, size, anim=None):
        """把卡通形象画进 (size, size, 4) 的**预乘** RGBA 数组。

        `anim` 提供 tail / step / ear 三个动画量（尾巴摆角、步幅、耳朵抽动），
        缺省即中立姿势 —— 单帧入口（`_shot_pet.py` / `_ascii_pet.py` 仍在用）
        不必关心动画参数。
        """
        anim = anim or {"tail": 0.0, "step": 0.0, "ear": 0.0}
        S = max(self._SPRITE_MIN, int(size))
        W = S * self._SS
        img = _PImage.new("RGBA", (W, W), (0, 0, 0, 0))
        dr = _PDraw.Draw(img)

        bgr = hex_to_bgr(color_hex)
        base = (int(bgr[2]), int(bgr[1]), int(bgr[0]))
        body = base + (255,)
        dark = tuple(max(0, int(v * 0.34)) for v in base) + (255,)
        mid = tuple(int(v * 0.68) for v in base) + (255,)
        light = tuple(int(v + (255 - v) * 0.62) for v in base) + (255,)
        white = (255, 255, 255, 255)
        lw = max(1, int(round(W * 0.013)))

        def ell(cx, cy, rx, ry, fill, outline=None, wd=0):
            dr.ellipse([(cx - rx) * W, (cy - ry) * W,
                        (cx + rx) * W, (cy + ry) * W],
                       fill=fill, outline=outline, width=wd)

        def cir(cx, cy, r, fill, outline=None, wd=0):
            ell(cx, cy, r, r, fill, outline, wd)

        def poly(pts, fill):
            dr.polygon([(x * W, y * W) for x, y in pts], fill=fill)

        def line(pts, fill, wd):
            dr.line([(x * W, y * W) for x, y in pts], fill=fill,
                    width=max(1, int(wd)), joint="curve")

        args = (ell, cir, poly, line, body, dark, mid, light, white, lw, anim)
        if kind == "小狗":
            self._draw_dog(*args)
        else:
            self._draw_cat(*args)

        img = img.resize((int(size), int(size)), _LANCZOS)
        arr = np.asarray(img).astype(np.float32) * (1.0 / 255.0)
        arr[..., :3] *= arr[..., 3:4]           # 直通 alpha -> 预乘
        return np.ascontiguousarray(arr)

    @staticmethod
    def _draw_cat(ell, cir, poly, line, body, dark, mid, light, white, lw,
                  anim):
        """小猫（侧面朝右，Q 版大头小身）。

        画序 = 从后往前（尾巴 -> 后腿 -> 身体 -> 前腿 -> 耳 -> 头 -> 五官），
        后画的自然压住先画的，正好就是遮挡关系。坐标归一化到 0~1。
        耳朵的**底边落在头圆内部**，靠这一点保证耳根与头连成一体而不是飘着。

        不画胡须：96px 的精灵上它只有 1px 宽，LANCZOS 缩完糊成一片灰，
        反倒显脏；猫的特征交给耳形与三角鼻去表达。

        动画：尾巴绕根部旋转 `anim["tail"]` 弧度；前后腿按 `anim["step"]`
        交替抬起；耳朵尖随 `anim["ear"]` 轻微抽动。
        """
        tail = float(anim.get("tail", 0.0))
        step = float(anim.get("step", 0.0))
        ear = float(anim.get("ear", 0.0))

        # 尾巴：绕根部旋转，摆动幅度由动画量给
        pivot = (0.24, 0.70)
        rel = [(-0.05, 0.06), (-0.13, -0.04), (-0.16, -0.19), (-0.08, -0.31)]
        pts = [(pivot[0] + x, pivot[1] + y) for x, y in _pet_rot_rel(rel, tail)]
        line(pts, mid, lw * 3.0)

        # 后腿 / 前腿：交替迈步
        line(_pet_leg((0.26, 0.82), (0.23, 0.92), step, 1.0), mid, lw * 3.4)
        ell(0.40, 0.68, 0.30, 0.19, body, dark, lw)                # 身体
        line(_pet_leg((0.58, 0.82), (0.60, 0.92), step, -1.0),
             body, lw * 3.4)
        ell(0.52, 0.74, 0.115, 0.075, light)                       # 胸口
        # 耳朵（底边在头内，头会压住耳根）；tip 随 ear 微动
        poly([(0.50, 0.30), (0.52 + ear * 0.03, 0.05 - ear * 0.02),
              (0.68, 0.20)], body)
        poly([(0.72, 0.20), (0.88 - ear * 0.03, 0.05 - ear * 0.02),
              (0.90, 0.30)], body)
        poly([(0.545, 0.265), (0.557 + ear * 0.02, 0.115 - ear * 0.02),
              (0.655, 0.205)], light)
        poly([(0.752, 0.207), (0.845 - ear * 0.02, 0.115 - ear * 0.02),
              (0.858, 0.272)], light)
        cir(0.68, 0.40, 0.24, body, dark, lw)                      # 头
        ell(0.582, 0.376, 0.047, 0.058, dark)                      # 眼
        ell(0.778, 0.370, 0.047, 0.058, dark)
        cir(0.594, 0.359, 0.020, white)                            # 高光
        cir(0.790, 0.353, 0.020, white)
        poly([(0.652, 0.470), (0.708, 0.470), (0.680, 0.503)], dark)   # 鼻
        line([(0.680, 0.501), (0.645, 0.533)], mid, lw)            # 嘴
        line([(0.680, 0.501), (0.715, 0.533)], mid, lw)

    @staticmethod
    def _draw_dog(ell, cir, poly, line, body, dark, mid, light, white, lw,
                  anim):
        """小狗（侧面朝右，垂耳 + 突出的口鼻）。动画量含义同小猫。"""
        tail = float(anim.get("tail", 0.0))
        step = float(anim.get("step", 0.0))
        ear = float(anim.get("ear", 0.0))

        pivot = (0.22, 0.68)
        rel = [(-0.08, -0.02), (-0.15, -0.13), (-0.12, -0.25)]
        pts = [(pivot[0] + x, pivot[1] + y) for x, y in _pet_rot_rel(rel, tail)]
        line(pts, mid, lw * 3.0)                                   # 尾

        line(_pet_leg((0.26, 0.83), (0.23, 0.93), step, 1.0), mid, lw * 3.4)
        ell(0.40, 0.69, 0.30, 0.185, body, dark, lw)                      # 身体
        line(_pet_leg((0.58, 0.83), (0.60, 0.93), step, -1.0),
             body, lw * 3.4)
        ell(0.52, 0.75, 0.115, 0.075, light)                              # 胸口
        # 垂耳同样先画：头压住耳根，只让两侧垂下来；下缘随 ear 摆动
        ell(0.520, 0.500 + ear * 0.022, 0.085, 0.190, mid, dark, lw)
        ell(0.860, 0.480 - ear * 0.022, 0.085, 0.190, mid, dark, lw)
        cir(0.68, 0.40, 0.235, body, dark, lw)                            # 头
        ell(0.760, 0.505, 0.116, 0.090, light)                            # 口鼻
        ell(0.808, 0.472, 0.050, 0.038, dark)                             # 鼻头
        ell(0.596, 0.380, 0.049, 0.059, dark)                             # 眼
        ell(0.762, 0.373, 0.049, 0.059, dark)
        cir(0.608, 0.362, 0.021, white)
        cir(0.774, 0.355, 0.021, white)
        line([(0.808, 0.510), (0.768, 0.548)], mid, lw)                   # 嘴
        line([(0.808, 0.510), (0.848, 0.548)], mid, lw)


# ------------------------------------------------------------------ 点击特效
# 图案精灵的进程级缓存。
# 键 = (图案, 颜色, 尺寸, 旋转步) -> 预乘 RGBA 数组。
# 放在模块级而不是实例里：两个点击特效（表情 / 文字里的装饰）会用到同几种
# 图案，各存一份纯属浪费；而且图案只取决于配置，与是哪个特效实例无关。
_SHAPE_CACHE = {}
_SHAPE_CACHE_MAX = 400          # 超了就整体清空（配置驱动，重建很便宜）

# 彩带用一整套调色板而不是单色 —— 单色彩带看着像一堆纸片，多彩才有"彩带"感。
_CONFETTI_COLORS = ("#FF5D73", "#FFC145", "#4ADE80", "#38BDF8", "#A78BFA",
                    "#FB7185", "#FDE68A", "#5EEAD4")

# 「彩色」是一个**哨兵值**而不是颜色：选中它表示"逐粒子从调色板取色"，
# 于是任何图案都能做多色（不只是彩带）。它不是合法 hex，所以下游必须先
# 判断它、不能直接丢给 `hex_to_bgr`（那会静默回退成浅蓝）。
COLOR_RAINBOW = "彩色"

_SHAPE_NAMES = ("爱心", "星星", "彩带", "雪花", "气泡", "火焰", "花瓣", "音符")

# 每个图案的惯用色。选图案时面板会自动套上对应的一条（见 fx_ui 的图案选择），
# 省得每次都要自己去配颜色。彩带默认就是彩色 —— 它是这个特效的门面。
_SHAPE_DEFAULT_COLORS = {
    "爱心": "#FF5D73",
    "星星": "#FFC145",
    "彩带": COLOR_RAINBOW,
    "雪花": "#FFFFFF",
    "气泡": "#8FD8FF",
    "火焰": "#FF8A3D",
    "花瓣": "#FFA6C9",
    "音符": "#A78BFA",
}


def _shape_sprite(shape, color_hex, size, rot_step=0, rot_steps=12):
    """画一个图案精灵（预乘 RGBA），带缓存。

    `rot_step` 是预渲染的旋转档位：旋转不能在每帧对每个粒子现算（要重采样，
    几十个粒子根本扛不住），所以**按固定档位预渲染一批**，绘制时取最近的档。
    12 档在视觉上已经看不出跳变。
    """
    size = max(8, int(size))
    key = (shape, color_hex, size, rot_step, rot_steps)
    hit = _SHAPE_CACHE.get(key)
    if hit is not None:
        return hit
    if len(_SHAPE_CACHE) > _SHAPE_CACHE_MAX:
        _SHAPE_CACHE.clear()
    spr = _build_shape(shape, color_hex, size, rot_step, rot_steps)
    _SHAPE_CACHE[key] = spr
    return spr


def _build_shape(shape, color_hex, size, rot_step, rot_steps):
    """真正画图案。坐标一律归一化到 0~1，与最终像素尺寸无关。"""
    SS = 3                                  # 超采样倍数
    W = size * SS
    img = _PImage.new("RGBA", (W, W), (0, 0, 0, 0))
    dr = _PDraw.Draw(img)

    bgr = hex_to_bgr(color_hex)
    rgb = (int(bgr[2]), int(bgr[1]), int(bgr[0]))
    fill = rgb + (255,)
    light = tuple(int(v + (255 - v) * 0.55) for v in rgb) + (255,)
    dark = tuple(int(v * 0.55) for v in rgb) + (255,)

    def ell(cx, cy, rx, ry, col):
        dr.ellipse([(cx - rx) * W, (cy - ry) * W,
                    (cx + rx) * W, (cy + ry) * W], fill=col)

    def poly(pts, col):
        dr.polygon([(x * W, y * W) for x, y in pts], fill=col)

    def line(pts, col, wd):
        dr.line([(x * W, y * W) for x, y in pts], fill=col,
                width=max(1, int(wd)), joint="curve")

    if shape == "爱心":
        # 两个圆瓣 + 一个倒三角拼出心形（与宠物那套同样的构造）
        ell(0.355, 0.365, 0.205, 0.205, fill)
        ell(0.645, 0.365, 0.205, 0.205, fill)
        poly([(0.155, 0.435), (0.845, 0.435), (0.500, 0.880)], fill)
        ell(0.300, 0.305, 0.062, 0.045, light)      # 高光，否则整块颜色太平
    elif shape == "星星":
        pts = []
        for j in range(10):
            rr = 0.46 if j % 2 == 0 else 0.185
            aa = -math.pi / 2 + j * math.pi / 5
            pts.append((0.5 + rr * math.cos(aa), 0.5 + rr * math.sin(aa)))
        poly(pts, fill)
        ell(0.5, 0.52, 0.10, 0.10, light)
    elif shape == "彩带":
        # 一条**弯曲**的飘带。之前是两段直线矩形拼的，方方正正像硬纸片 ——
        # 纸屑和彩带的区别就在"卷"上。
        # 做法：先取一条 S 形中心线，再沿每个点的法向往两侧平移出上下边界，
        # 逐段围成四边形。宽度沿程收放 = 缎带在翻卷（被扭到侧面时变窄），
        # 直线段做不到这一点。
        NJ = 12
        cpts, wid = [], []
        for j in range(NJ + 1):
            t = j / NJ
            cpts.append((0.13 + 0.74 * t,
                         0.50 + 0.26 * math.sin((t - 0.5) * math.pi * 1.5)))
            # 中段被扭窄、两端舒展；系数不取 0 是为了任何一处都有可见宽度
            wid.append(0.088 * (0.42 + 0.58 * abs(math.cos(t * math.pi))))
        for j in range(NJ):
            (ax, ay), (bx, by) = cpts[j], cpts[j + 1]
            dx, dy = bx - ax, by - ay
            m = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / m, dx / m
            wa, wb = wid[j], wid[j + 1]
            poly([(ax + nx * wa, ay + ny * wa), (bx + nx * wb, by + ny * wb),
                  (bx - nx * wb, by - ny * wb), (ax - nx * wa, ay - ny * wa)],
                 fill)
        # 一道沿中心线的高光：缎带的丝面反射，也让"卷"的方向看得出来
        line(cpts, light, max(1, int(W * 0.016)))
    elif shape == "雪花":
        # 六条主枝 + 每枝一对小叉。只画细线，雪花才是"结晶"而不是"六角星"
        wd = max(1, int(W * 0.055))
        for j in range(6):
            aa = j * math.pi / 3
            dx, dy = math.cos(aa), math.sin(aa)
            line([(0.5 - 0.44 * dx, 0.5 - 0.44 * dy),
                  (0.5 + 0.44 * dx, 0.5 + 0.44 * dy)], fill, wd)
            for s in (-1, 1):
                bx, by = 0.5 + 0.30 * dx, 0.5 + 0.30 * dy
                aa2 = aa + s * 0.85
                line([(bx, by), (bx + 0.16 * math.cos(aa2),
                                 by + 0.16 * math.sin(aa2))], fill, wd * 0.8)
        ell(0.5, 0.5, 0.075, 0.075, light)
    elif shape == "气泡":
        # 空心圆 + 左上一点高光；内部留透，叠在桌面上才像泡
        wd = max(1, int(W * 0.07))
        dr.ellipse([0.09 * W, 0.09 * W, 0.91 * W, 0.91 * W],
                   outline=fill, width=wd)
        ell(0.34, 0.31, 0.115, 0.085, light)
        ell(0.68, 0.72, 0.055, 0.055, light)
    elif shape == "火焰":
        # 泪滴形：下半圆 + 上半收成尖，再点一个亮芯
        poly([(0.50, 0.06), (0.80, 0.52), (0.20, 0.52)], fill)
        ell(0.50, 0.62, 0.30, 0.30, fill)
        ell(0.50, 0.70, 0.145, 0.185, light)
    elif shape == "花瓣":
        # 与「落樱」同一套花瓣轮廓，整体缩到 0.1~0.9
        pts = [(5.5, 5.5), (7.5, 3.0), (9.0, 5.0), (10.5, 3.0), (12.5, 5.5),
               (12.5, 10.5), (9.0, 15.0), (5.5, 10.5), (5.5, 5.5)]
        poly([((x - 1.0) / 16.0, (y - 0.5) / 16.0) for x, y in pts], fill)
        ell(0.42, 0.42, 0.07, 0.055, light)
    else:   # 音符
        ell(0.335, 0.735, 0.185, 0.135, fill)               # 符头
        line([(0.505, 0.720), (0.505, 0.215)], fill, max(2, W * 0.075))
        line([(0.505, 0.215), (0.700, 0.290), (0.760, 0.430)],
             fill, max(2, W * 0.070))

    # 旋转：在超采样画布上转，再降采样 —— 先缩后转会把锯齿一起转进去
    if rot_step:
        img = img.rotate(-360.0 * rot_step / rot_steps, resample=_BICUBIC,
                         expand=False)
    img = img.resize((size, size), _LANCZOS)
    arr = np.asarray(img).astype(np.float32) * (1.0 / 255.0)
    arr[..., :3] *= arr[..., 3:4]           # 直通 alpha -> 预乘
    return np.ascontiguousarray(arr)


def _text_sprite(text, font_name, size, color_hex, scale=1.0, angle=0.0):
    """把一行文字渲染成预乘 RGBA（带描边，保证在任何桌面底色上都读得清）。

    字号按 `scale` 直接**重新渲染**而不是缩放位图 —— 文字是矢量轮廓，重渲染
    比放大位图清楚得多，而且这里只在"弹出"的几个档位上各渲一次，成本可忽略。

    `angle`：整体倾斜角度（度）。**先按 textbbox 精确裁剪、再 `expand=True`
    旋转** —— 反过来（先旋转再裁）会把旋转后多出来的边角裁掉。
    """
    px = max(8, int(round(size * scale)))
    font = _font(font_name, px)
    bgr = hex_to_bgr(color_hex)
    rgb = (int(bgr[2]), int(bgr[1]), int(bgr[0]), 255)

    # 先量尺寸（含描边留白），再按量到的尺寸建画布
    tmp = _PImage.new("RGBA", (1, 1))
    box = _PDraw.Draw(tmp).textbbox((0, 0), text, font=font,
                                    stroke_width=max(1, px // 14))
    pad = max(2, px // 6)
    w = max(1, box[2] - box[0]) + pad * 2
    h = max(1, box[3] - box[1]) + pad * 2
    img = _PImage.new("RGBA", (w, h), (0, 0, 0, 0))
    dr = _PDraw.Draw(img)
    # 描边用半透明黑：亮字在浅色桌面上会糊掉，一圈描边把它托起来
    dr.text((pad - box[0], pad - box[1]), text, font=font, fill=rgb,
            stroke_width=max(1, px // 14), stroke_fill=(0, 0, 0, 190))

    if angle:
        img = img.rotate(angle, resample=_BICUBIC, expand=True)

    arr = np.asarray(img).astype(np.float32) * (1.0 / 255.0)
    arr[..., :3] *= arr[..., 3:4]
    return np.ascontiguousarray(arr)


# 字体表：文件相对 `C:/Windows/Fonts`，找不到就逐级回退（见 `_font`）。
# 刻意保留「微软雅黑 / 雅黑粗体 / 黑体」这类清晰的基础款，其余全换成有笔锋的
# 花样款 —— 之前那几个（宋体 / 等线 / 楷体）太像正文，弹出来没有记忆点。
# 只列**系统自带**的：华文 / 方正 / 微软这几家随 Windows 分发的都在，用户
# 不用另外下载；"魏碑 / 方正黑体"这类不一定装的则不列（列了也只是静默回退
# 成雅黑，面板上却多一个点不出效果的选项）。
_FONT_FILES = {
    "微软雅黑": "msyh.ttc",
    "雅黑粗体": "msyhbd.ttc",
    "黑体": "simhei.ttf",
    "幼圆": "SIMYOU.TTF",       # 圆润
    "行楷": "STXINGKA.TTF",     # 手写连笔
    "隶书": "SIMLI.TTF",        # 古风扁平
    "琥珀": "STHUPO.TTF",       # 圆胖醒目
    "彩云": "STCAIYUN.TTF",     # 空心描边，很花
    "舒体": "FZSTK.TTF",        # 毛笔
}
_FONT_CACHE = {}


def _font(name, px):
    """按名字取字体。文件缺失/损坏就回退雅黑 —— 用户机器上不一定装全，
    少一款字体不该让文字整个弹不出来。"""
    key = (name, px)
    hit = _FONT_CACHE.get(key)
    if hit is not None:
        return hit
    for cand in (_FONT_FILES.get(name), _FONT_FILES["微软雅黑"],
                 "simhei.ttf", "simsun.ttc"):
        if not cand:
            continue
        try:
            f = _PImageFont.truetype(cand, px)
            break
        except Exception:
            continue
    else:
        f = _PImageFont.load_default()
    if len(_FONT_CACHE) > 160:
        _FONT_CACHE.clear()
    _FONT_CACHE[key] = f
    return f


# ------------------------------------------------------------ 点击特效的素材
# 「点击表情」的两种图案来源：内置图案（代码画）与自定义图片（用户文件夹）。
CLICK_SOURCE_BUILTIN = "内置图案"
CLICK_SOURCE_CUSTOM = "自定义图片"
CLICK_SOURCES = (CLICK_SOURCE_BUILTIN, CLICK_SOURCE_CUSTOM)

_IMG_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
# 音频只认 MCI 自带设备能放的那几种。flac / ogg / opus 在多数机器上装不上
# 解码器，列进来只会得到"选了文件夹却一声不响"。
_AUDIO_EXTS = (".wav", ".mp3", ".wma", ".m4a")


def _scan_files(folder, exts):
    """列出文件夹里指定扩展名的文件（完整路径 + 文件名排序）。扫不动给空表。

    **不要用 `listdir` + `isfile`**：那是对每个条目再补一次 stat。`scandir`
    的 `is_file()` 读的是目录项自带的属性，整目录一趟扫完 —— 这个函数会被
    面板每 200ms 跟一次，差别是能感觉到的。
    """
    folder = str(folder or "")
    if not folder:
        return []
    out = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                try:
                    if e.is_file() and e.name.lower().endswith(exts):
                        out.append(os.path.join(folder, e.name))
                except OSError:
                    continue        # 单个条目读不了就跳过，不废掉整个文件夹
    except Exception:
        return []
    out.sort()
    return out


def _load_click_image(path, px):
    """读一张图 -> 预乘 RGBA float32（与 `_build_shape` 同格式）。

    长边缩到 `px` 且**保持比例** —— 表情包里的图多是矩形，硬拉成正方形会
    变形。任何一步失败都返回 None：文件夹里混进一个 0 字节文件、一个改了
    后缀的网页图都是常事，不该让整池子作废。
    """
    if _PImage is None:
        return None
    try:
        im = _PImage.open(path)
        im.seek(0)                  # GIF / 动图只取第一帧
        im = im.convert("RGBA")
        w, h = im.size
        if w < 1 or h < 1:
            return None
        sc = float(max(8, int(px))) / float(max(w, h))
        im = im.resize((max(1, int(round(w * sc))), max(1, int(round(h * sc)))),
                       _LANCZOS)
    except Exception:
        return None
    arr = np.asarray(im).astype(np.float32) * (1.0 / 255.0)
    arr[..., :3] *= arr[..., 3:4]           # 直通 alpha -> 预乘
    return np.ascontiguousarray(arr)


class ClickImagePool:
    """自定义表情包的图库：扫文件夹 -> 解码 -> 存成能直接上屏的精灵。

    两条硬约束决定了它的形状：

    * **解码绝不能进渲染线程。** 一张 2000×2000 的 JPG 解码 + 缩放要几十
      毫秒，放在 `update()` 里就是"每次点击掉一帧"。所以扫描与解码跑在一条
      后台线程上，渲染端只读**已经就绪**的那一份精灵表。
    * **文件夹可能有几千张。** 先按文件名排序、再**随机打乱**取前
      `MAX_FILES` 张：不洗牌的话用户永远只见到文件名靠前的那几十张。

    每张图存 `SIZE_STEPS` 档尺寸，粒子直接从池里随机取条目 —— "图随机"与
    "大小随机"于是统一成一件事（不需要再给粒子单独做位图缩放）。

    线程协议：`configure()` 只登记需求并唤醒 worker；worker 解码完成后
    **整体替换**精灵表（从不就地改）。读取方拿到的永远是完整快照，这正是
    它能不加锁地面对渲染线程的原因。
    """

    MAX_FILES = 64
    SIZE_STEPS = (0.74, 1.0)

    def __init__(self):
        self._lock = threading.Lock()
        self._want = None           # 待处理的 (folder, px)
        self._dir = ""              # 已完成的 (folder, px)
        self._px = 0
        self._sprites = []          # 只整体替换，从不就地改
        self._files = 0             # 上次扫到的可用文件数
        self._failed = 0            # 解码失败个数（面板会显示，别静默）
        self._rng = random.Random(0x5F3C14)
        self._thread = None
        self._wake = threading.Event()

    # ---------------------------------------------------------- 渲染线程侧
    def configure(self, folder, px):
        """登记"我要用这个文件夹、这个尺寸"。重复登记是**零成本**的。

        每次 `update` 都会调它（拖动"大小"滑块时每帧都在变），所以这里
        只做一次比较、一次赋值 —— 真正的重建由 worker 去合并执行。
        """
        job = (str(folder or ""), max(8, int(px)))
        with self._lock:
            if job == self._want:
                return
            self._want = job
        self._start()
        self._wake.set()

    def sprites(self):
        """当前可用的精灵快照（list，元素是预乘 RGBA 数组）。空 = 还没就绪。"""
        with self._lock:
            return self._sprites

    def stats(self):
        """(精灵条目数, 扫到的文件数, 解码失败数)。"""
        with self._lock:
            return len(self._sprites), self._files, self._failed

    def state(self, folder):
        """给面板的一句话状态。

        `done` 必须比较**整个 job**（文件夹 + 尺寸）而不是只看文件夹：用户
        拖「大小」滑块时尺寸每帧都在变，"文件对了"并不代表这一批就是用当前
        尺寸做的。
        """
        with self._lock:
            n, files, failed = len(self._sprites), self._files, self._failed
            done = (self._want is not None and self._want == (self._dir, self._px))
        if not folder:
            return ""
        if not done:
            return "读取中…"
        if files == 0:
            return "没找到图片"
        out = "已载入 %d 张" % n
        if failed:
            out += "（%d 张读不了）" % failed
        return out

    def clear(self):
        """丢掉现有素材并停止装载（换文件夹时由 configure 直接覆盖，这是
        给"清空"和测试用的）。"""
        with self._lock:
            self._want = None
            self._dir, self._px = "", 0
            self._sprites = []
            self._files = 0
            self._failed = 0
        self._wake.set()

    # ---------------------------------------------------------- 后台线程
    def _start(self):
        t = self._thread
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=self._loop, name="ClickImagePool",
                             daemon=True)
        self._thread = t
        t.start()

    def _loop(self):
        while True:
            self._wake.wait(0.5)
            self._wake.clear()
            with self._lock:
                job = self._want
                done = (self._dir, self._px)
            if job is None or job == done:
                continue
            # **绝不能把异常漏出去**：pythonw 下后台线程死掉连 traceback 都
            # 没有（没有 stderr），池子会永远停在"正在读取"。宁可这次放弃，
            # 下一次点击或下一次 configure 再来一遍。
            try:
                self._load(job)
            except Exception:
                pass

    def _load(self, job):
        folder, px = job
        paths = _scan_files(folder, _IMG_EXTS)
        self._rng.shuffle(paths)
        out = []
        for p in paths[:self.MAX_FILES]:
            if not self._wanted(job):
                return              # 用户改过文件夹/尺寸，这一批作废
            ok = False
            for sc in self.SIZE_STEPS:
                spr = _load_click_image(p, int(round(px * sc)))
                if spr is not None:
                    ok = True
                    out.append(spr)
            if not ok:
                # 按**文件**计数而不是按档位：同一个坏文件会在两档上各失败
                # 一次，显示成"2 张读不了"会让用户去找并不存在的第二张。
                with self._lock:
                    self._failed += 1
        with self._lock:
            self._dir, self._px = job
            self._sprites = out
            self._files = len(paths)

    def _wanted(self, job):
        with self._lock:
            return self._want == job


# 模块级单例：同一个文件夹只解码一次，两个点击特效实例（或反复重建的实例）
# 共用同一份素材。
CLICK_IMAGE_POOL = ClickImagePool()

# ---------------------------------------------------------------- 点击语音
_VOICE_ALIAS = "fxclickvoice"
_AUDIO_COUNT_TTL = 2.0          # 面板状态显示的扫描缓存（秒）
_AUDIO_COUNT_CACHE = {}

try:
    _winmm = ctypes.WinDLL("winmm")
except Exception:                       # pragma: no cover - 非 Windows
    _winmm = None


def _mci_send(cmd):
    """发一条 MCI 命令，返回 (错误码, 返回串)。错误码 0 = 成功。

    独立成函数是为了**能被测试替换** —— 真去开声卡放一遍，在自动化里既
    听不见也没法判定，而"上一段没放完不响应下一次点击"这条规则必须验。
    """
    if _winmm is None:
        return 1, ""
    buf = ctypes.create_unicode_buffer(128)
    try:
        r = _winmm.mciSendStringW(ctypes.c_wchar_p(cmd), buf, 128, None)
    except Exception:
        return 1, ""
    return int(r), buf.value


def count_audio(folder):
    """文件夹里有多少个可播放的音频（面板状态显示用，2 秒内复用结果）。

    不缓存的话面板每 200ms 扫一次盘 —— 音频文件夹动辄几百首，白扫。
    """
    folder = str(folder or "")
    if not folder:
        return 0
    now = time.monotonic()
    hit = _AUDIO_COUNT_CACHE.get(folder)
    if hit is not None and now - hit[0] < _AUDIO_COUNT_TTL:
        return hit[1]
    n = len(_scan_files(folder, _AUDIO_EXTS))
    if len(_AUDIO_COUNT_CACHE) > 32:
        _AUDIO_COUNT_CACHE.clear()
    _AUDIO_COUNT_CACHE[folder] = (now, n)
    return n


class VoicePlayer:
    """点击语音：随机播一段音频，**同一时刻只响一个**。

    播放走 Windows 自带的 MCI（`winmm`）—— wav / mp3 / wma 都能放，不引
    第三方库。整段播放跑在一条后台线程里，渲染线程只负责"占坑"：

    * 上一段还没放完时 `play_random()` **立刻返回 False**，一秒都不等。
      排队等着放会让连点变成"越来越滞后的一串拖音"，听感像卡带，所以规则
      是"忙就丢弃这一次点击"。这也是用户要的"上一段没放完不响应下一个"。
    * 结束判定用 `status ... mode` 轮询，**不用 `play ... wait`**：后者在
      某些设备上会直接挂住不返回，那条线程就永远消失在等待里。
    * 600 秒硬上限兜底 —— 设备抽风时 `status` 可能永远回 "playing"。
    """

    MAX_SECONDS = 600.0

    def __init__(self):
        self._lock = threading.Lock()
        self._busy = False
        self._folder = ""
        self._cache = []
        self._stamp = 0.0
        self._rng = random.Random(0x51CE07)
        self._played = 0
        self._failed = 0

    # ---------------------------------------------------------- 渲染线程侧
    def playing(self):
        return self._busy

    def play_random(self, folder):
        """随机播一个；正在播 -> 放弃这次（返回 False）。"""
        if self._busy:
            return False
        path = self._pick(folder)
        if path is None:
            return False
        with self._lock:
            if self._busy:              # 同帧两次点击抢跑，让先到的那个播
                return False
            self._busy = True
            self._played += 1
        threading.Thread(target=self._worker, args=(path,),
                         name="VoicePlayer", daemon=True).start()
        return True

    def stats(self):
        with self._lock:
            return self._played, self._failed

    def _pick(self, folder):
        folder = str(folder or "")
        now = time.monotonic()
        if folder != self._folder or now - self._stamp > _AUDIO_COUNT_TTL:
            # 2 秒缓存：点击本来就稀疏，但连点时没必要每次都重扫目录。
            self._folder = folder
            self._stamp = now
            self._cache = _scan_files(folder, _AUDIO_EXTS)
        if not self._cache:
            return None
        return self._rng.choice(self._cache)

    # ---------------------------------------------------------- 播放线程
    def _worker(self, path):
        try:
            # 上一条如果因为异常没关干净，别名会占着媒体设备 -> 先清一次
            _mci_send("close %s" % _VOICE_ALIAS)
            ext = os.path.splitext(path)[1].lower()
            kind = "waveaudio" if ext == ".wav" else "mpegvideo"
            r, _ = _mci_send('open "%s" type %s alias %s'
                             % (path, kind, _VOICE_ALIAS))
            if r:
                with self._lock:
                    self._failed += 1
                return
            _mci_send("play %s" % _VOICE_ALIAS)
            t0 = time.monotonic()
            while time.monotonic() - t0 < self.MAX_SECONDS:
                time.sleep(0.1)
                r, mode = _mci_send("status %s mode" % _VOICE_ALIAS)
                if r or mode.strip().lower() != "playing":
                    break
        except Exception:
            pass
        finally:
            try:
                _mci_send("close %s" % _VOICE_ALIAS)
            except Exception:
                pass
            # 无论怎么结束都要放行 —— 少了这一步就"只响一次，之后永远是忙"
            with self._lock:
                self._busy = False


class _BurstEngine:
    """点击粒子爆发的物理与上屏（不含配置解析）。

    参数模型参考 pizzazz 那个点击特效库：一次点击炸开 `count` 个粒子，
    速度方向随机（决定"散得多开"）、可加重力（像彩纸往下飘）、可自旋、
    寿命内淡出。

    做成独立的"引擎"而不是直接写进某个特效里，是因为**两个点击特效都要用**：
    「点击表情」整个就是一次爆发，「点击文字」的彩带/星光装饰也是。
    两个特效互斥（同时只开一个），但代码没有必要抄两份。
    """

    MAX = 200                   # 粒子池上限（一次爆发最多这么多）
    ROT_STEPS = 12

    def __init__(self, seed=71):
        n = self.MAX
        self.rng = np.random.default_rng(seed)
        self.px = np.zeros(n, np.float32)
        self.py = np.zeros(n, np.float32)
        self.vx = np.zeros(n, np.float32)
        self.vy = np.zeros(n, np.float32)
        self.t = np.zeros(n, np.float32)
        self.life = np.zeros(n, np.float32)
        self.size = np.zeros(n, np.float32)
        self.rot = np.zeros(n, np.float32)
        self.spin = np.zeros(n, np.float32)
        self.rgb = np.zeros((n, 3), np.float32)
        # 自定义表情包模式下每颗粒子指向外部精灵表的下标（内置图案模式恒 0）
        self.spr_id = np.zeros(n, np.int32)
        self.count = 0              # 已用槽位数（紧凑存放，死掉的会被换到前面）

    def active(self):
        return self.count > 0

    def reset(self):
        self.count = 0

    def spawn(self, x, y, shape, colors, count, size, spread, gravity,
              rotate, life, up_bias=0.0, size_jitter=0.45, spr_ids=None):
        """在 (x, y) 炸开一簇粒子。

        `colors` 是 RGB 三元组列表（0~255）：单色图案传一个，彩带传一整盘，
        每个粒子随机取一个。`up_bias` > 0 时整体偏向上方 —— 「点击文字」的
        装饰要往文字那边飘，而不是均匀四散。

        `spr_ids` 给定（长度 = count）时这簇改用**外部精灵表**上屏（自定义
        表情包），`shape` / `colors` 都不再参与绘制，只为保持调用形状一致。
        """
        count = max(0, min(int(count), self.MAX))
        if count == 0 or size <= 0:
            return
        # 池满时先让老粒子让位：点击是新事件，比正在淡出的旧粒子重要
        if self.count + count > self.MAX:
            self._drop_oldest(self.count + count - self.MAX)

        idx = slice(self.count, self.count + count)
        k = count
        rng = self.rng
        ang = rng.uniform(0.0, 2.0 * math.pi, k)
        # 速度按 sqrt(uniform) 分布：均匀取半径会让粒子在中心堆成一坨，
        # 开方之后才像"从中心均匀铺开的一张圆盘"
        spd = spread * np.sqrt(rng.uniform(0.12, 1.0, k))
        self.vx[idx] = (np.cos(ang) * spd).astype(np.float32)
        self.vy[idx] = (np.sin(ang) * spd - up_bias * spread * 0.75).astype(np.float32)
        self.px[idx] = float(x)
        self.py[idx] = float(y)
        self.t[idx] = 0.0
        self.life[idx] = (life * rng.uniform(0.75, 1.25, k)).astype(np.float32)
        self.size[idx] = (size * rng.uniform(1.0 - size_jitter,
                                             1.0 + size_jitter, k)).astype(np.float32)
        self.rot[idx] = rng.uniform(0.0, 2.0 * math.pi, k)
        self.spin[idx] = ((rng.uniform(-1.0, 1.0, k) * 7.5) if rotate
                          else np.zeros(k))
        pal = np.asarray(colors, np.float32).reshape(-1, 3)
        pick = rng.integers(0, len(pal), k)
        self.rgb[idx] = pal[pick]
        if spr_ids is None:
            self.spr_id[idx] = 0
        else:
            sid = np.asarray(spr_ids, np.int32).reshape(-1)
            # 长度不足时用 `resize` 循环填满而不是让它抛错：这条路径在渲染
            # 线程上，宁可画得难看一点也不能把一帧打断。
            self.spr_id[idx] = np.resize(sid, k) if sid.size else 0
        self._gravity = float(gravity)
        self.count += count

    def _drop_oldest(self, need):
        """丢掉最老的 need 个粒子，给新粒子腾位置（把存活的后移压实）。"""
        need = min(need, self.count)
        if need <= 0:
            return
        keep = self.count - need
        src = slice(need, self.count)
        for arr in (self.px, self.py, self.vx, self.vy, self.t, self.life,
                    self.size, self.rot, self.spin, self.spr_id):
            arr[:keep] = arr[src]
        self.rgb[:keep] = self.rgb[src]
        self.count = keep

    def update(self, dt):
        if self.count == 0:
            return
        n = self.count
        sl = slice(0, n)
        self.t[sl] += dt
        if getattr(self, "_gravity", 0.0):
            self.vy[sl] += 260.0 * dt
        # 空气阻力：让粒子末段慢下来，比匀速飞出去自然（也顺手把速度压住，
        # 免得长时间不点击后速度累积成天文数字）
        damp = max(0.0, 1.0 - 1.6 * dt)
        self.vx[sl] *= damp
        self.vy[sl] *= damp
        self.px[sl] += self.vx[sl] * dt
        self.py[sl] += self.vy[sl] * dt
        self.rot[sl] += self.spin[sl] * dt

        dead = np.nonzero(self.t[sl] >= self.life[sl])[0]
        if dead.size:
            alive = np.nonzero(self.t[sl] < self.life[sl])[0]
            m = alive.size
            for arr in (self.px, self.py, self.vx, self.vy, self.t, self.life,
                        self.size, self.rot, self.spin, self.spr_id):
                arr[:m] = arr[alive]
            self.rgb[:m] = self.rgb[alive]
            self.count = m

    def draw(self, u32, w, h, opacity=1.0, tint=None, imgs=None):
        """上屏。

        `tint` 非空时整簇用同一个颜色（点击表情的"主色"模式）。
        `imgs` 非空时改用**外部精灵表**（自定义表情包）：每颗粒子按
        `spr_id` 取自己那一张，图案与颜色都不再参与 —— 这条路径下精灵是
        矩形（用户图片保持原比例），所以裁剪必须**分别**取宽与高。
        """
        if self.count == 0 or opacity <= 0.004:
            return
        n_img = len(imgs) if imgs is not None else 0
        for i in range(self.count):
            t = float(self.t[i])
            life = float(self.life[i])
            if life <= 0.0:
                continue
            u = t / life
            # 生灭包络：开场极快亮起、尾段渐渐淡出。两端都留一点缓冲，
            # 否则粒子会"啪"地出现又"啪"地消失。
            env = min(1.0, t / 0.06) * min(1.0, (1.0 - u) / 0.42)
            a = opacity * max(0.0, env)
            if a <= 0.01:
                continue
            if n_img:
                j = int(self.spr_id[i])
                if j < 0 or j >= n_img:
                    continue
                spr = imgs[j]
            else:
                r, g, b = (self.rgb[i] if tint is None else tint)
                hexc = "#%02X%02X%02X" % (int(r), int(g), int(b))
                shape = getattr(self, "_shape", "爱心")
                step = int(round(float(self.rot[i]) / (2.0 * math.pi)
                                 * self.ROT_STEPS)) % self.ROT_STEPS
                size = int(max(8, round(float(self.size[i]))))
                # 颜色进缓存键：彩带逐粒子不同色，单色图案则整簇共用一条缓存
                spr = _shape_sprite(shape, hexc, size, step, self.ROT_STEPS)
            # 高与宽**分开取**。内置图案是正方形（两者永远相等），但自定义
            # 表情包的图是矩形 —— 拿高当边长会让墨迹只落进左上角一块，
            # 也就是"图被切掉大半"（与 ClickTextField._blit 同一个坑）。
            sh, sw = spr.shape[0], spr.shape[1]
            x0 = int(round(float(self.px[i]) - sw * 0.5))
            y0 = int(round(float(self.py[i]) - sh * 0.5))
            gx0, gy0 = max(0, x0), max(0, y0)
            gx1, gy1 = min(int(w), x0 + sw), min(int(h), y0 + sh)
            if gx1 <= gx0 or gy1 <= gy0:
                continue
            sub = spr[gy0 - y0:gy1 - y0, gx0 - x0:gx1 - x0]
            aa = sub[:, :, 3] * a
            if not (aa > 0.004).any():
                continue
            dst = u32.reshape(int(h), int(w))[gy0:gy1, gx0:gx1]
            _over_blend(dst, sub, aa, 255.0 * a)


# 「弹出」的几个缩放档位（直接按不同字号重渲染，不是缩放位图）。
# 中段 1.08 是一次**过冲** —— 正是它给出"啵"地弹出来的手感，线性放大到 1
# 会显得很平。
_TEXT_POP_STEPS = (0.55, 0.78, 0.95, 1.08, 1.00)
_TEXT_CACHE = {}
_TEXT_CACHE_MAX = 400           # 多了"倾斜档"这一维，上限跟着放大

# 倾斜角度档位（度）。做量化档而不是连续值，是为了让精灵缓存键有限 ——
# 每次点击的角度是**固定的**（点击那一下定下来，之后不再变），所以 5° 的
# 粒度只影响"这次看起来偏多少度"，不会有动画跳动。
_TEXT_TILT_STEPS = (-30.0, -25.0, -20.0, -15.0, -10.0, -5.0, 0.0,
                    5.0, 10.0, 15.0, 20.0, 25.0, 30.0)
_TEXT_TILT_ZERO = 6             # 上表里 0.0 的下标


def _tilt_choices(amount):
    """按倾斜幅度（0~1）挑出允许的角度档下标。

    幅度 0 → 只有 0°，1 → 全部 13 档。文字歪得太狠会读不出来，所以上限
    就取表里的 ±30°。
    """
    if amount <= 0.0:
        return (_TEXT_TILT_ZERO,)
    lim = 30.0 * min(1.0, amount)
    ids = tuple(i for i, d in enumerate(_TEXT_TILT_STEPS) if abs(d) <= lim + 1e-6)
    return ids or (_TEXT_TILT_ZERO,)


def _text_sprites(text, font_name, size, color_hex, tilt_idx=_TEXT_TILT_ZERO):
    """取一行文字的 5 档缩放精灵（预乘 RGBA 列表），带缓存。

    `tilt_idx` 是 `_TEXT_TILT_STEPS` 的下标：同一次点击的 5 档共用同一个
    倾斜角，所以一档角度只多 5 个缓存条目。
    """
    key = (text, font_name, int(size), color_hex, int(tilt_idx))
    hit = _TEXT_CACHE.get(key)
    if hit is not None:
        return hit
    if len(_TEXT_CACHE) > _TEXT_CACHE_MAX:
        _TEXT_CACHE.clear()
    ang = _TEXT_TILT_STEPS[int(tilt_idx) % len(_TEXT_TILT_STEPS)]
    out = [_text_sprite(text, font_name, int(size), color_hex, s, ang)
           for s in _TEXT_POP_STEPS]
    _TEXT_CACHE[key] = out
    return out


class ClickEmojiField:
    """点击表情：每点一下在光标处炸开一簇图案。

    与「鼠标拖动特效」的区别在于触发方式 —— 它不是跟着光标连续铺开，而是
    **一次点击一个瞬时爆发**。参数模型参考 pizzazz 那个点击特效库
    （数量 / 尺寸 / 扩散 / 重力 / 自旋）。

    图案有两个来源：内置图案（`_build_shape` 用代码画，不依赖任何文件）
    与自定义表情包（`ClickImagePool` 从用户给的文件夹里随机取图，**在后台
    线程**解码）。后者的素材还没就绪时自动回退到内置图案 —— 点击永远
    有反馈，不会出现"点了没动静"。

    粒子物理与上屏在 `_BurstEngine` 里（「点击文字」的装饰也用它）。
    """

    def __init__(self, w, h, seed=73):
        self.rng = np.random.default_rng(seed)
        self.w, self.h = w, h
        self._mx = w * 0.5
        self._my = h * 0.5
        self._clicked = 0
        # 自定义表情包时的精灵快照（None = 这一簇走内置图案）。持有的是
        # 池子交出来的**不可变快照**，后台换素材不会影响正在飞的那一簇。
        self._imgs = None
        self.engine = _BurstEngine(seed=seed)

    def set_pointer(self, x, y, clicked=0):
        self._mx = float(x)
        self._my = float(y)
        if clicked:
            self._clicked = int(clicked)

    def resize(self, w, h):
        self.w, self.h = w, h

    def reset(self):
        self.engine.reset()
        self._imgs = None
        self._clicked = 0

    def active(self):
        return self.engine.active()

    def _spawn_builtin(self, cfg):
        """内置图案：颜色 / 图案 / 自旋全都听配置的。"""
        shape = str(cfg.get("click_emoji_shape", _SHAPE_NAMES[0]))
        if shape not in _SHAPE_NAMES:
            shape = _SHAPE_NAMES[0]
        # 颜色缺键时按**该图案的惯用色**兜底（爱心红、星星黄……），
        # 而不是所有图案都退回同一个粉。
        color = str(cfg.get("click_emoji_color",
                            _SHAPE_DEFAULT_COLORS[shape]))
        if color == COLOR_RAINBOW:
            # 逐粒子从调色板取色。哨兵值必须在 hex_to_bgr 之前拦住 ——
            # 它对非法串是"静默回退浅蓝"，不拦就成了"选了彩色却全蓝"。
            colors = [_hex_rgb(c) for c in _CONFETTI_COLORS]
        else:
            colors = [_hex_rgb(color)]
        self.engine._shape = shape
        self.engine.spawn(
            self._mx, self._my, shape, colors,
            count=int(cfg.get("click_emoji_count", 12)),
            size=float(cfg.get("click_emoji_size", 22)),
            spread=float(cfg.get("click_emoji_spread", 90)),
            gravity=float(cfg.get("click_emoji_gravity", 1.0)),
            rotate=bool(cfg.get("click_emoji_rotate", True)),
            life=float(cfg.get("click_emoji_life", 1.1)))

    def _spawn_custom(self, cfg, sprites):
        """自定义表情包：每颗粒子随机挑一张图。

        不自旋、不做尺寸抖动 —— 图片精灵不是矢量，两者都要重采样；而"图
        随机 × 池子里两档尺寸"本来就提供了同样的视觉变化，白付那笔账不划算。
        """
        count = max(0, min(int(cfg.get("click_emoji_count", 12)),
                           self.engine.MAX))
        if count <= 0:
            return
        self.engine.spawn(
            self._mx, self._my, "", [(255, 255, 255)],
            count=count,
            size=float(cfg.get("click_emoji_size", 22)),
            spread=float(cfg.get("click_emoji_spread", 90)),
            gravity=float(cfg.get("click_emoji_gravity", 1.0)),
            rotate=False,
            life=float(cfg.get("click_emoji_life", 1.1)),
            size_jitter=0.0,
            spr_ids=self.rng.integers(0, len(sprites), count))
        self._imgs = sprites

    def update(self, dt, cfg):
        dt = min(max(float(dt), 0.0), 0.05)
        clicked = self._clicked
        self._clicked = 0
        custom = (str(cfg.get("click_emoji_source", CLICK_SOURCE_BUILTIN))
                  == CLICK_SOURCE_CUSTOM)
        if custom:
            # 只登记需求：解码在后台线程做，这里是一次比较的代价。
            CLICK_IMAGE_POOL.configure(cfg.get("click_emoji_custom_dir", ""),
                                       float(cfg.get("click_emoji_size", 22)))
        if clicked:
            sprites = CLICK_IMAGE_POOL.sprites() if custom else None
            if sprites:
                self._spawn_custom(cfg, sprites)
            else:
                # 素材还没就绪（首次配置后要解码几十张图）或文件夹是空的：
                # 回退内置图案，让**点击始终有反馈**。
                self._imgs = None
                self._spawn_builtin(cfg)
        self.engine.update(dt)

    def draw(self, u32, w, h, cfg):
        op = float(np.clip(float(cfg.get("click_emoji_opacity", 1.0)), 0.0, 1.0))
        self.engine.draw(u32, w, h, op, imgs=self._imgs)


class ClickTextField:
    """点击文字：点击处弹出文字并上飘淡出，可内嵌彩带 / 星光。

    * **多个文字**：配置里一行一个，每次点击**轮换**取下一个 —— 轮换而不是
      随机，是为了让"我配了三条"能一眼确认三条都生效了。
    * **弹出**：开头按 5 档字号递进（中段有轻微过冲），不是缩放位图 ——
      文字是矢量轮廓，按字号重渲染才清楚。
    * **可读性**：文字带半透明描边，否则白字落在浅色桌面上会糊掉。
    * **装饰**：彩带 / 星光复用 `_BurstEngine`，`up_bias` 让它们整体偏上，
      朝着文字飘而不是均匀四散。
    """

    def __init__(self, w, h, seed=79):
        self.rng = np.random.default_rng(seed)
        self.w, self.h = w, h
        self._mx = w * 0.5
        self._my = h * 0.5
        self._clicked = 0
        self.pops = []              # [{x, y, t, life, idx}]
        self._next_idx = 0          # 多条文字轮换用的游标
        self.deco = _BurstEngine(seed=seed + 1)
        self._failed = False

    def set_pointer(self, x, y, clicked=0):
        self._mx = float(x)
        self._my = float(y)
        if clicked:
            self._clicked = int(clicked)

    def resize(self, w, h):
        self.w, self.h = w, h

    def reset(self):
        self.pops = []
        self.deco.reset()
        self._clicked = 0
        self._next_idx = 0

    def active(self):
        return bool(self.pops) or self.deco.active()

    @staticmethod
    def texts_of(cfg):
        """把配置里的多行文字解析成列表（去空行、去首尾空白）。"""
        raw = str(cfg.get("click_text_list", ""))
        return [s.strip() for s in raw.replace("\r", "\n").split("\n")
                if s.strip()]

    def update(self, dt, cfg):
        if self._failed:
            return
        dt = min(max(float(dt), 0.0), 0.05)
        clicked = self._clicked
        self._clicked = 0
        life = max(0.2, float(cfg.get("click_text_life", 1.4)))

        if clicked:
            texts = self.texts_of(cfg)
            if texts:
                # 倾斜与横向漂移在**点击这一下**定下来，之后不再变 ——
                # 每帧重随机会让文字疯狂抖动。
                spin = float(np.clip(float(cfg.get("click_text_spin", 0.75)),
                                     0.0, 1.0))
                tilt = int(self.rng.choice(_tilt_choices(spin)))
                drift = float(self.rng.uniform(-1.0, 1.0)) * 0.45
                self.pops.append({
                    "x": float(self._mx), "y": float(self._my), "t": 0.0,
                    "life": life, "idx": self._next_idx % len(texts),
                    "tilt": tilt, "drift": drift,
                })
                self._next_idx = (self._next_idx + 1) % len(texts)
            # 装饰：与文字同一个爆发点，整体偏上
            deco = str(cfg.get("click_text_deco", "彩带"))
            if deco != "无":
                if deco == "星光":
                    shape, colors, grav, rot = (
                        "星星", [_hex_rgb(str(cfg.get("click_text_color",
                                                      "#FFFFFF")))], 0.0, True)
                else:
                    shape, colors, grav, rot = (
                        "彩带", [_hex_rgb(c) for c in _CONFETTI_COLORS], 1.0,
                        True)
                self.deco._shape = shape
                self.deco.spawn(
                    self._mx, self._my, shape, colors,
                    count=14, size=float(cfg.get("click_text_size", 34)) * 0.62,
                    spread=95.0, gravity=grav, rotate=rot, life=life,
                    up_bias=0.45)

        if self.pops:
            for pp in self.pops:
                pp["t"] += dt
            self.pops = [pp for pp in self.pops if pp["t"] < pp["life"]]
        self.deco.update(dt)

    def draw(self, u32, w, h, cfg):
        if self._failed:
            return
        op = float(np.clip(float(cfg.get("click_text_opacity", 1.0)), 0.0, 1.0))
        if self.pops and op > 0.004:
            try:
                texts = self.texts_of(cfg)
                font_name = str(cfg.get("click_text_font", "微软雅黑"))
                size = float(cfg.get("click_text_size", 34))
                color = str(cfg.get("click_text_color", "#FFFFFF"))
                rise = float(cfg.get("click_text_rise", 90))
                for pp in self.pops:
                    if pp["idx"] >= len(texts):
                        continue
                    steps = _text_sprites(texts[pp["idx"]], font_name,
                                          size, color, pp.get("tilt",
                                                              _TEXT_TILT_ZERO))
                    u = pp["t"] / max(pp["life"], 1e-4)
                    # 选缩放档：弹出段走 5 档递进，之后固定最大档
                    si = min(len(steps) - 1,
                             int(pp["t"] / 0.055)) if pp["t"] < 0.22 else len(steps) - 1
                    spr = steps[max(0, si)]
                    # 上飘用 ease-out：开头冲上去、末段慢下来，比匀速自然；
                    # 再叠一点**点击时定好的**横向漂移，让几行字散开而不是
                    # 叠在同一条竖线上。
                    ease = 1.0 - (1.0 - min(1.0, u)) ** 2
                    dx = pp.get("drift", 0.0) * rise * ease
                    alpha = min(1.0, pp["t"] / 0.08) \
                        * min(1.0, (1.0 - u) / 0.35)
                    a = op * max(0.0, alpha)
                    if a <= 0.01:
                        continue
                    self._blit(u32, w, h, spr, pp["x"] + dx,
                               pp["y"] - rise * ease, a)
            except Exception:
                # 只报一次：反复抛会耗光"连续失败"配额把整个渲染线程熔断，
                # 一个文字效果画不出来不该让雨也停。
                self._failed = True
                raise
        self.deco.draw(u32, w, h, op)

    def _blit(self, u32, w, h, img, cx, cy, a):
        """把精灵以 (cx, cy) 为中心叠加到缓冲上。

        ⚠️ **必须分别取高与宽**：文字精灵宽高不等（"太好了" 是 116×46），
        拿 `shape[0]` 当正方形边长会只取到左边一个 46px 宽的方形区域 ——
        横向被切掉大半（实测墨迹 104px 只落屏 41px），表现为"文字显示
        不完整"。图案精灵（`_shape_sprite`）恰好是正方形，所以这个错误在
        那边不会被发现。
        """
        sh, sw = img.shape[:2]
        x0 = int(round(cx - sw * 0.5))
        y0 = int(round(cy - sh * 0.5))
        gx0, gy0 = max(0, x0), max(0, y0)
        gx1, gy1 = min(int(w), x0 + sw), min(int(h), y0 + sh)
        if gx1 <= gx0 or gy1 <= gy0:
            return
        sub = img[gy0 - y0:gy1 - y0, gx0 - x0:gx1 - x0]
        aa = sub[:, :, 3] * a
        if not (aa > 0.004).any():
            return
        dst = u32.reshape(int(h), int(w))[gy0:gy1, gx0:gx1]
        _over_blend(dst, sub, aa, 255.0 * a)


def _hex_rgb(hex_str):
    """#RRGGBB -> (R, G, B) 的 0~255 整数三元组。"""
    bgr = hex_to_bgr(hex_str)
    return (int(bgr[2]), int(bgr[1]), int(bgr[0]))


# ------------------------------------------------------------------ 聚光灯
class SpotlightField:
    """聚光灯：全屏压暗 + 鼠标处一个柔边透明圆洞，凸显光标周围。

    性能要点：全屏压暗是一句 `u32[:] = dim_pack`（整块内存赋值），只有圆洞
    周围一圈 bounding box 内才做逐像素 sqrt 径向计算 —— 远处屏幕只是被铺上
    一层均匀暗色，不会被逐像素扫描。

    必须先由 Scene 在 step() 里 set_cursor 喂入当前光标（覆盖层坐标系），
    再 draw。draw 会先把整张缓冲铺暗，再在光标处"挖"出透明圆洞。
    """

    def __init__(self, w, h, seed=17):
        self.w, self.h = w, h
        self.cx = float(w) * 0.5
        self.cy = float(h) * 0.5
        self.radius = 220.0
        self.dim = np.float32(0.6)
        self._rgb = (5.0, 7.0, 10.0)     # 近黑
        self._dim_pack = None
        self._last_color = None
        self._last_dim = None
        self._rebuild_dim()

    def resize(self, w, h):
        self.w, self.h = w, h

    def set_cursor(self, x, y):
        self.cx = float(x)
        self.cy = float(y)

    def set_radius(self, r):
        self.radius = max(20.0, float(r))

    def set_dim(self, d):
        d = float(d)
        if d != self._last_dim:
            self._last_dim = d
            self.dim = np.float32(min(0.98, max(0.02, d)))
            self._rebuild_dim()

    def set_color(self, hex_str):
        if hex_str == self._last_color:
            return
        self._last_color = hex_str
        bgr = hex_to_bgr(hex_str)
        self._rgb = (float(bgr[2]), float(bgr[1]), float(bgr[0]))
        self._rebuild_dim()

    def _rebuild_dim(self):
        a = np.rint(self.dim * 255.0).astype(np.uint32)
        cb, cg, cr = self._rgb
        self._dim_pack = _premul_pack(
            np.rint(cb * self.dim).astype(np.uint32),
            np.rint(cg * self.dim).astype(np.uint32),
            np.rint(cr * self.dim).astype(np.uint32), a)

    def draw(self, u32, w, h, cfg):
        if self._dim_pack is None:
            self._rebuild_dim()
        # 1) 全屏铺暗（整块赋值，O(1) 级）
        u32[:] = self._dim_pack

        R = self.radius
        # 2) 仅在光标周围 bounding box 内挖透明圆洞
        x0 = int(max(0, np.floor(self.cx - R)))
        x1 = int(min(w, np.ceil(self.cx + R)))
        y0 = int(max(0, np.floor(self.cy - R)))
        y1 = int(min(h, np.ceil(self.cy + R)))
        if x1 <= x0 or y1 <= y0:
            return
        ys = np.arange(y0, y1, dtype=np.float32)[:, None]
        xs = np.arange(x0, x1, dtype=np.float32)[None, :]
        d = np.sqrt((xs - self.cx) ** 2 + (ys - self.cy) ** 2)
        t = np.clip(d / R, 0.0, 1.0)
        hole = t * t * (3.0 - 2.0 * t)             # smoothstep：中心 0 全透，边缘 1 全暗
        a = self.dim * hole                        # 实际压暗强度（中心 0）
        a_c = np.rint(a * 255.0).astype(np.uint32)
        cb, cg, cr = self._rgb
        packed = _premul_pack(
            np.rint(cb * a).astype(np.uint32),
            np.rint(cg * a).astype(np.uint32),
            np.rint(cr * a).astype(np.uint32), a_c)
        grid = ys.astype(np.int32) * np.int32(w) + xs.astype(np.int32)
        u32[grid.reshape(-1)] = packed.reshape(-1)

