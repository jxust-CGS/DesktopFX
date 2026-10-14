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
from _paths import app_root, atomic_write_json
import fx_effects
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
# 宠物跟随：种类只有两种，用文字按钮而不是色块（"小猫/小狗"没有天然的颜色表示）。
PET_KINDS = ["小猫", "小狗"]
# 毛色。刻意避开纯白与纯黑：白猫在浅色桌面上糊成一片、黑猫在深色桌面上同样，
# 所以在两端各取"米白"与"深棕"作端点色。换色会连带耳朵内侧、描边、眼睛一起配色。
PET_COLORS = ["#F2C27B", "#E9A0A8", "#B9CBE8", "#9FD8C0", "#F5F1E8", "#6B5A50"]

# ---- 点击特效 ----
# 这三张表必须与 fx_effects 里的实现一一对应：
#   CLICK_SHAPES <-> fx_effects._SHAPE_NAMES（少了会画出空白，多了会静默退回爱心）
#   CLICK_FONTS  <-> fx_effects._FONT_FILES
#   CLICK_DECOS  <-> ClickTextField.update() 里的 deco 分支
# 跨文件的一致性由 `_verify_click.py` 断言，不靠人记。
CLICK_SHAPES = ["爱心", "星星", "彩带", "雪花", "气泡", "火焰", "花瓣", "音符"]
# 9 款而不是更多：都是系统自带的花样款或清晰基础款，去掉的"华文楷体 /
# 华文细黑 / 姚体"与留着的重复度太高，而字体网格每多一行就多占页面高度
# （面板有"装得下最长页 + 留拖动余量"的硬约束）。
CLICK_FONTS = ["微软雅黑", "雅黑粗体", "黑体", "幼圆", "行楷", "隶书",
               "琥珀", "彩云", "舒体"]
CLICK_DECOS = ["无", "彩带", "星光"]
# 「彩色」= 逐粒子从调色板取色（不是颜色值，是个哨兵）。任何图案都能选它，
# 彩带默认就用它。真正的取值定义在 fx_effects.COLOR_RAINBOW，一致性由
# `_verify_click.py` 断言。
CLICK_RAINBOW = "彩色"
CLICK_EMOJI_COLORS = ["#FF5D73", "#FFC145", "#8FD8FF", "#FFFFFF", "#A78BFA",
                      CLICK_RAINBOW]
# 每个图案的惯用色：选图案时自动套上（在 `_on_shape_change` 里用）。
# 与 fx_effects._SHAPE_DEFAULT_COLORS 的一致性由 `_verify_click.py` 断言。
SHAPE_DEFAULT_COLORS = {
    "爱心": "#FF5D73",
    "星星": "#FFC145",
    "彩带": CLICK_RAINBOW,
    "雪花": "#FFFFFF",
    "气泡": "#8FD8FF",
    "火焰": "#FF8A3D",
    "花瓣": "#FFA6C9",
    "音符": "#A78BFA",
}
CLICK_TEXT_COLORS = ["#FFFFFF", "#FFE066", "#7FE7FF", "#FF9DB6", "#A8E6A0",
                     "#FFB38A"]

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


def _short_path(path, n=30):
    """路径从**尾部**截断：`…\\表情\\猫猫`。

    用户认的永远是最后一级目录名 —— 从头部截会把它切掉，等于没显示。
    """
    p = str(path or "")
    if len(p) <= n:
        return p
    return "…" + p[-(n - 1):]


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
        pill_fill = ACCENT if on else TRACK
        pill_outline = None if on else LINE
        knob_fill = WHITE if on else SUB
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
                                         fill=_rgb(pill_fill),
                                         outline=None if pill_outline is None
                                         else _rgb(pill_outline),
                                         width=s)
                    k = kr * s
                    cx = (w - m - kr) * s if on else (m + kr) * s
                    cyy = cy * s
                    dr.ellipse([cx - k, cyy - k, cx + k, cyy + k],
                               fill=_rgb(knob_fill))
                self._img = _render_aa(self.cget("bg"), w, h, paint)
                self.create_image(0, 0, anchor="nw", image=self._img)
                return
            except Exception:
                pass  # 降级到 Canvas 原始绘制

        # 旧绘制路径（PIL 缺失时的降级，无抗锯齿）
        self.create_oval(1, 1, w - 1, h - 1,
                         fill=pill_fill,
                         outline="" if pill_outline is None else pill_outline,
                         width=1)
        knob_x = w - m - kr if on else m + kr
        self.create_oval(knob_x - kr, cy - kr, knob_x + kr, cy + kr,
                         fill=knob_fill, outline="")


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
        elif n == "宠物跟随":
            # 爪印：下方掌垫 + 上方三个脚趾
            dot(9.0, 12.0, 2.9)
            dot(4.6, 7.0, 1.7)
            dot(9.0, 5.2, 1.7)
            dot(13.4, 7.0, 1.7)
        elif n == "点击表情":
            # 一颗小爱心 + 两侧溅开的两个点 = "点一下就炸开"
            # （不能画成"圆心+射线"，那与「聚光灯」的图标撞脸）
            dot(6.4, 6.6, 2.5)
            dot(11.6, 6.6, 2.5)
            line([(4.2, 8.0), (9.0, 14.4)])
            line([(13.8, 8.0), (9.0, 14.4)])
            dot(2.6, 3.2, 1.1)
            dot(15.4, 3.2, 1.1)
        elif n == "点击文字":
            # 对话气泡：圆角方框 + 左下小尾巴 + 里面两行字
            line([(3.5, 4.0), (14.5, 4.0), (14.5, 11.5), (8.0, 11.5),
                  (5.0, 14.5), (5.0, 11.5), (3.5, 11.5), (3.5, 4.0)])
            line([(6.0, 6.6), (12.0, 6.6)], width=1.2)
            line([(6.0, 9.0), (10.5, 9.0)], width=1.2)
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
        elif n == "宠物跟随":
            for cx, cy, r in ((9.0, 12.0, 2.9), (4.6, 7.0, 1.7),
                              (9.0, 5.2, 1.7), (13.4, 7.0, 1.7)):
                self.create_oval(cx - r, cy - r, cx + r, cy + r,
                                 fill=self.color, outline="")
        elif n == "点击表情":
            for cx, cy, r in ((6.4, 6.6, 2.5), (11.6, 6.6, 2.5),
                              (2.6, 3.2, 1.1), (15.4, 3.2, 1.1)):
                self.create_oval(cx - r, cy - r, cx + r, cy + r,
                                 fill=self.color, outline="")
            self._legacy_line([4.2, 8.0, 9.0, 14.4], w)
            self._legacy_line([13.8, 8.0, 9.0, 14.4], w)
        elif n == "点击文字":
            self._legacy_line([3.5, 4.0, 14.5, 4.0, 14.5, 11.5, 8.0, 11.5,
                               5.0, 14.5, 5.0, 11.5, 3.5, 11.5, 3.5, 4.0], w)
            self._legacy_line([6.0, 6.6, 12.0, 6.6], 1.2)
            self._legacy_line([6.0, 9.0, 10.5, 9.0], 1.2)
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
    W_DEF, H_DEF = 564, 742   # 首次运行时的窗口尺寸（高度随后按内容自适应）
    # 最小宽度不由"好看"决定，而由布局本身决定：逐页量过自然宽度（reqwidth），
    # 最宽的一行是「落樱」的颜色行（长标签"边缘颜色(翻面泛白)" + 6 个色块 +
    # 调色/取色），336 内容宽 + 卡片与页面留白 64 = 400，加侧栏 120 就是 520。
    # 再窄这行就会被挤掉一部分（Tk 的 Label 不换行、也不缩，超出部分直接被裁）。
    # _verify_ui_resize.py 第 8 组断言把这条钉死：W_MIN 下不许有任何一页被挤。
    W_MIN = 520
    H_MIN = 420               # 最小高度：再矮就只剩两三行设置
    _SCREEN_FRAC = 0.92       # 窗口不超过屏幕的这个比例
    _CHROME = 42              # 标题栏高度：内容区 = 窗口高 − 它
    GRIP = 6                  # 边缘缩放手柄厚度
    GRIP_CORNER = 12          # 角落缩放手柄边长
    # 预设按钮每行几格。4 而不是 3：预设多的页（点击文字 10 个）从 4 行降到
    # 3 行，一页省约 42px —— 而"默认窗口要装得下最长页并且还留得出拖动余量"
    # 是个硬约束（见 `_verify_ui_resize`），页面高度是有限预算。
    PRESET_COLS = 4

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
        self._rz = None         # 正在拖拽的缩放手柄方向（None = 没在缩放）
        self._hints = []        # 说明文字 Label：窗口宽度变了要重算折行宽度
        self._folders = []      # 文件夹选择行的 refresh()：状态是后台加载的，要跟
        self._size_rows = {}    # "win_w"/"win_h" -> {"var","label"}（窗口尺寸数值行）
        self._grip = {}         # 边缘/角落缩放手柄
        self._chrome = self._CHROME

        # 窗口尺寸属于**界面偏好**（不是特效配置），和"上次停留的页面"一起放
        # ui_state.json。特效配置 config.json 会被预设批量改写，尺寸混进去会
        # 被预设顺手改掉。
        self._ui = self._read_ui_state()
        self._user_w = self._ui_int("win_w")   # None = 没自定义过，用默认值
        self._user_h = self._ui_int("win_h")   # None = 高度按内容自适应
        self.W = self._clamp_w(self._user_w or self.W_DEF)
        self.H = self._clamp_h(self._user_h or self.H_DEF)

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
    def _center_xy(self, w, h):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        return max(0, (sw - w) // 2), max(0, (sh - h) // 2 - 20)

    def _center(self):
        x, y = self._center_xy(self.W, self.H)
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")

    def _cap_w(self):
        return max(self.W_MIN, int(self.root.winfo_screenwidth() * self._SCREEN_FRAC))

    def _cap_h(self):
        return max(self.H_MIN, int(self.root.winfo_screenheight() * self._SCREEN_FRAC))

    def _clamp_w(self, w):
        return int(max(self.W_MIN, min(int(w), self._cap_w())))

    def _clamp_h(self, h):
        return int(max(self.H_MIN, min(int(h), self._cap_h())))

    def _clamp_pos(self, x, y, w, h):
        """窗口不许整个跑到虚拟桌面之外。

        **不能用主屏尺寸当边界**：本机副屏在主屏左侧，虚拟桌面原点是
        (-1920, 0)，窗口停在负坐标是正常的。winfo_vroot* 给的正是 Tk 自己
        坐标系下的虚拟桌面范围，和 winfo_x() 同一套，可以直接比。
        """
        vx = self.root.winfo_vrootx()
        vy = self.root.winfo_vrooty()
        vw = self.root.winfo_vrootwidth()
        vh = self.root.winfo_vrootheight()
        return (min(max(int(x), vx), max(vx, vx + vw - w)),
                min(max(int(y), vy), max(vy, vy + vh - h)))

    def _apply_geometry(self, w, h):
        """把窗口改成 w×h（夹取到合法范围）。

        已经上屏时**保持左上角不动** —— 拖拽缩放时若重新居中，窗口会跟着手
        乱跳。构造期窗口还在 withdraw（未上屏），此时才按屏幕居中。
        """
        w, h = self._clamp_w(w), self._clamp_h(h)
        if self.root.winfo_ismapped():
            x, y = self.root.winfo_x(), self.root.winfo_y()
        else:
            x, y = self._center_xy(w, h)
        x, y = self._clamp_pos(x, y, w, h)
        self.W, self.H = w, h
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self._apply_wraplength(w)
        self._layout_grips(w, h)

    # ------------------------------------------------------------ 拖拽缩放
    _GRIP_CURSOR = {
        "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
        "w": "sb_h_double_arrow", "e": "sb_h_double_arrow",
        "nw": "size_nw_se", "se": "size_nw_se",
        "ne": "size_ne_sw", "sw": "size_ne_sw",
    }

    def _build_grips(self):
        """窗口四边/四角的拖拽缩放手柄。

        本窗口是 overrideredirect（无系统边框），Windows 不会给原生缩放边框，
        只能自己铺 8 条细边。它们只占最外圈 6px（角落 12px），压在内容的外侧
        留白上（内容两侧本来就有 16px padx），不会盖住任何控件；底色与窗口
        一致，正常看不见，只靠鼠标形状提示。

        位置每次尺寸变化时按实际宽高显式摆放（_layout_grips）——不依赖
        place 的 relwidth/负 width 叠加语义，那套写法各版本 Tk 说法不一。
        """
        for edge in ("n", "s", "w", "e", "nw", "ne", "sw", "se"):
            g = tk.Frame(self.root, bg=BG, cursor=self._GRIP_CURSOR[edge])
            g.bind("<ButtonPress-1>",
                   lambda e, k=edge: self._rz_begin(e, k))
            g.bind("<B1-Motion>", self._rz_motion)
            g.bind("<ButtonRelease-1>", self._rz_end)
            self._grip[edge] = g
        self._layout_grips(self.W, self.H)

    def _layout_grips(self, w, h):
        if not self._grip:
            return
        e, c = self.GRIP, self.GRIP_CORNER
        w, h = int(w), int(h)
        spots = {
            "n":  (c, 0, w - 2 * c, e),
            "s":  (c, h - e, w - 2 * c, e),
            "w":  (0, c, e, h - 2 * c),
            "e":  (w - e, c, e, h - 2 * c),
            "nw": (0, 0, c, c),
            "ne": (w - c, 0, c, c),
            "sw": (0, h - c, c, c),
            "se": (w - c, h - c, c, c),
        }
        for k, (x, y, gw, gh) in spots.items():
            self._grip[k].place(x=max(0, x), y=max(0, y),
                                width=max(1, gw), height=max(1, gh))

    def _rz_begin(self, e, edge):
        self._rz = (edge, e.x_root, e.y_root,
                    self.root.winfo_x(), self.root.winfo_y(), self.W, self.H)
        return "break"

    def _rz_motion(self, e):
        if not self._rz:
            return
        edge, x0, y0, wx, wy, w0, h0 = self._rz
        dx, dy = e.x_root - x0, e.y_root - y0
        left, top, w, h = wx, wy, w0, h0
        if "e" in edge:
            w = w0 + dx
        if "w" in edge:
            w = w0 - dx
        if "s" in edge:
            h = h0 + dy
        if "n" in edge:
            h = h0 - dy
        # 先夹尺寸，再把"被拖的那条边"落到新位置 —— 对边必须钉住不动，
        # 否则拖西边会让整窗平移（看着像在拖窗口而不是在改大小）。
        w, h = self._clamp_w(w), self._clamp_h(h)
        if "w" in edge:
            left = wx + w0 - w
        if "n" in edge:
            top = wy + h0 - h
        left, top = self._clamp_pos(left, top, w, h)
        self.W, self.H = w, h
        self.root.geometry(f"{w}x{h}+{left}+{top}")
        self._apply_wraplength(w)
        self._layout_grips(w, h)

    def _rz_end(self, _e):
        if not self._rz:
            return
        edge = self._rz[0]
        self._rz = None
        # 只记**真正被拖到的方向**：拖左右边时高度保持原模式（可能是"按内容
        # 自适应"），不该因为顺手拖了宽度就把高度也钉死。
        dims = []
        if "e" in edge or "w" in edge:
            dims.append("win_w")
        if "n" in edge or "s" in edge:
            dims.append("win_h")
        self._remember_size(dims)

    def _apply_wraplength(self, w):
        """窗口宽度变了就重算说明文字的折行宽度。

        `_hint` 的 wraplength 原来写死 372（对应窗口 564）。窗口能缩放之后，
        写死的值在变宽时浪费空间（说明文字折成一堆短行）、变窄时溢出被裁。
        按当前宽度实时算：卡片内可用宽 = 窗口 − 侧栏 120 − 页面留白 32 −
        卡片内留白 32，再留 8px 余量，避免最后一个字单独折到下一行。
        """
        wl = max(160, int(w) - 192)
        for lb in self._hints:
            try:
                lb.config(wraplength=wl)
            except Exception:
                pass
        eb = getattr(self, "err_bar", None)
        if eb is not None:
            try:
                eb.config(wraplength=max(160, int(w) - 20))
            except Exception:
                pass

    def _set_size(self, win_w=None, win_h=None):
        dims = []
        if win_w is not None:
            dims.append("win_w")
        if win_h is not None:
            dims.append("win_h")
        self._apply_geometry(win_w if win_w is not None else self.W,
                             win_h if win_h is not None else self.H)
        self._remember_size(dims)

    def _remember_size(self, dims=("win_w", "win_h")):
        """把指定方向记为用户指定值，并落盘。

        拖拽/拉滑块**结束**时才调用（不是每帧），避免移动过程中反复写文件。
        """
        if "win_w" in dims:
            self._user_w = int(self.W)
            self._ui["win_w"] = self._user_w
        if "win_h" in dims:
            self._user_h = int(self.H)
            self._ui["win_h"] = self._user_h
        self._save_ui_state()
        self._sync_size_rows()

    def _sync_size_rows(self):
        for key, c in self._size_rows.items():
            v = int(self.W if key == "win_w" else self.H)
            try:
                c["var"].set(float(v))
                c["label"].config(text=f"{v} 像素")
            except Exception:
                pass

    def _size_auto_h(self):
        """高度回到"按内容自适应"（宽度仍记着当前值）。"""
        self._user_h = None
        self._ui.pop("win_h", None)
        self._user_w = int(self.W)
        self._ui["win_w"] = self._user_w
        self._save_ui_state()
        self._fit_height()      # 内部会同步「窗口尺寸」的数值标签

    def _size_reset(self):
        """两个方向都回到默认：宽度用默认值，高度按内容自适应。"""
        self._user_w = self._user_h = None
        self._ui.pop("win_w", None)
        self._ui.pop("win_h", None)
        self.W, self.H = self.W_DEF, self.H_DEF
        self._save_ui_state()
        self._fit_height()      # 内部会同步「窗口尺寸」的数值标签

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
        # 退出中不再续期：窗口销毁后 Tcl 解释器还会把已排队的 after 跑一遍，
        # 报一串 "invalid command name ..._raise_top"（验证脚本连开多个面板时
        # 尤其吵），而且对一个正在关闭的面板去抢置顶本身也没意义。
        if not getattr(self, "_quitting", False):
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
            "宠物跟随": self._page_pet,
            "涟漪": self._page_ripple,
            "真实水波": self._page_wave,
            "点击表情": self._page_click_emoji,
            "点击文字": self._page_click_text,
            "窗口水波": self._page_win,
            "其它": self._page_other,
        }
        # 按侧栏顺序注册：_pages 的键序即导航顺序，测试直接比对二者
        for name in fx_kinds.nav_order():
            self._pages[name] = builders[name](inner)
        for f in self._pages.values():
            f.pack_forget()
        self._show_page(self._load_last_page())

        # 原先贴在窗口最底部的"托盘图标右键可唤出菜单 / Ctrl+Alt+R 快速暂停"
        # 提示条已挪进「其他设置」页（见 _page_other 的「快捷操作」卡片）——
        # 那行灰字常年占着窗口底部，窗口一缩就成了最先被挤掉的东西。
        # 手柄最后铺，保证压在内容之上（place 的堆叠顺序 = 创建顺序）。
        self._build_grips()

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
        """内容区可用高度 = 窗口高 − chrome（标题栏 + 错误横幅）。

        **不要直接读 `canvas.winfo_height()`**：窗口尺寸变化后，canvas 的实际
        高度要等 Tk 处理完下一次 Configure 才跟上，在那之前读到的是旧值 ——
        自适应算窗口高度、判断要不要滚动时都会用错。
        """
        h = int(self.root.winfo_height()) - self._chrome
        # 错误横幅出现时也会占掉一条，不算进来的话"装得下"会被高估，
        # 滚轮就被误判成无需滚动、内容再也滚不到（横幅是临时出现的，容易漏）。
        eb = getattr(self, "err_bar", None)
        if eb is not None and eb.winfo_ismapped():
            h -= int(eb.winfo_reqheight())
        return max(0, h)

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
        """定下窗口尺寸：宽度用默认值或用户记忆值，高度**按内容自适应**。

        各页内容高度差很大（下雨 ~633、真实水波 ~872）。只要系统缩放不是
        100%（字体按 DPI 放大、控件整体变高）或者用户多存了几个自定义预设，
        连"下雨"这种要素很少的页面都会溢出 —— 滚轮于是能把顶部拉走，看着莫
        名其妙。让窗口跟着内容长高（不超出屏幕）能从根上避免；真的装不下
        （屏幕不够高）时才滚动。

        **用户显式调过高度（win_h）时不再自适应** —— 那时候"装得下"是他的
        选择，装不下就滚动（滚轮路径已由 _need_scroll 守好）。
        """
        # 堆叠 pack 全部页面，单次 flush 即可测量每页自然高度（canvas window item
        # 的尺寸只在布局 pass 里算出，但 10 页只需一次 flush，不必逐页 pack+flush
        # —— 旧实现每页一次、共 10 次，是 23 次刷新里的大头）。flush 时窗口处于
        # withdraw 状态，不会上屏。
        for f in self._pages.values():
            f.pack()
        self.root.update_idletasks()
        hs = {n: int(f.winfo_reqheight()) for n, f in self._pages.items()}
        content = max(hs.values()) if hs else 0
        self._content_h = content
        if self._user_h:
            h = self._clamp_h(self._user_h)
        else:
            h = self._clamp_h(max(self.H_MIN, content + self._chrome))
        w = self._clamp_w(self._user_w or self.W_DEF)
        # 不能只在 `w/h != self.W/self.H` 时才应用：窗口尺寸可能被外部改过
        # （测试里压矮造溢出、用户拖过了）而 W/H 没变 -> 就恢复不回来了。
        if (w != self.W or h != self.H
                or int(self.root.winfo_height()) != h):
            self._apply_geometry(w, h)
            self.root.update_idletasks()     # 落实最终尺寸（构造期仍在 withdraw，
                                            # 仅写 geometry，不触发可视上屏）
        # 恢复当前页（pack fill + 复位滚动）；滚动区交给 <Configure> 绑定自愈，
        # 此处不再 flush。
        self._show_page(self._active)
        # 「窗口尺寸」卡片里的数值必须反映**真实**尺寸。构造期它是按 H_DEF
        # (742) 建的，自适应之后 H 变成按内容算出的值（例如 916）—— 不在这里
        # 同步，标签就永远停在 742，和窗口实际高度对不上。
        self._sync_size_rows()

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

    def _read_ui_state(self):
        try:
            with open(_UI_STATE_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _ui_int(self, key):
        """读一个正整数偏好；缺失或非法一律返回 None（= 用默认值）。"""
        try:
            v = int(self._ui.get(key))
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

    def _save_ui_state(self):
        """整体重写 ui_state.json。

        内存里始终持有一份 self._ui，所以每次写都包含全部键（上次页面 +
        窗口宽高），不会像原来那样把其它键覆盖掉。
        """
        try:
            # 原子写：写坏了读取端会静默回到默认尺寸 + 默认页面（这里
            # except: pass 连 error.log 都不留），用户看不出发生过什么。
            # indent=None —— ui_state.json 本来就是紧凑无缩进的。
            atomic_write_json(_UI_STATE_PATH, dict(self._ui), indent=None)
        except Exception:
            pass

    def _load_last_page(self):
        name = self._ui.get("last_page")
        if name in self._pages:
            return name
        return "下雨"

    def _save_last_page(self, name):
        self._ui["last_page"] = name
        self._save_ui_state()

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
                cell.grid(row=i // self.PRESET_COLS, column=i % self.PRESET_COLS,
                          padx=4, pady=3, sticky="ew")
                pf.grid_columnconfigure(i % self.PRESET_COLS, weight=1)
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
                      font=FONT_SMALL, padx=4, pady=2, cursor="hand2",
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
        cell.grid(row=i // self.PRESET_COLS, column=i % self.PRESET_COLS,
                  padx=4, pady=3, sticky="ew")
        pf.grid_columnconfigure(i % self.PRESET_COLS, weight=1)
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
                cells[n].grid(row=i // self.PRESET_COLS,
                              column=i % self.PRESET_COLS,
                              padx=4, pady=3, sticky="ew")
                pf.grid_columnconfigure(i % self.PRESET_COLS, weight=1)
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

    def _page_pet(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "宠物跟随")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用宠物跟随", "pet_enabled")
        self._choices(c, "种类", "pet_kind", PET_KINDS)
        self._slider(c, "数量", "pet_count", 1, 10, 1, "{:.0f} 只")
        self._slider(c, "大小", "pet_size", 24, 120, 2, "{:.0f} 像素")
        self._slider(c, "跟随紧密度", "pet_follow", 0.05, 1.0, 0.01, "{:.0%}")
        self._slider(c, "最小移动距离", "pet_min_travel", 0.0, 300.0, 10.0,
                     "{:.0f} 像素")
        self._slider(c, "静止后消失", "pet_idle_fade", 0.0, 6.0, 0.1,
                     "{:.1f} 秒")
        self._swatch(c, "毛色", "pet_color", PET_COLORS)
        self._slider(c, "不透明度", "pet_opacity", 0.2, 1.0, 0.01, "{:.0%}")
        self._hint(c, "小猫小狗沿着鼠标划过的路线跟上来：第一只追光标，"
                      "其余排成一列跟在它身后（间距跟着体型走），最多 10 只。"
                      "它们会甩着尾巴走、偶尔眨眼，停下时也会轻轻晃。")
        self._hint(c, "「最小移动距离」是让它们露面之前，鼠标得先真的走多远"
                      "（按净位移算，原地来回抖动不算）：轻轻碰一下不会惊动它们。"
                      "露面时它们已经散在你刚走过的那段路线上，一出现就追上来，"
                      "而不是在原地列队等你。")
        self._hint(c, "「静止后消失」是光标停住多久后它们淡出（填 0 表示一直"
                      "跟着不消失）；再次出现同样要先把鼠标移动一段距离。")
        self._hint(c, "转身带一点迟滞，所以鼠标原地画圈时它们不会左右乱跳；"
                      "「跟随紧密度」调低会更像慢悠悠地追，调高就紧咬着不放。")
        self._hint(c, "形象是用代码画出来的，不依赖任何图片文件。"
                      "换「毛色」会把描边、耳内与眼睛一起配色。")
        self._save_bar(c, "宠物跟随")
        self._preset_block(p, "宠物跟随")
        return p

    def _on_shape_change(self, shape):
        """选了图案就把主色换成它的惯用色（爱心红 / 星星黄 / 雪花白……）。

        只在**用户点图案**时触发 —— 预设、启动加载都不走这里，所以预设里
        写好的配色不会被覆盖。选完图案再自己调色即可。
        """
        col = SHAPE_DEFAULT_COLORS.get(shape)
        if col:
            self.app.set("click_emoji_color", col)

    def _page_click_emoji(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "点击表情")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用点击表情", "click_emoji_enabled")
        # 8 个图案排单行会把页面撑破最小宽度（内容区只有 400px），折成 4 列
        self._choices(c, "图案", "click_emoji_shape", CLICK_SHAPES, columns=4,
                      on_change=self._on_shape_change)
        self._slider(c, "数量", "click_emoji_count", 2, 40, 1, "{:.0f} 个")
        self._slider(c, "大小", "click_emoji_size", 8, 64, 1, "{:.0f} 像素")
        self._slider(c, "扩散", "click_emoji_spread", 100, 600, 10, "{:.0f}")
        self._slider(c, "重力", "click_emoji_gravity", -2.0, 2.0, 0.1, "{:+.1f}")
        self._slider(c, "持续时间", "click_emoji_life", 0.2, 1.5, 0.05,
                     "{:.2f} 秒")
        self._toggle(c, "粒子自旋", "click_emoji_rotate")
        self._swatch(c, "颜色", "click_emoji_color", CLICK_EMOJI_COLORS)
        self._slider(c, "不透明度", "click_emoji_opacity", 0.2, 1.0, 0.01,
                     "{:.0%}")
        self._hint(c, "点一下鼠标就在光标处炸开一簇：「扩散」是飞出去的速度，"
                      "「重力」调大往下飘、负数向上浮，最长 1.5 秒。"
                      "选图案会自动配上它的惯用色。")
        self._save_bar(c, "点击表情")

        # 自定义素材单独一张卡：这两样都**不吃**上面那几个旋钮 —— 用文件夹
        # 里的图当图案时「图案 / 颜色」根本不参与绘制，语音连画都不画。单独
        # 放一块，用户一眼就知道是另一条路。
        c2 = tk.Frame(p, bg=CARD)
        c2.pack(fill="x", padx=16, pady=(0, 8))
        self._choices(c2, "图案来源", "click_emoji_source",
                      fx_effects.CLICK_SOURCES)
        self._folder(c2, "图片文件夹", "click_emoji_custom_dir",
                     status=self._img_status)
        self._toggle(c2, "点击时播放语音", "click_voice_enabled")
        self._folder(c2, "语音文件夹", "click_voice_dir",
                     status=self._voice_status)
        self._hint(c2, "「自定义图片」从文件夹随机取图当粒子，读图在后台、"
                       "读完前先用内置图案；语音上一段没播完时不响应新的点击。")

        self._preset_block(p, "点击表情")
        return p

    def _img_status(self):
        """图片文件夹的附加状态（每 200ms 由 _poll 刷一次）。"""
        folder = str(self.app.cfg.get("click_emoji_custom_dir", "") or "")
        if not folder:
            return ""
        return fx_effects.CLICK_IMAGE_POOL.state(folder)

    def _voice_status(self):
        folder = str(self.app.cfg.get("click_voice_dir", "") or "")
        if not folder:
            return ""
        n = fx_effects.count_audio(folder)
        return "找到 %d 个音频" % n if n else "没找到音频"

    def _page_click_text(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "点击文字")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用点击文字", "click_text_enabled")
        # 这块文字框会被「语音与文字统一」接管：闸门一开就变成只读、内容由
        # 语音包的文件名派生（见 _sync_text）。
        self._textbox(c, "文字（一行一个，点击时轮换取下一个）",
                      "click_text_list", height=2,
                      gate="click_text_voice_match",
                      fill=self._voice_text_preview)
        # 9 款字体排 5 列 = 2 行（3 列要 3 行，多 34px）。名字最长 4 个字，
        # 5 列在最小窗口宽度下也放得下（内容区 400px / 5 ≈ 80px）。
        self._choices(c, "字体", "click_text_font", CLICK_FONTS, columns=5)
        self._slider(c, "字号", "click_text_size", 14, 90, 1, "{:.0f} 像素")
        self._swatch(c, "颜色", "click_text_color", CLICK_TEXT_COLORS)
        self._choices(c, "装饰", "click_text_deco", CLICK_DECOS)
        self._slider(c, "上飘高度", "click_text_rise", 0, 260, 5, "{:.0f} 像素")
        self._slider(c, "倾斜幅度", "click_text_spin", 0.0, 1.0, 0.05, "{:.0%}")
        self._slider(c, "持续时间", "click_text_life", 0.4, 3.0, 0.1, "{:.1f} 秒")
        self._slider(c, "不透明度", "click_text_opacity", 0.2, 1.0, 0.01,
                     "{:.0%}")
        self._hint(c, "点一下鼠标，在光标处弹出一行字并向上飘走。文字框可以写"
                      "多行，点一次换下一个；「倾斜幅度」是每次点击的随机歪斜"
                      "角度，字体取系统已装的花样款（没装的自动回退雅黑）。")
        self._save_bar(c, "点击文字")

        # 语音包单独一张卡：它不只是"加个声音"，开着「统一」时**上面那个文字
        # 框会被接管**（只读、内容来自音频文件名），所以必须和文字框分开说。
        # 两条说明**合并成一条**：页面自然高已经贴着上限（1062），提示是按
        # 行数占高的，多条短文比一条长文贵。
        c2 = tk.Frame(p, bg=CARD)
        c2.pack(fill="x", padx=16, pady=(0, 8))
        self._folder(c2, "语音包文件夹", "click_text_voice_dir",
                     status=self._voice_pack_status)
        self._toggle(c2, "语音与文字统一", "click_text_voice_match")
        self._hint(c2, "统一开启后文字栏自动填入语音包里每条音频的名字并变为"
                       "只读，点击时随机挑一条 —— 弹出的字与声音永远是同一条；"
                       "关闭则文字自己写。文件名的序号（如「01 你好」）会自动"
                       "去掉。")

        self._preset_block(p, "点击文字")
        return p

    def _voice_pack_status(self):
        """语音包文件夹的附加状态。"""
        folder = str(self.app.cfg.get("click_text_voice_dir", "") or "")
        if not folder:
            return ""
        n = fx_effects.count_audio(folder)
        return "找到 %d 个音频" % n if n else "没找到音频"

    def _voice_text_preview(self):
        """统一模式下文字栏要显示的内容：语音包里每条音频的名字。"""
        folder = str(self.app.cfg.get("click_text_voice_dir", "") or "")
        return "\n".join(fx_effects.audio_labels(folder))

    def _page_win(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "窗口水波")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用窗口水波", "win_ripple_enabled")
        self._slider(c, "扰动强度", "win_ripple_strength", 0.05, 1.0, 0.01, "{:.0%}")
        self._slider(c, "扰动范围", "win_ripple_size", 0.4, 3.0, 0.1, "{:.1f} 倍")
        self._hint(c, "把窗口当成浮在水面上的东西：移动或缩放时它推开水——"
                      "朝哪边走，哪边的前缘就涌起、后缘就塌陷，拖过去的是一道浪；"
                      "最小化时水从四周合拢填掉它，恢复时再把水压开。")
        self._hint(c, "只有速度才会起波，而且水只往窗外涌 —— 窗口静静放着时水面是平的，"
                      "波纹也不会灌进窗口里。")
        self._hint(c, "它和「真实水波」共用同一片水面，"
                      "水面的波速、余波时长、光照与浓度都在那一页设置。")
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
        self._layer_row(c2, "鼠标特效（拖动 / 点击）", "layer_mouse")

        # ---- 特效多开 ----
        c3 = tk.Frame(p, bg=CARD)
        c3.pack(fill="x", padx=16, pady=(0, 8))
        self._card_title(c3, "特效多开")
        self._hint(c3, "关闭时同一分类只能开一个特效，开启新的会自动关掉旧的。")
        self._toggle(c3, "全屏特效允许多开", "multi_fullscreen")
        self._toggle(c3, "鼠标特效允许多开", "multi_mouse")
        self._hint(c3, "「鼠标特效」这一项同时管**拖动**与**点击**两组"
                      "（它们本来就在同一条鼠标层上）：打开后聚光灯可以和"
                      "水波同屏，点击表情也可以和点击文字一起弹。")

        # ---- 任务栏 ----
        # 覆盖窗是置顶窗、任务栏不是，所以特效默认会被画在任务栏上 ——
        # 真实水波那种整片明暗条纹糊在任务栏上就是用户报的"残影"。
        c4 = tk.Frame(p, bg=CARD)
        c4.pack(fill="x", padx=16, pady=(0, 8))
        self._card_title(c4, "任务栏")
        self._hint(c4, "任务栏永远浮在特效之前（含副屏任务栏）；"
                       "关掉后特效会盖在任务栏上。")
        self._toggle(c4, "不在任务栏上绘制", "avoid_taskbar")

        # ---- 窗口尺寸 ----
        # 尺寸是界面偏好，存 ui_state.json（不是特效配置）。
        c5 = tk.Frame(p, bg=CARD)
        c5.pack(fill="x", padx=16, pady=(0, 8))
        self._card_title(c5, "窗口尺寸")
        self._hint(c5, "拖拽窗口的任意一条边或角也能改；调整后会记住。")
        self._size_slider(c5, "窗口宽度", "win_w", self.W_MIN, self._cap_w())
        self._size_slider(c5, "窗口高度", "win_h", self.H_MIN, self._cap_h())
        srow = tk.Frame(c5, bg=CARD)
        srow.pack(fill="x", padx=16, pady=(0, 12))
        self._mini_btn(srow, "按内容自适应高度", self._size_auto_h)
        self._mini_btn(srow, "恢复默认尺寸", self._size_reset)

        # ---- 快捷操作 ----
        # 原先贴在窗口最底部的那行提示挪进来；拆成两行更好读，也顺便把
        # "托盘右键"和"快捷键"两件事分开说清。
        c6 = tk.Frame(p, bg=CARD)
        c6.pack(fill="x", padx=16, pady=(0, 16))
        self._card_title(c6, "快捷操作")
        self._key_row(c6, "托盘图标右键", "唤出菜单")
        self._key_row(c6, "Ctrl+Alt+R", "快速暂停")
        return p

    def _size_slider(self, parent, label, key, lo, hi):
        """窗口宽/高的数值调节行。

        写的是 ui_state（界面偏好）而不是 app.cfg —— 特效配置会被预设批量
        改写，窗口尺寸混进去会被预设顺手改掉。所以这一行不走 _slider。
        """
        f = tk.Frame(parent, bg=CARD)
        f.pack(fill="x", padx=16, pady=8)
        f.grid_columnconfigure(1, weight=1)
        tk.Label(f, text=label, bg=CARD, fg=TEXT,
                 font=FONT_LABEL).grid(row=0, column=0, sticky="w")
        var = tk.DoubleVar(value=float(self.W if key == "win_w" else self.H))
        val = tk.Label(f, text="", bg=CARD, fg=ACCENT, font=FONT_NUM)

        def on(*_):
            v = int(round(var.get()))
            val.config(text=f"{v} 像素")
            self._set_size(**{key: v})

        _Slider(f, lo, hi, 1, var, command=on,
                width=120).grid(row=0, column=1, sticky="ew", padx=(14, 10))
        val.grid(row=0, column=2, sticky="e")
        # 这里**不能**调 on()：构造期调一次就会把尺寸当成"用户自定义"落盘，
        # 首次运行也会被写死成默认值，之后就永远失去内容自适应。
        val.config(text=f"{int(var.get())} 像素")
        self._size_rows[key] = {"var": var, "label": val}

    def _mini_btn(self, parent, text, cmd):
        """卡片里的小动作按钮，与颜色行的「调色/取色」同一套扁平样式。"""
        b = tk.Button(parent, text=text, bd=0, relief="flat", bg=BTN, fg=SUB,
                      font=FONT_SMALL, cursor="hand2", padx=10, pady=4,
                      activebackground=BTN_H, activeforeground=TEXT,
                      command=cmd)
        b.pack(side="left", padx=(0, 8))
        b.bind("<Enter>", lambda e: b.config(bg=BTN_H, fg=TEXT))
        b.bind("<Leave>", lambda e: b.config(bg=BTN, fg=SUB))
        return b

    def _key_row(self, card, keys, desc):
        """「按键 …… 说明」行：按键用等宽字 + 强调色，说明用次级灰。"""
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", padx=16, pady=4)
        tk.Label(row, text=keys, bg=CARD, fg=ACCENT, font=FONT_NUM,
                 anchor="w").pack(side="left")
        tk.Label(row, text=desc, bg=CARD, fg=SUB, font=FONT_SMALL,
                 anchor="w").pack(side="right")

    def _card_title(self, card, text):
        """卡片内的小标题，与「预设方案」标题同一套样式。"""
        tk.Label(card, text=text, bg=CARD, fg=SUB,
                 font=(FONT_LABEL[0], FONT_LABEL[1], "bold")).pack(
            anchor="w", padx=12, pady=(10, 4))

    def _hint(self, card, text):
        """设置项下方的说明文字：用次级灰，避免和主标签抢注意力。"""
        # wraplength 必须 ≤ 卡片内可用宽度（窗口 564 时：卡片 412 − 两侧
        # padx 32 = 380）：给大了会让最后一个字单独折到下一行，出现"）。"这种
        # 孤字。窗口现在可缩放，所以注册进 _hints，宽度一变由
        # _apply_wraplength 统一重算 —— 否则窗口缩小后说明文字会溢出被裁。
        lb = tk.Label(card, text=text, bg=CARD, fg=SUB, font=FONT_SMALL,
                      anchor="w", justify="left",
                      wraplength=max(160, self.W - 192))
        lb.pack(fill="x", padx=16, pady=(0, 4))
        self._hints.append(lb)

    def _layer_row(self, parent, label, key):
        """层级行：名称 …… 最上层 (开关) 仅桌面图层。

        层级是两选一，但只有一边是"非默认"的，所以沿用项目统一的胶囊开关：
        关＝最上层（默认），开＝仅桌面图层。两侧文字跟着开关染色，不用点开
        就知道当前在哪一档。

        「仅桌面图层」= 把这一层画到壁纸层上（SetParent 到 WorkerW）。它会被
        任何窗口遮住，所以"桌面被窗口盖满时看不见"是这一档的正常表现。真正会
        坏的是挂载本身 —— SetParent 会废掉分层表面，见
        fx_winlayer.Overlay._rebuild_layered_surface。
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
        """通用开关行：名称 …… 开关（开/关）。"""
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
                sel = str(c).lower() == cur
                named = not str(c).startswith("#")
                # 选中项用强调色描边 2px，不用系统亮色边框。命名项（文字按钮）
                # 未选中时用次级字色，否则它会比色块还抢眼。
                b.config(relief="solid" if sel else "flat",
                         bd=2 if sel else 0,
                         fg=ACCENT if sel else (SUB if named else TEXT))

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
            # 不是 hex 的项当"命名选项"渲染成文字按钮（如「彩色」）——
            # 它没有单一颜色可当色块，硬填一个底色反而误导。
            named = not str(c).startswith("#")
            b = tk.Button(f, bg=BTN if named else c, width=3 if named else 2,
                          height=1, bd=0, relief="flat", cursor="hand2",
                          text=c if named else "", fg=SUB if named else TEXT,
                          font=FONT_SMALL if named else FONT_LABEL,
                          activebackground=BTN_H if named else c,
                          activeforeground=TEXT,
                          command=lambda cc=c: (app.set(key, cc), refresh()))
            b.pack(side="right", padx=3)
            if named:
                b.bind("<Enter>", lambda e, w=b: w.config(bg=BTN_H, fg=TEXT))
                b.bind("<Leave>", lambda e, w=b: w.config(bg=BTN, fg=SUB))
            btns.append(b)
        refresh()
        self._swatches[key] = refresh

    def _choices(self, parent, label, key, options, columns=None, on_change=None):
        """分段选择行：名称 …… 一排文字按钮（选中项用强调色描边 2px）。

        视觉语言与 `_swatch` 保持一致，只是把色块换成文字 —— "小猫 / 小狗"
        没有天然的颜色表示，硬造两个色块反而更难认。

        `columns` 给定时改为**等宽网格**（名称独占一行，按钮按列数折行）。
        选项多的时候必须折行：8 个图案排在单行会把页面撑到 538px，而窗口最小
        宽度只给内容区留 400px —— 撑破之后 Tk 既不换行也不缩，只会把右边的
        选项直接裁掉。

        `on_change(选项)` 在写入配置**之后**再调，供"选 A 顺带把 B 也设好"
        这种联动用（点击表情的图案 → 惯用色）。
        """
        app = self.app
        f = tk.Frame(parent, bg=CARD)
        f.pack(fill="x", padx=16, pady=8)
        btns = []

        def refresh():
            cur = app.cfg[key]
            for b, opt in zip(btns, options):
                sel = (opt == cur)
                b.config(relief="solid" if sel else "flat",
                         bd=2 if sel else 0,
                         fg=ACCENT if sel else TEXT)

        def pick(opt):
            app.set(key, opt)
            if on_change is not None:
                on_change(opt)
            # refresh 放最后：on_change 可能改了本控件显示的键，得让它反映出来
            refresh()

        if columns:
            tk.Label(f, text=label, bg=CARD, fg=TEXT, font=FONT_LABEL,
                     anchor="w").grid(row=0, column=0, columnspan=columns,
                                      sticky="w", pady=(0, 4))
            for c in range(columns):
                f.grid_columnconfigure(c, weight=1, uniform="choice")
            for i, opt in enumerate(options):
                r, c = divmod(i, columns)
                b = tk.Button(f, text=opt, bd=0, relief="flat", bg=BTN, fg=TEXT,
                              font=FONT_LABEL, cursor="hand2",
                              activebackground=BTN_H, activeforeground=TEXT,
                              command=lambda o=opt: pick(o))
                b.grid(row=1 + r, column=c, sticky="ew", padx=3, pady=3)
                b.bind("<Enter>", lambda e, w=b: w.config(bg=BTN_H))
                b.bind("<Leave>", lambda e, w=b: w.config(bg=BTN))
                btns.append(b)
        else:
            tk.Label(f, text=label, bg=CARD, fg=TEXT,
                     font=FONT_LABEL).pack(side="left")
            # 倒着 pack 到右侧，视觉顺序才与 options 一致（btns[i] 对应 options[i]）
            for opt in reversed(options):
                b = tk.Button(f, text=opt, bd=0, relief="flat", bg=BTN, fg=TEXT,
                              font=FONT_LABEL, padx=10, cursor="hand2",
                              activebackground=BTN_H, activeforeground=TEXT,
                              command=lambda o=opt: pick(o))
                b.pack(side="right", padx=3)
                b.bind("<Enter>", lambda e, w=b: w.config(bg=BTN_H))
                b.bind("<Leave>", lambda e, w=b: w.config(bg=BTN))
                btns.insert(0, b)
        refresh()
        self._ctrls[key] = {"kind": "choices", "refresh": refresh}

    def _textbox(self, parent, label, key, height=3, gate=None, fill=None):
        """多行文本输入行：名称在上、输入框在下（横排会太窄）。

        `tk.Text` 而不是 `Entry`：要配多个文字就得能换行。写入走 `<KeyRelease>`
        —— 自动保存是 1 秒轮询的脏标记，逐键写 cfg 不会真的每次落盘。

        `gate` 给定时，这个框会在 `cfg[gate]` 为真的期间**变成只读**，内容改由
        `fill()` 提供（每 200ms 重算）——"语音与文字统一"要用它：文字由语音包
        的文件名决定，用户能看不能改。**框里显示的不是 cfg 那条键的值**，所以
        同步逻辑要单独走一条分支，否则 `_sync_from_cfg` 会把派生内容覆盖回去。
        """
        app = self.app
        f = tk.Frame(parent, bg=CARD)
        f.pack(fill="x", padx=16, pady=8)
        tk.Label(f, text=label, bg=CARD, fg=TEXT,
                 font=FONT_LABEL).pack(anchor="w", pady=(0, 4))
        box = tk.Frame(f, bg=LINE)                       # 1px 描边靠外框露边实现
        box.pack(fill="x")
        txt = tk.Text(box, height=height, width=1, bg=CARD2, fg=TEXT, bd=0,
                      relief="flat",
                      insertbackground=ACCENT, font=FONT_LABEL, wrap="word",
                      padx=8, pady=6, highlightthickness=0,
                      selectbackground=ACCENT_DARK, selectforeground=ON_ACCENT)
        txt.pack(fill="x", padx=1, pady=1)
        txt.insert("1.0", str(app.cfg.get(key, "")))

        def on_key(_e=None):
            app.set(key, txt.get("1.0", "end-1c"))

        txt.bind("<KeyRelease>", on_key)
        # 粘贴/剪切不触发 KeyRelease，单独补一次；切焦点时也补，避免漏写
        txt.bind("<<Paste>>", lambda e: txt.after(0, on_key))
        txt.bind("<<Cut>>", lambda e: txt.after(0, on_key))
        txt.bind("<FocusOut>", on_key)
        self._ctrls[key] = {"kind": "text", "widget": txt,
                            "gate": gate, "fill": fill, "editable": (CARD2, TEXT)}
        if gate is not None:
            self._sync_text(self._ctrls[key], app.cfg.get(key, ""))

    def _folder(self, parent, label, key, status=None, on_change=None):
        """文件夹选择行：名称 | 路径（从尾部截断） | 选择 / 清除。

        路径一律**截尾显示** —— 用户认的是最后一级目录名，前面被省略号吃掉
        不影响判断；不截的话一个长路径就能把最小窗口撑破。

        `status()` 由页面给，返回一句附加状态（"已载入 12 张"）。它由 `_poll`
        每 200ms 刷一次：自定义素材是**后台**解码的，界面不跟着动，用户就会
        以为"选了文件夹没反应"。
        """
        app = self.app
        f = tk.Frame(parent, bg=CARD)
        f.pack(fill="x", padx=16, pady=8)
        top = tk.Frame(f, bg=CARD)
        top.pack(fill="x")
        tk.Label(top, text=label, bg=CARD, fg=TEXT,
                 font=FONT_LABEL).pack(side="left")

        pick_btn = clear_btn = None
        for txt in ("清除", "选择"):
            b = tk.Button(top, text=txt, bd=0, relief="flat", bg=BTN, fg=SUB,
                          font=FONT_SMALL, padx=8, cursor="hand2",
                          activebackground=BTN_H, activeforeground=TEXT)
            b.pack(side="right", padx=(6, 0))
            b.bind("<Enter>", lambda e, w=b: w.config(bg=BTN_H, fg=TEXT))
            b.bind("<Leave>", lambda e, w=b: w.config(bg=BTN, fg=SUB))
            if txt == "清除":
                clear_btn = b
            else:
                pick_btn = b

        # 路径**独占第二行**而不是挤在名称与按钮之间：单行版的行宽由
        # "名称 + 路径自然宽 + 两个按钮"决定，而 Tk 的 Label 既不换行也不缩
        # ——一个长路径就能把页面顶到最小宽度之外（右侧被裁）。第二行能跟随
        # 窗口宽度折行（注册进 `_hints`），多花 24px 换掉这个隐患很划算。
        path = tk.Label(f, text="", bg=CARD, fg=SUB, font=FONT_SMALL,
                        anchor="w", justify="left",
                        wraplength=max(160, self.W - 192))
        path.pack(fill="x", pady=(3, 0))
        self._hints.append(path)

        def refresh():
            cur = str(app.cfg.get(key, "") or "")
            txt = _short_path(cur) if cur else "（未选择）"
            if status is not None:
                try:
                    extra = status() or ""
                except Exception:
                    extra = ""
                if extra:
                    txt = "%s   · %s" % (txt, extra)
            if path.cget("text") != txt:
                path.config(text=txt)

        def _set(v):
            app.set(key, v)
            refresh()
            if on_change is not None:
                on_change(v)

        def _choose():
            from tkinter import filedialog
            kw = {"parent": self.root, "title": label}
            cur = str(app.cfg.get(key, "") or "")
            if cur and os.path.isdir(cur):
                kw["initialdir"] = cur
            d = filedialog.askdirectory(**kw)
            if d:
                # normpath：系统对话框给的是正斜杠，存下来再显示会与用户
                # 手输的路径长得不一样（同一个目录两种写法）
                _set(os.path.normpath(d))

        pick_btn.config(command=_choose)
        clear_btn.config(command=lambda: _set(""))
        self._ctrls[key] = {"kind": "path", "refresh": refresh, "label": path}
        self._folders.append(refresh)
        refresh()

    def _sync_text(self, c, v):
        """把一个多行文本框刷成它当前该显示的内容。

        两种形态：
        * **只读闸门开着**（`cfg[gate]` 为真）—— 内容由 `fill()` 派生（语音包里
          每条音频的名字），框置为 disabled 并用次级字色，让"这不能改"一眼可见；
        * 平常 —— 显示 cfg 的值、可编辑。

        切换 state 时必须**先把框改回 normal 再增删内容**：disabled 的 `tk.Text`
        接受 `delete/insert` 却不显示（内容改了屏幕不变，很难查）。
        """
        w = c["widget"]
        gate = c.get("gate")
        locked = bool(gate is not None and self.app.cfg.get(gate))
        if locked:
            try:
                want = (c.get("fill")() if c.get("fill") else "") or ""
            except Exception:
                want = ""
        else:
            want = str(v if v is not None else "")
        cur = w.get("1.0", "end-1c")
        # 值没变、形态也没变就别动：写回会把光标顶到开头，正在打字时尤其难受
        if cur != want or str(w.cget("state")) != ("disabled" if locked else "normal"):
            bg, fg = (CARD2, SUB) if locked else c["editable"]
            w.config(state="normal", bg=bg, fg=fg)
            if cur != want:
                w.delete("1.0", "end")
                w.insert("1.0", want)
            w.config(state="disabled" if locked else "normal")

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
            elif c["kind"] == "choices":  # 分段选择（如宠物种类），重画描边
                c["refresh"]()
            elif c["kind"] == "path":     # 文件夹选择行：只有文字要重画
                c["refresh"]()
            elif c["kind"] == "text":     # 多行文本（如点击文字列表）
                self._sync_text(c, v)
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

        # 文件夹行的状态（"已载入 N 张"）：素材是后台解码的，这里跟着刷新 ——
        # 只在文字真的变了时才写 Label，免得每 200ms 触发一次重绘。
        for rf in self._folders:
            try:
                rf()
            except Exception:
                pass
        # 只读文本框（语音包统一模式）的内容也依赖目录扫描，跟着一起刷；
        # 用户中途往文件夹里丢了几首歌，两秒内文字栏就能反映出来。
        for key, c in self._ctrls.items():
            if c["kind"] == "text" and c.get("gate") is not None:
                try:
                    self._sync_text(c, app.cfg.get(key))
                except Exception:
                    pass

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
