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
import numpy as np

from fx_effects import (RainField, RippleField, MeteorField, MouseProbe,
                        PaintField, PetalField, SnowField, SpotlightField,
                        WindowProbe, hex_to_bgr)


class Scene:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.rain = RainField(w, h, seed=7)
        self.snow = SnowField(w, h, seed=9)
        self.petals = PetalField(w, h, seed=23)         # 落樱
        self.meteors = MeteorField(w, h, seed=31)       # 流星雨
        self.paint = PaintField(w, h, seed=37)          # 颜料盘
        self.ripples = RippleField()
        self.mouse = MouseProbe()
        self.spot = SpotlightField(w, h, seed=17)       # 聚光灯
        self.win = WindowProbe()                        # 窗口枚举（窗口交互地基）

        # 覆盖层在虚拟屏幕里的左上角偏移（鼠标坐标要折算进来）
        self.ov_x = 0
        self.ov_y = 0

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

        # 窗口水波跨帧状态
        self._win_moving = False
        self._win_move_t = 0.0          # 距上次 move 事件的累计时间
        self._win_last_rect = None
        self._win_events = []           # 测试可注入合成事件，绕过真实窗口枚举
        self._last_win_color = None
        self._win_ripple_color_cache = hex_to_bgr("#7FE7FF")

    def set_origin(self, x, y):
        self.ov_x, self.ov_y = x, y
        self.win.set_origin(x, y)

    def resize(self, w, h):
        self.w, self.h = w, h
        self.rain.resize(w, h)
        self.snow.resize(w, h)
        self.petals.resize(w, h)
        self.meteors.resize(w, h)
        self.paint.resize(w, h)
        self.spot.resize(w, h)

    def _ripple_color(self):
        c = self.cfg.get("ripple_color", "#7FE7FF")
        if self._last_ripple_color != c:
            self._ripple_color_cache = hex_to_bgr(c)
            self._last_ripple_color = c
        return self._ripple_color_cache

    # ------------------------------------------------------------ 每帧
    def step(self, dt, u32, w, h):
        cfg = self.cfg

        # 鼠标坐标最先更新：聚光灯与鼠标涟漪都要用到当前光标
        dist = self.mouse.update(dt, self.ov_x, self.ov_y)

        # ---------------- 窗口水波（窗口移动 / 最小化 / 恢复触发，复用涟漪池）
        if cfg.get("win_ripple_enabled", False):
            if self._win_events:
                events = self._win_events
                self._win_events = []
            else:
                events = self.win.update(dt)
            moved = False
            for et, rect in events:
                if et == "move":
                    moved = True
                    self._win_last_rect = rect
                    self._win_perimeter_ripples(rect, cfg)
                elif et in ("minimize", "restore"):
                    self._win_splash(rect, cfg, big=True)
            if moved:
                self._win_moving = True
                self._win_move_t = 0.0
            else:
                self._win_move_t += dt
                if self._win_moving and self._win_move_t > 0.2:
                    # 拖动结束（停止移动超过 0.2s）-> 落点大爆发，如石子入水
                    self._win_moving = False
                    if self._win_last_rect:
                        self._win_splash(self._win_last_rect, cfg, big=True)

        # ---------------- 聚光灯（背景压暗，最先画，让其它特效叠在上面）
        if cfg.get("spotlight_enabled", False):
            self.spot.set_cursor(float(self.mouse.mx), float(self.mouse.my))
            self.spot.set_radius(float(cfg.get("spotlight_radius", 220)))
            self.spot.set_dim(float(cfg.get("spotlight_dim", 0.6)))
            self.spot.set_color(cfg["spotlight_color"])
            self.spot.draw(u32, w, h, cfg)

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
            self.paint.draw(u32, w, h, cfg)
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
            self.rain.draw(u32, w, h, cfg)

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
            self.snow.draw(u32, w, h, cfg)

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
            self.petals.draw(u32, w, h, cfg)

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
            self.meteors.draw(u32, w, h, cfg)

        # ---------------- 鼠标涟漪（由鼠标轨迹 / 点击触发，dist 已在上面临时算出）
        if cfg.get("ripple_move", True) and dist > 0.0:
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

        if cfg.get("ripple_click", True) and self.mouse.clicked:
            self.ripples.spawn(float(self.mouse.mx), float(self.mouse.my),
                              float(cfg["ripple_size"]) * 1.9,
                              float(cfg["ripple_life"]) * 1.35,
                              0.95 * float(cfg["ripple_opacity"]),
                              self._ripple_color(), r0=3.0)

        # ---------------- 涟漪
        self.ripples.update(dt)
        self.ripples.draw(u32, w, h)

    # ------------------------------------------------------------ 窗口水波
    def _win_ripple_color(self, cfg):
        c = cfg.get("win_ripple_color", "#7FE7FF")
        if self._last_win_color != c:
            self._win_ripple_color_cache = hex_to_bgr(c)
            self._last_win_color = c
        return self._win_ripple_color_cache

    def _win_perimeter_ripples(self, rect, cfg):
        """沿窗口四条边采样若干点，各发一圈小涟漪 -> 水波从窗缘向外推。"""
        x0, y0, x1, y1 = rect
        w = x1 - x0
        h = y1 - y0
        if w <= 0 or h <= 0:
            return
        col = self._win_ripple_color(cfg)
        strength = float(cfg["win_ripple_strength"]) * 0.5      # 持续波弱一些
        life = float(cfg["win_ripple_life"]) * 0.7
        size = float(cfg["win_ripple_size"])
        rmax = max(18.0, min(160.0, (w + h) * 0.06 * size))
        per_edge = max(1, int(cfg["win_ripple_edge_density"]) // 4)
        pts = []
        for i in range(per_edge):
            t = (i + 1) / (per_edge + 1)
            pts.append((x0 + w * t, y0))
            pts.append((x0 + w * t, y1))
            pts.append((x0, y0 + h * t))
            pts.append((x1, y0 + h * t))
        for (px, py) in pts:
            self.ripples.spawn(px, py, rmax, life, strength, col, r0=2.0, squash=0.85)

    def _win_splash(self, rect, cfg, big=False):
        """在窗口中心放一团（大）涟漪。big=True 用于松手 / 最小化 / 恢复。"""
        x0, y0, x1, y1 = rect
        cx = (x0 + x1) * 0.5
        cy = (y0 + y1) * 0.5
        w = x1 - x0
        h = y1 - y0
        col = self._win_ripple_color(cfg)
        size = float(cfg["win_ripple_size"])
        if big:
            rmax = max(60.0, min(360.0, (w + h) * 0.22 * size))
            life = float(cfg["win_ripple_life"]) * 1.15
            strength = float(cfg["win_ripple_strength"])
        else:
            rmax = max(40.0, min(220.0, (w + h) * 0.14 * size))
            life = float(cfg["win_ripple_life"])
            strength = float(cfg["win_ripple_strength"]) * 0.8
        self.ripples.spawn(cx, cy, rmax, life, strength, col, r0=3.0, squash=0.9)
