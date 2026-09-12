# -*- coding: utf-8 -*-
"""
特效引擎：下雨 + 鼠标涟漪 + 落地水花 + 闪电。

所有绘制都直接写入 Overlay 暴露的 uint32 视图（0xAARRGGBB，小端 BGRA，
RGB 需预乘 alpha），整层再由 Windows 以 per-pixel alpha 合成到桌面，
因此这里只需"画上去"，半透明混合由系统完成。
"""
import ctypes
import ctypes.wintypes as wt

import numpy as np

from _win32 import user32, HANDLE, INT, SHORT

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


# ------------------------------------------------------------------ 闪电
class Lightning:
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
    """枚举桌面窗口并跨帧 diff，输出 move / minimize / restore 事件。

    这是"窗口交互"特效的公共地基：Scene 每帧消费事件，在窗口移动、最小化、
    恢复时触发涟漪。为不增加每帧负担，update 内部节流到 ~10Hz。

    仅在 Windows 下真正工作（依赖 user32.EnumWindows）；非 Windows 环境
    update 安全返回空列表，不影响其它特效。
    """

    def __init__(self, throttle=0.1):
        self._throttle = float(throttle)
        self._acc = 1.0                      # 首次立即扫一次
        self.windows = []                    # 可见窗口矩形（覆盖层坐标系）
        self.active = None                   # 活动窗口矩形 或 None
        self.ov_x = 0
        self.ov_y = 0
        self._prev = {}                      # hwnd -> ((x0,y0,x1,y1), minimized)
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

    def _diff(self, prev, cur):
        """对比前后两帧窗口集合，输出事件（rect 已折算到覆盖层坐标系）。"""
        events = []
        for hwnd, (rect, minimized) in cur.items():
            p = prev.get(hwnd)
            if p is None:
                continue                        # 新建窗口（open）暂不处理
            prect, pmin = p
            if minimized and not pmin:
                events.append(("minimize", self._to_local(prect)))
            elif not minimized and pmin:
                events.append(("restore", self._to_local(rect)))
            elif not minimized and not pmin:
                if abs(rect[0] - prect[0]) + abs(rect[1] - prect[1]) > 4:
                    events.append(("move", self._to_local(rect)))
        return events

    def update(self, dt):
        """返回本帧窗口事件列表 [(type, rect), ...]，rect 为覆盖层坐标系。"""
        self._acc += dt
        if self._acc < self._throttle:
            return []
        self._acc = 0.0
        try:
            cur = self._enumerate()
        except Exception:
            return []
        if not cur:
            return []
        events = self._diff(self._prev, cur)
        # 更新可见窗口列表与活动窗口（覆盖层坐标系）
        self.windows = [self._to_local(r) for (r, m) in cur.values() if not m]
        # GetForegroundWindow() 在"焦点在桌面"或切换窗口的瞬间会返回 NULL，
        # 而 restype 是 c_void_p —— ctypes 会把 NULL 变成 None。直接 int(None)
        # 会抛 TypeError 并每帧打穿渲染循环（见 error.log 的 render: 条目）。
        fg = _GetForegroundWindow() if _WIN32_OK else None
        fg = int(fg) if fg else None
        act = cur.get(fg)
        self.active = self._to_local(act[0]) if (act and not act[1]) else None
        self._prev = cur
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


# ------------------------------------------------------------------ 光标拖尾
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

