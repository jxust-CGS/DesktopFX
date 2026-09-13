# -*- coding: utf-8 -*-
"""UI 运行时冒烟：在真实 Tk 下实例化面板，遍历分组页 / 应用预设 / 就地刷新，
确认侧栏导航 + 可滚动内容 + 预设就地刷新这条新代码路径不抛异常。"""
import queue
import tkinter as tk
import desktop_fx as d
import fx_ui


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
        built = [n for n in d.PRESET_GROUPS.get(page, []) if n in d.PRESETS]
        # 模拟"用户在下雨页存过自定义预设"：预设区会多一格，页面变高 ——
        # 这正是用户报"下雨页也被往下拉"时的实际状态，窗口自适应必须算进去。
        if page == "下雨":
            built = built + ["我的自定义预设"]
        return built

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


def main():
    app = _StubApp()
    p = fx_ui.ControlPanel(app)

    # 1) 遍历所有分组页（验证 _show_page / 页面构建无异常）
    seen = []
    for name in list(p._pages.keys()):
        p._show_page(name)
        seen.append(name)
    # 顺序必须与 nav_defs 一致（可读性检查：侧栏点哪一项，右侧就得有对应页）
    assert seen == ["下雨", "下雪", "落樱", "流星雨", "颜料盘", "聚光灯",
                    "窗口水波", "涟漪", "其它"], seen
    # 侧栏导航不再有「预设」独立页，预设方案已下沉到各特效页
    assert "预设" not in p._pages, "预设 page should be removed"

    # 2) 应用一个会改写多键的预设，再就地刷新（验证 _preset + _sync_from_cfg）
    p._preset("舞台光")
    p._sync_from_cfg()
    # 刷新后，UI 变量应与 cfg 一致
    assert bool(app.cfg["spotlight_enabled"]) is True
    assert float(app.cfg["spotlight_radius"]) == 200.0

    # 2b) 验证「下雨」页内置预设方案（毛毛雨/暴雨）能正确套用
    p._preset("暴雨")
    p._sync_from_cfg()
    assert float(app.cfg["rain_amount"]) == 620.0, app.cfg["rain_amount"]
    p._preset("毛毛雨")
    p._sync_from_cfg()
    assert float(app.cfg["rain_amount"]) == 130.0, app.cfg["rain_amount"]
    assert bool(app.cfg["rain_enabled"]) is True

    # 3) 切到「下雨」页并强制布局
    p._show_page("下雨")
    p.root.update_idletasks()

    # 4) 「记忆上次」持久化：切页后 _load_last_page 应回读为「下雨」
    assert p._load_last_page() == "下雨", p._load_last_page()

    # 5) 窗口高度会**按内容自适应**（这样"下雨"这种要素少的页面才不需要滚）
    assert p.root.winfo_height() >= p._content_h, (
        p.root.winfo_height(), p._content_h)
    p._show_page("下雨")
    p.root.update_idletasks()
    assert not p._need_scroll(), \
        f"下雨页不该需要滚动：内容 {p._pages['下雨'].winfo_reqheight()} " \
        f"视口 {p._canvas.winfo_height()}"

    # 5a) 滚轮必须对页面内的**子控件**也生效 —— 但只在内容真的超出时。
    #     只把 <MouseWheel> 绑在 canvas / inner 上是抓不到的：Tk 把滚轮事件发给
    #     光标正下方那个控件、不会冒泡给父容器，鼠标停在卡片/滑块上滚时完全没
    #     反应，配置项一多，下面的"预设方案"就永远滚不到。
    #     正常状态下所有页面都装得下（窗口自适应），所以这里**人为把窗口压矮**
    #     制造溢出，才能验到"绑对了"这件事。
    p._show_page("颜料盘")
    p.root.geometry(f"{p.W}x620")
    p.root.update_idletasks()
    assert p._need_scroll(), "压矮窗口后颜料盘页应当需要滚动"
    deep = p._pages["颜料盘"]
    for _ in range(6):
        kids = deep.winfo_children()
        if not kids:
            break
        deep = kids[0]
    assert "all" in deep.bindtags(), ("滚轮没挂到全局绑定上", deep.bindtags())
    p._canvas.yview_moveto(0.0)
    before = p._canvas.yview()[0]
    deep.event_generate("<MouseWheel>", delta=-120)
    p.root.update()
    assert p._canvas.yview()[0] > before, (
        f"滚轮在子控件上没生效：yview {before} -> {p._canvas.yview()[0]}")

    # 5b) 内容装得下时滚轮必须**完全无效**：这时候滚起来只能把顶部拉走、
    #     露出下面一片空白（用户报过："下雨界面这些要素较少的，不要把顶部往下拉"）。
    p._fit_height()
    p.root.update_idletasks()
    assert not p._need_scroll(), "窗口恢复自适应后颜料盘页又需要滚动了"
    p._canvas.yview_moveto(0)
    deep.event_generate("<MouseWheel>", delta=-120)
    p.root.update()
    assert p._canvas.yview()[0] == 0.0, (
        f"装得下的页面被滚轮拉动了：yview={p._canvas.yview()}")
    deep.event_generate("<MouseWheel>", delta=120)
    p.root.update()
    assert p._canvas.yview()[0] == 0.0, (
        f"反方向也拉动了：yview={p._canvas.yview()}")

    # 6) 每个颜色项都要有"调色"（系统调色板）与"取色"（屏幕吸管）两个入口
    btns = set()

    def _walk(w):
        for c in w.winfo_children():
            try:
                if c.winfo_class() == "Button":
                    btns.add(c.cget("text"))
            except Exception:
                pass
            _walk(c)

    _walk(p._pages["颜料盘"])
    for txt in ("调色", "取色"):
        assert txt in btns, (f"颜料盘页缺少「{txt}」按钮", sorted(btns))
    assert p._swatches, "一个颜色项都没注册"
    for key, refresh in p._swatches.items():
        assert callable(refresh), key

    # 7) 屏幕取色的底层读点必须可用且返回合法色值
    from _win32 import screen_pixel
    got = screen_pixel(0, 0)
    assert got is None or (len(got) == 3 and all(0 <= v <= 255 for v in got)), got
    assert screen_pixel(-4, -4) is None, "越界坐标应当返回 None"

    p.root.destroy()
    # 清理测试写出的 UI 状态文件，避免污染实机默认页
    try:
        import os as _os
        _os.remove(fx_ui._UI_STATE_PATH)
    except Exception:
        pass
    print("UI RUNTIME OK  -> pages:", seen)


if __name__ == "__main__":
    main()
