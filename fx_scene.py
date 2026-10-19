# -*- coding: utf-8 -*-
"""场景合成器：把若干独立特效层组合到同一张全屏缓冲上。

这是"图层组合"架构的核心——预设不再是一组雨的参数，而是一组可独立
开关的特效层。Scene 持有全部特效实例，每帧按 cfg 里的开关决定哪些层参与，
因此任意组合（雪 + 闪电、雨 + 雪、仅涟漪……）天然支持，无需为每种组合
手写独立代码。

新增一种特效 = 在 Scene 里加一个属性 + 在 step() 里加一段（参考雪）+
在 DEFAULTS 里加对应的开关/参数键。逐帧物理逻辑与原 _render_loop 完全
一致，只是从"硬编码顺序"搬进了可组合的 Scene。
"""
import time

import numpy as np

import _win32
from fx_effects import (RainField, RippleField, MeteorField, MouseProbe,
                        PaintField, PetalField, SnowField, SpotlightField,
                        PetField, ClickEmojiField, ClickTextField, ClickSfx,
                        WaterRippleField, WindowProbe, VoicePlayer, hex_to_bgr)


class Scene:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.rain = RainField(w, h, seed=7)
        self.snow = SnowField(w, h, seed=9)
        self.petals = PetalField(w, h, seed=23)         # 落樱
        self.meteors = MeteorField(w, h, seed=31)       # 流星雨
        self.paint = PaintField(w, h, seed=37)          # 颜料盘
        self.ripples = RippleField()
        # 「真实水波」是**另一套独立实现**（二维波动方程 + 迎光/背光条纹），
        # 和上面的彩色圆环 `RippleField` 并存 —— 用户要开哪个自己选，两边可以同时开。
        self.wave = WaterRippleField(w, h, seed=41)
        self.mouse = MouseProbe()
        self.spot = SpotlightField(w, h, seed=17)       # 聚光灯
        self.pets = PetField(w, h, seed=53)             # 宠物跟随
        self.click_emoji = ClickEmojiField(w, h, seed=73)   # 点击表情
        self.click_text = ClickTextField(w, h, seed=79)     # 点击文字
        # 点击语音：不画任何东西，只是"点一下随机播一段"。整段播放跑在它
        # 自己的后台线程里，渲染循环只负责在点击那一帧登记一次。
        self.voice = VoicePlayer()
        # 「点击音效」是独立的特效分类，但**共用上面这一个播放器** —— 一次
        # 点击最多响一声：上一个来源占了播放器，后面的直接丢弃（不排队、
        # 更不叠着放）。两个 Player 会各放各的，那就成了"点一下响两声"。
        self.click_sfx = ClickSfx(self.voice)
        self.win = WindowProbe()                        # 窗口枚举（窗口交互地基）

        # 覆盖层在虚拟屏幕里的左上角偏移（鼠标坐标要折算进来）
        self.ov_x = 0
        self.ov_y = 0

        # 配置面板的根窗口句柄（0 = 未设置）+ 面板上交互控件的 HWND 集合。
        # 两者配合做**三态判定**（见 _panel_hit）：
        #   * 不在面板上          -> 点击照常触发（桌面 / 其它窗口 / WorkBuddy）
        #   * 命中面板交互控件    -> 抑制：点按钮/滑块/侧栏时放烟花、出声纯属
        #     噪音，更糟的是"按下触发的旧音效"会和"选择后的试听"叠成双响
        #   * 命中面板其它区域    -> 放行：用户明确要求"控制面板上也生效"
        # 由渲染循环每帧从 App.ui_hwnd / App.ui_btn_hwnds 同步过来。
        self.ui_hwnd = 0
        self.ui_btn_hwnds = frozenset()
        # 面板空白处点击特效的"播放截止时刻"（monotonic 秒）。渲染循环据此
        # 在播放期间把鼠标层临时提到面板之上 —— 面板（TkTopLevel）本身在
        # 覆盖层之上（z 序最顶），不提升的话文字/表情会被面板挡住，只剩音效。
        self.panel_fx_until = 0.0
        self._panel_click = False        # 本帧是否发生了"放行的面板点击"

        # 每帧由渲染循环喂入的当前配置快照
        self.cfg = {}

        # 跨帧状态（原本散落在 _render_loop 局部变量里，搬进 Scene 以便复用）
        self.acc = 0.0
        self._last_color = None
        self._last_snow_color = None
        self._last_petal_color = None
        self._last_petal_edge = None
        self._last_meteor_color = None
        self._last_meteor_tail = None
        self._last_paint_color = None
        self._last_ripple_color = None
        self._ripple_color_cache = hex_to_bgr("#7FE7FF")

        # 真实水波的跨帧状态
        self._last_wave_color = None
        self._wave_acc = 0.0            # 鼠标行程累加（按间距落点）
        self._wave_amb = 0.0            # 环境自动滴落的计时

        # 窗口水波跨帧状态。它现在**不再自带画法**：窗口只是水面的一个扰动源，
        # 和鼠标共用同一片水面（self.wave），于是颜色 / 波速 / 余波 / 光照都由
        # 「真实水波」页那一套参数统一决定 —— 一个桌面本来就只有一片水。
        self._win_events = []           # 测试可注入合成事件，绕过真实窗口枚举

    def set_origin(self, x, y):
        self.ov_x, self.ov_y = x, y
        self.win.set_origin(x, y)

    def layer_activity(self):
        """本帧两层**可能**有内容吗 —— 返回 (全屏层, 鼠标层)。

        只给渲染循环用来决定"这一层还需不需要提交"：两个覆盖窗口意味着每层每帧
        一次全屏 UpdateLayeredWindow，而全空层再提交一次全 0 与上一帧画面完全
        一样，纯属白付。（调用方在信号为 False 时还会用真实像素复核，所以这里
        允许保守 —— 多提交只是不省，漏提交才会把上一帧冻在桌面上。）

        保守体现在"开着就算有内容"：聚光灯恒画满屏、颜料盘/真实水波开着就可能
        随时出图，都懒得去猜。真正能省下来的是**涟漪池空着**的时候，所以要按池子
        的真实活跃状态判，而不是按 ripple_enabled —— 后者开着但用户没动鼠标时
        池子是空的。宠物同理按"队伍是否真的摆在场上了"判（`pets.active()`），
        而不是按 pet_enabled：开关开着但队伍还没落位时不该让它一直上屏。
        两个点击特效也按"场上还有没有粒子 / 弹字"判 —— 它们绝大多数时间都是空的
        （要点一下才有），这一条比别的都划算。
        """
        cfg = self.cfg
        fs = bool(cfg.get("rain_enabled", True) or cfg.get("snow_enabled", False)
                  or cfg.get("petal_enabled", False)
                  or cfg.get("meteor_enabled", False))
        # 2026-09-15 修：早先这里是"开着就算有内容"（`wave_enabled` /
        # `paint_enabled` / `win_ripple_enabled` 一律当 True）。后果是**真实水波
        # 一开，鼠标层就永远上屏** —— 哪怕场上一个波都没有，也每帧白付约 5.9ms。
        # 实测用户配置（真实水波 + 雪）只有 49 帧/秒、12.5% 掉帧，其中鼠标层
        # 那 5.7ms 全是空转。改成读特效**自身的状态**：
        #   - 水波 `_alive`：draw() 开头就用它早退，精确。真实水波与窗口水波
        #     **共用这一片水**（窗口只往里注入位移、不自己画），所以一个信号
        #     同时覆盖两者，两个开关任开其一都按它判；
        #   - 颜料盘 `_dirty`：颜料散尽时 draw() 自己会清零，精确；
        #   - 聚光灯恒画满屏压暗，只有它继续按开关判（开 = 真有内容）。
        # 这样收紧是**安全**的：调用方的像素复核只会把 False 翻成 True，
        # 判空判错了也不会把上一帧冻在桌面上。
        ms = bool(cfg.get("spotlight_enabled", False)
                  or self.paint._dirty
                  or ((cfg.get("wave_enabled", False)
                       or cfg.get("win_ripple_enabled", False)) and self.wave._alive)
                  or self.ripples.active.any()
                  or self.pets.active()
                  or self.click_emoji.active()
                  or self.click_text.active())
        return fs, ms

    def resize(self, w, h):
        self.w, self.h = w, h
        self.rain.resize(w, h)
        self.snow.resize(w, h)
        self.petals.resize(w, h)
        self.meteors.resize(w, h)
        self.paint.resize(w, h)
        self.wave.resize(w, h)
        self.spot.resize(w, h)
        self.pets.resize(w, h)
        self.click_emoji.resize(w, h)
        self.click_text.resize(w, h)

    def _ripple_color(self):
        c = self.cfg.get("ripple_color", "#7FE7FF")
        if self._last_ripple_color != c:
            self._ripple_color_cache = hex_to_bgr(c)
            self._last_ripple_color = c
        return self._ripple_color_cache

    # ------------------------------------------------------------ 每帧
    def _panel_hit(self):
        """光标（虚拟屏幕物理坐标）下的窗口相对配置面板的位置。

        返回 (在面板上, 命中交互控件)。面板未设置（`ui_hwnd == 0`，如测试
        环境 / 面板还没建起来）时两者皆 False。WindowFromPoint 命中子窗口返
        回的是子窗口句柄，所以还要 GetAncestor 归一到根窗口再比。
        全程纯 Win32 查询，渲染线程调用安全（不碰 Tk）。
        """
        hwnd = self.ui_hwnd
        if not hwnd:
            return False, False
        try:
            h = _win32.window_at(int(self.mouse.x), int(self.mouse.y))
            if not h or _win32.root_window(h) != int(hwnd):
                return False, False
            return True, int(h) in self.ui_btn_hwnds
        except Exception:
            return False, False

    def _over_ui(self):
        """本次点击是否是"面板 UI 操作"（该抑制）。_panel_hit 的布尔形式。"""
        return self._panel_hit()[1]

    def step(self, dt, u32, w, h, u32_mouse=None):
        """推进一帧。u32 = 全屏特效层缓冲。

        u32_mouse 不为 None 时按**层分流**绘制：全屏特效（雨/雪/落樱/流星雨）
        画进 u32，「鼠标特效」层（聚光灯/颜料盘/涟漪/真实水波/窗口水波）画进
        u32_mouse。两层各是一个独立的覆盖窗口，用户可分别设定"最上层 / 仅
        桌面图层"，所以不能合到一张缓冲上。

        u32_mouse 为 None（旧调用方式）时全部画进同一张缓冲，绘制顺序与
        分层前逐条一致 —— 测试脚本仍按老签名调用，行为保持不变。

        注意：**模拟状态只推进一步**（与调用几个缓冲无关）。若按层各建一个
        Scene 实例，物理就会被推进两次，波形/粒子速度会翻倍。
        """
        cfg = self.cfg
        # 画到哪张缓冲：全屏层恒为 u32；鼠标层缺省与全屏层共用一张
        buf_fs = u32
        buf_ms = u32 if u32_mouse is None else u32_mouse

        # 鼠标坐标最先更新：聚光灯与鼠标涟漪都要用到当前光标
        # 键盘只在"点击音效开着、且勾了键盘触发"时才去查 —— 一个键一次内核
        # 调用，八十来个键就是八十来次，没人要这功能时不该白付。
        keys = bool(cfg.get("click_sfx_enabled", False)
                    and cfg.get("click_sfx_keyboard", False))
        dist = self.mouse.update(dt, self.ov_x, self.ov_y, keys=keys)

        # 点自己配置面板上的**交互控件**（按钮 / 滑块 / 侧栏导航…）不算"桌面
        # 点击"：鼠标钩子是全局的，不拦这一下的话，在面板里点个按钮会同时触发
        # 「当前音效（旧配置）+ 试听（新选择）」两声，且每拨一个开关 / 拖一下
        # 滑块都附带放一次点击特效（音效 / 涟漪 / 水波 / 点击图案）。
        # 但**面板空白处放行** —— 用户明确要求点击特效在控制面板上也生效；
        # 那一下会被标 _panel_click，渲染循环随即把鼠标层临时提到面板之上，
        # 让文字/表情不被面板本身挡住（见 panel_fx_until）。
        self._panel_click = False
        if self.mouse.clicked:
            on_panel, on_ia = self._panel_hit()
            if on_panel and on_ia:
                self.mouse.clicked = 0
            elif on_panel:
                self._panel_click = True

        # ---------------- 窗口水波（窗口移动 / 缩放 / 最小化 / 恢复 -> 扰动水面）
        # 窗口是水面上的障碍物，把它当活塞：四条边各按**自身速度**推水（见
        # WaterRippleField.push_rect）。它**不自己画**，只往水里注入位移 ——
        # 绘制由下面共用的那一段水面统一负责，所以窗口水波与鼠标水波永远是
        # 同一片水、同一套光照，而不是两个各画各的独立效果。
        win_on = bool(cfg.get("win_ripple_enabled", False))
        if win_on:
            if self._win_events:
                events = self._win_events
                self._win_events = []
            else:
                events = self.win.update(dt)
            for ev in events:
                self._win_perturb(ev, cfg)
        else:
            self.win.reset()

        # ---------------- 聚光灯（背景压暗，最先画，让其它特效叠在上面）
        if cfg.get("spotlight_enabled", False):
            self.spot.set_cursor(float(self.mouse.mx), float(self.mouse.my))
            self.spot.set_radius(float(cfg.get("spotlight_radius", 220)))
            self.spot.set_dim(float(cfg.get("spotlight_dim", 0.6)))
            self.spot.set_color(cfg["spotlight_color"])
            self.spot.draw(buf_ms, w, h, cfg)

        # ---------------- 颜料盘（鼠标搅动的流体颜料）
        # 排在聚光灯**之后**：它的 draw 会写包围盒内的整片像素，先画就把聚光灯抹了
        # （聚光灯在的时候它会自动改成"只写有颜料的像素"，见 PaintField.draw）。
        # 指针位置**每帧都喂，特效关着也喂** —— 否则重新启用时"上一次被消费的
        # 指针"还停在很久以前的位置，第一帧会画出一条横贯屏幕的长笔触。
        self.paint.set_pointer(float(self.mouse.mx), float(self.mouse.my),
                               int(self.mouse.clicked))
        if cfg.get("paint_enabled", False):
            if self._last_paint_color != cfg["paint_color"]:
                self.paint.set_color(cfg["paint_color"])
                self._last_paint_color = cfg["paint_color"]
            self.paint.update(dt, cfg)
            self.paint.draw(buf_ms, w, h, cfg)
        else:
            self.paint.reset()

        # ---------------- 雨（独立开关）
        if cfg.get("rain_enabled", True):
            amount = int(cfg["rain_amount"])
            self.rain.set_amount(amount, w, h)
            if self._last_color != cfg["rain_color"]:
                self.rain.set_color(cfg["rain_color"])
                self._last_color = cfg["rain_color"]
            splash_x = self.rain.update(dt, cfg)
            self.rain.draw(buf_fs, w, h, cfg)

            if cfg.get("splash", True) and splash_x.size:
                col = self._ripple_color()
                n = min(6, splash_x.size)
                for k in range(n):
                    self.ripples.spawn(
                        float(splash_x[k]), float(h - 2),
                        rmax=float(np.random.uniform(10.0, 30.0)),
                        life=float(np.random.uniform(0.32, 0.55)),
                        strength=0.42 * float(cfg["ripple_opacity"]),
                        color_bgr=col, r0=1.0, squash=0.30)

        # ---------------- 雪（独立开关，与雨正交）
        if cfg.get("snow_enabled", False):
            amount = int(cfg.get("snow_amount", 240))
            self.snow.set_amount(amount, w, h)
            if self._last_snow_color != cfg["snow_color"]:
                self.snow.set_color(cfg["snow_color"])
                self._last_snow_color = cfg["snow_color"]
            self.snow.update(dt, cfg)
            self.snow.draw(buf_fs, w, h, cfg)

        # ---------------- 落樱（独立开关，与雨/雪正交）----
        if cfg.get("petal_enabled", False):
            amount = int(cfg.get("petal_amount", 160))
            self.petals.set_amount(amount, w, h)
            if self._last_petal_color != cfg["petal_color"]:
                self.petals.set_color(cfg["petal_color"])
                self._last_petal_color = cfg["petal_color"]
            if self._last_petal_edge != cfg["petal_edge_color"]:
                self.petals.set_edge_color(cfg["petal_edge_color"])
                self._last_petal_edge = cfg["petal_edge_color"]
            self.petals.update(dt, cfg)
            self.petals.draw(buf_fs, w, h, cfg)

        # ---------------- 流星雨（独立开关，与雨/雪/落樱正交）----
        if cfg.get("meteor_enabled", False):
            amount = int(cfg.get("meteor_amount", 14))
            self.meteors.set_amount(amount, w, h)
            if self._last_meteor_color != cfg["meteor_color"]:
                self.meteors.set_color(cfg["meteor_color"])
                self._last_meteor_color = cfg["meteor_color"]
            if self._last_meteor_tail != cfg["meteor_tail_color"]:
                self.meteors.set_tail_color(cfg["meteor_tail_color"])
                self._last_meteor_tail = cfg["meteor_tail_color"]
            self.meteors.update(dt, cfg)
            self.meteors.draw(buf_fs, w, h, cfg)

        # ---------------- 鼠标涟漪（由鼠标轨迹 / 点击触发，dist 已在上面临时算出）
        # ripple_enabled 是「鼠标涟漪」的主开关（同类互斥靠它开关），但它只管
        # **鼠标生成的涟漪** —— 雨滴溅起的水花、窗口水波照旧落进同一个池子。
        if cfg.get("ripple_enabled", True) \
                and cfg.get("ripple_move", True) and dist > 0.0:
            self.acc += dist
            step_r = max(6.0, float(cfg["ripple_step"]))
            cnt = 0
            while self.acc >= step_r and cnt < 3:
                self.acc -= step_r
                cnt += 1
                sp = min(1.0, self.mouse.speed / 2200.0)
                rmax = (float(cfg["ripple_size"]) * (0.55 + 0.55 * np.random.random())
                        * (0.80 + 0.50 * sp))
                strength = (0.55 + 0.45 * sp) * float(cfg["ripple_opacity"])
                self.ripples.spawn(float(self.mouse.mx), float(self.mouse.my),
                                  float(rmax),
                                  float(cfg["ripple_life"]) * (0.9 + 0.25 * np.random.random()),
                                  float(strength), self._ripple_color(), r0=2.0)
            if self.acc > step_r * 4:
                self.acc = 0.0
        else:
            self.acc *= 0.90

        if cfg.get("ripple_enabled", True) \
                and cfg.get("ripple_click", True) and self.mouse.clicked:
            self.ripples.spawn(float(self.mouse.mx), float(self.mouse.my),
                              float(cfg["ripple_size"]) * 1.9,
                              float(cfg["ripple_life"]) * 1.35,
                              0.95 * float(cfg["ripple_opacity"]),
                              self._ripple_color(), r0=3.0)

        # ---------------- 涟漪
        self.ripples.update(dt)
        self.ripples.draw(buf_ms, w, h)

        # ---------------- 水面（真实水波 / 窗口水波 **共用同一片水**）
        # 排在最后 = 最上层：它写的是半透明**暗**像素（波谷），被别层压住就看不出来了。
        # 水面在场 = 真实水波开着（鼠标扰动）**或** 窗口水波开着（窗口扰动）。
        # 它们不是两个各画各的效果：桌面本来就只有一片水，鼠标划过和窗口推过都该
        # 落在同一张水面上，也自然共用同一套光照 / 波速 / 余波 / 颜色参数。
        wave_on = bool(cfg.get("wave_enabled", False)) or win_on
        if wave_on:
            if self._last_wave_color != cfg["wave_color"]:
                self.wave.set_color(cfg["wave_color"])
                self._last_wave_color = cfg["wave_color"]
            if cfg.get("wave_enabled", False):
                self._wave_trigger(cfg, dist, w, h, dt)
            self.wave.update(dt, cfg)
            # 鼠标层这帧是否**只有水波**：缓冲区是空的，就能整片直写上屏，
            # 省掉"逐像素比掩码"（3.3M 像素下 7.0ms vs 1.0ms）。
            # 判据一律取**保守**的一侧：误判成 False 只是慢一点，误判成 True
            # 会把同一层上别人的像素整片抹掉。旧单缓冲签名（u32_mouse=None）
            # 里这张缓冲还装着雨雪等全屏特效，一律不许直写。
            self.wave.draw(buf_ms, w, h, cfg, solo=(
                u32_mouse is not None
                and not cfg.get("spotlight_enabled", False)
                and not self.paint._dirty
                and not self.ripples.active.any()))
        elif self.wave._alive:
            # 关闭时清场：否则重新打开时会冒出上一次留下的波
            self.wave.reset()
            self._wave_acc = 0.0
            self._wave_amb = 0.0

        # ---------------- 宠物跟随（画在最后 = 鼠标层最上）
        # 宠物是这一层的主体，压在涟漪与水波之上才看得清；水波那次 solo 整片
        # 直写发生在本段**之前**，所以不会把宠物抹掉。
        # 指针每帧都喂（特效关着也喂）：重新启用后 PetField 要按当前光标重新
        # 累计"现身门槛"，喂不到就会拿旧位置当起点、凭空攒出一大截净位移。
        # 点击也一起喂，但它只刷新"静止计时"，不会让宠物现身（见 PetField）。
        self.pets.set_pointer(float(self.mouse.mx), float(self.mouse.my),
                              int(self.mouse.clicked))
        if cfg.get("pet_enabled", False):
            self.pets.update(dt, cfg)
            self.pets.draw(buf_ms, w, h, cfg)
        else:
            self.pets.reset()

        # ---------------- 点击特效（点一下炸开一簇 / 弹出一行字）
        # 画在最后 = 鼠标层最上：它们是**瞬时事件**，被谁盖住都可惜 ——
        # 一次点击就那么零点几秒，正好压在宠物和水波之上才看得清。
        # 两者默认互斥（同属「鼠标点击特效」分类），所以通常只有一个在跑，
        # 但各自仍要按自己的开关独立 reset，否则关掉的那个会留着残影。
        self.click_emoji.set_pointer(float(self.mouse.mx), float(self.mouse.my),
                                     int(self.mouse.clicked))
        if cfg.get("click_emoji_enabled", False):
            self.click_emoji.update(dt, cfg)
            self.click_emoji.draw(buf_ms, w, h, cfg)
        else:
            self.click_emoji.reset()

        self.click_text.set_pointer(float(self.mouse.mx), float(self.mouse.my),
                                    int(self.mouse.clicked))
        if cfg.get("click_text_enabled", False):
            self.click_text.update(dt, cfg)
            self.click_text.draw(buf_ms, w, h, cfg)
        else:
            self.click_text.reset()

        # ---------------- 播哪一段音频
        # 优先级：「点击文字」在"语音与文字统一"模式下挑中的那条**最高** ——
        # 它同时决定了弹出的文字，播的必须就是同一条。取路径要**无条件**调
        # `take_voice()`：文字特效关掉时也得把队列清掉，否则下次开起来会播一条
        # 上一轮遗留的音频。它没挑到才轮到「点击音效」那一档（鼠标点击 / 键盘）。
        # 两者**共用同一个播放器**：上一个还响着时后者直接丢弃 —— 一次点击只响
        # 一声是自然结果，不用额外判重。
        path = self.click_text.take_voice()
        if path:
            # 音量与「点击音效」同一支滑块：统一模式挑中的这条语音也按它缩放，
            # 否则"开着统一时点击声不听音量"—— 正是"还有部分音效不受控"的那类。
            self.voice.play_path(path, volume=cfg.get("click_sfx_volume", 1.0))
        elif cfg.get("click_sfx_enabled", False) \
                and (self.mouse.clicked or self.mouse.key_clicked):
            self.click_sfx.trigger(cfg)

        # 面板空白处放行了这一次点击：记下播放截止时刻，渲染循环会在这段
        # 时间里把鼠标层临时提到面板之上，文字/表情才看得见（否则全被面板
        # 盖住，只剩音效能听）。时长取文字/表情寿命的上限再留余量；三类
        # 点击特效全关着时不置位 —— 没有要播的东西，别让面板白闪一下。
        # ⚠️ 时钟必须与渲染循环一致：那里用 time.perf_counter() 比。
        # 曾经这里用 time.monotonic() —— Windows 上两者同源但刻度/值域不同
        # （monotonic=GetTickCount64，perf_counter=QPC），比较恒真，提升状态
        # 永远降不回来。
        if self._panel_click and (
                cfg.get("click_text_enabled", False)
                or cfg.get("click_emoji_enabled", False)
                or cfg.get("click_sfx_enabled", False)):
            life = max(float(cfg.get("click_text_life", 1.4)),
                       float(cfg.get("click_emoji_life", 1.1)))
            self.panel_fx_until = time.perf_counter() + life + 0.4

    # ------------------------------------------------------------ 真实水波
    def _wave_trigger(self, cfg, dist, w, h, dt):
        """给真实水波喂触发源：移动（按行程）/ 点击 / 环境自动滴落。

        移动走的是**按行程落点**而不是"每帧一滴"：帧率高的机器上每帧落点会让
        陡坡那一侧积成一片，而"每走 N 像素落一滴"才是"手划过水面"的物理图像。
        """
        mx, my = float(self.mouse.mx), float(self.mouse.my)

        if cfg.get("wave_move", True) and dist > 0.0:
            self._wave_acc += float(dist)
            step_w = max(6.0, float(cfg["wave_step"]))
            cnt = 0
            while self._wave_acc >= step_w and cnt < 3:
                self._wave_acc -= step_w
                cnt += 1
                self.wave.splash(mx, my, cfg)
            if self._wave_acc > step_w * 4:
                self._wave_acc = 0.0        # 瞬移保护：别攒出一长串待落的点
        else:
            self._wave_acc *= 0.90

        if cfg.get("wave_click", True) and self.mouse.clicked:
            self.wave.splash(mx, my, cfg, big=True)

        amb = float(cfg.get("wave_ambient", 0.0))
        if amb > 0.0:
            self._wave_amb += float(dt)
            if self._wave_amb >= amb:
                self._wave_amb = 0.0
                self.wave.splash(float(np.random.uniform(0.0, w)),
                                 float(np.random.uniform(0.0, h)), cfg, big=True)
        else:
            self._wave_amb = 0.0

    # ------------------------------------------------------------ 窗口水波
    # 量级常数只有一份，定义在 WaterRippleField 里（那里是物理所在），这里引用它
    # 而不是各写一遍 22.0 / 0.10 —— 两处各写一个数，改一处忘一处就是"手感变了
    # 但没人知道为什么"。
    _WIN_BAND_PX = WaterRippleField._PUSH_W_DEF   # 扰动带基础宽度（再乘 win_ripple_size）
    _WIN_DOME = WaterRippleField._DOME_AMP        # 最小化 / 恢复的塌陷 / 抬升幅度

    def _win_perturb(self, ev, cfg):
        """把一条窗口事件变成水面上的扰动。

        三类事件各自的物理图像：

        * **change**（移动或缩放）：窗口像活塞，四条边各按**自身速度**推水。
          朝哪边走，哪边的前缘就隆起、后缘就塌陷 —— 船首波与船尾迹一起出现。
          平移与改尺寸走的是同一条公式（见 `WaterRippleField.push_rect`）。
          **水只往窗外推**：扰动带只铺窗口外侧，并且连速度场一起写（单向行波
          初值），所以不会像"往水里丢一个包"那样一分为二、再往窗口里钻回去。
        * **minimize**：窗口离开水面，水从四周合拢去填它占的位置（水面塌陷）。
        * **restore**：窗口重新压回水面，把水向外推开。

        共同点：**只有速度才产生波**。窗口静止时水面是平的 —— 哪怕它就浮在水上，
        泡着不动的石头也不搅水。所以这里没有任何"定时器"式的持续注入。
        """
        strength = float(cfg.get("win_ripple_strength", 0.7))
        if strength <= 0.0:
            return
        kind = ev[0]
        if kind == "change":
            old, new, edt = ev[1], ev[2], ev[3]
            if edt <= 1e-6:
                return
            inv = 1.0 / edt
            # 扰动带骑在**这一帧扫过区间的中点**上。边在 dt 里从 old 走到 new，
            # 水面"看到"的是这段行程的平均像，不是一个端点 —— 取中点等于数值
            # 积分里的中点法（二阶精度）。
            # 曾把带钉在行程末端（= 左端点法）：帧率越低、每帧跨得越远，注入
            # 越被高估 —— 实测同一个拖动手势 30 帧/秒比 120 帧/秒强 1.4 倍。
            self.wave.push_rect(
                (0.5 * (old[0] + new[0]), 0.5 * (old[1] + new[1]),
                 0.5 * (old[2] + new[2]), 0.5 * (old[3] + new[3])),
                ((new[0] - old[0]) * inv, (new[2] - old[2]) * inv,
                 (new[1] - old[1]) * inv, (new[3] - old[3]) * inv),
                strength, edt,
                self._WIN_BAND_PX * float(cfg.get("win_ripple_size", 1.0)))
        elif kind == "minimize":
            self.wave.dome_rect(ev[1], -self._WIN_DOME * strength)
        elif kind == "restore":
            self.wave.dome_rect(ev[1], self._WIN_DOME * strength)
