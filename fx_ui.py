# -*- coding: utf-8 -*-
"""设置面板：Linear/Raycast 风格深色工具面板。

无边框窗口 + 侧栏导航 + 可滚动内容区；控件全部为 Canvas 自绘，
以贴近设计稿的填充滑块、胶囊开关、线性图标与 8px 间距网格。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import random
import tkinter as tk
import time

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

# 图片当图案时的朝向。取值与 fx_effects.CLICK_ALIGN_* 一一对应，
# 一致性由 `_verify_click.py` 断言（少了画不出来、多了静默不生效）。
IMG_ALIGNS = [fx_effects.CLICK_ALIGN_VERTICAL, fx_effects.CLICK_ALIGN_SPREAD]
# 内置音效那一档的「随机」选项。它是个哨兵值（不是一个真文件名），
# 从配置往返时按文件名归一化（见 ControlPanel._sfx_builtin_norm）。
SFX_BUILTIN_RANDOM = "随机"

FONT_LABEL = ("Microsoft YaHei UI", 9)
FONT_TITLE = ("Microsoft YaHei UI", 10, "bold")   # 顶栏程序名
FONT_H2 = ("Microsoft YaHei UI", 12, "bold")      # 页头大标题
FONT_NUM = ("Consolas", 9)
FONT_SMALL = ("Microsoft YaHei UI", 8)

# ---- 悬浮提示 ----
# 配置项的说明不再常驻在卡片里（页面高度是有限预算，见 MEMORY「面板高度」），
# 改成挂在**配置项名称**上：鼠标停 0.4 秒弹出，离开或点击即收起。
TIP_DELAY = 400      # 鼠标停多久才弹出（ms）
TIP_WRAP = 300       # 提示文字折行宽度（px）

HWND_TOPMOST = wt.HWND(-1)
HWND_NOTOPMOST = wt.HWND(-2)   # 摘掉置顶位，窗口留在当前 z 序位置
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


def _clamp_xy(x, y, w, h, rect):
    """把 w×h 的矩形左上角夹进 rect=(l, t, r, b) 内，返回 (x, y)。

    **rect 的四边允许是负数** —— 多屏机器的虚拟桌面原点常常不在 (0,0)
    （副屏在主屏左侧时原点是 (-1920, 0)）。夹取边界一律用调用方给的 rect，
    不要在函数里再拿主屏尺寸兜底，那正是"面板在副屏、提示弹回主屏"的根因。
    """
    l, t, r, b = rect
    x = min(max(l, x), max(l, r - w))
    y = min(max(t, y), max(t, b - h))
    return int(x), int(y)


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
                 on_release=None, width=200, **kw):
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
        self.on_release = on_release
        self._req_w = int(width)   # 请求宽度；勿用 _w（tkinter 内部 Tcl 路径）
        self._bake_key = None   # 上次烘焙的 (宽, 取值, 底色)，同键不重烘焙
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
        w = max(self.winfo_width(), self._req_w, self._xpad * 2 + 8)
        h = self.HEIGHT
        v = float(self.var.get())
        bg = self.cget("bg")
        # 切页会把每个滑块重新 <Configure> 一遍（宽、取值、底色都没变）——
        # 不判缓存的话每次切页都白烘焙十几张 PIL 抗锯齿图，还把上屏绘图摊成
        # "从上往下一点点刷出来"。同键就一个控件都不碰。
        key = (w, round(v, 6), bg)
        if key == self._bake_key:
            return
        self._bake_key = key
        self.delete("all")
        cy = h // 2
        ratio = (max(self.lo, min(self.hi, v)) - self.lo) / (self.hi - self.lo)
        x = self._xpad + ratio * (w - 2 * self._xpad)
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
        if self.on_release:
            try:
                self.on_release()
            except Exception:
                pass

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


class _WinBtn(tk.Canvas):
    """标题栏右端的窗控钮（最小化 / 最大化 / 关闭），画成普通窗口那副样子。

    **为什么用 Canvas 画而不用 Button + 文字**：符号 "— □ ×" 用字符画出来会
    随字体缺字变豆腐块，而且粗细/基线对不齐（三颗按钮大小不一看着就是残的）。
    Canvas 画线画框完全不依赖字体，也顺手把悬停底色一起接管（Button 的
    activebackground 只在按住时生效，悬停要自己 bind）。

    三种符号的画法：
      * min  —— 一条横线（居中偏下）
      * max  —— 一个方框；已最大化时改成**两个错开的方框**（还原）
      * close—— 两条对角线
    """

    W = 44
    H = 42                 # 与标题栏同高（chrome）

    def __init__(self, parent, kind, command, hover):
        super().__init__(parent, width=self.W, height=self.H, bg=SIDEBAR_BG,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.kind = kind
        self.command = command
        self.hover = hover
        self.maximized = False       # 由 _toggle_maximize 回写
        self._hot = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", lambda e: self._press())
        self.bind("<Configure>", lambda e: self.repaint())

    # ---------------------------------------------------------- 交互
    def _press(self):
        self.command()
        return "break"

    def _on_enter(self, _e=None):
        self._hot = True
        self.repaint()

    def _on_leave(self, _e=None):
        self._hot = False
        self.repaint()

    def set_maximized(self, on):
        if bool(on) != bool(self.maximized):
            self.maximized = bool(on)
            self.repaint()

    # ---------------------------------------------------------- 绘制
    def repaint(self):
        """整块重画：先清掉所有图元，再按当前状态画一遍。"""
        self.delete("all")
        w = max(int(self.winfo_width()), self.W)
        h = max(int(self.winfo_height()), self.H)
        bg = self.hover if self._hot else SIDEBAR_BG
        fg = WHITE if self._hot else SUB
        self.configure(bg=bg)
        cx, cy = w // 2, h // 2
        k = self.kind

        def line(x1, y1, x2, y2, width=1):
            self.create_line(x1, y1, x2, y2, fill=fg, width=width)

        if k == "min":
            line(cx - 5, cy + 4, cx + 5, cy + 4, width=1)
        elif k == "close":
            d = 5
            line(cx - d, cy - d, cx + d, cy + d, width=1)
            line(cx + d, cy - d, cx - d, cy + d, width=1)
        else:                                   # max / restore
            if self.maximized:
                # 还原：两个错开的方框（后面那个只画右上角两段，像叠在一起）
                d = 5
                self.create_rectangle(cx - d + 3, cy - d - 3, cx + d + 3,
                                      cy + d - 3, outline=fg, width=1)
                line(cx - d + 3, cy - d - 3, cx - d + 3, cy + d - 3)      # 左
                line(cx - d + 3, cy - d - 3, cx + d + 3, cy - d - 3)      # 上
                self.create_rectangle(cx - d - 3, cy - d + 3, cx + d - 3,
                                      cy + d + 3, outline=fg, width=1)
                line(cx + d - 3, cy + d + 3, cx - d - 3, cy + d + 3)      # 下
                line(cx + d - 3, cy + d + 3, cx + d - 3, cy - d + 3)      # 右
            else:
                d = 5
                self.create_rectangle(cx - d, cy - d, cx + d, cy + d,
                                      outline=fg, width=1)


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
        if c == self.color:
            return      # 切页时 15 个图标里 13 个颜色没变，别白烘焙
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
        elif n == "点击音效":
            # 喇叭 + 两道声波（纯听觉特效，图标要一眼看出"在发声"）
            line([(2.6, 7.0), (5.4, 7.0), (8.6, 4.2), (8.6, 12.8),
                  (5.4, 10.0), (2.6, 10.0), (2.6, 7.0)], width=1.3)
            line([(11.0, 6.4), (12.6, 8.6), (11.0, 10.8)], width=1.3)
            line([(13.4, 4.6), (15.2, 8.6), (13.4, 12.6)], width=1.3)
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
        elif n == "点击音效":
            self._legacy_line([2.6, 7.0, 5.4, 7.0, 8.6, 4.2, 8.6, 12.8,
                               5.4, 10.0, 2.6, 10.0, 2.6, 7.0], 1.3)
            self._legacy_line([11.0, 6.4, 12.6, 8.6, 11.0, 10.8], 1.3)
            self._legacy_line([13.4, 4.6, 15.2, 8.6, 13.4, 12.6], 1.3)
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
        # **面板是普通窗口，不置顶**（2026-10-19 起）。
        # 之前设了 -topmost 且每秒抢一次顶，代价是面板永远压住特效层：
        # "点击文字/表情"这类要在面板上可见的效果得靠渲染循环临时把覆盖层
        # 抬到面板之上（raise_topmost + 每帧自检补提升），一有别的置顶窗口
        # 参与就来回打架。现在面板就是普通窗口：
        #   * 点它就正常获得焦点、正常被别的窗口盖住 —— 与其他软件一致；
        #   * 特效层仍是 topmost，天然显示在面板之上，不需要任何临时提升。
        # 因此 desktop_fx 里那套"骑乘面板"机制（_ride_above_panel /
        # keep_riding_panel）也随之失去存在意义，见 fx_winlayer 的注释。
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

        # 卡片行的可见性表：card -> [{"w","when","opts"}]。`when()` 为假的行
        # 整行隐藏（如「图案来源=自选图片」时不该再显示内置图案选择）。
        # 重排必须走 _repack（先全部 pack_forget 再按登记顺序 pack）——
        # 直接 pack 一个刚显示的行会把它排到卡片末尾（pack 顺序即视觉顺序）。
        self._card_rows = {}
        # 侧栏顺序表：kind ∈ {"group","item","sep"}，折叠分组时按它重排
        self._nav_entries = []
        self._nav_groups = {}      # 组名 -> {"frame","label","open"}
        self._nav_group_of = {}    # 页键 -> 组名（活动页被折起来时自动展开）
        self._nav_collapsed = set(self._ui.get("nav_collapsed") or [])
        # 悬浮提示的窗口与待弹计时器（懒创建）
        self._tip_win = None
        self._tip_lb = None
        self._tip_after = None
        # 交互控件 HWND 集合：渲染线程据此区分"点按钮/滑块"（UI 操作，抑制
        # 点击特效）与"点面板空白处"（放行 —— 用户要求面板上也生效）。
        # 由 _poll 每 1s 扫描一次控件树同步（预设按钮/行内按钮会动态增删）。
        self._ia_hwnds = frozenset()
        self._ia_last_scan = 0.0

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
        return _clamp_xy(int(x), int(y), int(w), int(h),
                        (vx, vy, vx + vw, vy + vh))

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
        # 最大化（铺满虚拟桌面）时收掉全部手柄：这时拖边框改尺寸没有意义，
        # 留着反而会让人以为窗口还能调。
        if getattr(self, "_maximized", False):
            for g in self._grip.values():
                g.place_forget()
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
        """把面板 hwnd 登记给覆盖层，并让面板自己保持在置顶组。

        ⚠️ 2026-10-19 起**不再周期性抢顶**（`SetWindowPos(HWND_TOPMOST)` 已
        移除，只保留首次）。原因：面板每秒把 z 序抢回最顶，会把"点击文字/表情
        临时骑在面板之上"的提升反复打掉（渲染循环要靠 `keep_riding_panel`
        每帧补一次才能扛住），也是用户报的"面板上的特效第二次就被盖住"的
        放大器之一。抢顶保活交给覆盖层单方面做即可 —— 它本来就有每帧自检，
        而面板只是设置界面，被别人盖住时用户从托盘再点一次就行（`show()`
        会临时置顶一次再立刻摘掉）。

        同轮还去掉了 `set_panel_fx_mode`（播放点击特效期间把 acrylic 从
        alpha 210 切到 255 去毛玻璃）：那套是为"特效需要临时提升"配套的，
        面板变成普通窗口后前提消失，而它造成的副作用很直观 —— 点一下面板
        颜色就变深、播完又变回来，用户报的就是这个。

        **必须保留的是下面 `set_panel_hwnd` 那段**：覆盖层重建后要重新登记
        面板 hwnd（`Overlay._anchor` 会读它；登记为空时抢顶退回 HWND_TOPMOST，
        那是安全降级，不会把覆盖层的 TOPMOST 清掉）。
        """
        try:
            h = self._hwnd()
            if h:
                # 把面板 hwnd 交给覆盖层：它做置顶保活时会把面板排除在
                # "谁盖住了我"之外，并在"面板压得住对手"时拿面板当插入锚点
                # （插到面板正下方 = 既复位又不遮住设置界面）。
                ovs = getattr(self.app, "overlays", None) \
                    or [getattr(self.app, "overlay", None)]
                for ov in ovs:
                    if ov is not None:
                        ov.set_panel_hwnd(h)
                # 抢顶已移除（见方法 docstring）。面板的置顶位靠构造时的
                # `attributes("-topmost", True)` 维持 —— WS_EX_TOPMOST 不会自己
                # 掉，只会被"插到某个非置顶窗之后"清掉，而现在没有任何地方再把
                # 面板当插入点用了。
        except Exception:
            pass
        # 提示浮层此刻可能正开着：补抬一次（它本来是**从属**于面板的，正常
        # 压得住；这一手是兜底，防个别系统上 owner 关系被别处重置 —— 提示
        # 一闪就不见，用户只会说"这提示根本看不见"）。
        self._tip_relift()
        # 仍 1s 续期，但只为"覆盖窗可能重建"服务：重建后的 Overlay 没有
        # panel_hwnd，不重新登记的话它会把面板当普通对手插下去。
        # 退出中不再续期：窗口销毁后 Tcl 解释器还会把已排队的 after 跑一遍，
        # 报一串 "invalid command name ..._raise_top"（验证脚本连开多个面板时
        # 尤其吵）。
        if not getattr(self, "_quitting", False):
            self.root.after(1000, self._raise_top)

    def _tip_relift(self):
        """把可见的提示浮层再抬一次（面板刚抢完置顶位之后调）。"""
        w = getattr(self, "_tip_win", None)
        if w is None:
            return
        try:
            if w.winfo_ismapped():
                w.lift()
        except Exception:
            pass

    def _start_move(self, e):
        self._drag = (e.x_root, e.y_root)

    def _do_move(self, e):
        dx = e.x_root - self._drag[0]
        dy = e.y_root - self._drag[1]
        self._drag = (e.x_root, e.y_root)
        self.root.geometry(f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}")

    def hide(self):
        self._tip_hide()
        self.root.withdraw()

    def show(self):
        """从托盘唤出面板。

        面板本身是**普通窗口**（不再 topmost，见 __init__ 的说明），但用户
        双击托盘图标把它叫出来时，期待的是"它出现在最前面"—— 这时要临时
        置顶一次，再立刻摘掉置顶位。

        为什么必须"临时"而不是常驻：常驻置顶就是上一轮取消掉的那个行为，
        面板会永远压住特效层，且被别的置顶窗口压住后还得每秒抢顶。而唤出
        这一个动作需要的只是"排在前面"，`SetWindowPos(HWND_NOTOPMOST)`
        正好把置顶位摘掉、窗口仍留在置顶组的当前位置上（还在最前面），
        之后再被谁激活/盖住都是普通窗口的标准行为。

        `root.lift()` 保留：它是 Tk 侧的抬升，与 Win32 置顶是两条路。
        """
        self.root.deiconify()
        self.root.lift()
        h = self._hwnd()
        if h:
            try:
                # 先置顶（保证排在最前），再立刻摘掉置顶位 —— 这一对调用
                # 必须连续，中间的几毫秒里窗口处于置顶组顶部。
                user32.SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                user32.SetWindowPos(h, HWND_NOTOPMOST, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            except Exception:
                pass
        self._tip_relift()

    # ------------------------------------------------------------ 构建
    def _build(self):
        root = self.root

        # 标题栏
        bar = tk.Frame(root, bg=SIDEBAR_BG, height=42)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text="  桌面特效", bg=SIDEBAR_BG, fg=TEXT,
                 font=FONT_TITLE).pack(side="left")
        # 状态区兼作**暂停开关**：点一下切换暂停/继续。原先它在右下角的悬浮条上，
        # 那一块这一轮整个删掉了，得在这里留住这个能力（托盘菜单与 Ctrl+Alt+R
        # 也在，但面板上点得到比去托盘右键顺手）。
        self._paused = False
        self.status = tk.Label(bar, text="", bg=SIDEBAR_BG, fg=SUB,
                               font=FONT_NUM, cursor="hand2")
        self.status.pack(side="left", padx=8)
        self.status.bind("<ButtonPress-1>", lambda e: self._pause())
        self.status.bind("<Enter>", lambda e: self._status_paint(True))
        self.status.bind("<Leave>", lambda e: self._status_paint(False))
        self._bind_tip(self.status, "点一下暂停 / 继续全部特效"
                                   "（快捷键 Ctrl+Alt+R，效果一样）。")

        # ---- 标题栏右端的窗控钮：最小化 / 最大化 / 关闭 ----
        # 与普通窗口一致（这是这一轮的明确要求）：最小化收进托盘，最大化在
        # 「铺满工作区」与原尺寸之间切换，关闭**也是**收进托盘 —— 托盘图标
        # 才是真正的退出入口（fx_tray 的右键菜单里有「退出」）。
        #
        # 为什么不用系统的边框按钮：本窗口是 overrideredirect（无系统边框，
        # 圆角/阴影/置顶组都要自己管），系统按钮根本不存在。
        self._maximized = False
        self._restore_rect = None      # 最大化前的 (x, y, w, h)
        self._win_btns = []
        for kind, cmd, hov in (("close", self.hide, DANGER),
                               ("max", self._toggle_maximize, BAR_HOVER),
                               ("min", self.hide, BAR_HOVER)):
            b = _WinBtn(bar, kind, cmd, hov)
            b.pack(side="right")
            self._win_btns.append(b)
            self._bind_tip(b, {"min": "最小化：收进托盘，特效继续运行。",
                               "max": "最大化：在铺满工作区与原尺寸之间切换。",
                               "close": "关闭面板：收进托盘（右键托盘图标可退出"
                                        "程序）。"}[kind])

        bar.bind("<ButtonPress-1>", self._start_move)
        bar.bind("<B1-Motion>", self._do_move)
        # 双击标题栏 = 最大化/还原（普通窗口的习惯动作）
        bar.bind("<Double-Button-1>", lambda e: self._toggle_maximize())

        # 错误横幅：默认不 pack
        self.err_bar = tk.Label(root, text="", bg=ERROR_BG, fg=ERROR_FG,
                                font=FONT_SMALL, anchor="w",
                                padx=10, pady=5, wraplength=470, justify="left")
        self._err_shown = None

        # 主区域
        main = tk.Frame(root, bg=BG)
        main.pack(fill="both", expand=True)
        self._main = main

        # ---- 侧栏（可滚动 + 分组可折叠）----
        side = tk.Frame(main, bg=SIDEBAR_BG, width=120)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        # 13 个特效项 + 4 个分组标题，窗口被压矮时会溢出屏幕 —— 装进 Canvas
        # 用滚轮兜底（装得下时不滚，理由同内容区）。折叠分组是更快的办法：
        # 点分组标题整组收起，只留当前在用的那一组。
        nav_cv = tk.Canvas(side, bg=SIDEBAR_BG, highlightthickness=0, bd=0)
        nav_cv.pack(fill="both", expand=True)
        nav_in = tk.Frame(nav_cv, bg=SIDEBAR_BG)
        nav_win = nav_cv.create_window((0, 0), window=nav_in, anchor="nw")
        # inner 尺寸一变就重算（折叠分组、增删项）。**必须走 `_sync_nav_scroll`**
        # 而不是直接写 bbox：那里还要给左下角的悬浮操作条留出让位。写成
        # `configure(scrollregion=bbox("all"))` 会让留白偶尔被冲掉 —— 表现是
        # "有时滚到底最后一项能露出来、有时又躲到条子后面"。
        nav_in.bind("<Configure>", lambda e: self._sync_nav_scroll())
        nav_cv.bind("<Configure>",
                    lambda e: (nav_cv.itemconfig(nav_win, width=e.width),
                               self._sync_nav_scroll()))
        # 滚轮**不能只绑在 canvas 上**：Tk 把滚轮事件发给光标正下方那个控件、
        # 不会冒泡给父容器 —— 鼠标停在导航项（Frame/Label）上时一点反应都没有，
        # 窗口一矮、分组一多，下面的组就永远点不到。统一挂到 bind_all 上、
        # 由 _on_wheel_all 按归属分派（见那里）。
        # **只能有一个 bind_all**：后来者会整条替换掉前面的绑定（不带 add="+"），
        # 侧栏那一份就是这么被内容区那一份悄悄顶掉的。
        self._nav_cv = nav_cv
        self._nav_in = nav_in

        # 注意：name 必须与 _pages 的注册键一致，否则点击后右侧空白。
        # 分组与顺序取自 fx_kinds.NAV_GROUPS —— 侧栏分类、渲染分层、同类
        # 互斥共用同一份定义，避免"界面归了类、别处还对不上"。
        for cat, pages in fx_kinds.NAV_GROUPS:
            self._nav_group(nav_in, cat)
            for name in pages:
                self._nav_group_of[name] = cat
                self._nav_item(nav_in, fx_kinds.page_title(name), name, cat)
        self._nav_sep(nav_in)
        self._nav_item(nav_in, "其他设置", "其它", None)

        # 暂停/隐藏到托盘/退出原先做成右下角的悬浮操作条，这一轮去掉了：
        # 暂停挪到标题栏状态区（点一下切换，见 `_status_paint`），"隐藏到托盘"
        # 与"最小化"合并成同一个动作，退出只留在托盘右键菜单里 —— 那是唯一
        # 可靠的退出入口（面板被隐藏时它是唯一的路）。
        #
        # 少一块浮在内容上的东西，两处连带收益：内容区与侧栏都不用再为它预留
        # 让位高度（`_fab_clear` 一并删掉），自适应窗高也少算一笔。

        # ---- 内容区（无滚动条，滚轮仍可上下滚动）----
        # 内容区留在 nav_cv 右边 = 宽 `W - 120`，悬浮操作条按这个宽度铺满
        cont = tk.Frame(main, bg=BG)
        cont.pack(side="left", fill="both", expand=True)
        canvas = tk.Canvas(cont, bg=BG, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        inner = tk.Frame(canvas, bg=BG)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        # inner 尺寸一变就重算滚动区（切页、增删预设都会变）。走 _sync_scroll
        # 而不是直接写 bbox：滚到底时还要给悬浮操作条留出让位（见那里）。
        inner.bind("<Configure>", lambda e: self._sync_scroll())
        canvas.bind("<Configure>",
                    lambda e: (canvas.itemconfig(win, width=e.width),
                               self._sync_scroll()))
        # 滚轮只绑在 canvas / inner 上是**不够**的：Tk 把滚轮事件发给光标正下方
        # 那个控件，不会冒泡到父容器 —— 鼠标停在页面里的卡片、滑块上时毫无反应，
        # 配置项一多，下面的"预设方案"就永远滚不到（颜料盘页踩过）。
        # 改成 bind_all + 归属判断：只有事件来自内容区才滚，对话框不受影响。
        # 侧栏也走同一个 bind_all，由 _on_wheel_all 分派 —— 挂两个 bind_all
        # 时后者会整条顶掉前者（不带 add="+"），这是踩过一次的真坑。
        canvas.bind("<MouseWheel>", self._on_wheel)
        inner.bind("<MouseWheel>", self._on_wheel)
        root.bind_all("<MouseWheel>", self._on_wheel_all)
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
            "点击音效": self._page_click_sfx,
            "窗口水波": self._page_win,
            "其它": self._page_other,
        }
        # 按侧栏顺序注册：_pages 的键序即导航顺序，测试直接比对二者
        for name in fx_kinds.nav_order():
            self._pages[name] = builders[name](inner)
        # 建页时所有行都是按创建顺序 pack 的（when 条件还没生效）—— 这里按
        # 当前配置收一次：默认「内置图案」时不该看见素材行与单次选择。
        # 必须赶在 _fit_height 量页面高度之前，否则量到的是"全都露出来"的高。
        self._refresh_rows()
        for f in self._pages.values():
            f.pack_forget()
        self._show_page(self._load_last_page())

        # 原先贴在窗口最底部的"托盘图标右键可唤出菜单 / Ctrl+Alt+R 快速暂停"
        # 提示条已挪进「其他设置」页（见 _page_other 的「快捷操作」卡片）——
        # 那行灰字常年占着窗口底部，窗口一缩就成了最先被挤掉的东西。
        # 手柄最后铺，保证压在内容之上（place 的堆叠顺序 = 创建顺序）。
        self._build_grips()
        # 交互控件集合先同步一次（此刻窗口还是 withdraw，控件未必 realize，
        # 很可能扫出空集合 —— 无妨，_poll 每秒会重扫，面板显示后必然齐整）
        self._sync_interactive_hwnds()

    # ------------------------------------------------------------ 侧栏/导航
    def _nav_group(self, side, text):
        """侧栏分组标题：点一下折叠/展开整组。

        特效项有 13 个，窗口压矮时侧栏会溢出屏幕 —— 除了滚轮，折叠是更快的
        办法：只留当前在用的那一组。折叠状态存 ui_state，重启后还记得。
        """
        open_ = text not in self._nav_collapsed
        b = tk.Frame(side, bg=SIDEBAR_BG, cursor="hand2")
        b.pack(fill="x", padx=6, pady=(10, 2))
        lbl = tk.Label(b, text=("▼  " if open_ else "▶  ") + text, bg=SIDEBAR_BG,
                       fg=SUB, font=FONT_SMALL, anchor="w")
        lbl.pack(fill="x", padx=8, pady=3)

        def _enter(_e):
            b.config(bg=NAV_HOVER)
            lbl.config(bg=NAV_HOVER)

        def _leave(_e):
            b.config(bg=SIDEBAR_BG)
            lbl.config(bg=SIDEBAR_BG)

        def _toggle(_e=None):
            self._toggle_nav_group(text)

        for w in (b, lbl):
            w.bind("<ButtonPress-1>", _toggle)
            w.bind("<Enter>", _enter)
            w.bind("<Leave>", _leave)
        self._nav_groups[text] = {"frame": b, "label": lbl, "open": open_}
        self._nav_entries.append({"w": b, "when": None,
                                  "opts": dict(fill="x", padx=6,
                                               pady=(10, 2))})

    def _nav_sep(self, side):
        """导航项与设置之间的发丝分隔线。"""
        f = tk.Frame(side, bg=LINE, height=1)
        f.pack(fill="x", padx=12, pady=(12, 4))
        self._nav_entries.append({"w": f, "when": None,
                                  "opts": dict(fill="x", padx=12,
                                               pady=(12, 4))})

    def _nav_item(self, side, label, name, group=None):
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
        # 分组折叠时这一项跟着藏起来；`when` 每次重排时现算，所以改完
        # _nav_groups 的 open 立刻生效
        if group is not None:
            when = (lambda g=group: self._nav_groups[g]["open"])
        else:
            when = None
        self._nav_entries.append({"w": b, "when": when, "group": group,
                                  "opts": dict(fill="x")})

    def _repack_nav_group(self, group):
        """只重排**某一个分组**的条目（折叠/展开用）。

        为什么不走整栏 `_repack(self._nav_entries)`：那会把侧栏每个 Frame 先
        pack_forget 再逐个 pack —— 整栏控件全部重排一遍，用户看到的就是"点一下
        折叠，整个左边刷了一次"。折叠只需要让本组的子项消失/回来，别的组一个都
        不该碰。

        顺序问题：pack 的顺序即视觉顺序，子项重新 pack 会排到栏尾。所以不能直接
        pack，要 pack 到**组后第一个可见条目之前**（`before=`），别的控件自始至终
        不被触碰，顺序也还原。
        """
        idx = [k for k, e in enumerate(self._nav_entries)
               if e.get("group") == group]
        if not idx:
            return
        i0, i1 = idx[0], idx[-1] + 1
        for e in self._nav_entries[i0:i1]:
            try:
                e["w"].pack_forget()
            except Exception:
                pass
        # 组后第一个仍处于 pack 状态的条目 = 重排锚点；整组后面都空着才追加到栏尾
        before = None
        for e in self._nav_entries[i1:]:
            try:
                if e["w"].winfo_manager() == "pack":
                    before = e["w"]
                    break
            except Exception:
                pass
        for e in self._nav_entries[i0:i1]:
            if e["when"] is not None and not e["when"]():
                continue
            try:
                if before is not None:
                    e["w"].pack(before=before, **e["opts"])
                else:
                    e["w"].pack(**e["opts"])
            except Exception:
                pass

    def _toggle_nav_group(self, name):
        """折叠/展开一个分组，并把状态落盘（重启后还记得）。"""
        g = self._nav_groups.get(name)
        if g is None:
            return
        g["open"] = not g["open"]
        g["label"].config(text=("▼  " if g["open"] else "▶  ") + name)
        if g["open"]:
            self._nav_collapsed.discard(name)
        else:
            self._nav_collapsed.add(name)
        self._ui["nav_collapsed"] = sorted(self._nav_collapsed)
        self._save_ui_state()
        self._repack_nav_group(name)
        self._sync_nav_scroll()
        # 折叠/展开同样要一次画完：子项消失后保留下来的项会整体上移，
        # 逐条重绘就是「闪一下」。
        self._flush_ui()

    def _on_nav_wheel(self, e):
        """侧栏滚轮：只认来自侧栏子树的滚轮事件。

        装得下时不滚：这时候滚起来只会把分组标题拉出可见区。
        返回值不用管 —— 分派由 `_on_wheel_all` 做（两处各绑一个 bind_all 时，
        后绑的会整条替换掉先绑的，这个坑已经踩过一次）。
        """
        if not self._nav_need_scroll():
            self._nav_cv.yview_moveto(0)
            return
        self._nav_cv.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def _in_subtree(self, w, anc):
        """w 是否在 anc 的子树里（含 anc 自己）。

        Tk 的 widget 树用 `master` 串起来，但 **bind_all 收到的 e.widget 可能是
        内部子控件**（如 Text 的显示框），沿 master 一定走得回 anc。
        """
        while w is not None:
            if w is anc:
                return True
            w = getattr(w, "master", None)
        return False

    def _nav_view_h(self):
        """侧栏可用高度 = canvas 高。

        操作条现在贴在**右下角、与内容区同宽**，不再压在侧栏之上，所以这里
        不用为它扣高度（早期版本贴在左下角时需要扣 —— 那时最下面几项会正好
        躲在条子后面，滚到底也点不着）。
        """
        try:
            return max(120, int(self._nav_cv.winfo_height()))
        except Exception:
            return 120

    def _nav_need_scroll(self):
        """侧栏装不下吗？用 inner 的请求高度比（scrollregion 在折叠/展开
        之后要等下一次 Configure 才更新，不能拿它判）。"""
        try:
            return int(self._nav_in.winfo_reqheight()) > self._nav_view_h()
        except Exception:
            return False

    def _sync_nav_scroll(self):
        """重算侧栏滚动区；装得下时收成视口高度，滚轮就彻底动不了。"""
        bb = self._nav_cv.bbox("all")
        if not bb:
            return
        vh = self._nav_view_h()
        top, bottom = int(bb[1]), int(bb[3])
        if bottom - top <= vh:
            self._nav_cv.configure(scrollregion=(bb[0], top, bb[2], top + vh))
            self._nav_cv.yview_moveto(0)
        else:
            self._nav_cv.configure(scrollregion=(bb[0], top, bb[2], bottom))

    # ------------------------------------------------------------ 悬浮提示
    def _bind_tip(self, widget, text):
        """把一段说明挂到控件上：鼠标停 0.4 秒弹出，离开/点击即收起。

        只挂**配置项名称**（不是整行）：名称是"这是什么"的唯一答案，滑块与
        开关自己已经会用光标语言说话。整行都挂的话，鼠标只是路过就会弹一
        堆框，反而看不清东西。
        """
        if not text:
            return
        widget.bind("<Enter>",
                    lambda e: self._tip_schedule(widget, text), add="+")
        widget.bind("<Leave>", lambda e: self._tip_hide(), add="+")
        widget.bind("<ButtonPress-1>", lambda e: self._tip_hide(), add="+")

    def _tip_schedule(self, widget, text):
        self._tip_hide()
        self._tip_after = self.root.after(
            TIP_DELAY, lambda: self._tip_show(widget, text))

    def _tip_hide(self):
        if getattr(self, "_tip_after", None):
            try:
                self.root.after_cancel(self._tip_after)
            except Exception:
                pass
            self._tip_after = None
        w = getattr(self, "_tip_win", None)
        if w is not None:
            try:
                w.withdraw()
            except Exception:
                pass

    def _tip_show(self, widget, text):
        self._tip_after = None
        if getattr(self, "_quitting", False):
            return
        try:
            if not widget.winfo_ismapped():
                return          # 行被藏起来了（换来源/折分组），别弹
        except Exception:
            return
        w = getattr(self, "_tip_win", None)
        if w is None:
            w = tk.Toplevel(self.root)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            w.configure(bg=LINE, highlightthickness=1,
                        highlightbackground=LINE, highlightcolor=LINE)
            self._tip_lb = tk.Label(w, bg=CARD, fg=TEXT, font=FONT_SMALL,
                                    anchor="w", justify="left",
                                    wraplength=TIP_WRAP, padx=10, pady=6)
            self._tip_lb.pack()
            self._tip_win = w
            w.withdraw()       # 先藏着，避免在 (0,0) 闪一下
        self._tip_lb.config(text=text)
        w.update_idletasks()
        tw, th = int(w.winfo_reqwidth()), int(w.winfo_reqheight())
        x = int(widget.winfo_rootx()) + 8
        y = int(widget.winfo_rooty()) + int(widget.winfo_height()) + 4
        # 夹回可见区域：**必须按虚拟桌面夹，不能按主屏** —— 副屏在主屏左侧时
        # 虚拟原点是负的，用主屏尺寸夹（max(0, …)）会把负坐标的提示整个弹到
        # 主屏上去，用户看到的就是"窗口在副屏、提示还留在主屏"。winfo_vroot*
        # 与 winfo_rootx 是同一套坐标系，可以直接比（同 _clamp_pos）。
        vx = int(self.root.winfo_vrootx())
        vy = int(self.root.winfo_vrooty())
        vw = int(self.root.winfo_vrootwidth())
        vh = int(self.root.winfo_vrootheight())
        x, y = _clamp_xy(x, y, tw, th, (vx, vy, vx + vw, vy + vh))
        w.geometry(f"+{x}+{y}")
        w.deiconify()
        self._tip_adopt()
        w.lift()

    def _tip_adopt(self):
        """把提示浮层设成面板的**从属窗口**（GWLP_HWNDPARENT）。

        为什么必须做：面板自己是置顶窗，而且每 1 秒抢一次置顶组顶部
        （`_raise_top`）。一个"只是也置顶"的提示会被它挤到下面 —— 表现就是
        提示一闪就没了、用户根本看不见。被拥有的窗口**永远画在拥有者之上**，
        这条关系不受抢位影响，也不用去跟自己的抢位逻辑打配合。

        时机很关键：要在 `deiconify()` **之后**设。overrideredirect 的 Toplevel
        在 withdraw/deiconify 之间会换掉底层窗口，之前设的 owner 会随着旧
        hwnd 一起作废（实测：创建时就设，回读拿到的是 0）。
        """
        w = getattr(self, "_tip_win", None)
        if w is None:
            return
        try:
            from _win32 import set_window_owner, top_hwnd
            set_window_owner(top_hwnd(w), self._hwnd())
        except Exception:
            pass

    # ------------------------------------------------------------ 行重排
    def _reg_row(self, card, widget, when=None, **opts):
        """把卡片里的一行登记进可见性表（opts = 它原本的 pack 参数）。

        为什么要登记：条件行（如「图案来源=自选图片」时才显示的素材行）
        显示/隐藏时若直接 pack，会被排到卡片末尾 —— pack 顺序即视觉顺序。
        重排时先全部 pack_forget 再按登记顺序 pack，行才会回到原位。
        """
        self._card_rows.setdefault(card, []).append(
            {"w": widget, "when": when, "opts": opts})

    def _repack(self, entries):
        """按登记顺序重排一组控件：先全收起，再让 when() 为真的按原位回来。"""
        for e in entries:
            try:
                e["w"].pack_forget()
            except Exception:
                pass
        for e in entries:
            if e["when"] is None or e["when"]():
                try:
                    e["w"].pack(**e["opts"])
                except Exception:
                    pass

    def _refresh_rows(self, force=False):
        """重排所有带条件行的卡片（目前只有「点击表情」「点击音效」有）。

        **可见集合没变就一个都不碰**。这不是图省事，是治闪烁：`_repack` 是
        一次 pack_forget + 重新 pack，卡片在两次布局之间会短暂空掉，而行数一变
        还会连带改页面高度 —— 表现就是"套用一个预设，整页闪一下"。预设本来就不
        带素材来源（见 desktop_fx.PRESET_EXCLUDE），条件行在套用预设时不该动，
        这条短路几乎总是命中，闪烁也就没了。
        """
        for rows in self._card_rows.values():
            if not any(r["when"] is not None for r in rows):
                continue
            if not force and not self._rows_changed(rows):
                continue
            self._repack(rows)

    @staticmethod
    def _rows_changed(rows):
        """按 `when()` 现算的可见集合与当前实际管理状态是否一致。"""
        for r in rows:
            if r["when"] is None:
                continue
            want = bool(r["when"]())
            try:
                now = bool(r["w"].winfo_manager())
            except Exception:
                return True
            if want != now:
                return True
        return False

    def _src_is_builtin(self):
        """点击表情的图案来源是不是内置图案。"""
        return fx_effects.normalize_source(
            self.app.cfg.get("click_emoji_source")) == \
            fx_effects.CLICK_SOURCE_BUILTIN

    def _src_has_images(self):
        """来源是图片（自选/文件夹）—— 素材行在这两档下才出现。"""
        return not self._src_is_builtin()

    def _src_is_folder(self):
        """来源是「图片文件夹」—— 只有这一档才需要「单次选择」。

        自选图片那一档用户在文件对话框里挑的就是**最终那几张**，一次点击
        从它们里取哪张已经是明摆着的意图，再让他决定"每颗各抽各的还是整簇
        同图"是多余的一层。内置图案同理（就一张图案而已）。
        """
        return fx_effects.normalize_source(
            self.app.cfg.get("click_emoji_source")) == \
            fx_effects.CLICK_SOURCE_FOLDER

    def _on_emoji_source(self, _src=None):
        """图案来源变了：立刻重排该显哪几行，并让素材状态行跟上。"""
        fx_effects.invalidate_media_cache()
        self._refresh_rows()
        rf = self._ctrls.get("click_emoji_files")
        if rf is not None:
            rf["refresh"]()

    def _on_wheel_all(self, e):
        """滚轮总入口：按事件来源分派给侧栏或内容区。

        必须是**唯一**一个 bind_all 处理器 —— 每次 `bind_all` 都会整条替换掉
        同名序列上的既有绑定（`add="+"` 才会追加），两个地方各绑一个时后绑的
        会把先绑的悄悄顶掉（侧栏滚轮就是这么失效的：内容区那份在后面建的）。

        两侧各自再做一次归属判断：来自别处（保存预设对话框等）的滚轮一律不管，
        否则在对话框里滚动会把背后的页面一起滚走。
        """
        w = e.widget
        if self._in_subtree(w, self._nav_cv):
            self._on_nav_wheel(e)
        elif self._in_subtree(w, self._canvas):
            self._on_wheel(e)

    def _on_wheel(self, e):
        """内容区滚轮。归属判断由 `_on_wheel_all` 做完了，这里只管滚。

        内容装得下时**直接不滚**：这时候滚起来只能把顶部拉走、露出下面一片空白。
        （用户报过："下雨界面这些要素较少的，不要把顶部往下拉"。）
        """
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

    # ------------------------------------------------------------ 最大化
    def _work_area(self):
        """最大化要铺满的矩形 (x, y, w, h) —— **主屏**，不是虚拟桌面。

        铺虚拟桌面在多屏机器上会把窗口拉成横跨两块屏的一条（3840 宽），
        页面被拉得极宽、左右两大片空白，卡片全挤在中间 —— 那不是"最大化"
        该有的样子。`winfo_screenwidth/height` 报的正是**主屏**尺寸，且 Tk 的
        屏幕坐标原点 (0,0) 就是主屏左上角，所以 x/y 直接给 0。
        """
        return (0, 0, int(self.root.winfo_screenwidth()),
                int(self.root.winfo_screenheight()))

    def _toggle_maximize(self):
        """在「铺满**主屏**」与原尺寸之间切换。"""
        if self._maximized:
            r = self._restore_rect or self._center_xy(self.W_DEF, self.H_DEF)
            self._maximized = False
            self._restore_rect = None
            x, y, w, h = r
            w, h = self._clamp_w(w), self._clamp_h(h)
        else:
            # 先记下**当前**矩形再改：_clamp_w/_clamp_h 会把尺寸夹到
            # `_SCREEN_FRAC`(0.92) 以内，所以必须存夹之前的值，否则还原时
            # 窗口会莫名其妙小一圈。
            self._restore_rect = (self.root.winfo_x(), self.root.winfo_y(),
                                  self.W, self.H)
            self._maximized = True
            x, y, w, h = self._work_area()
            self.W, self.H = w, h
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.W, self.H = w, h
        self._apply_wraplength(w)
        self._layout_grips(w, h)      # 最大化时会自己把手柄收掉
        for b in self._win_btns:
            b.set_maximized(self._maximized)
        self._flush_ui()
        self._sync_scroll()

    def _sync_scroll(self):
        """重算滚动区域。装得下时把区域收成**视口高度**，滚轮就彻底动不了。

        滚动区下沿就是内容本身的底边 —— 右下角那块悬浮操作条这一轮删掉了，
        不再需要为它预留一段（顺带消掉了「页面明明装得下、底部却多出一条空白」
        的观感问题）。
        """
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

        **最大化状态下整段跳过**：铺满虚拟桌面是用户当下的选择，自适应会把它
        拽回内容高度，看着像"窗口自己缩回去了"。守卫必须在**算 h 之前**，
        放错分支（比如只护 `else` 那条）就等于没护。
        """
        if getattr(self, "_maximized", False):
            return
        # 各页自然高度**不用先 pack 再 flush**：`winfo_reqheight()` 对从未
        # pack 过的页面同样准（实测 14 页与 pack 全页后逐一比对完全一致）。
        # 而"pack 全部 + 一次全树 flush"要 1 秒以上 —— 那是构造期最大的单笔
        # 开销：把 1100 个控件排一遍又马上收起来，纯粹为了量高度。省掉后构造
        # 从 ~1.4s 降到 ~0.4s（量测：tools/_meas_switch.py）。
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
            # 落实最终尺寸。构造期窗口还没上屏，这里只写 geometry、不触发
            # 可视上屏（_flush_ui 会按映射状态自己选 update / update_idletasks）。
            self._flush_ui()
        # 当前页在 _build 末尾就已 pack 好，这里只把滚动复位到顶部并重算滚动区
        # （窗口尺寸变了，旧区域可能装不下新视口）。不再调 _show_page：那会顺带
        # 重刷一遍侧栏 15 个图标的烘焙、再写一次 last_page，纯属白干。
        self._canvas.yview_moveto(0)
        self._sync_scroll()
        # 「窗口尺寸」卡片里的数值必须反映**真实**尺寸。构造期它是按 H_DEF
        # (742) 建的，自适应之后 H 变成按内容算出的值（例如 916）—— 不在这里
        # 同步，标签就永远停在 742，和窗口实际高度对不上。
        self._sync_size_rows()

    def _flush_ui(self):
        """立刻把挂起的界面工作做完：几何 + 重绘，一次性画完。

        已上屏时用 `root.update()` 而不是 `update_idletasks()`：后者只管几何
        和空闲重绘，页面里几十个控件映射产生的 Expose 事件会留在事件队列里被
        **一条一条**处理 —— 观感就是「右边的设置从上到下刷出来」。update() 把
        Expose 一并处理掉，处理器返回时整页就是画好的。

        未上屏（构造期）走 update_idletasks：没什么可画的，别白等；启动无闪烁
        测试也正是靠数它来断言「构造期刷新全部发生在隐藏期」。

        update() 会递归处理事件 → `_in_flush` 重入保护：连点两个导航项时，内层
        _show_page 跳过自己的 flush，由外层收尾，不会套娃。
        """
        try:
            if self.root.winfo_ismapped():
                if getattr(self, "_in_flush", False):
                    return
                self._in_flush = True
                try:
                    self.root.update()
                finally:
                    self._in_flush = False
            else:
                self.root.update_idletasks()
        except Exception:
            pass

    def _show_page(self, name):
        self._tip_hide()
        for n, f in self._pages.items():
            if n == name:
                f.pack(fill="x")
            else:
                f.pack_forget()
        self._active = name
        self._save_last_page(name)
        # 活动页所在的分组若是折叠的，侧栏里就看不到高亮在哪 —— 展开它
        # （只能发生在"重启后恢复的上次页面"：折叠状态下那一次点击根本
        #  点不到，所以不会和用户正在进行的折叠操作打架）。
        g = self._nav_group_of.get(name)
        if g is not None and g in self._nav_groups \
                and not self._nav_groups[g]["open"]:
            self._nav_groups[g]["open"] = True
            self._nav_groups[g]["label"].config(text="▼  " + g)
            self._nav_collapsed.discard(g)
            self._ui["nav_collapsed"] = sorted(self._nav_collapsed)
            self._save_ui_state()
            self._repack_nav_group(g)
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
        # 切页后强制一次布局 + 重绘（见 _flush_ui）：滚动区（scrollregion）要
        # 立即基于新页内容算准 —— 滚轮处理是同步读滚动区的，若等事件循环自愈，
        # 切页当帧滚轮会读到旧区域。同时把整页一次画完，避免逐条 Expose 造成
        # 的「从上到下渲染」观感。
        self._flush_ui()
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
            # sync=False：ui_state 只是界面偏好（窗口尺寸 / 上次停留页），
            # 每次切页都会写一次；fsync 在机械盘或网盘目录上一次 6ms 起，
            # 切页能明显感到卡。os.replace 仍保证原子（读者不会看到半截
            # JSON），丢的只是"最后一次偏好没落盘"—— 重启回默认而已。
            # config.json / presets_user.json 不在此列，照旧 fsync。
            atomic_write_json(_UI_STATE_PATH, dict(self._ui), indent=None,
                              sync=False)
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
        """“保存当前为预设”主操作：追加到配置属性卡片底部，与属性同块。

        整体包进一个容器 Frame 再登记进行可见性表：这个卡片里可能有条件行
        （换来源时显隐），重排会把它连同其它行一起重新 pack —— 不包容器的
        话，分隔线与按钮是两次独立 pack，重排后顺序就乱了。
        """
        box = tk.Frame(card, bg=CARD)
        box.pack(fill="x")
        self._reg_row(card, box, fill="x")
        tk.Frame(box, height=1, bg=LINE).pack(fill="x", padx=16, pady=(12, 8))
        btn = tk.Button(box, text="保存当前为预设", bg=ACCENT, fg=ON_ACCENT, bd=0,
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
        self._toggle(c, "启用下雨", "rain_enabled",
                     tip="打开后全屏落雨；关掉时这一层什么都不画。")
        self._slider(c, "雨量", "rain_amount", 0, 800, 5, "{:.0f} 滴",
                     tip="同时在屏幕上的雨滴数。调大更密，开销也线性上升。")
        self._slider(c, "下落速度", "rain_speed", 200, 2200, 10,
                     "{:.0f} 像素/秒", tip="雨滴掉得多快。")
        self._slider(c, "雨丝长度", "rain_length", 0.3, 2.6, 0.05,
                     "{:.2f} 倍",
                     tip="每滴雨拖出的尾线长度倍数：0.3 接近点状，"
                         "2.6 是细长斜线。")
        self._slider(c, "风力（左负右正）", "wind", -1.0, 1.0, 0.01, "{:+.2f}",
                     tip="横向推力：负值向左、正值向右，雨丝会跟着倾斜。")
        self._slider(c, "浓度", "rain_opacity", 0.05, 1.0, 0.01, "{:.0%}",
                     tip="整层雨的不透明度，也就是桌面能透出多少。")
        # 溅水花属于下雨本身（它只在雨滴落到底边时触发），从"其它"页搬过来
        self._toggle(c, "雨滴落地溅水花", "splash",
                     tip="雨滴落到底边时溅起一小圈水花。")
        self._swatch(c, "雨丝颜色", "rain_color", RAIN_COLORS,
                     tip="雨丝用哪一族颜色；每滴新雨从里面随机取一色。")
        self._save_bar(c, "下雨")
        self._preset_block(p, "下雨")
        return p

    def _page_snow(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "下雪")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用下雪", "snow_enabled",
                     tip="打开后全屏飘雪。")
        self._slider(c, "雪量", "snow_amount", 0, 800, 5, "{:.0f} 片",
                     tip="同时在屏幕上的雪花数。")
        self._slider(c, "飘落速度", "snow_speed", 20, 300, 5,
                     "{:.0f} 像素/秒", tip="雪花掉得多快。")
        self._slider(c, "雪花大小", "snow_size", 0.4, 2.5, 0.1, "{:.1f} 倍",
                     tip="雪花直径的倍数。")
        self._slider(c, "横向漂移", "snow_drift", -0.6, 0.6, 0.01, "{:+.2f}",
                     tip="雪花左右摇摆的幅度（相对下落速度）。")
        self._slider(c, "风速(阵风)", "snow_wind", -180, 180, 5,
                     "{:+.0f} 像素/秒",
                     tip="一阵一阵的风，把雪整体推向一边；数值是最大风速。")
        self._slider(c, "闪烁", "snow_twinkle", 0.0, 0.7, 0.05, "{:.0%}",
                     tip="雪花亮度随机起伏的幅度，0 就是不闪。")
        self._slider(c, "辉光", "snow_glow", 0.0, 1.0, 0.05, "{:.0%}",
                     tip="雪花边缘柔光的强度。")
        self._slider(c, "浓度", "snow_opacity", 0.05, 1.0, 0.01, "{:.0%}",
                     tip="整层雪的不透明度。")
        self._swatch(c, "雪花颜色", "snow_color", SNOW_COLORS,
                     tip="雪花用哪一族颜色。")
        self._save_bar(c, "下雪")
        self._preset_block(p, "下雪")
        return p

    def _page_petal(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "落樱")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用落樱", "petal_enabled",
                     tip="打开后全屏飘落花瓣。")
        self._slider(c, "花瓣数量", "petal_amount", 0, 600, 5, "{:.0f} 片",
                     tip="同时在屏幕上的花瓣数。")
        self._slider(c, "飘落速度", "petal_speed", 10, 260, 5,
                     "{:.0f} 像素/秒", tip="花瓣掉得多快。")
        self._slider(c, "花瓣大小", "petal_size", 0.4, 2.5, 0.1, "{:.1f} 倍",
                     tip="花瓣直径的倍数。")
        self._slider(c, "横向漂移", "petal_drift", -0.6, 0.6, 0.01, "{:+.2f}",
                     tip="花瓣左右摇摆的幅度。")
        self._slider(c, "风速(阵风)", "petal_wind", -180, 180, 5,
                     "{:+.0f} 像素/秒",
                     tip="一阵一阵的风，把花瓣整体推向一边。")
        self._slider(c, "自旋", "petal_spin", 0.0, 2.0, 0.05, "{:.2f} 倍",
                     tip="花瓣自转的快慢。")
        self._slider(c, "翻面", "petal_flutter", 0.0, 1.0, 0.05, "{:.0%}",
                     tip="花瓣翻面（正反面颜色互换）的频率。")
        self._slider(c, "浓度", "petal_opacity", 0.05, 1.0, 0.01, "{:.0%}",
                     tip="整层花瓣的不透明度。")
        self._swatch(c, "花瓣颜色", "petal_color", PETAL_COLORS,
                     tip="花瓣正面的颜色。")
        self._swatch(c, "边缘颜色(翻面泛白)", "petal_edge_color",
                     PETAL_EDGE_COLORS, tip="花瓣边缘的颜色，翻面时会泛白。")
        self._save_bar(c, "落樱")
        self._preset_block(p, "落樱")
        return p

    def _page_meteor(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "流星雨")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用流星雨", "meteor_enabled",
                     tip="打开后夜空中不时划过流星，可以配一片静态星空。")
        self._slider(c, "流星数量", "meteor_amount", 0, 120, 1, "{:.0f} 颗",
                     tip="同时在场的流星数。")
        self._slider(c, "飞行速度", "meteor_speed", 120, 2400, 20,
                     "{:.0f} 像素/秒", tip="流星划过天空的速度。")
        self._slider(c, "拖尾长度", "meteor_length", 40, 900, 10,
                     "{:.0f} 像素", tip="尾部拉出多长。")
        self._slider(c, "飞行角度", "meteor_angle", -80, 80, 1, "{:+.0f} °",
                     tip="相对水平面的夹角：负值向左下、正值向右下。")
        self._slider(c, "流星粗细", "meteor_size", 0.4, 3.0, 0.1,
                     "{:.1f} 倍", tip="流星本体的粗细倍数。")
        self._slider(c, "头部辉光", "meteor_glow", 0.0, 1.0, 0.05, "{:.0%}",
                     tip="流星头部光晕的强度。")
        self._slider(c, "星空密度", "meteor_stars", 0.0, 1.0, 0.05, "{:.0%}",
                     tip="背景星星的多少，0 就是一片黑。")
        self._slider(c, "浓度", "meteor_opacity", 0.05, 1.0, 0.01, "{:.0%}",
                     tip="整层的不透明度。")
        self._swatch(c, "头部颜色", "meteor_color", METEOR_COLORS,
                     tip="流星头（最亮那一端）的颜色。")
        self._swatch(c, "拖尾颜色", "meteor_tail_color", METEOR_TAIL_COLORS,
                     tip="尾部渐隐到的颜色。")
        self._save_bar(c, "流星雨")
        self._preset_block(p, "流星雨")
        return p

    def _page_paint(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "颜料盘")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用颜料盘", "paint_enabled",
                     tip="鼠标拖过的地方留下颜料，像在桌面上画画。")
        self._slider(c, "笔刷大小", "paint_brush", 0.3, 3.0, 0.1,
                     "{:.1f} 倍", tip="笔刷的粗细倍数。")
        self._slider(c, "落色量", "paint_flow", 0.0, 2.5, 0.05, "{:.2f} 倍",
                     tip="笔刷每次落下多少颜料。")
        self._slider(c, "搅动强度", "paint_swirl", 0.0, 2.5, 0.05,
                     "{:.2f} 倍", tip="把已有的颜料搅出旋涡的力度。")
        self._slider(c, "点击扩散", "paint_click", 0.0, 2.5, 0.05,
                     "{:.2f} 倍", tip="点一下鼠标时颜料向外洇开的幅度。")
        self._slider(c, "晕染", "paint_diffusion", 0.0, 1.0, 0.05, "{:.0%}",
                     tip="颜料边缘向外模糊的程度。")
        self._slider(c, "消隐速度", "paint_fade", 0.0, 1.0, 0.05, "{:.0%}",
                     tip="颜料慢慢褪色消失的快慢。")
        self._slider(c, "辉光", "paint_glow", 0.0, 1.0, 0.05, "{:.0%}",
                     tip="颜料边缘柔光的强度。")
        self._slider(c, "立体感", "paint_shading", 0.0, 1.0, 0.05, "{:.0%}",
                     tip="颜料团的高光与阴影，让它有厚度。")
        self._slider(c, "色相铺开", "paint_hue_spread", 0.0, 1.0, 0.05,
                     "{:.0%}", tip="每团颜料在基准色附近色相抖动的范围。")
        self._slider(c, "浓度", "paint_opacity", 0.05, 1.0, 0.01, "{:.0%}",
                     tip="颜料整体的不透明度。")
        self._swatch(c, "基准色", "paint_color", PAINT_COLORS,
                     tip="颜料的基准色；实际每团会在这个色附近抖动。")
        self._save_bar(c, "颜料盘")
        self._preset_block(p, "颜料盘")
        return p

    def _page_spot(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "聚光灯")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用聚光灯", "spotlight_enabled",
                     tip="鼠标变成一盏灯：光圈里是亮的，光圈外整屏压暗。")
        self._slider(c, "亮区半径", "spotlight_radius", 80, 600, 10,
                     "{:.0f} 像素", tip="光圈有多大。")
        self._slider(c, "压暗强度", "spotlight_dim", 0.1, 0.95, 0.01,
                     "{:.0%}", tip="光圈外被压暗的程度。")
        self._swatch(c, "压暗颜色", "spotlight_color", SPOTLIGHT_COLORS,
                     tip="压暗时叠上去的颜色，默认接近黑。")
        self._save_bar(c, "聚光灯")
        self._preset_block(p, "聚光灯")
        return p

    def _page_pet(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "宠物跟随")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用宠物跟随", "pet_enabled",
                     tip="小猫小狗沿着鼠标划过的路线跟上来：第一只追光标，"
                         "其余排成一列跟在它身后。形象由代码绘制，"
                         "不依赖任何图片文件。")
        self._choices(c, "种类", "pet_kind", PET_KINDS,
                      tip="跟在你身后的是小猫还是小狗。")
        self._slider(c, "数量", "pet_count", 1, 10, 1, "{:.0f} 只",
                     tip="最多 10 只：第一只追光标，其余一个追一个。")
        self._slider(c, "大小", "pet_size", 24, 120, 2, "{:.0f} 像素",
                     tip="体型，也决定队列间距。")
        self._slider(c, "跟随紧密度", "pet_follow", 0.05, 1.0, 0.01, "{:.0%}",
                     tip="追得有多紧：调低是慢悠悠地追，调高就紧咬着不放。")
        self._slider(c, "最小移动距离", "pet_min_travel", 0.0, 300.0, 10.0,
                     "{:.0f} 像素",
                     tip="露面之前鼠标得先真的走多远（按净位移算，原地来回"
                         "抖动不算）：轻轻碰一下不会惊动它们。填 0 表示"
                         "一动就现身。")
        self._slider(c, "静止后消失", "pet_idle_fade", 0.0, 6.0, 0.1,
                     "{:.1f} 秒",
                     tip="光标停住多久后它们淡出；填 0 表示一直跟着。")
        self._swatch(c, "毛色", "pet_color", PET_COLORS,
                     tip="毛色会连带耳朵内侧、描边和眼睛一起配色。")
        self._slider(c, "不透明度", "pet_opacity", 0.2, 1.0, 0.01, "{:.0%}",
                     tip="整体的不透明度。")
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
        self._toggle(c, "启用点击表情", "click_emoji_enabled",
                     tip="打开后点一下鼠标，就在光标处炸开一簇图案。")
        # 图案来源排在最前面：它决定下面露出来的是"挑图案"还是"挑素材"——
        # 放到最后面时，用户改完来源还得滚回去找对应的控件。
        self._choices(c, "图案来源", "click_emoji_source",
                      fx_effects.CLICK_SOURCES, norm=fx_effects.normalize_source,
                      on_change=self._on_emoji_source,
                      tip="内置图案＝从自带形状里挑；自选图片＝选一张或多张"
                          "（GIF 会动）；图片文件夹＝整摞里随机取。")
        # 内置图案才谈得上挑形状与配色：用图片当图案时这两行不参与绘制，
        # 所以整行藏掉（when 条件见 _src_is_builtin）。
        # 8 个图案排单行会把页面撑破最小宽度（内容区只有 400px），折成 4 列
        self._choices(c, "图案", "click_emoji_shape", CLICK_SHAPES, columns=4,
                      on_change=self._on_shape_change, when=self._src_is_builtin,
                      tip="炸开的图案形状；选好形状会自动换上它的惯用色。")
        self._asset_row(c, when=self._src_has_images,
                        tip="自选图片可以选一张或多张；图片文件夹则每次从整摞"
                            "里随机取。GIF 会按原来的速度循环播放。")
        self._choices(c, "单次选择", "click_emoji_pick", fx_effects.CLICK_PICKS,
                      when=self._src_is_folder,
                      tip="整簇同图＝一次点击全用同一张；随机多图＝每颗粒子"
                          "各抽各的。只在「图片文件夹」下有意义 —— 自选图片里"
                          "挑的就是最终那几张，来源不是文件夹时这一档藏起来。")
        # 图片方向是**通用**选项：内置图案（爱心/彩带有朝向）、自选图片、
        # 图片文件夹都适用，所以不挂 when。
        self._choices(c, "图片方向", "click_emoji_align", IMG_ALIGNS,
                      tip="竖直＝保持原朝向（像贴纸撒开）；"
                          "随扩散方向＝跟着自己飞出去的方向转，甩出一条条"
                          "轨迹。三种来源都适用（内置图案里也有朝向明确的"
                          "形状）；勾了它就等于不要自旋。")
        self._slider(c, "数量", "click_emoji_count", 2, 40, 1, "{:.0f} 个",
                     tip="一次点击炸出多少颗。")
        self._slider(c, "大小", "click_emoji_size", 8, 64, 1, "{:.0f} 像素",
                     tip="单颗图案的基准大小。")
        self._slider(c, "扩散", "click_emoji_spread", 100, 600, 10, "{:.0f}",
                     tip="炸开的空间范围。")
        self._slider(c, "重力", "click_emoji_gravity", -2.0, 2.0, 0.1, "{:+.1f}",
                     tip="图案炸开后往上飘还是往下掉：正数下坠、负数上浮。")
        self._slider(c, "持续时间", "click_emoji_life", 0.2, 1.5, 0.05,
                     "{:.2f} 秒", tip="一簇图案从炸开到散尽的时间。")
        self._toggle(c, "粒子自旋", "click_emoji_rotate",
                     tip="飞行过程中图案自个儿旋转（勾了「随扩散方向」时"
                         "自动让位给方向）。")
        self._swatch(c, "颜色", "click_emoji_color", CLICK_EMOJI_COLORS,
                     when=self._src_is_builtin,
                     tip="内置图案的颜色（用图片当图案时不参与绘制）；"
                         "「彩色」＝每颗随机取一色。")
        self._slider(c, "不透明度", "click_emoji_opacity", 0.2, 1.0, 0.01,
                     "{:.0%}", tip="整簇图案的不透明度。")
        self._save_bar(c, "点击表情")
        self._preset_block(p, "点击表情")
        return p

    def _asset_row(self, parent, label="图片素材", kind="img", status=None,
                   when=None, tip=None):
        """素材行：一排按钮 + 一行状态，内容跟着「来源」走。

        「图案来源」与「音效来源」共用这一行（`kind` 区分）—— 两者是同一件事
        的两个实例：来源有三档，后两档各自要"选文件 / 选文件夹 / 清除"。

        「选图片」「选文件夹」两个按钮**按来源只显示对应那一个**：来源是
        自选图片时只留「选图片」，是图片文件夹时只留「选文件夹」，是内置
        那一档时整行藏起来（见 `_reg_row` 的 when）。点按钮会把来源切到那一种，
        比"先去上面改来源、再回来点按钮"少一步，也排除了"来源是文件夹却
        点了选图片"这种自相矛盾的状态。当前来源对应的那个按钮会点亮。

        两个按钮**互斥**（同时只显示一个），所以显隐变化时直接 pack_forget /
        pack 就能保持视觉顺序 —— 创建顺序即视觉顺序，重新 pack 的按钮仍会
        回到它原来的位置（side="right" 从右往左按创建顺序排）。

        状态行注册进 `_hints`（跟随窗口宽度折行）：路径可以很长，而 Tk
        的 Label 既不换行也不缩。
        """
        app = self.app
        sfx = (kind == "sfx")
        f = tk.Frame(parent, bg=CARD)
        f.pack(fill="x", padx=16, pady=6)
        self._reg_row(parent, f, when=when, fill="x", padx=16, pady=6)
        top = tk.Frame(f, bg=CARD)
        top.pack(fill="x")
        lb = tk.Label(top, text=label, bg=CARD, fg=TEXT, font=FONT_LABEL)
        lb.pack(side="left")
        self._bind_tip(lb, tip)

        btns = {}
        # 倒着 pack 到右侧，视觉顺序才是「选图片 | 选文件夹 | 清除」
        for txt in ("清除", "选文件夹", "选文件" if sfx else "选图片"):
            b = tk.Button(top, text=txt, bd=0, relief="flat", bg=BTN, fg=SUB,
                          font=FONT_SMALL, padx=8, cursor="hand2",
                          activebackground=BTN_H, activeforeground=TEXT)
            b.pack(side="right", padx=(6, 0))
            b.bind("<Enter>", lambda e, w=b: w.config(bg=BTN_H, fg=TEXT))
            # 离场时回去跑一遍 refresh：否则悬停一次就把"当前来源"的高亮抹掉
            b.bind("<Leave>", lambda e: refresh())
            btns[txt] = b

        status_lb = tk.Label(f, text="", bg=CARD, fg=SUB, font=FONT_SMALL,
                             anchor="w", justify="left",
                             wraplength=max(160, self.W - 192))
        status_lb.pack(fill="x", pady=(2, 0))
        self._hints.append(status_lb)

        def _clear_target():
            """当前来源对应的是哪两个配置键（清哪个、按钮是哪两个）。"""
            if sfx:
                return ("click_sfx_file", "click_sfx_dir",
                        fx_effects.SFX_SOURCE_FILE, "选文件",
                        fx_effects.SFX_SOURCE_FOLDER, "选文件夹")
            return ("click_emoji_files", "click_emoji_custom_dir",
                    fx_effects.CLICK_SOURCE_FILES, "选图片",
                    fx_effects.CLICK_SOURCE_FOLDER, "选文件夹")

        def _set_source(v):
            app.set("click_sfx_source" if sfx else "click_emoji_source", v)
            if sfx:
                self._on_sfx_source(v)
            else:
                self._on_emoji_source(v)

        def refresh():
            if status is not None:
                # 音效那一档的状态交给页面自己算（"还没选"、"文件不在了"、
                # "找到 N 个音频"三种说法都在 _sfx_status 里）
                txt = status() or ""
            else:
                src_now = app.cfg.get("click_emoji_source")
                k, src = fx_effects.click_source_target(app.cfg)
                if not k:
                    txt = "当前用内置图案，不需要素材"
                elif k == "files":
                    n = len(fx_effects.list_image_files(src))
                    txt = ("已选 %d 张图片" % n) if n else "还没选图片"
                else:
                    st = fx_effects.CLICK_IMAGE_POOL.state(
                        k, src, float(app.cfg.get("click_emoji_size", 22)))
                    txt = "%s   ·   %s" % (_short_path(src), st or "")
            if status_lb.cget("text") != txt:
                status_lb.config(text=txt)
            # 只留当前来源那一组按钮；互斥保证了重排后顺序不会乱
            _fk, _dk, k_file, b_file, k_dir, b_dir = _clear_target()
            cur = (fx_effects.normalize_sfx_source if sfx
                   else fx_effects.normalize_source)(
                       app.cfg.get("click_sfx_source" if sfx
                                   else "click_emoji_source"))
            for b, key in ((btns[b_file], k_file), (btns[b_dir], k_dir)):
                on = (cur == key)
                if on and b.winfo_manager() == "":
                    b.pack(side="right", padx=(6, 0))
                elif not on and b.winfo_manager() != "":
                    b.pack_forget()
                if str(b.cget("fg")) != (ACCENT if on else SUB):
                    b.config(fg=ACCENT if on else SUB)

        def choose_files():
            from tkinter import filedialog
            if sfx:
                kw = {"parent": self.root, "title": "选择音效文件",
                      "filetypes": [("音频", "*.wav *.mp3 *.wma *.m4a"),
                                    ("所有文件", "*.*")]}
                cur = str(app.cfg.get("click_sfx_file", "") or "")
                if cur and os.path.isfile(cur):
                    kw["initialdir"] = os.path.dirname(cur)
                p1 = filedialog.askopenfilename(**kw)
                if p1:
                    app.set("click_sfx_file", os.path.normpath(p1))
                    _set_source(fx_effects.SFX_SOURCE_FILE)
                refresh()
                return
            kw = {"parent": self.root, "title": "选择图片",
                  "filetypes": [("图片",
                                 "*.png *.jpg *.jpeg *.bmp *.gif *.webp"),
                                ("所有文件", "*.*")]}
            cur = fx_effects.list_image_files(
                app.cfg.get("click_emoji_files", ""))
            if cur:
                kw["initialdir"] = os.path.dirname(cur[0])
            ps = filedialog.askopenfilenames(**kw)
            if ps:
                app.set("click_emoji_files", "\n".join(ps))
                _set_source(fx_effects.CLICK_SOURCE_FILES)
            refresh()

        def choose_folder():
            from tkinter import filedialog
            kw = {"parent": self.root,
                  "title": "选择音效文件夹" if sfx else "选择图片文件夹"}
            key_dir = "click_sfx_dir" if sfx else "click_emoji_custom_dir"
            cur = str(app.cfg.get(key_dir, "") or "")
            if cur and os.path.isdir(cur):
                kw["initialdir"] = cur
            d = filedialog.askdirectory(**kw)
            if d:
                # normpath：对话框给的是正斜杠，存下来会与用户手输的路径
                # 长得不一样（同一个目录两种写法）
                app.set(key_dir, os.path.normpath(d))
                _set_source(fx_effects.SFX_SOURCE_FOLDER if sfx
                            else fx_effects.CLICK_SOURCE_FOLDER)
            refresh()

        def clear():
            key_file, key_dir, k_file, _bf, k_dir, _bd = _clear_target()
            cur = (fx_effects.normalize_sfx_source if sfx
                   else fx_effects.normalize_source)(
                       app.cfg.get("click_sfx_source" if sfx
                                   else "click_emoji_source"))
            if cur == k_file:
                app.set(key_file, "")
            elif cur == k_dir:
                app.set(key_dir, "")
            # 清掉列表缓存：否则"清除"之后状态栏还会显示 2 秒的旧张数，
            # 看着就像按钮没生效
            fx_effects.invalidate_media_cache()
            refresh()

        _fk, _dk, _kf, b_file, _kd, b_dir = _clear_target()
        btns[b_file].config(command=choose_files)
        btns[b_dir].config(command=choose_folder)
        btns["清除"].config(command=clear)
        # 两个键共用这一行（来源决定它服务哪一个），都登记成 path 让
        # `_sync_from_cfg` 能刷到它
        for k in (_fk, _dk):
            self._ctrls[k] = {"kind": "path", "refresh": refresh,
                              "label": status_lb, "btns": list(btns.values())}
        self._folders.append(refresh)
        refresh()

    def _page_click_text(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "点击文字")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用点击文字", "click_text_enabled",
                     tip="打开后点一下鼠标，在光标处弹出一行字并向上飘走。")
        # 语音与音效的设置**全部搬到了「点击音效」页**：语音包就是那一页的
        # 「音效文件夹」，「语音与文字统一」也在那边（且只在那一档下可用）。
        # 这里只留"文字自己怎么显示"。
        # 这个框会被「语音与文字统一」接管：闸门一开就变成只读、内容由音效
        # 文件夹里的音频名派生（见 _sync_text）。
        self._textbox(c, "文字（一行一个，点击时轮换取下一个）",
                      "click_text_list", height=2,
                      gate="click_text_voice_match",
                      fill=self._voice_text_preview,
                      tip="一行一句，点一次换下一句。\n"
                          "在「点击音效」页把「语音与文字统一」打开后，这里会由"
                          "音效文件夹里的音频名自动填入并**锁定为只读** —— "
                          "弹出的字与听到的声音永远是同一条。")
        # 9 款字体排 5 列 = 2 行（3 列要 3 行，多 34px）。名字最长 4 个字，
        # 5 列在最小窗口宽度下也放得下（内容区 400px / 5 ≈ 80px）。
        self._choices(c, "字体", "click_text_font", CLICK_FONTS, columns=5,
                      tip="只列系统自带的字体；没装的自动回退微软雅黑。")
        self._slider(c, "字号", "click_text_size", 14, 90, 1, "{:.0f} 像素",
                     tip="弹出文字的大小。")
        self._swatch(c, "颜色", "click_text_color", CLICK_TEXT_COLORS,
                     tip="文字的颜色。")
        self._choices(c, "装饰", "click_text_deco", CLICK_DECOS,
                      tip="文字背后的点缀。")
        self._slider(c, "上飘高度", "click_text_rise", 0, 260, 5,
                     "{:.0f} 像素", tip="文字向上飘多远后消失。")
        self._slider(c, "倾斜幅度", "click_text_spin", 0.0, 1.0, 0.05, "{:.0%}",
                     tip="每次点击随机歪斜的角度范围。")
        self._slider(c, "持续时间", "click_text_life", 0.4, 3.0, 0.1,
                     "{:.1f} 秒", tip="一行字从弹出到消失的时间。")
        self._slider(c, "不透明度", "click_text_opacity", 0.2, 1.0, 0.01,
                     "{:.0%}", tip="文字的不透明度。")
        self._save_bar(c, "点击文字")
        self._preset_block(p, "点击文字")
        return p

    def _voice_text_preview(self):
        """统一模式下文字栏要显示的内容：音效文件夹里每条音频的名字。"""
        return "\n".join(
            fx_effects.audio_labels(fx_effects.voice_pack_dir(self.app.cfg)))

    # ------------------------------------------------------------ 点击音效
    def _sfx_is_builtin(self):
        return fx_effects.normalize_sfx_source(
            self.app.cfg.get("click_sfx_source")) == \
            fx_effects.SFX_SOURCE_BUILTIN

    def _sfx_is_file(self):
        return fx_effects.normalize_sfx_source(
            self.app.cfg.get("click_sfx_source")) == fx_effects.SFX_SOURCE_FILE

    def _sfx_is_folder(self):
        return fx_effects.normalize_sfx_source(
            self.app.cfg.get("click_sfx_source")) == fx_effects.SFX_SOURCE_FOLDER

    def _sfx_has_file(self):
        """音效素材行只在「单个音效 / 音效文件夹」两档下出现。"""
        return self._sfx_is_file() or self._sfx_is_folder()

    def _sfx_builtin_options(self):
        """内置音效的选项：`随机` + 每一个内置音效的名字。

        选项表是**动态**的（从 assets/sfx 扫出来），所以"内置里挑哪一个"
        不能写成常量表 —— 用户往那个目录里丢一个文件，重启就能选上。
        """
        return [SFX_BUILTIN_RANDOM] + fx_effects.builtin_sfx_names()

    def _sfx_builtin_norm(self, value):
        """把配置里的文件名归一化成界面上的显示名。

        配置存的是**文件名**（`青轴.wav`），界面显示的是**去扩展名的名字**
        （`青轴`）—— 不归一化的话一个选项都不会高亮（与图案来源从旧值迁移
        踩的是同一个坑）。
        """
        v = str(value or "")
        if not v:
            return SFX_BUILTIN_RANDOM
        return fx_effects.audio_label(v)

    def _on_sfx_source(self, _src=None):
        fx_effects.invalidate_media_cache()
        self._fix_voice_match()
        self._refresh_rows()
        self._sync_sfx_lock()

    def _fix_voice_match(self):
        """统一开着但来源不是「音效文件夹」→ 把它关掉。

        统一模式需要"一个装着多条音频的目录"才能把文件名一行行变成弹出文字；
        内置音效与单个音效都给不出多行文字。与其让界面显示"开着"却实际不生效
        （文字框没被接管，用户只会以为是自己哪里配错了），不如直接关掉这条
        不变量。
        """
        if not self.app.cfg.get("click_text_voice_match"):
            return
        if not self._sfx_is_folder():
            self.app.set("click_text_voice_match", False)

    def _on_voice_match(self, on):
        """「语音与文字统一」打开时的联动。

        * 来源切到「音效文件夹」—— 统一模式只在那一档下成立；
        * **自动启用「点击文字」**：统一的意义就是"弹出的字跟着声音走"，文字
          特效关着的话用户什么也看不到，这个开关等于白开；
        * 来源那一组控件被锁住（见 `_sync_sfx_lock`）：开着统一还去换来源，
          统一会在下一帧失效，而那一帧用户根本看不出来发生了什么。
        """
        if not on:
            self._sync_sfx_lock()
            return
        if not self._sfx_is_folder():
            self.app.set("click_sfx_source", fx_effects.SFX_SOURCE_FOLDER)
            self._on_sfx_source()
        if not self.app.cfg.get("click_text_enabled"):
            self.app.set("click_text_enabled", True)
        self._sync_sfx_lock()

    def _sync_sfx_lock(self):
        """「语音与文字统一」开着时，把音效来源那一组控件整体锁住。

        锁的是**按钮**（`state=disabled`）而不是把整行藏起来：那几行正是用户
        想改的内容，藏掉等于让他猜"开关为什么失效"；灰掉则一眼看得出"被统一
        占用了，关掉统一就能改"。
        """
        locked = bool(self.app.cfg.get("click_text_voice_match"))
        for key in ("click_sfx_source", "click_sfx_builtin",
                    "click_sfx_file", "click_sfx_dir"):
            c = self._ctrls.get(key)
            if not c:
                continue
            for b in c.get("btns", ()):
                try:
                    b.config(state="disabled" if locked else "normal")
                except Exception:
                    pass

    def _sfx_status(self):
        """音效素材行的状态文案：`路径 · 说明`。

        路径**要显示出来**（从尾部截断）：用户选的是哪个文件夹/哪个文件，
        只回一句"找到 3 个音频"的话他没法确认自己选对了没有 —— 而这正是
        "选了文件夹却一声不响"最难查的一环。
        """
        cfg = self.app.cfg
        src = fx_effects.normalize_sfx_source(cfg.get("click_sfx_source"))
        if src == fx_effects.SFX_SOURCE_FILE:
            p = str(cfg.get("click_sfx_file", "") or "")
            if not p:
                return "还没选音效文件"
            if not os.path.isfile(p):
                return "%s   ·   文件不在了，请重选" % _short_path(p)
            return "%s   ·   已选" % _short_path(p)
        if src == fx_effects.SFX_SOURCE_FOLDER:
            folder = str(cfg.get("click_sfx_dir", "") or "")
            if not folder:
                return "还没选文件夹"
            n = fx_effects.count_audio(folder)
            return "%s   ·   %s" % (
                _short_path(folder),
                ("找到 %d 个音频" % n) if n else "没找到音频")
        items = fx_effects.list_builtin_sfx()
        return ("内置 %d 个音效" % len(items)) if items else "内置音效缺失"

    def _preview_click_sfx(self):
        """按当前「点击音效」配置挑一条**立即试听**（所见即所闻）。

        与实际点击同源：同一个播放器、同一支音量滑块，听到的与真去点一下桌面
        完全一致。选内置音效 / 拖音量滑块松手时调它，消掉"选了却没声音、要等
        下次点击才听到"的断层。来源没配到东西时静默跳过。
        """
        cfg = self.app.cfg
        items, is_rand = fx_effects.sfx_target(cfg)
        if not items:
            return
        path = random.choice(items) if (is_rand and len(items) > 1) else items[0]
        self.app.preview_audio(path, cfg.get("click_sfx_volume", 1.0))

    def _pick_sfx_builtin(self, name):
        """内置音效里挑了一个 —— 存回**文件名**，并立即试听。

        存名字而不是下标：往目录里加/删一个文件不会让已选的项错位。
        """
        if name == SFX_BUILTIN_RANDOM:
            self.app.set("click_sfx_builtin", "")
        else:
            for p in fx_effects.list_builtin_sfx():
                if fx_effects.audio_label(p) == name:
                    self.app.set("click_sfx_builtin", os.path.basename(p))
                    break
        self._preview_click_sfx()      # 选中即试听，不用等下次真实点击

    def _page_click_sfx(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "点击音效")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用点击音效", "click_sfx_enabled",
                     tip="点一下鼠标就响一声。它不画任何图案 —— 只想要声音、"
                         "不要视觉特效时用这一页。")
        self._choices(c, "音效来源", "click_sfx_source", fx_effects.SFX_SOURCES,
                      norm=fx_effects.normalize_sfx_source,
                      on_change=self._on_sfx_source,
                      tip="内置音效＝随程序带的那几个（开箱就能响）；"
                          "单个音效＝一直响你挑的那一个；"
                          "音效文件夹＝每次点击随机挑一条。")
        # 内置那一档再让用户挑具体用哪个（或随机）
        self._choices(c, "内置音效", "click_sfx_builtin",
                      self._sfx_builtin_options(),
                      norm=self._sfx_builtin_norm, columns=4,
                      on_change=self._pick_sfx_builtin,
                      when=self._sfx_is_builtin,
                      tip="挑一个固定用它，或者选「随机」每次换一个。")
        self._asset_row(c, label="音效素材", kind="sfx",
                        status=self._sfx_status, when=self._sfx_has_file,
                        tip="「单个音效」一直响这一个；「音效文件夹」每次"
                            "点击从里面随机挑。支持 wav / mp3 / wma / m4a。")
        # 「语音与文字统一」只在这一档下存在：它要把**多条**音频各自变成一行
        # 弹出文字，内置音效与单个音效都给不出多行。
        self._toggle(c, "语音与文字统一", "click_text_voice_match",
                     when=self._sfx_is_folder, on_change=self._on_voice_match,
                     tip="开启后点一下弹出的字就是听到的那条音频的名字，"
                         "「点击文字」里的文字框会被**锁定为只读**（由这个"
                         "文件夹里的音频名自动填入）。会自动打开「点击文字」"
                         "（两者同类互斥，所以「点击表情」会被关掉）；"
                         "开启期间上面的来源选择会被锁住，关掉统一即可改。")
        self._toggle(c, "键盘点击也触发", "click_sfx_keyboard",
                     tip="开着时打字每按一次键也响一声（只认能出字符的键，"
                         "Ctrl / Shift 这类修饰键不算）。青轴那类打字音效"
                         "配这一项最合适。")
        self._slider(c, "音量", "click_sfx_volume", 0.0, 1.0, 0.05,
                     "{:.0%}",
                     on_release=self._preview_click_sfx,
                     tip="相对系统主音量的大小（0%~100%）。100% = 等于当前系统"
                         "音量；拖到 0 即静音。作用于点击发出的所有声音"
                         "（点击音效，以及「语音与文字统一」挑中的语音）；"
                         "松手会按当前音量试听一条。")
        self._save_bar(c, "点击音效")
        self._preset_block(p, "点击音效")
        return p

    def _page_win(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "窗口水波")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用窗口水波", "win_ripple_enabled",
                     tip="把窗口当成浮在水面上的东西：移动或缩放时它推开水——"
                         "朝哪边走，哪边的前缘就涌起、后缘就塌陷；最小化时水"
                         "从四周合拢填掉它，恢复时再把水压开。只有速度才会起波，"
                         "而且水只往窗外涌。")
        self._slider(c, "扰动强度", "win_ripple_strength", 0.05, 1.0, 0.01,
                     "{:.0%}", tip="窗口运动推水的力气。")
        self._slider(c, "扰动范围", "win_ripple_size", 0.4, 3.0, 0.1,
                     "{:.1f} 倍", tip="涟漪铺到窗外的宽度倍数。")
        self._save_bar(c, "窗口水波")
        self._preset_block(p, "窗口水波")
        return p

    def _page_ripple(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "鼠标涟漪")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用鼠标涟漪", "ripple_enabled",
                     tip="打开后鼠标划过的水面上会荡开一圈圈涟漪。")
        self._toggle(c, "跟随鼠标移动", "ripple_move",
                     tip="光标滑动时间歇落下一圈圈涟漪。")
        self._toggle(c, "点击时扩散", "ripple_click",
                     tip="点一下鼠标，以点击点为中心扩散一圈。")
        self._slider(c, "触发灵敏度", "ripple_step", 6, 90, 1, "{:.0f} 像素",
                     tip="移动多远才落下一圈：数值越大越稀疏。")
        self._slider(c, "涟漪大小", "ripple_size", 40, 420, 5, "{:.0f} 像素",
                     tip="单圈涟漪的最大直径。")
        self._slider(c, "存续时间", "ripple_life", 0.3, 2.5, 0.05,
                     "{:.2f} 秒", tip="一圈涟漪从出现到消失的时间。")
        self._slider(c, "浓度", "ripple_opacity", 0.05, 1.0, 0.01, "{:.0%}",
                     tip="涟漪圈的不透明度。")
        self._swatch(c, "涟漪颜色", "ripple_color", RIPPLE_COLORS,
                     tip="涟漪用哪一族颜色。")
        self._save_bar(c, "涟漪")
        self._preset_block(p, "涟漪")
        return p

    def _page_wave(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "真实水波")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._toggle(c, "启用真实水波", "wave_enabled",
                     tip="把桌面当成一片水：落点会荡开、有折射和高光。它和"
                         "「窗口水波」共用同一片水面。")
        self._toggle(c, "跟随鼠标移动", "wave_move",
                     tip="光标划过时在水面上留下尾迹。")
        self._toggle(c, "点击时落一大滴", "wave_click",
                     tip="点一下鼠标砸下一大滴水。")
        self._slider(c, "波速", "wave_speed", 60, 300, 10, "{:.0f} 像素/秒",
                     tip="波纹向外传播的速度。")
        self._slider(c, "余波时长", "wave_persist", 0.0, 1.0, 0.05, "{:.0%}",
                     tip="波纹能回荡多久：0＝一荡就散，1＝久久不消。")
        self._slider(c, "落点大小", "wave_size", 8, 60, 2, "{:.0f} 像素",
                     tip="每一滴在水面上砸出的初始大小。")
        self._slider(c, "落点强度", "wave_strength", 0.02, 0.30, 0.01,
                     "{:.2f}", tip="每一滴把水面压下去多深。")
        self._slider(c, "触发间距", "wave_step", 8, 80, 2, "{:.0f} 像素",
                     tip="连续移动时隔多远落一滴。")
        self._slider(c, "自动落点间隔", "wave_ambient", 0.0, 8.0, 0.2,
                     "{:.1f} 秒",
                     tip="没人操作时也自己滴水的时间间隔；0 表示不自动落。")
        self._slider(c, "光照方向", "wave_light", 0, 350, 10, "{:.0f} 度",
                     tip="高光从哪个角度来，决定波峰哪一侧发亮。")
        self._slider(c, "迎光提亮", "wave_contrast", 0.0, 1.2, 0.05, "{:.0%}",
                     tip="迎着光的波峰提亮多少。")
        self._slider(c, "背光压暗", "wave_shading", 0.0, 1.0, 0.05, "{:.0%}",
                     tip="背光的波谷压暗多少。")
        self._slider(c, "浪尖高光", "wave_highlight", 0.0, 1.2, 0.05,
                     "{:.0%}", tip="浪尖上那一道细亮边。")
        # 上限 20%：用户要求"浓度限制在 20 以内"。它是一层叠在桌面上的半透明条纹，
        # 浓度 = "桌面被盖住多少"，超过 20% 就开始影响看桌面了。
        self._slider(c, "浓度", "wave_opacity", 0.02, 0.20, 0.01, "{:.0%}",
                     tip="水面条纹整体的不透明度，上限 20% —— 再高就开始"
                         "盖住桌面了。")
        self._swatch(c, "高光颜色", "wave_color", WAVE_COLORS,
                     tip="浪尖高光用哪一族颜色。")
        self._save_bar(c, "真实水波")
        self._preset_block(p, "真实水波")
        return p

    def _page_other(self, parent):
        p = tk.Frame(parent, bg=BG)
        self._page_head(p, "其他设置")
        c = tk.Frame(p, bg=CARD)
        c.pack(fill="x", padx=16, pady=(0, 8))
        self._slider(c, "帧率上限", "fps", 24, 144, 1, "{:.0f} 帧/秒",
                     tip="特效每秒重绘多少帧。调低省电，调高更顺滑。")
        # 开机自启用与其它开关同款的胶囊控件，避免老式 Checkbutton 破坏统一
        row = tk.Frame(c, bg=CARD)
        row.pack(fill="x", padx=16, pady=8)
        self._reg_row(c, row, fill="x", padx=16, pady=8)
        lb = tk.Label(row, text="开机时自动启动", bg=CARD, fg=TEXT,
                      font=FONT_LABEL)
        lb.pack(side="left")
        self._bind_tip(lb, "登录 Windows 后自动把特效跑起来。")
        self._autostart_var = tk.BooleanVar(value=self.app.get_autostart())
        _Toggle(row, self._autostart_var,
                command=lambda v: self._toggle_autostart()).pack(side="right")

        # ---- 特效层级：全屏层 / 鼠标层各一套（两层是独立的覆盖窗口）----
        c2 = tk.Frame(p, bg=CARD)
        c2.pack(fill="x", padx=16, pady=(0, 8))
        self._card_title(c2, "特效层级")
        self._layer_row(c2, "全屏特效", "layer_fullscreen",
                        tip="最上层＝始终浮在窗口之上（默认）；仅桌面图层＝被"
                            "窗口盖住、只在桌面可见。")
        self._layer_row(c2, "鼠标特效（拖动 / 点击）", "layer_mouse",
                        tip="这一层普遍依赖浮在最上层（聚光灯压暗全屏、水波"
                            "抓屏折射）：选「仅桌面图层」后会被窗口遮住，那是"
                            "该档位的语义，不是故障。")

        # ---- 特效多开 ----
        c3 = tk.Frame(p, bg=CARD)
        c3.pack(fill="x", padx=16, pady=(0, 8))
        self._card_title(c3, "特效多开")
        self._toggle(c3, "全屏特效允许多开", "multi_fullscreen",
                     tip="关闭时同一层只能开一个特效，开启新的会自动关掉旧的。")
        self._toggle(c3, "鼠标特效允许多开", "multi_mouse",
                     tip="这一项同时管「拖动」与「点击」两组（它们本来就在同一"
                         "条鼠标层上）：打开后聚光灯可以和水波同屏，点击表情也"
                         "可以和点击文字一起弹。")

        # ---- 任务栏 ----
        # 覆盖窗是置顶窗、任务栏不是，所以特效默认会被画在任务栏上 ——
        # 真实水波那种整片明暗条纹糊在任务栏上就是用户报的"残影"。
        c4 = tk.Frame(p, bg=CARD)
        c4.pack(fill="x", padx=16, pady=(0, 8))
        self._card_title(c4, "任务栏")
        self._toggle(c4, "不在任务栏上绘制", "avoid_taskbar",
                     tip="任务栏永远浮在特效之前（含副屏任务栏）；关掉后特效"
                         "会盖在任务栏上。")

        # ---- 窗口尺寸 ----
        # 尺寸是界面偏好，存 ui_state.json（不是特效配置）。
        c5 = tk.Frame(p, bg=CARD)
        c5.pack(fill="x", padx=16, pady=(0, 8))
        self._card_title(c5, "窗口尺寸")
        self._size_slider(c5, "窗口宽度", "win_w", self.W_MIN, self._cap_w(),
                          tip="拖动窗口的任意一条边或角也能改；调整后会记住。")
        self._size_slider(c5, "窗口高度", "win_h", self.H_MIN, self._cap_h(),
                          tip="「按内容自适应高度」会把高度交回给当前页面："
                              "内容放得下就不出现滚动条。")
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

    def _size_slider(self, parent, label, key, lo, hi, tip=None):
        """窗口宽/高的数值调节行。

        写的是 ui_state（界面偏好）而不是 app.cfg —— 特效配置会被预设批量
        改写，窗口尺寸混进去会被预设顺手改掉。所以这一行不走 _slider。
        """
        f = tk.Frame(parent, bg=CARD)
        f.pack(fill="x", padx=16, pady=8)
        self._reg_row(parent, f, fill="x", padx=16, pady=8)
        f.grid_columnconfigure(1, weight=1)
        lb = tk.Label(f, text=label, bg=CARD, fg=TEXT, font=FONT_LABEL)
        lb.grid(row=0, column=0, sticky="w")
        self._bind_tip(lb, tip)
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
        self._reg_row(card, row, fill="x", padx=16, pady=4)
        tk.Label(row, text=keys, bg=CARD, fg=ACCENT, font=FONT_NUM,
                 anchor="w").pack(side="left")
        tk.Label(row, text=desc, bg=CARD, fg=SUB, font=FONT_SMALL,
                 anchor="w").pack(side="right")

    def _card_title(self, card, text):
        """卡片内的小标题，与「预设方案」标题同一套样式。"""
        tk.Label(card, text=text, bg=CARD, fg=SUB,
                 font=(FONT_LABEL[0], FONT_LABEL[1], "bold")).pack(
            anchor="w", padx=12, pady=(10, 4))

    def _layer_row(self, parent, label, key, tip=None):
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
        self._reg_row(parent, row, fill="x", padx=16, pady=8)
        lb = tk.Label(row, text=label, bg=CARD, fg=TEXT, font=FONT_LABEL)
        lb.pack(side="left")
        self._bind_tip(lb, tip)
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
    def _toggle(self, parent, label, key, pady=8, tip=None, when=None,
                on_change=None):
        """通用开关行：名称 …… 开关（开/关）。

        `pady` 可收紧：控件多的页面靠它挤出十几像素（页面高度是有限预算，
        见 MEMORY 里的「面板高度」一节）。

        `on_change(开)` 在写入配置之后、`_sync_from_cfg` 之前调 —— 联动动作
        （"统一打开时顺便把来源切到文件夹、并自动启用文字特效"）必须在那之前
        做完，才能被同一轮 `_sync_from_cfg` 刷到界面上。
        """
        app = self.app
        var = tk.BooleanVar(value=bool(app.cfg[key]))
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", padx=16, pady=pady)
        self._reg_row(parent, row, when=when, fill="x", padx=16, pady=pady)
        lb = tk.Label(row, text=label, bg=CARD, fg=TEXT, font=FONT_LABEL)
        lb.pack(side="left")
        self._bind_tip(lb, tip)

        def _on(v):
            app.set(key, bool(v))
            if on_change is not None:
                on_change(bool(v))
            # 同类互斥可能连带关掉别的特效开关，回读一次让面板跟着变
            self._sync_from_cfg()

        _Toggle(row, var, command=_on).pack(side="right")
        # 不再挂 var.trace：_sync_from_cfg 回写变量时不该反过来再 set 一次
        self._ctrls[key] = {"kind": "toggle", "var": var, "on": _on,
                            "label": lb}

    def _slider(self, parent, label, key, lo, hi, res, fmt, tip=None,
                when=None, on_release=None):
        app = self.app
        f = tk.Frame(parent, bg=CARD)
        f.pack(fill="x", padx=16, pady=8)
        self._reg_row(parent, f, when=when, fill="x", padx=16, pady=8)
        # 单行布局：名称 | 滑块（弹性） | 数值
        f.grid_columnconfigure(1, weight=1)
        lb = tk.Label(f, text=label, bg=CARD, fg=TEXT, font=FONT_LABEL)
        lb.grid(row=0, column=0, sticky="w")
        self._bind_tip(lb, tip)
        var = tk.DoubleVar(value=float(app.cfg[key]))

        def on(*_):
            v = var.get()
            app.set(key, v)
            val.config(text=fmt.format(v))

        val = tk.Label(f, text="", bg=CARD, fg=ACCENT, font=FONT_NUM)
        _Slider(f, lo, hi, res, var, command=on, on_release=on_release,
                width=120).grid(row=0, column=1, sticky="ew", padx=(14, 10))
        val.grid(row=0, column=2, sticky="e")
        on()
        self._ctrls[key] = {"kind": "slider", "var": var, "label": val, "fmt": fmt}

    def _swatch(self, parent, label, key, colors, tip=None, when=None):
        app = self.app
        f = tk.Frame(parent, bg=CARD)
        f.pack(fill="x", padx=16, pady=8)
        self._reg_row(parent, f, when=when, fill="x", padx=16, pady=8)
        lb = tk.Label(f, text=label, bg=CARD, fg=TEXT, font=FONT_LABEL)
        lb.pack(side="left")
        self._bind_tip(lb, tip)
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

    def _choices(self, parent, label, key, options, columns=None, on_change=None,
                 norm=None, pady=8, tip=None, when=None):
        """分段选择行：名称 …… 一排文字按钮（选中项用强调色描边 2px）。

        视觉语言与 `_swatch` 保持一致，只是把色块换成文字 —— "小猫 / 小狗"
        没有天然的颜色表示，硬造两个色块反而更难认。

        `columns` 给定时改为**等宽网格**（名称独占一行，按钮按列数折行）。
        选项多的时候必须折行：8 个图案排在单行会把页面撑到 538px，而窗口最小
        宽度只给内容区留 400px —— 撑破之后 Tk 既不换行也不缩，只会把右边的
        选项直接裁掉。

        `on_change(选项)` 在写入配置**之后**再调，供"选 A 顺带把 B 也设好"
        这种联动用（点击表情的图案 → 惯用色；图案来源 → 露哪几行）。

        `norm` 给定时用 `norm(值)` 比较选中态。旧配置里存着已被改名的取值
        （如图源从「自定义图片」改成「图片文件夹」）时，不归一化就会**一个
        选项都不高亮** —— 看着像界面坏了。
        """
        app = self.app
        f = tk.Frame(parent, bg=CARD)
        f.pack(fill="x", padx=16, pady=pady)
        self._reg_row(parent, f, when=when, fill="x", padx=16, pady=pady)
        btns = []

        def refresh():
            cur = norm(app.cfg[key]) if norm is not None else app.cfg[key]
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
            lb = tk.Label(f, text=label, bg=CARD, fg=TEXT, font=FONT_LABEL,
                          anchor="w")
            lb.grid(row=0, column=0, columnspan=columns,
                    sticky="w", pady=(0, 4))
            self._bind_tip(lb, tip)
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
            lb = tk.Label(f, text=label, bg=CARD, fg=TEXT, font=FONT_LABEL)
            lb.pack(side="left")
            self._bind_tip(lb, tip)
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
        # 按钮一并登记：外层要能在「语音与文字统一」打开时把来源那一组**锁住**
        # （统一模式要求来源固定在「音效文件夹」，不能让用户中途换掉）。
        self._ctrls[key] = {"kind": "choices", "refresh": refresh, "btns": btns}

    def _textbox(self, parent, label, key, height=3, gate=None, fill=None,
                 tip=None, when=None):
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
        self._reg_row(parent, f, when=when, fill="x", padx=16, pady=8)
        lb = tk.Label(f, text=label, bg=CARD, fg=TEXT, font=FONT_LABEL)
        lb.pack(anchor="w", pady=(0, 4))
        self._bind_tip(lb, tip)
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

    def _folder(self, parent, label, key, status=None, on_change=None, pady=8,
                tip=None, when=None):
        """文件夹选择行：名称 | 路径（从尾部截断） | 选择 / 清除。

        路径一律**截尾显示** —— 用户认的是最后一级目录名，前面被省略号吃掉
        不影响判断；不截的话一个长路径就能把最小窗口撑破。

        `status()` 由页面给，返回一句附加状态（"已载入 12 张"）。它由 `_poll`
        每 200ms 刷一次：自定义素材是**后台**解码的，界面不跟着动，用户就会
        以为"选了文件夹没反应"。
        """
        app = self.app
        f = tk.Frame(parent, bg=CARD)
        f.pack(fill="x", padx=16, pady=pady)
        self._reg_row(parent, f, when=when, fill="x", padx=16, pady=pady)
        top = tk.Frame(f, bg=CARD)
        top.pack(fill="x")
        lb = tk.Label(top, text=label, bg=CARD, fg=TEXT, font=FONT_LABEL)
        lb.pack(side="left")
        self._bind_tip(lb, tip)

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
            # 选/清文件夹之后要立刻反映，不能等列表缓存的 2 秒 TTL
            fx_effects.invalidate_media_cache()
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
        # 先收一条不变量：「统一」只在音效文件夹那一档下成立（外部改配置 /
        # 预设 / 老配置都可能在别的来源下开着它）。放在最前面，控件回读之前
        # 就收干净，后面读到的都是最终状态。
        self._fix_voice_match()
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
        # 预设/外部改动可能换掉「图案来源」：条件行（图案选择、素材行、
        # 单次选择）要跟着显隐，不然面板会和实际配置对不上。
        self._refresh_rows()
        self._sync_sfx_lock()

    def _pause(self):
        self._paused = bool(self.app.toggle_pause())
        self._status_paint(False)

    def _status_paint(self, hover):
        """标题栏状态区：帧率 + 耗时，兼作暂停开关。hover=True 为悬停预览色。

        文本由 `_poll` 每 200ms 刷一次，这里只管配色 —— 分开是因为文本刷新
        频率高、配色变化频率低，混在一起会让 hover 效果被下一帧刷掉。
        """
        paused = getattr(self, "_paused", False)
        try:
            if hover:
                self.status.config(bg=BAR_HOVER,
                                   fg=ACCENT_HOVER if paused else WHITE)
            else:
                self.status.config(bg=SIDEBAR_BG, fg=ACCENT if paused else SUB)
        except Exception:
            pass

    def _quit(self):
        self._quitting = True
        self.app.quit()
        try:
            self.root.destroy()
        except Exception:
            pass

    # ------------------------------------------------ 交互控件集合（给渲染线程）
    # 判定"这个控件是不是交互控件"用三条规则的并集：
    #   ① Win32 类名白名单（Tk 原生控件在 Windows 上的真实类名）；
    #   ② 类型名 _Slider —— 它是 tk.Canvas 子类，winfo_class 是 "Canvas"，
    #      类名白名单收不到，只能按类型名特判；
    #   ③ 控件自身绑了 <ButtonPress-1> —— 覆盖侧栏导航 / 分组标题 / 标题栏
    #      拖动 / 缩放手柄这类 Frame+Label 组合的 bind 型交互。
    # 实测确认：bind_all 只挂 <MouseWheel>，且 bind_all 的绑定**不会**出现在
    # `w.bind()` 的返回里，所以规则 ③ 不会把整棵树误收进来。
    _IA_CLASSES = frozenset({
        "Button", "Checkbutton", "Radiobutton", "Menubutton", "Entry",
        "Spinbox", "Listbox", "Treeview", "Scale", "Menu",
        "TButton", "TCheckbutton", "TRadiobutton", "TCombobox", "TSpinbox",
        "TMenubutton", "TEntry",
    })

    @staticmethod
    def _ia_pressed(w):
        """控件自身是否绑了鼠标按下（bind_all 的那条不算）。

        两个 Tk 细节都实测过，别凭印象写：
        * `w.bind()` 无参返回的是**序列名**元组（如 `('<Button-1>',)`），
          不是 (sequence, function) 对 —— 不能按 `x[0]` 取序列名；
        * 绑 `<ButtonPress-1>` 会被规范成 `<Button-1>`（Press 可省略）。
        bind_all 的绑定不会出现在这里（项目只拿它挂 MouseWheel，双重安全）。
        """
        try:
            seqs = w.bind() or ()
            return any(s in ("<Button-1>", "<ButtonPress-1>") for s in seqs)
        except Exception:
            return False

    def _ia_scan(self, w, out):
        """递归收集交互控件的 HWND。

        收录后**继续往下递归**：_nav_item 这类组合控件是 Frame 上绑点击、
        里面还套着也绑了点击的 Label —— WindowFromPoint 命中的是最具体的
        子窗口，父级被收录但子级漏收的话，点子控件就会漏过滤。
        原生控件（Button/Scale）没有子窗口，递归无害。
        """
        try:
            if not w.winfo_exists():
                return
            if (w.winfo_class() in self._IA_CLASSES
                    or type(w).__name__ == "_Slider"
                    or self._ia_pressed(w)):
                hid = int(w.winfo_id())
                if hid:                  # 未 realize 的控件 winfo_id 为 0，跳过
                    out.add(hid)
            for c in w.winfo_children():
                self._ia_scan(c, out)
        except Exception:
            pass

    def _sync_interactive_hwnds(self):
        """把面板上所有交互控件的 HWND 同步给 App（渲染线程每帧读）。

        面板自身（TkTopLevel 根）**不**进集合 —— 扫描从 root 的子节点开始，
        点面板最底层的背景算"面板其它区域"，点击特效照常放行（用户诉求：
        控制面板上也生效）。异常一律吞掉：这是给渲染线程的辅助信息，
        同步失败的最坏结果只是过滤失灵，不能让面板线程炸掉。
        """
        try:
            out = set()
            for c in self.root.winfo_children():
                self._ia_scan(c, out)
            self._ia_hwnds = frozenset(out)
            self.app.ui_btn_hwnds = self._ia_hwnds
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
                kind, payload = app.events.get_nowait()
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

        # 交互控件集合限频同步：预设按钮 / 行内按钮会随配置动态增删，全树扫描
        # 一次几百微秒，1s 一次完全无感；首次立即扫一次，别让面板刚建起来的
        # 头一秒处于"零集合"状态（那期间点按钮会放特效）。
        now = time.monotonic()
        if now - self._ia_last_scan > 1.0:
            self._ia_last_scan = now
            self._sync_interactive_hwnds()

        self.root.after(200, self._poll)

    def run(self):
        try:
            self.root.mainloop()
        finally:
            # 面板销毁：清掉同步出去的交互控件集合，别让渲染线程拿一堆死
            # 窗口的 HWND 当过滤依据（HWND 会被系统回收复用，风险实打实）
            try:
                self.app.ui_btn_hwnds = frozenset()
            except Exception:
                pass
            if not getattr(self, "_quitting", False):
                self.app.quit()
