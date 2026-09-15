# -*- coding: utf-8 -*-
"""设置面板：Linear/Raycast 风格深色工具面板。

无边框窗口 + 侧栏导航 + 可滚动内容区；控件全部为 Canvas 自绘，
以贴近设计稿的填充滑块、胶囊开关、线性图标与 8px 间距网格。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import tkinter as tk

from _win32 import user32, HANDLE, HWND, INT, UINT, hwnd_int, screen_pixel
from _paths import app_root
import fx_kinds

# ------------------------------------------------------------------ 设计系统
# 冷色底 + 单一强调色（青），红色只表示破坏性/错误。语义令牌避免散落字面色。
BG = "#0B0E14"          # 窗口主背景（接近纯黑）
SIDEBAR_BG = "#0B0E14"  # 侧栏与窗口融为一体
CARD = "#121824"        # 卡片背景，比窗口略抬升
CARD2 = "#1A2230"       # 次级表面：预设按钮 / 滑块轨道
TRACK = "#212936"       # 滑块轨道 / 开关关态填充
LINE = "#232B38"        # 发丝分隔线
TEXT = "#E8EDF3"        # 主文本
SUB = "#6F7D90"         # 次级文本
ACCENT = "#4FD1E0"      # 唯一强调色（青）
ACCENT_DARK = "#2A8A99" # 强调色深阶（静止/按下）
ACCENT_HOVER = "#8FDFFF"  # 强调色浅阶（悬停）
ON_ACCENT = "#04121B"   # 强调色底上的文字（深色）
WHITE = "#FFFFFF"       # 纯白（标题栏图标悬停文字等）
BTN = "#1A2230"         # 中性按钮静止（=CARD2）
BTN_H = "#253041"       # 中性按钮悬停
NAV_ACTIVE = "#141C27"  # 导航激活背景
NAV_HOVER = "#10161F"   # 导航悬停背景
BAR_HOVER = "#3A4B63"   # 标题栏窗控钮悬停
ERROR_BG = "#5A2020"
ERROR_FG = "#FFD9D9"
DANGER = "#C44E4E"        # 破坏性填充/悬停
DANGER_SOFT = "#4A2B33"   # 破坏性按钮静止底（退出）
DANGER_SOFT_FG = "#FFC9C9"  # 破坏性按钮静止文字
DANGER_TEXT = "#FF8A8A"   # 深底上的破坏性小字（× 悬停/错误提示）

RAIN_COLORS = ["#BFE3FF", "#FFFFFF", "#A9D6F5", "#7FE7C4", "#FFD9A0", "#E0B3FF"]
RIPPLE_COLORS = ["#7FE7FF", "#FFFFFF", "#5FD0FF", "#7CFFB2", "#FFC46B", "#FF8FB1"]
SNOW_COLORS = ["#FFFFFF", "#EAF2FF", "#DCEBFF", "#C9DEFF", "#F2F6FF", "#FFEAF2"]
SPOTLIGHT_COLORS = ["#05070A", "#04060A", "#0A1A2A", "#1A0A12", "#100A00"]
# 落樱双色：花瓣是"主体 -> 边缘"的渐变，所以要两组（不是一组色的明暗变体）
PETAL_COLORS = ["#FF9DB6", "#FF8AA8", "#F58BB0", "#FFB3C7", "#FFFFFF", "#E8A0C8"]
PETAL_EDGE_COLORS = ["#FFF0F4", "#FFEAF0", "#FFFFFF", "#FFD9E6", "#FFF6F9",
                     "#F2D7E4"]
# 流星双色：头部是 additive 的纯白热点，尾部渐隐到彩色
METEOR_COLORS = ["#FFFFFF", "#EAF6FF", "#FFF6E0", "#E8F0FF", "#FFE9F2", "#F0FFFF"]
METEOR_TAIL_COLORS = ["#5FB8F0", "#9FC8FF", "#7FD0FF", "#5FE0D0", "#C08FE8",
                      "#F0A860"]
# 颜料盘：只取基准色的**色相/饱和度**，实际每团色相再按 paint_hue_spread 抖动，
# 所以这里给的是"基准色"而不是"唯一色"。
PAINT_COLORS = ["#FF4D6D", "#7C4DFF", "#20C997", "#FFB020", "#38BDF8", "#F06292"]
WAVE_COLORS = ["#FFFFFF", "#EAF6FF", "#CFE6FF", "#D8F2FF", "#FFF3E0", "#E8FFE8"]

FONT_LABEL = ("Microsoft YaHei UI", 9)
FONT_TITLE = ("Microsoft YaHei UI", 10, "bold")   # 顶栏程序名
FONT_H2 = ("Microsoft YaHei UI", 12, "bold")      # 页头大标题
FONT_NUM = ("Consolas", 9)
FONT_SMALL = ("Microsoft YaHei UI", 8)

HWND_TOPMOST = wt.HWND(-1)
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010

# UI 状态（仅面板自身用，独立于特效 cfg）：记住上次停留的分组页
# 放用户数据目录（exe 所在目录），冻结运行时不写进 _MEIPASS 临时解压区
_UI_STATE_PATH = os.path.join(app_root(), "ui_state.json")

# ------------------------------------------------------------------ 抗锯齿渲染
# tkinter Canvas 本身无抗锯齿，圆/斜线全是一格一格的硬边。
# 做法：用 PIL 在 ss 倍分辨率离屏绘制，再 LANCZOS 缩小贴回 Canvas，
# 得到平滑边缘；背景用控件自己的纯色底，缩放后无缝融合。
try:
    from PIL import Image as _PImage
    from PIL import ImageDraw as _PDraw
    from PIL import ImageTk as _PImageTk
    _AA = True
except Exception:
    _AA = False

_AA_SS = 4  # 超采样倍数（4x 足够平滑，体积仍小）


def _rgb(hex_color):
    h = str(hex_color).lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _render_aa(bg, w, h, paint, ss=_AA_SS):
    """离屏 ss 倍分辨率绘制 → LANCZOS 缩小 → PhotoImage（抗锯齿）。

    paint(draw, W, H) 在放大后的画布上绘制，坐标需乘以 ss。
    """
    if not _AA:
        return None
    img = _PImage.new("RGB", (w * ss, h * ss), _rgb(bg))
    paint(_PDraw.Draw(img), w * ss, h * ss)
    return _PImageTk.PhotoImage(img.resize((w, h), _PImage.LANCZOS))


# ------------------------------------------------------------------ 自定义控件
class _Slider(tk.Canvas):
    """Canvas 自绘水平滑块：扁平胶囊轨道 + accent 填充 + 浅色小圆点。

    与胶囊开关同语言：轨道=TRACK、填充=ACCENT、拇指=TEXT（同开关旋钮），
    无描边光环，避免 iOS 风格与整体不一致。"""

    HEIGHT = 20
    RADIUS = 5
    TRACK_H = 4

    def __init__(self, parent, lo, hi, res, variable, command=None,
                 width=200, **kw):
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        kw.setdefault("bg", CARD)
        kw.setdefault("height", self.HEIGHT)
        super().__init__(parent, width=width, **kw)
        self.lo = float(lo)
        self.hi = float(hi)
        self.res = float(res)
        self.var = variable
        self.command = command
        self._req_w = int(width)   # 请求宽度；勿用 _w（tkinter 内部 Tcl 路径）
        self._xpad = self.RADIUS + 3
        self._drag = False
        self._img = None  # 持有 PhotoImage 引用，防止被 GC

        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", lambda e: self._redraw())
        self.var.trace_add("write", lambda *a: self._redraw())
        self._redraw()

    def _value_to_x(self, v):
        w = max(self.winfo_width(), self._xpad * 2 + 4)
        ratio = (max(self.lo, min(self.hi, float(v))) - self.lo) / (self.hi - self.lo)
        return self._xpad + ratio * (w - 2 * self._xpad)

    def _x_to_value(self, x):
        w = max(self.winfo_width(), self._xpad * 2 + 4)
        ratio = (max(self._xpad, min(w - self._xpad, x)) - self._xpad) / (w - 2 * self._xpad)
        raw = self.lo + ratio * (self.hi - self.lo)
        if self.res >= 1:
            raw = round(raw / self.res) * self.res
        else:
            # 保留与 resolution 相同的小数位，避免浮点噪音
            places = max(0, len(str(self.res).split(".")[-1]))
            raw = round(raw / self.res) * self.res
            raw = round(raw, places)
        return max(self.lo, min(self.hi, raw))

    def _redraw(self):
        self.delete("all")
        w = max(self.winfo_width(), self._req_w, self._xpad * 2 + 8)
        h = self.HEIGHT
        cy = h // 2
        v = float(self.var.get())
        ratio = (max(self.lo, min(self.hi, v)) - self.lo) / (self.hi - self.lo)
        x = self._xpad + ratio * (w - 2 * self._xpad)
        bg = self.cget("bg")
        ss = _AA_SS

        if _AA:
            try:
                def paint(dr, W, H):
                    s = ss
                    y0 = H // 2
                    x0 = self._xpad * s
                    x1 = (w - self._xpad) * s
                    tx = int(round(x * s))
                    tr = self.TRACK_H * s // 2
                    # 圆头胶囊轨道
                    dr.rounded_rectangle([x0, y0 - tr, x1, y0 + tr],
                                         radius=tr, fill=_rgb(TRACK))
                    if tx - x0 > s:
                        dr.rounded_rectangle([x0, y0 - tr, tx, y0 + tr],
                                             radius=tr, fill=_rgb(ACCENT))
                    k = self.RADIUS * s
                    dr.ellipse([tx - k, y0 - k, tx + k, y0 + k],
                               fill=_rgb(WHITE))
                self._img = _render_aa(bg, w, h, paint)
                self.create_image(0, 0, anchor="nw", image=self._img)
                return
            except Exception:
                pass  # 降级到下方 Canvas 原始绘制

        # 旧绘制路径（PIL 缺失时的降级，无抗锯齿）
        self.create_line(self._xpad, cy, w - self._xpad, cy,
                         fill=TRACK, width=self.TRACK_H, capstyle="round")
        if x > self._xpad + 1:
            self.create_line(self._xpad, cy, x, cy,
                             fill=ACCENT, width=self.TRACK_H, capstyle="round")
        self.create_oval(x - self.RADIUS, cy - self.RADIUS,
                         x + self.RADIUS, cy + self.RADIUS,
                         fill=WHITE, outline="")

    def _on_press(self, e):
        self._drag = True
        self._set_from_x(e.x)

    def _on_drag(self, e):
        if self._drag:
            self._set_from_x(e.x)

    def _on_release(self, _e):
        self._drag = False

    def _set_from_x(self, x):
        v = self._x_to_value(x)
        if abs(float(self.var.get()) - v) > 1e-9:
            self.var.set(v)
            if self.command:
                self.command(v)


class _Toggle(tk.Canvas):
    """Canvas 自绘胶囊开关。"""

    W = 40
    H = 22
    R = 9

    def __init__(self, parent, variable, command=None, **kw):
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        kw.setdefault("bg", CARD)
        kw.setdefault("width", self.W)
        kw.setdefault("height", self.H)
        super().__init__(parent, **kw)
        self.var = variable
        self.command = command
        self._img = None  # 持有 PhotoImage 引用，防止被 GC
        self.bind("<Button-1>", self._toggle)
        self.var.trace_add("write", lambda *a: self._redraw())
        self._redraw()

    def _toggle(self, _e):
        self.var.set(not self.var.get())
        if self.command:
            self.command(self.var.get())

    def _redraw(self):
        self.delete("all")
        on = bool(self.var.get())
        w, h = self.W, self.H
        cy = h // 2
        ss = _AA_SS
        # 旋钮直径比胶囊内高小 4px，留出内边距，观感更精密
        kr = min(self.R - 1, cy - 2)
        m = cy - kr  # 水平内边距

        if _AA:
            try:
                def paint(dr, W, H):
                    s = ss
                    # 药丸形：两端半圆
                    pill = [s, s, w * s - s, h * s - s]
                    dr.rounded_rectangle(pill, radius=(cy - 1) * s,
                                         fill=_rgb(ACCENT if on else TRACK),
                                         outline=None if on else _rgb(LINE),
                                         width=s)
                    k = kr * s
                    cx = (w - m - kr) * s if on else (m + kr) * s
                    cyy = cy * s
                    dr.ellipse([cx - k, cyy - k, cx + k, cyy + k],
                               fill=_rgb(WHITE if on else SUB))
                self._img = _render_aa(self.cget("bg"), w, h, paint)
                self.create_image(0, 0, anchor="nw", image=self._img)
                return
            except Exception:
                pass  # 降级到 Canvas 原始绘制

        # 旧绘制路径（PIL 缺失时的降级，无抗锯齿）
        self.create_oval(1, 1, w - 1, h - 1,
                         fill=ACCENT if on else TRACK,
                         outline="" if on else LINE, width=1)
        knob_x = w - m - kr if on else m + kr
        self.create_oval(knob_x - kr, cy - kr, knob_x + kr, cy + kr,
                         fill=WHITE if on else SUB, outline="")


class _NavIcon(tk.Canvas):
    """18x18 线性图标；PIL 超采样抗锯齿渲染（背景随 Canvas bg 融合）。"""

    W = 18

    def __init__(self, parent, name, color=SUB):
        super().__init__(parent, width=self.W, height=self.W, bg=SIDEBAR_BG,
                         highlightthickness=0, bd=0)
        self.name = name
        self.color = color
        self._img = None  # 持有 PhotoImage 引用，防止被 GC
        self.repaint()

    def set_color(self, c):
        self.color = c
        self.repaint()

    def repaint(self, color=None):
        """重绘。底色来自 Canvas 当前 bg（导航 hover/激活改色后调用）。"""
        if color is not None:
            self.color = color
        self.delete("all")
        if _AA:
            try:
                self._img = _render_aa(self.cget("bg"), self.W, self.W,
                                       self._paint)
                self.create_image(0, 0, anchor="nw", image=self._img)
                return
            except Exception:
                pass  # 降级到 Canvas 原始绘制
        self._paint_legacy()

    # ---------- PIL 抗锯齿绘制 ----------
    def _paint(self, dr, W, H):
        s = W // self.W
        col = _rgb(self.color)

        def line(pts, width=1.5):
            dr.line([v * s for v in pts], fill=col, width=max(1, int(width * s)),
                    joint="curve")

        def dot(cx, cy, r=1):
            rr = r * s
            dr.ellipse([cx * s - rr, cy * s - rr, cx * s + rr, cy * s + rr],
                       fill=col)

        def ring(cx, cy, r, width=1.5):
            rr = r * s
            dr.ellipse([cx * s - rr, cy * s - rr, cx * s + rr, cy * s + rr],
                       outline=col, width=max(1, int(width * s)))

        n = self.name
        if n == "下雨":
            for x0 in (6, 10, 14):
                line([x0, 5, x0 - 2, 11])
        elif n == "下雪":
            import math
            c = 9
            for ang in (0, 60, 120):
                rad = math.radians(ang)
                line([c - 5 * math.cos(rad), c - 5 * math.sin(rad),
                      c + 5 * math.cos(rad), c + 5 * math.sin(rad)])
            dot(c, c, 1.5)
        elif n == "落樱":
            # 顶端两个小瓣中间凹一下 = 花瓣的凹口，底端收成尖
            line([5.5, 5.5, 7.5, 3.0, 9.0, 5.0, 10.5, 3.0, 12.5, 5.5,
                  12.5, 10.5, 9.0, 15.0, 5.5, 10.5, 5.5, 5.5])
        elif n == "流星雨":
            # 一条斜向的长尾：头部实心点 + 尾巴由粗到细三笔
            line([4.5, 13.5, 13.5, 4.5])
            dot(14.0, 4.0, 1.6)
            dot(12.4, 5.6, 1.2)
        elif n == "颜料盘":
            # 画家调色板：椭圆盘 + 拇指孔 + 三坨颜料
            ring(9.5, 9.5, 6.0, 1.4)
            dot(5.6, 6.4, 1.5)
            dot(10.6, 5.6, 1.4)
            dot(6.2, 11.2, 1.3)
            ring(12.4, 12.6, 1.5, 1.2)
        elif n == "聚光灯":
            ring(9, 8, 4)
            line([9, 1, 9, 3])
            line([9, 13, 9, 15])
            line([1, 8, 3, 8])
            line([15, 8, 17, 8])
        elif n == "窗口水波":
            line([3, 4, 15, 4, 15, 14, 3, 14, 3, 4])
            line([5, 11, 7, 9, 9, 11, 11, 9, 13, 11])
        elif n == "涟漪":
            ring(9, 8, 3)
            ring(9, 8, 6)
        elif n == "真实水波":
            # 三条横向水波线 —— 与「涟漪」的同心圆区分开，一眼能认出是"水面"
            for yy in (5.4, 9.0, 12.6):
                line([3, yy, 5.4, yy - 1.7, 7.8, yy, 10.2, yy + 1.7, 12.6, yy,
                      15, yy - 1.7], width=1.4)
        else:  # 其它 / 设置：三条水平滑块
            for y, x1, x2 in ((5, 3, 9), (9, 6, 12), (13, 4, 10)):
                line([x1, y, x2, y])
                dot(x2 + 2, y, 1.5)

    # ---------- 无 PIL 的降级 ----------
    def _paint_legacy(self):
        n = self.name
        w = 1.5
        if n == "下雨":
            self._legacy_line([6, 5, 4, 11], w)
            self._legacy_line([10, 5, 8, 11], w)
            self._legacy_line([14, 5, 12, 11], w)
        elif n == "下雪":
            import math
            c = 9
            for ang in (0, 60, 120):
                rad = math.radians(ang)
                self._legacy_line([c - 5 * math.cos(rad), c - 5 * math.sin(rad),
                                   c + 5 * math.cos(rad), c + 5 * math.sin(rad)], w)
            self.create_oval(c - 1.5, c - 1.5, c + 1.5, c + 1.5,
                             fill=self.color, outline="")
        elif n == "落樱":
            self._legacy_line([5.5, 5.5, 7.5, 3.0, 9.0, 5.0, 10.5, 3.0,
                               12.5, 5.5, 12.5, 10.5, 9.0, 15.0, 5.5, 10.5,
                               5.5, 5.5], w)
        elif n == "流星雨":
            self._legacy_line([4.5, 13.5, 13.5, 4.5], w)
            self.create_oval(12.6, 2.6, 15.4, 5.4, fill=self.color, outline="")
        elif n == "颜料盘":
            self.create_oval(3.5, 3.5, 15.5, 15.5, outline=self.color, width=w)
            for cx, cy in ((5.6, 6.4), (10.6, 5.6), (6.2, 11.2)):
                self.create_oval(cx - 1.5, cy - 1.5, cx + 1.5, cy + 1.5,
                                 fill=self.color, outline="")
            self.create_oval(10.9, 11.1, 13.9, 14.1, outline=self.color, width=w)
        elif n == "聚光灯":
            self.create_oval(5, 4, 13, 12, outline=self.color, width=w)
            self._legacy_line([9, 1, 9, 3], w)
            self._legacy_line([9, 13, 9, 15], w)
            self._legacy_line([1, 8, 3, 8], w)
            self._legacy_line([15, 8, 17, 8], w)
        elif n == "窗口水波":
            self._legacy_line([3, 4, 15, 4, 15, 14, 3, 14, 3, 4], w)
            self._legacy_line([5, 11, 7, 9, 9, 11, 11, 9, 13, 11], w)
        elif n == "涟漪":
            self.create_oval(6, 5, 12, 11, outline=self.color, width=w)
            self.create_oval(3, 2, 15, 14, outline=self.color, width=w)
        elif n == "真实水波":
            for yy in (5.4, 9.0, 12.6):
                self._legacy_line([3, yy, 5.4, yy - 1.7, 7.8, yy, 10.2, yy + 1.7,
                                   12.6, yy, 15, yy - 1.7], w)
        else:  # 其它 / 设置
            for y, x1, x2 in ((5, 3, 9), (9, 6, 12), (13, 4, 10)):
                self._legacy_line([x1, y, x2, y], w)
                self.create_oval(x2 + 2, y, x2 + 2, y, fill=self.color,
                                 outline="", width=3)

    def _legacy_line(self, coords, width=1.5):
        self.create_line(coords, fill=self.color, width=width,
                         capstyle="round", joinstyle="round")


# ------------------------------------------------------------------ 面板
class ControlPanel:
    W, H = 564, 742
    _MIN_H = 520            # 窗口最小高度（内容再少也别比这矮）
    _SCREEN_FRAC = 0.92     # 窗口最高不超过屏幕高度的这个比例

    def __init__(self, app):
        self.app = app
        self.root = tk.Tk()
        self.root.title("桌面特效")
        self.root.configure(bg=BG)
        # 构造期间先把窗口藏起来：页面被放进 canvas 的 window item，其尺寸只能
        # 在布局 pass（update_idletasks）里算出来，所以构造仍需 1~2 次 flush 来
        # 测各页高度、应用最终窗口高。这些 flush 全程在 withdraw 状态下进行，
        # 窗口不会上屏；全部就绪后 __init__ 末尾 deiconify + lift 一次性显示成品。
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-alpha", 0.97)
        self.root.attributes("-topmost", True)
        self._drag = (0, 0)

        # 控件注册表：用于预设切换时就地刷新
        self._ctrls = {}        # key -> {"kind","var",...}
        self._swatches = {}     # key -> refresh()
        self._pages = {}        # 分组名 -> 页面 Frame
        self._nav = {}          # 分组名 -> {"btn","icon","line"}
        self._preset_frames = {}  # 分组名 -> 预设区 Frame
        self._preset_grids = {}   # 分组名 -> 网格容器 Frame（持久，不随刷新销毁）
        self._preset_title = {}   # 分组名 -> “预设方案”标题 Label
        self._preset_cells = {}   # 分组名 -> {预设名: 单元格 Frame}
        self._active = "下雨"

        self._center()
        self._build()
        self._fit_height()
        self._apply_dwm()
        self._raise_top()
        self._poll()
        # 构造期间窗口一直 withdraw，到这里才一次性显示最终成品，避免启动闪动
        self.root.deiconify()
        self.root.lift()

    # ------------------------------------------------------------ 窗口
    def _center(self):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - self.W) // 2)
        y = max(0, (sh - self.H) // 2 - 20)
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")

    def _hwnd(self):
        """面板真正的顶层 hwnd。

        tk 的 winfo_id() 返回的是内层子窗口，无边框窗口的真实 hwnd 是它的
        parent。统一返回 int：ctypes 里"手动构造的 HWND(x)"没有 __int__，
        直接 int() 会把它当字节串解析并抛 ValueError，跨模块传递迟早踩雷。
        """
        try:
            hw = self.root.winfo_id()
            return hwnd_int(user32.GetParent(HWND(hw))) or hwnd_int(hw)
        except Exception:
            return 0

    def _apply_dwm(self):
        """尝试启用 Win11 圆角 + 亚克力背景；失败时静默回退。

        构造期间（窗口 withdraw 时）先同步做一遍，这样窗口在 __init__ 末尾
        deiconify 一次性出现时外观就已经是最终态，不会“先出现、再变圆角/模糊”。
        再 after(50) 补一遍兜底：个别系统要求窗口可见后才肯接受亚克力。
        """
        def _try():
            try:
                from _win32 import set_window_rounded, set_window_acrylic
                h = self._hwnd()
                if h:
                    set_window_rounded(h)
                    set_window_acrylic(h, rgba=(11, 14, 20, 210))
            except Exception:
                pass
        _try()
        self.root.after(50, _try)

    def _raise_top(self):
        try:
            h = self._hwnd()
            if h:
                # 把面板 hwnd 交给覆盖层：它做置顶保活时会把面板排除在
                # "谁盖住了我"之外，并在"面板压得住对手"时拿面板当插入锚点
                # （插到面板正下方 = 既复位又不遮住设置界面）。否则 overlay
                # 每 0.5s、面板每 2s 各抢一次 topmost 组顶部，互相翻转 z 序
                # -> 表现为"每隔几秒闪一下"。
                ovs = getattr(self.app, "overlays", None) \
                    or [getattr(self.app, "overlay", None)]
                for ov in ovs:
                    if ov is not None:
                        ov.set_panel_hwnd(h)
                # **面板隐藏时也要补置顶位**，不能跳过。覆盖层拿面板当插入锚点
                # 之前会先验"面板是不是置顶窗"：插到一个非置顶窗之后会把覆盖层
                # 自己的 WS_EX_TOPMOST 一起清掉（实测：清掉之后连抢 30 次都回不来，
                # 效果从此被任何普通窗口盖住）。而面板一旦从未被置顶过、又一直
                # 缩在托盘里，就会踩中这条。置顶位对隐藏窗口同样有效，也不改变
                # 可见性，所以这里无条件补。
                user32.SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception:
            pass
        # 1s 一次（早先 2s）：对手（Citrix 那类十几 Hz 重新置顶的全屏窗）在
        # 两次之间的窗口期里会把面板压下去，面板一被压住就不能再给覆盖层当
        # 天花板 —— 覆盖层此时会退回"插到置顶组顶部"（见 Overlay._anchor），
        # 所以不再是硬依赖，但面板勤快一点能让"面板浮在特效之上"这件事更稳。
        self.root.after(1000, self._raise_top)

    def _start_move(self, e):
        self._drag = (e.x_root, e.y_root)

    def _do_move(self, e):
        dx = e.x_root - self._drag[0]
        dy = e.y_root - self._drag[1]
        self._drag = (e.x_root, e.y_root)
        self.root.geometry(f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}")

    def hide(self):
        self.root.withdraw()

    def show(self):
        self.root.deiconify()
        self.root.lift()

    # ------------------------------------------------------------ 构建
    def _build(self):
        root = self.root

        # 标题栏
        bar = tk.Frame(root, bg=SIDEBAR_BG, height=42)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text="  桌面特效", bg=SIDEBAR_BG, fg=TEXT,
                 font=FONT_TITLE).pack(side="left")
        self.status = tk.Label(bar, text="", bg=SIDEBAR_BG, fg=SUB,
                               font=FONT_NUM)
        self.status.pack(side="left", padx=8)

        for txt, cmd, hov in (("—", self.hide, BAR_HOVER),
                              ("×", self.hide, DANGER)):
            b = tk.Button(bar, text=txt, bg=SIDEBAR_BG, fg=SUB, bd=0, relief="flat",
                          width=3, font=("Microsoft YaHei UI", 11),
                          activebackground=hov, activeforeground=WHITE, command=cmd)
            b.pack(side="right")
            b.bind("<Enter>", lambda e, w=b, c=hov: w.config(bg=c))
            b.bind("<Leave>", lambda e, w=b: w.config(bg=SIDEBAR_BG))

        bar.bind("<ButtonPress-1>", self._start_move)
        bar.bind("<B1-Motion>", self._do_move)

        # 错误横幅：默认不 pack
        self.err_bar = tk.Label(root, text="", bg=ERROR_BG, fg=ERROR_FG,
                                font=FONT_SMALL, anchor="w",
                                padx=10, pady=5, wraplength=470, justify="left")
        self._err_shown = None

        # 主区域
        main = tk.Frame(root, bg=BG)
        main.pack(fill="both", expand=True)
        self._main = main

        # ---- 侧栏 ----
        side = tk.Frame(main, bg=SIDEBAR_BG, width=120)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        # 注意：name 必须与 _pages 的注册键一致，否则点击后右侧空白。
        # 分组与顺序取自 fx_kinds.NAV_GROUPS —— 侧栏分类、渲染分层、同类
        # 互斥共用同一份定义，避免"界面归了类、别处还对不上"。
        for cat, pages in fx_kinds.NAV_GROUPS:
            self._nav_group(side, cat)
            for name in pages:
                self._nav_item(side, fx_kinds.page_title(name), name)
        self._nav_sep(side)
        self._nav_item(side, "其他设置", "其它")

        fb = tk.Frame(side, bg=SIDEBAR_BG)
        fb.pack(side="bottom", fill="x", pady=(8, 12))
        # 暂停/继续：运行=中性底；暂停=强调色底(与保存同语言)，不用暖棕
        self._paused = False
        self.pause_btn = tk.Button(fb, text="暂停", bg=BTN, fg=TEXT, bd=0,
                                   relief="flat", font=FONT_LABEL, pady=8,
                                   activebackground=ACCENT_HOVER,
                                   activeforeground=ON_ACCENT,
                                   command=self._pause)
        self.pause_btn.pack(fill="x", padx=10, pady=4)
        self.pause_btn.bind("<Enter>", lambda e: self._paint_pause(True))
        self.pause_btn.bind("<Leave>", lambda e: self._paint_pause(False))
        brow = tk.Frame(fb, bg=SIDEBAR_BG)
        brow.pack(fill="x", padx=4, pady=(0, 4))
        b2 = tk.Button(brow, text="隐藏到托盘", bg=BTN, fg=TEXT, bd=0, relief="flat",
                       font=FONT_LABEL, pady=8,
                       activebackground=BTN_H, command=self.hide)
        b2.pack(side="left", fill="x", expand=True, padx=(6, 2))
        b2.bind("<Enter>", lambda e: b2.config(bg=BTN_H))
        b2.bind("<Leave>", lambda e: b2.config(bg=BTN))
        b3 = tk.Button(brow, text="退出", bg=DANGER_SOFT, fg=DANGER_SOFT_FG,
                       bd=0, relief="flat", font=FONT_LABEL, pady=8,
                       activebackground=DANGER, activeforeground=WHITE,
                       command=self._quit)
        b3.pack(side="left", fill="x", expand=True, padx=(2, 6))
        b3.bind("<Enter>", lambda e: b3.config(bg=DANGER, fg=WHITE))
        b3.bind("<Leave>", lambda e: b3.config(bg=DANGER_SOFT, fg=DANGER_SOFT_FG))

        # ---- 内容区（无滚动条，滚轮仍可上下滚动）----
        cont = tk.Frame(main, bg=BG)
        cont.pack(side="left", fill="both", expand=True)
        canvas = tk.Canvas(cont, bg=BG, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        inner = tk.Frame(canvas, bg=BG)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: (canvas.itemconfig(win, width=e.width),
                               self._sync_scroll()))
        # 滚轮只绑在 canvas / inner 上是**不够**的：Tk 把滚轮事件发给光标正下方
        # 那个控件，不会冒泡到父容器 —— 鼠标停在页面里的卡片、滑块上时毫无反应，
        # 配置项一多，下面的"预设方案"就永远滚不到（颜料盘页踩过）。
        # 改成 bind_all + 归属判断：只有事件来自内容区才滚，对话框不受影响。
        canvas.bind("<MouseWheel>", self._on_wheel)
        inner.bind("<MouseWheel>", self._on_wheel)
        root.bind_all("<MouseWheel>", self._on_wheel)
        self._canvas = canvas
        self._inner = inner

        # ---- 构建各分组页面 ----
        builders = {
            "下雨": self._page_rain,
            "下雪": self._page_snow,
            "落樱": self._page_petal,
            "流星雨": self._page_meteor,
            "聚光灯": self._page_spot,
            "颜料盘": self._page_paint,
            "涟漪": self._page_ripple,
            "真实水波": self._page_wave,
            "窗口水波": self._page_win,
            "其它": self._page_other,
        }
        # 按侧栏顺序注册：_pages 的键序即导航顺序，测试直接比对二者
        for name in fx_kinds.nav_order():
            self._pages[name] = builders[name](inner)
        for f in self._pages.values():
            f.pack_forget()
        self._show_page(self._load_last_page())

        self._tip = tk.Label(root, text="托盘图标右键可唤出菜单    Ctrl+Alt+R 快速暂停",
                             bg=BG, fg=SUB, font=FONT_SMALL)
        self._tip.pack(pady=(0, 10))

    # ------------------------------------------------------------ 侧栏/导航
    def _nav_group(self, side, text):
        """侧栏分组标题：小一号的次级灰字，与导航项拉开层级。"""
        lbl = tk.Label(side, text=text, bg=SIDEBAR_BG, fg=SUB,
                       font=FONT_SMALL, anchor="w")
        lbl.pack(fill="x", padx=14, pady=(12, 2))

    def _nav_sep(self, side):
        """导航项与设置之间的发丝分隔线。"""
        tk.Frame(side, bg=LINE, height=1).pack(fill="x", padx=12, pady=(12, 4))

    def _nav_item(self, side, label, name):
        b = tk.Frame(side, bg=SIDEBAR_BG, height=44)
        b.pack(fill="x")
        b.pack_propagate(False)
        b.bind("<ButtonPress-1>", lambda e, n=name: self._show_page(n))

        line = tk.Frame(b, bg=SIDEBAR_BG, width=2)
        line.pack(side="left", fill="y", padx=(0, 6))
        icon = _NavIcon(b, name)
        icon.pack(side="left", padx=(4, 0), pady=12)
        txt = tk.Label(b, text=label, bg=SIDEBAR_BG, fg=TEXT,
                       font=FONT_LABEL, anchor="w")
        txt.pack(side="left", fill="x", expand=True, padx=(6, 0))
        txt.bind("<ButtonPress-1>", lambda e, n=name: self._show_page(n))

        def _enter(_e):
            if self._active != name:
                b.config(bg=NAV_HOVER)
                txt.config(bg=NAV_HOVER)
                line.config(bg=NAV_HOVER)
                icon.config(bg=NAV_HOVER)
                icon.repaint()  # 底色已变，重烘焙抗锯齿图

        def _leave(_e):
            if self._active != name:
                b.config(bg=SIDEBAR_BG)
                txt.config(bg=SIDEBAR_BG)
                line.config(bg=SIDEBAR_BG)
                icon.config(bg=SIDEBAR_BG)
                icon.repaint()

        for w in (b, txt):
            w.bind("<Enter>", _enter)
            w.bind("<Leave>", _leave)
        self._nav[name] = {"frame": b, "label": txt, "line": line, "icon": icon}

    def _on_wheel(self, e):
        """内容区滚轮。挂在 bind_all 上接收，但要判断事件来自哪 ——
        否则"保存为预设"对话框里滚动会把背后的页面一起滚走。

        内容装得下时**直接不滚**：这时候滚起来只能把顶部拉走、露出下面一片空白。
        （用户报过："下雨界面这些要素较少的，不要把顶部往下拉"。）
        """
        w = e.widget
        while w is not None:
            if w is self._canvas:
                break
            w = getattr(w, "master", None)
        else:
            return
        if not self._need_scroll():
            self._canvas.yview_moveto(0)
            return
        self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def _view_h(self):
        """内容区可用高度 = 窗口高 − chrome（标题栏 + 底部提示条）。

        **不要直接读 `canvas.winfo_height()`**：窗口尺寸变化后，canvas 的实际
        高度要等 Tk 处理完下一次 Configure 才跟上，在那之前读到的是旧值 ——
        自适应算窗口高度、判断要不要滚动时都会用错。
        """
        return max(0, int(self.root.winfo_height())
                   - getattr(self, "_chrome", 74))

    def _need_scroll(self):
        """当前页装不下、真的需要滚动吗？

        用**页面自身的请求高度**判断，而不是 canvas 的 scrollregion：切页那一
        瞬间 scrollregion 可能还是上一页（更高的那页）留下的旧值，会让"下雨"
        这种一屏放得下的页面也能被拉走。
        """
        f = self._pages.get(self._active)
        if f is None:
            return False
        return int(f.winfo_reqheight()) > self._view_h()

    def _sync_scroll(self):
        """重算滚动区域。装得下时把区域收成**视口高度**，滚轮就彻底动不了。"""
        bb = self._canvas.bbox("all")
        if not bb:
            return
        vh = self._view_h()
        top, bottom = int(bb[1]), int(bb[3])
        if bottom - top <= vh:
            self._canvas.configure(scrollregion=(bb[0], top, bb[2], top + vh))
            self._canvas.yview_moveto(0)
        else:
            self._canvas.configure(scrollregion=(bb[0], top, bb[2], bottom))

    def _fit_height(self):
        """按内容把窗口调高到"最长的那个页面刚好装得下"，上限为屏幕的 92%。

        窗口尺寸本来写死 564x742，而各页内容高度差很大（下雨 ~594、颜料盘
        ~717）。只要系统缩放不是 100%（字体按 DPI 放大、控件整体变高）或者
        用户多存了几个自定义预设，连"下雨"这种要素很少的页面都会溢出 ——
        滚轮于是能把顶部拉走，看着莫名其妙。让窗口跟着内容长高（不超出屏幕）
        能从根上避免；真的装不下（屏幕不够高）时才滚动。
        """
        # 堆叠 pack 全部页面，单次 flush 即可测量每页自然高度（canvas window item
        # 的尺寸只在布局 pass 里算出，但 10 页只需一次 flush，不必逐页 pack+flush
        # —— 旧实现每页一次、共 10 次，是 23 次刷新里的大头）。flush 时窗口处于
        # withdraw 状态，不会上屏。
        for f in self._pages.values():
            f.pack()
        self.root.update_idletasks()
        hs = {n: int(f.winfo_reqheight()) for n, f in self._pages.items()}
        # chrome = 标题栏(固定 42) + 底部提示条(自身高度 + 下边距 10)。
        # 用「固定值 + 提示条请求高度」解析算出，不依赖窗口实际像素：构造期窗口
        # 处于 withdraw，winfo_height() 是占位值 1，直接相减会得到 chrome≈42 的
        # 假值，tallest 页会被算短 30+ 像素而误触发滚动。提示条是 root 的直接子
        # 控件，其 reqheight 在未 flush 时即可得（探针验证 = 23）。
        if getattr(self, "_chrome", None) is None:
            self._chrome = 42 + self._tip.winfo_reqheight() + 10
        chrome = self._chrome
        content = max(hs.values()) if hs else 0
        self._content_h = content
        cap = int(self.root.winfo_screenheight() * self._SCREEN_FRAC)
        h = max(self._MIN_H, min(content + chrome, cap))
        # 不能只在 `h != self.H` 时才应用：窗口高度可能被外部改过（测试里压矮
        # 造溢出、将来若支持拖拽缩放），而 H 没变 -> 就恢复不回来了。
        if h != self.H or int(self.root.winfo_height()) != h:
            self.H = h
            self._center()
            self.root.update_idletasks()     # 落实最终高度（仍在 withdraw 期，
                                            # 仅写 geometry，不触发可视上屏）
        # 恢复当前页（pack fill + 复位滚动）；滚动区交给 <Configure> 绑定自愈，
        # 此处不再 flush。
        self._show_page(self._active)

    def _show_page(self, name):
        for n, f in self._pages.items():
            if n == name:
                f.pack(fill="x")
            else:
                f.pack_forget()
        self._active = name
        self._save_last_page(name)
        for n, info in self._nav.items():
            if n == name:
                info["frame"].config(bg=NAV_ACTIVE)
                info["label"].config(bg=NAV_ACTIVE, fg=ACCENT)
                info["line"].config(bg=ACCENT)
                info["icon"].config(bg=NAV_ACTIVE)
                info["icon"].set_color(ACCENT)
            else:
                info["frame"].config(bg=SIDEBAR_BG)
                info["label"].config(bg=SIDEBAR_BG, fg=TEXT)
                info["line"].config(bg=SIDEBAR_BG)
                info["icon"].config(bg=SIDEBAR_BG)
                info["icon"].set_color(SUB)
        # 切页后强制 canvas 局部布局一次，让滚动区（scrollregion）立即基于新页
        # 内容算准——滚轮处理是同步读滚动区的，若等事件循环自愈，切页当帧滚轮
        # 会读到旧区域。只 flush canvas 子树（不是整窗），开销极小、不会重映射窗口。
        self._canvas.update_idletasks()
        self._canvas.yview_moveto(0)
        self._sync_scroll()

    def _load_last_page(self):
        try:
            with open(_UI_STATE_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            name = d.get("last_page")
            if name in self._pages:
                return name
        except Exception:
            pass
        return "下雨"

    def _save_last_page(self, name):
        try:
            with open(_UI_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump({"last_page": name}, f, ensure_ascii=False)
        except Exception:
            pass

    def _page_head(self, parent, text):
        """页头大标题 + 左侧强调色短标线。

        字号比 9pt 行标签高两级，与顶栏程序名区分层级；强调色只用
        一段短标线做品牌提示，避免整页标题染成大色块。"""
        tk.Label(parent, text=text, bg=BG, fg=TEXT,
                 font=FONT_H2).pack(anchor="w", padx=16, pady=(16, 5))
        bar = tk.Frame(parent, bg=ACCENT, width=28, height=2)
        bar.pack(anchor="w", padx=16, pady=(0, 9))
        bar.pack_propagate(False)

    # ------------------------------------------------------------ 分组页
    def _preset_block(self, parent, page_key):
        """“预设方案”独立分区：标题 + 各方案按钮。

        保存按钮不在本区（它在配置属性卡片底部，见 _save_bar）。
        本区构件持久化，增删预设只动单元格，避免整列表闪烁。"""
        frame = tk.Frame(parent, bg=CARD)
        frame.pack(fill="x", padx=16, pady=(0, 16))

        title = tk.Label(frame, text="预设方案", bg=CARD, fg=SUB,
                         font=(FONT_LABEL[0], FONT_LABEL[1], "bold"))
        title.pack(anchor="w", padx=12, pady=(10, 4))
        pf = tk.Frame(frame, bg=CARD)
        pf.pack(fill="x", padx=8, pady=(0, 10))

        self._preset_frames[page_key] = frame
        self._preset_grids[page_key] = pf
        self._preset_title[page_key] = title
        self._preset_cells[page_key] = {}
        self._refresh_presets(page_key)
        return frame

    def _save_bar(self, card, page_key):
        """“保存当前为预设”主操作：追加到配置属性卡片底部，与属性同块。"""
        tk.Frame(card, height=1, bg=LINE).pack(fill="x", padx=16, pady=(12, 8))
        btn = tk.Button(card, text="保存当前为预设", bg=ACCENT, fg=ON_ACCENT, bd=0,
                        relief="flat", font=FONT_LABEL, pady=7, cursor="hand2",
                        activebackground=ACCENT_HOVER,
                        command=lambda: self._save_preset_dialog(page_key))
        btn.pack(fill="x", padx=16, pady=(0, 14))
        btn.bind("<Enter>", lambda e, w=btn: w.config(bg=ACCENT_HOVER))
        btn.bind("<Leave>", lambda e, w=btn: w.config(bg=ACCENT))

    def _show_preset_section(self, page_key):
        frame = self._preset_frames.get(page_key)
        if frame is None:
            return
        if frame.winfo_manager() != "pack":
            frame.pack(fill="x", padx=16, pady=(0, 16))
        title = self._preset_title.get(page_key)
        if title is not None and title.winfo_manager() != "pack":
            title.pack(anchor="w", padx=12, pady=(10, 4))

    def _hide_preset_section(self, page_key):
        frame = self._preset_frames.get(page_key)
        if frame is not None:
            frame.pack_forget()

    def _refresh_presets(self, page_key):
        """重建某页的预设方案单元格。仅销毁/重建单元格本身，分区标题等
        持久构件原位不动，杜绝整列表闪烁。"""
        pf = self._preset_grids.get(page_key)
        if pf is None:
            return
        cells = self._preset_cells.setdefault(page_key, {})
        # 清空现有单元格（只清格子，不动分区/标题）
        for c in cells.values():
            c.destroy()
        cells.clear()

        names = self.app.presets_for_page(page_key)
        if names:
            self._show_preset_section(page_key)
            for i, name in enumerate(names):
                cell = self._make_preset_cell(pf, name)
                cell.grid(row=i // 3, column=i % 3, padx=4, pady=4, sticky="ew")
                pf.grid_columnconfigure(i % 3, weight=1)
                cells[name] = cell
        else:
            self._hide_preset_section(page_key)
        # 不 flush：新增/移除单元格后，inner 的 <Configure> 会在下一轮布局时
        # 自动重算 scrollregion，无需在此强制整窗布局。

    def _make_preset_cell(self, pf, name):
        """构造单个预设单元格（含可选的删除按钮）。"""
        is_user = self.app.is_user_preset(name)
        cell = tk.Frame(pf, bg=CARD2)
        cell.grid_columnconfigure(0, weight=1)

        b = tk.Button(cell, text=name, bg=CARD2, fg=TEXT, bd=0, relief="flat",
                      font=FONT_SMALL, padx=4, pady=4, cursor="hand2",
                      activebackground=BTN_H,
                      command=lambda n=name: self._preset(n))
        b.grid(row=0, column=0, sticky="ew")
        if is_user:
            del_btn = tk.Button(cell, text="×", bg=CARD2, fg=SUB,
                                bd=0, relief="flat",
                                font=("Microsoft YaHei UI", 9),
                                cursor="hand2",
                                activebackground=CARD2,
                                activeforeground=DANGER_TEXT,
                                command=lambda n=name: self._delete_preset(n))
            del_btn.grid(row=0, column=1, padx=(0, 4))
            del_btn.bind("<Enter>", lambda e, w=del_btn: w.config(fg=DANGER_TEXT))
            del_btn.bind("<Leave>", lambda e, w=del_btn: w.config(fg=SUB))
        else:
            tk.Label(cell, text="", bg=CARD2, width=0).grid(row=0, column=1)

        b.bind("<Enter>", lambda e, w=b: w.config(bg=BTN_H))
        b.bind("<Leave>", lambda e, w=b: w.config(bg=CARD2))
        return cell

    def _append_preset_widget(self, page, name):
        """新增预设后只追加一格，不重建整列表。"""
        pf = self._preset_grids.get(page)
        if pf is None:
            return
        cells = self._preset_cells.setdefault(page, {})
        if name in cells:
            return
        cell = self._make_preset_cell(pf, name)
        i = len(cells)
        cell.grid(row=i // 3, column=i % 3, padx=4, pady=4, sticky="ew")
        pf.grid_columnconfigure(i % 3, weight=1)
        cells[name] = cell
        # 从无到有时把分区与标题也亮出来
        self._show_preset_section(page)
        # 不 flush：见 _refresh_presets 说明，scrollregion 由 <Configure> 自愈。

    def _save_preset_dialog(self, page_key):
        top = tk.Toplevel(self.root)
        top.title("保存预设")
        top.configure(bg=BG)
        top.transient(self.root)
        top.grab_set()
        top.resizable(False, False)
        top.geometry("+{}+{}".format(self.root.winfo_x() + 120,
                                     self.root.winfo_y() + 300))
        top.overrideredirect(False)
        top.attributes("-topmost", True)

        tk.Label(top, text="预设名称", bg=BG, fg=TEXT, font=FONT_LABEL).pack(
            anchor="w", padx=16, pady=(16, 6))
        ent = tk.Entry(top, bg=CARD2, fg=TEXT, insertbackground=ACCENT,
                       relief="flat", font=FONT_LABEL)
        ent.pack(fill="x", padx=16, pady=(0, 16))
        ent.focus_set()

        msg = tk.Label(top, text="", bg=BG, fg=DANGER_TEXT, font=FONT_SMALL)
        msg.pack(fill="x", padx=16)

        def _save():
            name = ent.get()
            ok, info = self.app.save_preset(name, page_key)
            if ok:
                self._append_preset_widget(page_key, name)
                top.destroy()
            else:
                msg.config(text=info)

        def _cancel():
            top.destroy()

        btn_row = tk.Frame(top, bg=BG)
        btn_row.pack(fill="x", padx=16, pady=16)
        tk.Button(btn_row, text="取消", bg=BTN, fg=TEXT, bd=0, relief="flat",
                  font=FONT_LABEL, pady=6, command=_cancel).pack(
            side="left", fill="x", expand=True, padx=(0, 6))
        tk.Button(btn_row, text="保存", bg=ACCENT, fg=ON_ACCENT, bd=0,
                  relief="flat", font=FONT_LABEL, pady=6, command=_save).pack(
            side="left", fill="x", expand=True, padx=(6, 0))
        ent.bind("<Return>", lambda e: _save())

    def _delete_preset(self, name):
        # 必须先取归属页再删除：删除后 user_presets 里已无该预设，
        # preset_page 会拿不到 _page 标签、回退到 "其它"，从而刷新了错误的帧，
        # 表现为"点删除不立即消失，要等下次新增才刷新"。
        page = self.app.preset_page(name)
        ok, _ = self.app.delete_preset(name)
        if ok:
            self._remove_preset_widget(page, name)

    def _remove_preset_widget(self, page, name):
        """只销毁被删除的那一格，其余单元格就地重排（不销毁重建），
        因此不会整列表闪烁。"""
        cells = self._preset_cells.get(page, {})
        cell = cells.pop(name, None)
        if cell is not None:
            cell.destroy()
        pf = self._preset_grids.get(page)
        if pf is not None:
            names = list(cells.keys())
            for i, n in enumerate(names):
                cells[n].grid(row=i // 3, column=i % 3, padx=4, pady=4, sticky="ew")
                pf.grid_columnconfigure(i % 3, weight=1)
        # 不 flush：scrollregion 由 inner 的 <Configure> 自愈。
        if not cells:
            self._hide_preset_section(page)

    def _page_rain(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "下雨")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用下雨", "rain_enabled")
        self._slider(c, "雨量", "rain_amount", 0, 800, 5, "{:.0f} 滴")
        self._slider(c, "下落速度", "rain_speed", 200, 2200, 10, "{:.0f} 像素/秒")
        self._slider(c, "雨丝长度", "rain_length", 0.3, 2.6, 0.05, "{:.2f} 倍")
        self._slider(c, "风力（左负右正）", "wind", -1.0, 1.0, 0.01, "{:+.2f}")
        self._slider(c, "浓度", "rain_opacity", 0.05, 1.0, 0.01, "{:.0%}")
        # 溅水花属于下雨本身（它只在雨滴落到底边时触发），从"其它"页搬过来
        self._toggle(c, "雨滴落地溅水花", "splash")
        self._swatch(c, "雨丝颜色", "rain_color", RAIN_COLORS)
        self._save_bar(c, "下雨")
        self._preset_block(p, "下雨")
        return p

    def _page_snow(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "下雪")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用下雪", "snow_enabled")
        self._slider(c, "雪量", "snow_amount", 0, 800, 5, "{:.0f} 片")
        self._slider(c, "飘落速度", "snow_speed", 20, 300, 5, "{:.0f} 像素/秒")
        self._slider(c, "雪花大小", "snow_size", 0.4, 2.5, 0.1, "{:.1f} 倍")
        self._slider(c, "横向漂移", "snow_drift", -0.6, 0.6, 0.01, "{:+.2f}")
        self._slider(c, "风速(阵风)", "snow_wind", -180, 180, 5, "{:+.0f} 像素/秒")
        self._slider(c, "闪烁", "snow_twinkle", 0.0, 0.7, 0.05, "{:.0%}")
        self._slider(c, "辉光", "snow_glow", 0.0, 1.0, 0.05, "{:.0%}")
        self._slider(c, "浓度", "snow_opacity", 0.05, 1.0, 0.01, "{:.0%}")
        self._swatch(c, "雪花颜色", "snow_color", SNOW_COLORS)
        self._save_bar(c, "下雪")
        self._preset_block(p, "下雪")
        return p

    def _page_petal(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "落樱")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用落樱", "petal_enabled")
        self._slider(c, "花瓣数量", "petal_amount", 0, 600, 5, "{:.0f} 片")
        self._slider(c, "飘落速度", "petal_speed", 10, 260, 5, "{:.0f} 像素/秒")
        self._slider(c, "花瓣大小", "petal_size", 0.4, 2.5, 0.1, "{:.1f} 倍")
        self._slider(c, "横向漂移", "petal_drift", -0.6, 0.6, 0.01, "{:+.2f}")
        self._slider(c, "风速(阵风)", "petal_wind", -180, 180, 5, "{:+.0f} 像素/秒")
        self._slider(c, "自旋", "petal_spin", 0.0, 2.0, 0.05, "{:.2f} 倍")
        self._slider(c, "翻面", "petal_flutter", 0.0, 1.0, 0.05, "{:.0%}")
        self._slider(c, "浓度", "petal_opacity", 0.05, 1.0, 0.01, "{:.0%}")
        self._swatch(c, "花瓣颜色", "petal_color", PETAL_COLORS)
        self._swatch(c, "边缘颜色(翻面泛白)", "petal_edge_color", PETAL_EDGE_COLORS)
        self._save_bar(c, "落樱")
        self._preset_block(p, "落樱")
        return p

    def _page_meteor(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "流星雨")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用流星雨", "meteor_enabled")
        self._slider(c, "流星数量", "meteor_amount", 0, 120, 1, "{:.0f} 颗")
        self._slider(c, "飞行速度", "meteor_speed", 120, 2400, 20, "{:.0f} 像素/秒")
        self._slider(c, "拖尾长度", "meteor_length", 40, 900, 10, "{:.0f} 像素")
        self._slider(c, "飞行角度", "meteor_angle", -80, 80, 1, "{:+.0f} °")
        self._slider(c, "流星粗细", "meteor_size", 0.4, 3.0, 0.1, "{:.1f} 倍")
        self._slider(c, "头部辉光", "meteor_glow", 0.0, 1.0, 0.05, "{:.0%}")
        self._slider(c, "星空密度", "meteor_stars", 0.0, 1.0, 0.05, "{:.0%}")
        self._slider(c, "浓度", "meteor_opacity", 0.05, 1.0, 0.01, "{:.0%}")
        self._swatch(c, "头部颜色", "meteor_color", METEOR_COLORS)
        self._swatch(c, "拖尾颜色", "meteor_tail_color", METEOR_TAIL_COLORS)
        self._save_bar(c, "流星雨")
        self._preset_block(p, "流星雨")
        return p

    def _page_paint(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "颜料盘")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用颜料盘", "paint_enabled")
        self._slider(c, "笔刷大小", "paint_brush", 0.3, 3.0, 0.1, "{:.1f} 倍")
        self._slider(c, "落色量", "paint_flow", 0.0, 2.5, 0.05, "{:.2f} 倍")
        self._slider(c, "搅动强度", "paint_swirl", 0.0, 2.5, 0.05, "{:.2f} 倍")
        self._slider(c, "点击扩散", "paint_click", 0.0, 2.5, 0.05, "{:.2f} 倍")
        self._slider(c, "晕染", "paint_diffusion", 0.0, 1.0, 0.05, "{:.0%}")
        self._slider(c, "消隐速度", "paint_fade", 0.0, 1.0, 0.05, "{:.0%}")
        self._slider(c, "辉光", "paint_glow", 0.0, 1.0, 0.05, "{:.0%}")
        self._slider(c, "立体感", "paint_shading", 0.0, 1.0, 0.05, "{:.0%}")
        self._slider(c, "色相铺开", "paint_hue_spread", 0.0, 1.0, 0.05, "{:.0%}")
        self._slider(c, "浓度", "paint_opacity", 0.05, 1.0, 0.01, "{:.0%}")
        self._swatch(c, "基准色", "paint_color", PAINT_COLORS)
        self._save_bar(c, "颜料盘")
        self._preset_block(p, "颜料盘")
        return p

    def _page_spot(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "聚光灯")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用聚光灯", "spotlight_enabled")
        self._slider(c, "亮区半径", "spotlight_radius", 80, 600, 10, "{:.0f} 像素")
        self._slider(c, "压暗强度", "spotlight_dim", 0.1, 0.95, 0.01, "{:.0%}")
        self._swatch(c, "压暗颜色", "spotlight_color", SPOTLIGHT_COLORS)
        self._save_bar(c, "聚光灯")
        self._preset_block(p, "聚光灯")
        return p

    def _page_win(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "窗口水波")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用窗口水波", "win_ripple_enabled")
        self._slider(c, "波大小", "win_ripple_size", 0.4, 3.0, 0.1, "{:.1f} 倍")
        self._slider(c, "存续时间", "win_ripple_life", 0.3, 2.5, 0.05, "{:.2f} 秒")
        self._slider(c, "浓度", "win_ripple_strength", 0.05, 1.0, 0.01, "{:.0%}")
        self._slider(c, "边缘密度", "win_ripple_edge_density", 4, 40, 1, "{:.0f} 点")
        self._swatch(c, "涟漪颜色", "win_ripple_color", RIPPLE_COLORS)
        self._save_bar(c, "窗口水波")
        self._preset_block(p, "窗口水波")
        return p

    def _page_ripple(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "鼠标涟漪")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用鼠标涟漪", "ripple_enabled")
        self._toggle(c, "跟随鼠标移动", "ripple_move")
        self._toggle(c, "点击时扩散", "ripple_click")
        self._slider(c, "触发灵敏度", "ripple_step", 6, 90, 1, "{:.0f} 像素")
        self._slider(c, "涟漪大小", "ripple_size", 40, 420, 5, "{:.0f} 像素")
        self._slider(c, "存续时间", "ripple_life", 0.3, 2.5, 0.05, "{:.2f} 秒")
        self._slider(c, "浓度", "ripple_opacity", 0.05, 1.0, 0.01, "{:.0%}")
        self._swatch(c, "涟漪颜色", "ripple_color", RIPPLE_COLORS)
        self._save_bar(c, "涟漪")
        self._preset_block(p, "涟漪")
        return p

    def _page_wave(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "真实水波")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用真实水波", "wave_enabled")
        self._toggle(c, "跟随鼠标移动", "wave_move")
        self._toggle(c, "点击时落一大滴", "wave_click")
        self._slider(c, "波速", "wave_speed", 60, 300, 10, "{:.0f} 像素/秒")
        self._slider(c, "余波时长", "wave_persist", 0.0, 1.0, 0.05, "{:.0%}")
        self._slider(c, "落点大小", "wave_size", 8, 60, 2, "{:.0f} 像素")
        self._slider(c, "落点强度", "wave_strength", 0.02, 0.30, 0.01, "{:.2f}")
        self._slider(c, "触发间距", "wave_step", 8, 80, 2, "{:.0f} 像素")
        self._slider(c, "自动落点间隔", "wave_ambient", 0.0, 8.0, 0.2, "{:.1f} 秒")
        self._slider(c, "光照方向", "wave_light", 0, 350, 10, "{:.0f} 度")
        self._slider(c, "迎光提亮", "wave_contrast", 0.0, 1.2, 0.05, "{:.0%}")
        self._slider(c, "背光压暗", "wave_shading", 0.0, 1.0, 0.05, "{:.0%}")
        self._slider(c, "浪尖高光", "wave_highlight", 0.0, 1.2, 0.05, "{:.0%}")
        # 上限 20%：用户要求"浓度限制在 20 以内"。它是一层叠在桌面上的半透明条纹，
        # 浓度 = "桌面被盖住多少"，超过 20% 就开始影响看桌面了。
        self._slider(c, "浓度", "wave_opacity", 0.02, 0.20, 0.01, "{:.0%}")
        self._swatch(c, "高光颜色", "wave_color", WAVE_COLORS)
        self._save_bar(c, "真实水波")
        self._preset_block(p, "真实水波")
        return p

    def _page_other(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "其他设置")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._slider(c, "帧率上限", "fps", 24, 144, 1, "{:.0f} 帧/秒")
        # 开机自启用与其它开关同款的胶囊控件，避免老式 Checkbutton 破坏统一
        row = tk.Frame(c, bg=CARD)
        row.pack(fill="x", padx=16, pady=8)
        tk.Label(row, text="开机时自动启动", bg=CARD, fg=TEXT,
                 font=FONT_LABEL).pack(side="left")
        self._autostart_var = tk.BooleanVar(value=self.app.get_autostart())
        _Toggle(row, self._autostart_var,
                command=lambda v: self._toggle_autostart()).pack(side="right")

        # ---- 特效层级：全屏层 / 鼠标层各一套（两层是独立的覆盖窗口）----
        c2 = tk.Frame(p, bg=CARD)
        c2.pack(fill="x", padx=16, pady=(0, 8))
        self._card_title(c2, "特效层级")
        self._hint(c2, "最上层＝始终浮在窗口之上（默认）；"
                       "仅桌面图层＝被窗口盖住、只在桌面可见。")
        self._layer_row(c2, "全屏特效", "layer_fullscreen")
        self._layer_row(c2, "鼠标特效", "layer_mouse")

        # ---- 特效多开 ----
        c3 = tk.Frame(p, bg=CARD)
        c3.pack(fill="x", padx=16, pady=(0, 8))
        self._card_title(c3, "特效多开")
        self._hint(c3, "关闭时同一分类只能开一个特效，开启新的会自动关掉旧的。")
        self._toggle(c3, "全屏特效允许多开", "multi_fullscreen")
        self._toggle(c3, "鼠标特效允许多开", "multi_mouse")

        # ---- 任务栏 ----
        # 覆盖窗是置顶窗、任务栏不是，所以特效默认会被画在任务栏上 ——
        # 真实水波那种整片明暗条纹糊在任务栏上就是用户报的"残影"。
        c4 = tk.Frame(p, bg=CARD)
        c4.pack(fill="x", padx=16, pady=(0, 16))
        self._card_title(c4, "任务栏")
        self._hint(c4, "任务栏永远浮在特效之前（含副屏任务栏）；"
                       "关掉后特效会盖在任务栏上。")
        self._toggle(c4, "不在任务栏上绘制", "avoid_taskbar")
        return p

    def _card_title(self, card, text):
        """卡片内的小标题，与「预设方案」标题同一套样式。"""
        tk.Label(card, text=text, bg=CARD, fg=SUB,
                 font=(FONT_LABEL[0], FONT_LABEL[1], "bold")).pack(
            anchor="w", padx=12, pady=(10, 4))

    def _hint(self, card, text):
        """设置项下方的说明文字：用次级灰，避免和主标签抢注意力。"""
        # wraplength 必须 ≤ 卡片内可用宽度（卡片 412 − 两侧 padx 32 = 380）：
        # 给大了会让最后一个字单独折到下一行，出现"）。"这种孤字。
        tk.Label(card, text=text, bg=CARD, fg=SUB, font=FONT_SMALL,
                 anchor="w", justify="left", wraplength=372).pack(
            fill="x", padx=16, pady=(0, 4))

    def _layer_row(self, parent, label, key):
        """层级行：名称 …… 最上层 (开关) 仅桌面图层。

        层级是两选一，但只有一边是"非默认"的，所以沿用项目统一的胶囊开关：
        关＝最上层（默认），开＝仅桌面图层。两侧文字跟着开关染色，不用点开
        就知道当前在哪一档。
        """
        app = self.app
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", padx=16, pady=8)
        tk.Label(row, text=label, bg=CARD, fg=TEXT,
                 font=FONT_LABEL).pack(side="left")
        right = tk.Frame(row, bg=CARD)
        right.pack(side="right")
        lbl_desk = tk.Label(right, text="仅桌面图层", bg=CARD, fg=SUB,
                            font=FONT_SMALL)
        lbl_desk.pack(side="right", padx=(8, 0))
        var = tk.BooleanVar(value=str(app.cfg.get(key)) == "desktop")

        def repaint():
            on = bool(var.get())
            lbl_top.config(fg=ACCENT if not on else SUB)
            lbl_desk.config(fg=ACCENT if on else SUB)

        def _on(v):
            app.set(key, "desktop" if v else "topmost")
            repaint()

        _Toggle(right, var, command=_on).pack(side="right", padx=8)
        lbl_top = tk.Label(right, text="最上层", bg=CARD, fg=ACCENT,
                           font=FONT_SMALL)
        lbl_top.pack(side="right")
        repaint()
        # 注册写入回调：测试可以直接驱动开关，不必去翻 widget 树
        self._ctrls[key] = {"kind": "layer", "var": var, "repaint": repaint,
                            "on": _on}

    # ------------------------------------------------------------ 控件原语
    def _toggle(self, parent, label, key):
        app = self.app
        var = tk.BooleanVar(value=bool(app.cfg[key]))
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", padx=16, pady=8)
        tk.Label(row, text=label, bg=CARD, fg=TEXT,
                 font=FONT_LABEL).pack(side="left")

        def _on(v):
            app.set(key, bool(v))
            # 同类互斥可能连带关掉别的特效开关，回读一次让面板跟着变
            self._sync_from_cfg()

        _Toggle(row, var, command=_on).pack(side="right")
        # 不再挂 var.trace：_sync_from_cfg 回写变量时不该反过来再 set 一次
        self._ctrls[key] = {"kind": "toggle", "var": var, "on": _on}

    def _slider(self, parent, label, key, lo, hi, res, fmt):
        app = self.app
        f = tk.Frame(parent, bg=CARD)
        f.pack(fill="x", padx=16, pady=8)
        # 单行布局：名称 | 滑块（弹性） | 数值
        f.grid_columnconfigure(1, weight=1)
        tk.Label(f, text=label, bg=CARD, fg=TEXT,
                 font=FONT_LABEL).grid(row=0, column=0, sticky="w")
        var = tk.DoubleVar(value=float(app.cfg[key]))

        def on(*_):
            v = var.get()
            app.set(key, v)
            val.config(text=fmt.format(v))

        val = tk.Label(f, text="", bg=CARD, fg=ACCENT, font=FONT_NUM)
        _Slider(f, lo, hi, res, var, command=on,
                width=120).grid(row=0, column=1, sticky="ew", padx=(14, 10))
        val.grid(row=0, column=2, sticky="e")
        on()
        self._ctrls[key] = {"kind": "slider", "var": var, "label": val, "fmt": fmt}

    def _swatch(self, parent, label, key, colors):
        app = self.app
        f = tk.Frame(parent, bg=CARD)
        f.pack(fill="x", padx=16, pady=8)
        tk.Label(f, text=label, bg=CARD, fg=TEXT,
                 font=FONT_LABEL).pack(side="left")
        btns = []

        def refresh():
            cur = str(app.cfg[key]).lower()
            for b, c in zip(btns, colors):
                sel = c.lower() == cur
                # 选中项用强调色描边 2px，不用系统亮色边框
                b.config(relief="solid" if sel else "flat",
                         bd=2 if sel else 0,
                         fg=ACCENT if sel else TEXT)

        # 预设色块之外再给两个入口：取色（吸管，抓屏幕上任意一点）与
        # 调色（系统调色板，任意 RGB）。放在最右，色块顺势左移。
        for txt, cmd in (("调色", lambda k=key: self._choose_color(k)),
                         ("取色", lambda k=key: self._pick_screen_color(k))):
            bb = tk.Button(f, text=txt, width=3, height=1, bd=0, relief="flat",
                           bg=BTN, fg=SUB, font=FONT_SMALL, cursor="hand2",
                           activebackground=BTN_H, activeforeground=TEXT,
                           command=cmd)
            bb.pack(side="right", padx=(6, 0))
            bb.bind("<Enter>", lambda e, w=bb: w.config(bg=BTN_H, fg=TEXT))
            bb.bind("<Leave>", lambda e, w=bb: w.config(bg=BTN, fg=SUB))

        for c in colors:
            b = tk.Button(f, bg=c, width=2, height=1, bd=0, relief="flat",
                          cursor="hand2",
                          activebackground=c,
                          command=lambda cc=c: (app.set(key, cc), refresh()))
            b.pack(side="right", padx=3)
            btns.append(b)
        refresh()
        self._swatches[key] = refresh

    def _choose_color(self, key):
        """系统调色板：任意 RGB 都能选（预设色块只有 6 个，覆盖不到）。"""
        from tkinter import colorchooser
        cur = str(self.app.cfg.get(key, "#FFFFFF"))
        _rgb_t, hexs = colorchooser.askcolor(color=cur, title="选择颜色",
                                             parent=self.root)
        if hexs:
            self.app.set(key, hexs.upper())
            self._sync_from_cfg()

    def _pick_screen_color(self, key):
        """屏幕取色器：藏起面板 -> 3 秒后读光标处的像素 -> 回填。

        必须先 `hide()`：面板自己就压在屏幕上，不藏起来读到的是面板的颜色。
        留 3 秒是给用户把鼠标移到目标颜色的时间，到点轻响一声再取。
        """
        if getattr(self, "_picking", False):
            return
        self._picking = True
        self.hide()

        def _beep():
            try:
                import winsound
                winsound.Beep(1000, 70)
            except Exception:
                pass

        def _grab():
            try:
                pt = wt.POINT()
                if user32.GetCursorPos(ctypes.byref(pt)):
                    got = screen_pixel(pt.x, pt.y)
                    if got:
                        self.app.set(key, "#%02X%02X%02X" % got)
            except Exception:
                pass
            finally:
                self._picking = False
                self.show()
                self._sync_from_cfg()

        self.root.after(2600, _beep)
        self.root.after(3000, _grab)

    # ------------------------------------------------------------ 交互
    def _preset(self, name):
        self.app.apply_preset(name)
        self._sync_from_cfg()

    def _sync_from_cfg(self):
        app = self.app
        for key, c in self._ctrls.items():
            v = app.cfg.get(key)
            if v is None:
                continue
            if c["kind"] == "slider":
                fv = float(v)
                c["var"].set(fv)
                c["label"].config(text=c["fmt"].format(fv))
            elif c["kind"] == "toggle":
                c["var"].set(bool(v))
            elif c["kind"] == "layer":   # 层级是字符串键，开关只反映"是否桌面层"
                c["var"].set(str(v) == "desktop")
                c["repaint"]()
        for refresh in self._swatches.values():
            refresh()

    def _pause(self):
        p = self.app.toggle_pause()
        self._paused = bool(p)
        self._paint_pause(False)

    def _paint_pause(self, hover):
        """暂停/继续按钮状态着色。hover=True 为悬停预览色。"""
        paused = getattr(self, "_paused", False)
        if hover:
            self.pause_btn.config(bg=ACCENT_HOVER if paused else BTN_H,
                                  fg=ON_ACCENT if paused else TEXT)
        elif paused:
            self.pause_btn.config(text="继续", bg=ACCENT_DARK, fg=ON_ACCENT)
        else:
            self.pause_btn.config(text="暂停", bg=BTN, fg=TEXT)

    def _quit(self):
        self._quitting = True
        self.app.quit()
        try:
            self.root.destroy()
        except Exception:
            pass

    def _toggle_autostart(self):
        target = self._autostart_var.get()
        ok = self.app.set_autostart(target)
        if not ok:
            self._autostart_var.set(self.app.get_autostart())
            ctypes.windll.user32.MessageBoxW(
                0,
                "修改开机自启失败。\n请确认未被安全软件拦截。",
                "桌面特效", 0x10)

    def _poll(self):
        app = self.app
        if getattr(self, "_quitting", False):
            return

        try:
            import fx_winlayer
            if fx_winlayer.take_show_panel_request() and \
                    self.root.state() == "withdrawn":
                self.show()
        except Exception:
            pass

        try:
            while True:
                kind, _ = app.events.get_nowait()
                if kind == "panel":
                    if self.root.state() == "withdrawn":
                        self.show()
                    else:
                        self.hide()
                elif kind == "pause":
                    self._pause()
                elif kind == "quit":
                    self._quit()
                    return
                elif kind == "hotkey-failed":
                    app.report_error("hotkey", RuntimeError(
                        "Ctrl+Alt+R 已被其他程序占用，暂停快捷键不可用"))
                elif kind == "tray-failed":
                    app.report_error("tray", RuntimeError(
                        "托盘窗口创建失败，托盘图标与右键菜单不可用"))
        except Exception:
            pass

        self.status.config(text=f"{app.fps:5.1f} 帧/秒   {app.cost_ms:4.1f} 毫秒/帧"
                                + ("   [已暂停]" if app.paused else ""))

        err = getattr(app, "last_error", None)
        if err != self._err_shown:
            self._err_shown = err
            if err:
                self.err_bar.config(text=f"注意  {err}\n详情见 error.log")
                self.err_bar.pack(fill="x", before=self._main)
            else:
                self.err_bar.pack_forget()

        self.root.after(200, self._poll)

    def run(self):
        try:
            self.root.mainloop()
        finally:
            if not getattr(self, "_quitting", False):
                self.app.quit()
