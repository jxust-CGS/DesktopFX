# -*- coding: utf-8 -*-
"""量测面板启动与切页的开销拆分（先量根因再动手）。

量这些：
  1. ControlPanel 构造总耗时（= 建 14 页 + _fit_height + 首屏）
  2. _render_aa 调用次数与累计耗时（PIL 4× 超采样烘焙）
  3. 每个 _show_page 切页耗时（热切换）+ 切页后 root.update() 的上屏耗时
  4. 切页期间 _sync_scroll 被调了几次（inner <Configure> 级联）

用法：python tools/_meas_switch.py
"""
import os
import queue
import sys
import time
import tkinter as tk

ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import desktop_fx as d
import fx_ui
import fx_kinds

# 隔离 UI 状态（别碰用户真实的 ui_state.json）
import tempfile as _tf
fx_ui._UI_STATE_PATH = os.path.join(_tf.mkdtemp(prefix="fx_meas_"), "ui_state.json")


class _StubApp:
    def __init__(self):
        self.cfg = dict(d.DEFAULTS)
        self.events = queue.Queue()
        self.fps = 60.0
        self.cost_ms = 9.0
        self.paused = False

    def set(self, k, v):
        self.cfg[k] = v

    def get_autostart(self):
        return False

    def set_autostart(self, v):
        return True

    def apply_preset(self, n):
        self.cfg.update(d.PRESETS[n])

    def preset_names(self):
        return list(d.PRESETS.keys())

    def presets_for_page(self, page):
        return [n for n in d.PRESET_GROUPS.get(page, []) if n in d.PRESETS]

    def is_user_preset(self, name):
        return False

    def save_preset(self, name, page):
        return True, "ok"

    def delete_preset(self, name):
        return True, "ok"

    def toggle_pause(self):
        self.paused = not self.paused
        return self.paused

    def quit(self):
        pass

    def report_error(self, *a):
        pass


# ---- 统计 _render_aa（烘焙）----
_orig_aa = fx_ui._render_aa
AA_STAT = {"n": 0, "ms": 0.0}


def _counting_aa(*a, **kw):
    t0 = time.perf_counter()
    r = _orig_aa(*a, **kw)
    AA_STAT["n"] += 1
    AA_STAT["ms"] += (time.perf_counter() - t0) * 1000.0
    return r


fx_ui._render_aa = _counting_aa


def _reset_aa():
    AA_STAT["n"] = 0
    AA_STAT["ms"] = 0.0


def main():
    root = tk.Tk()
    root.withdraw()
    app = _StubApp()

    t0 = time.perf_counter()
    panel = fx_ui.ControlPanel(app)
    t_build = (time.perf_counter() - t0) * 1000.0
    print("构造面板总耗时            %8.1f ms" % t_build)
    print("  其中 _render_aa 烘焙     %8.1f ms  (%d 次)"
          % (AA_STAT["ms"], AA_STAT["n"]))

    pages = list(panel._pages.keys())
    print("页面数 %d：%s" % (len(pages), "、".join(pages)))

    # 切页：先各自热一遍（建页已在构造期完成，都是热的）
    print()
    print("== 切页耗时（_show_page + 紧随的 root.update 上屏）==")
    order = [p for p in pages if p != panel._active]
    worst = 0.0
    for name in order + [panel._active]:
        _reset_aa()
        sync_n = [0]
        orig_sync = panel._sync_scroll

        def _counting_sync(*a, _o=orig_sync, _c=sync_n):
            _c[0] += 1
            return _o(*a)

        panel._sync_scroll = _counting_sync
        t1 = time.perf_counter()
        panel._show_page(name)
        t_switch = (time.perf_counter() - t1) * 1000.0
        t2 = time.perf_counter()
        root.update()
        t_draw = (time.perf_counter() - t2) * 1000.0
        panel._sync_scroll = orig_sync
        worst = max(worst, t_switch + t_draw)
        print("  %-6s 切换 %6.1f ms  上屏 %6.1f ms  | 烘焙 %2d 次 %6.1f ms  _sync_scroll %d 次"
              % (name, t_switch, t_draw, AA_STAT["n"], AA_STAT["ms"], sync_n[0]))
    print("最慢一次合计 %.1f ms" % worst)

    # 反复来回切同一对页，看有没有随次数恶化
    print()
    print("== 往返切换 10 次（下雨<->其它）==")
    t3 = time.perf_counter()
    for _ in range(10):
        panel._show_page("下雨")
        panel._show_page("其它")
    print("  10 次往返共 %.1f ms（均值 %.1f ms/次）"
          % ((time.perf_counter() - t3) * 1000.0,
             (time.perf_counter() - t3) * 100.0))

    # 每页高度（_fit_height 的数据来源）
    hs = {n: int(f.winfo_reqheight()) for n, f in panel._pages.items()}
    print()
    print("== 各页请求高度 ==")
    print("  " + "  ".join("%s=%d" % (n, h) for n, h in hs.items()))

    # _show_page 分步拆解（复刻其步骤，逐段计时）
    print()
    print("== _show_page 分步拆解 ==")

    def timed_switch(name):
        t = {}
        t0 = time.perf_counter()
        for n, f in panel._pages.items():
            if n == name:
                f.pack(fill="x")
            else:
                f.pack_forget()
        t1 = time.perf_counter()
        t["pack"] = (t1 - t0) * 1000.0
        panel._active = name
        for n, info in panel._nav.items():
            if n == name:
                info["frame"].config(bg=fx_ui.NAV_ACTIVE)
                info["label"].config(bg=fx_ui.NAV_ACTIVE, fg=fx_ui.ACCENT)
                info["line"].config(bg=fx_ui.ACCENT)
                info["icon"].config(bg=fx_ui.NAV_ACTIVE)
                info["icon"].set_color(fx_ui.ACCENT)
            else:
                info["frame"].config(bg=fx_ui.SIDEBAR_BG)
                info["label"].config(bg=fx_ui.SIDEBAR_BG, fg=fx_ui.TEXT)
                info["line"].config(bg=fx_ui.SIDEBAR_BG)
                info["icon"].config(bg=fx_ui.SIDEBAR_BG)
                info["icon"].set_color(fx_ui.SUB)
        t2 = time.perf_counter()
        t["nav"] = (t2 - t1) * 1000.0
        panel._canvas.update_idletasks()
        t3 = time.perf_counter()
        t["idle"] = (t3 - t2) * 1000.0
        panel._canvas.yview_moveto(0)
        panel._sync_scroll()
        t4 = time.perf_counter()
        t["scroll"] = (t4 - t3) * 1000.0
        panel._save_last_page(name)
        t5 = time.perf_counter()
        t["save"] = (t5 - t4) * 1000.0
        return t

    for name in ("下雪", "真实水波", "其它", "下雨"):
        # 先随便切到别处，保证是"切换"而不是"首建"
        panel._show_page("聚光灯")
        tt = timed_switch(name)
        print("  %-6s " % name + "  ".join(
            "%s %6.1f" % (k, v) for k, v in tt.items()))

    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
