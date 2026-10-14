# -*- coding: utf-8 -*-
"""UI 运行时冒烟：在真实 Tk 下实例化面板，遍历分组页 / 应用预设 / 就地刷新，
确认侧栏导航 + 可滚动内容 + 预设就地刷新这条新代码路径不抛异常。"""
import queue
import tkinter as tk
import desktop_fx as d
import fx_ui

# 隔离 UI 状态文件。这两个脚本原来直接 os.remove(fx_ui._UI_STATE_PATH) 来
# "清理测试痕迹"，而 ui_state.json 现在装的是**用户真实记住的窗口尺寸和上次
# 停留页** —— 跑一次测试就把用户设置清掉了。改成写临时文件。
import os as _os2
import tempfile as _tf
fx_ui._UI_STATE_PATH = _os2.path.join(_tf.mkdtemp(prefix="fx_uistate_"),
                                      "ui_state.json")


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
    # 顺序必须与侧栏一致（可读性检查：侧栏点哪一项，右侧就得有对应页），
    # 且直接等于分类表的导航顺序 —— 侧栏分组不允许在 UI 里再写一份
    import fx_kinds
    assert seen == ["下雨", "下雪", "落樱", "流星雨", "聚光灯", "颜料盘",
                    "宠物跟随", "涟漪", "真实水波", "点击表情", "点击文字",
                    "窗口水波", "其它"], seen
    assert seen == fx_kinds.nav_order(), (seen, fx_kinds.nav_order())


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

    # 2d) 各特效页的启停开关都能写回 cfg（同类互斥要靠它触发）
    for page, key in (("下雨", "rain_enabled"), ("下雪", "snow_enabled"),
                      ("落樱", "petal_enabled"), ("流星雨", "meteor_enabled"),
                      ("聚光灯", "spotlight_enabled"), ("颜料盘", "paint_enabled"),
                      ("涟漪", "ripple_enabled"), ("真实水波", "wave_enabled"),
                      ("窗口水波", "win_ripple_enabled")):
        assert key in p._ctrls, f"{page} 页缺少启停开关 {key}"
        p._ctrls[key]["on"](True)
        assert app.cfg[key] is True, f"{key} 未写回 cfg"
        p._ctrls[key]["on"](False)
        assert app.cfg[key] is False, f"{key} 未写回 cfg"

    # 2d2) 「点击表情」页的自定义素材块：来源 / 两个文件夹行 / 语音开关都注册
    #      了，文件夹行的状态文案跟着**后台**加载走（不跟就会像"选了没反应"）。
    for k in ("click_emoji_source", "click_emoji_custom_dir",
              "click_voice_enabled", "click_voice_dir"):
        assert k in p._ctrls, f"点击表情页缺少控件 {k}"
    assert p._ctrls["click_emoji_source"]["kind"] == "choices", \
        p._ctrls["click_emoji_source"]["kind"]
    assert p._ctrls["click_voice_enabled"]["kind"] == "toggle"
    for k in ("click_emoji_custom_dir", "click_voice_dir"):
        assert p._ctrls[k]["kind"] == "path", (k, p._ctrls[k]["kind"])
        # 路径行必须跟着窗口宽度折行 —— 否则长路径会把页面顶到最小宽度之外
        assert p._ctrls[k]["label"] in p._hints, f"{k} 的路径行没注册折行宽度"
    p._ctrls["click_voice_enabled"]["on"](True)
    assert app.cfg["click_voice_enabled"] is True
    p._ctrls["click_voice_enabled"]["on"](False)

    # 2d3) 文件夹行的回填：非音频文件不算数，长目录名从**尾部**保留
    _aud = _tf.mkdtemp(prefix="fx_ui_voice_")
    for _nm in ("a.wav", "b.wav", "note.txt"):
        with open(_os2.path.join(_aud, _nm), "wb") as _fh:
            _fh.write(b"\x00")
    app.cfg["click_voice_dir"] = _aud
    p._sync_from_cfg()
    _lb = p._ctrls["click_voice_dir"]["label"].cget("text")
    assert "找到 2 个音频" in _lb, _lb
    assert "note.txt" not in _lb, _lb
    _deep = _os2.path.join(_aud, "一二三四五六七八九十" * 3)
    _os2.makedirs(_deep, exist_ok=True)
    app.cfg["click_voice_dir"] = _deep
    p._ctrls["click_voice_dir"]["refresh"]()
    _lb2 = p._ctrls["click_voice_dir"]["label"].cget("text")
    assert _lb2.startswith("…"), _lb2
    assert _deep[-10:] in _lb2, (_lb2, _deep[-10:])
    app.cfg["click_voice_dir"] = ""

    # 2d4) 「点击文字」页的语音包 + 只读文字栏。
    #      开着「语音与文字统一」时，文字框必须**自动填入语音包里的音频名并
    #      禁止编辑**，且**不能顺手把用户自己写的文字从 cfg 里抹掉**（关掉
    #      统一之后得原样回来）—— 这条最容易实现成"直接覆盖配置"。
    for k in ("click_text_voice_dir", "click_text_voice_match"):
        assert k in p._ctrls, f"点击文字页缺少控件 {k}"
    assert p._ctrls["click_text_voice_match"]["kind"] == "toggle"
    assert p._ctrls["click_text_voice_dir"]["kind"] == "path"
    _pack = _tf.mkdtemp(prefix="fx_ui_pack_")
    for _nm in ("01-你好.wav", "02_再见.mp3", "note.txt"):
        with open(_os2.path.join(_pack, _nm), "wb") as _fh:
            _fh.write(b"\x00")
    _box = p._ctrls["click_text_list"]["widget"]
    _custom = str(app.cfg["click_text_list"])
    assert str(_box.cget("state")) == "normal", "默认应当可编辑"
    app.cfg["click_text_voice_dir"] = _pack
    p._ctrls["click_text_voice_match"]["on"](True)
    p.root.update_idletasks()
    _shown = _box.get("1.0", "end-1c")
    assert str(_box.cget("state")) == "disabled", \
        f"统一开启后文字框仍可编辑（state={_box.cget('state')}）"
    assert _shown == "你好\n再见", f"文字框没自动填入音频名：{_shown!r}"
    assert app.cfg["click_text_list"] == _custom, \
        f"统一模式改写了用户的文字配置：{app.cfg['click_text_list']!r}"
    assert "找到 2 个音频" in p._ctrls["click_text_voice_dir"]["label"].cget("text"), \
        p._ctrls["click_text_voice_dir"]["label"].cget("text")
    # 关掉统一：恢复可编辑、内容回到用户自己写的那份
    p._ctrls["click_text_voice_match"]["on"](False)
    p.root.update_idletasks()
    assert str(_box.cget("state")) == "normal", "关掉统一后文字框仍被锁着"
    assert _box.get("1.0", "end-1c") == _custom, \
        f"关掉统一后没回到用户文字：{_box.get('1.0', 'end-1c')!r}"
    # 空目录时不锁成空框：仍禁止编辑（统一模式就是这样），但内容应是空
    app.cfg["click_text_voice_dir"] = _tf.mkdtemp(prefix="fx_ui_pack0_")
    p._ctrls["click_text_voice_match"]["on"](True)
    p.root.update_idletasks()
    assert _box.get("1.0", "end-1c") == "", "空语音包目录下文字框应为空"
    assert "没找到音频" in p._ctrls["click_text_voice_dir"]["label"].cget("text")
    p._ctrls["click_text_voice_match"]["on"](False)
    app.cfg["click_text_voice_dir"] = ""

    # 2e) 「其他设置」页：层级 / 多开开关已注册，且写的是字符串档位
    for k in ("layer_fullscreen", "layer_mouse", "multi_fullscreen", "multi_mouse"):
        assert k in p._ctrls, f"其他设置页缺少控件 {k}"
    p._ctrls["layer_fullscreen"]["on"](True)
    assert app.cfg["layer_fullscreen"] == "desktop", app.cfg["layer_fullscreen"]
    p._ctrls["layer_fullscreen"]["on"](False)
    assert app.cfg["layer_fullscreen"] == "topmost"
    app.cfg["layer_mouse"] = "desktop"          # cfg -> UI 的同步方向
    p._sync_from_cfg()
    assert p._ctrls["layer_mouse"]["var"].get() is True
    app.cfg["layer_mouse"] = "topmost"
    p._sync_from_cfg()
    assert p._ctrls["layer_mouse"]["var"].get() is False
    p._ctrls["multi_fullscreen"]["on"](True)
    assert app.cfg["multi_fullscreen"] is True
    p._ctrls["multi_fullscreen"]["on"](False)

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
    # 压矮之后**必须把布局跑完再发滚轮**：update_idletasks 只跑空闲任务、不处理
    # 窗口的 Configure，内容区的实际高度这时还是压矮前的旧值。
    # （早先这里不 update 也能过，是因为内容区恰好停在更早一代的旧高度 —— 742
    #  那一版 —— 比滚动区矮，于是"滚得动"。那是巧合，不是这条断言想验的东西。
    #  窗口支持缩放之后内容区会及时跟上窗口尺寸，量到的就是"装得下"，此时滚不
    #  动反而是正确的。所以这里补一次 update，把断言钉在"布局落定后的真实状态"。）
    p.root.update()
    assert p._canvas.winfo_height() < p._pages["颜料盘"].winfo_reqheight(), (
        "压矮后内容区仍没跟上窗口尺寸："
        f"{p._canvas.winfo_height()} vs "
        f"{p._pages['颜料盘'].winfo_reqheight()}")
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

    # 6b) 分组归属：「雨滴落地溅水花」属于**下雨**页（它只在雨滴落到底边时触发），
    #     「其它」页不该再有它，也不该再有已下线的「随机闪电」。
    def _labels(page):
        out = []

        def walk(wd):
            for c in wd.winfo_children():
                try:
                    if c.winfo_class() in ("Label", "Button"):
                        out.append(str(c.cget("text")))
                except Exception:
                    pass
                walk(c)

        walk(p._pages[page])
        return out

    rain_lb = _labels("下雨")
    other_lb = _labels("其它")
    assert "雨滴落地溅水花" in rain_lb, ("下雨页缺少溅水花开关", rain_lb)
    assert "雨滴落地溅水花" not in other_lb, ("其它页还有溅水花", other_lb)
    assert "随机闪电" not in other_lb, ("其它页还有已下线的随机闪电", other_lb)
    assert "lightning" not in d.DEFAULTS, "lightning 已下线，不该留在 DEFAULTS 里"

    # 6c) 「涟漪」与「真实水波」是**两套独立实现**，配置项必须各归各页、互不串台。
    #     这两套是并存供用户挑选的（不是替代关系），任何一边的键跑到另一边都说明接错了。
    ripple_lb = _labels("涟漪")
    wave_lb = _labels("真实水波")
    assert "涟漪颜色" in ripple_lb and "跟随鼠标移动" in ripple_lb, ripple_lb
    assert "高光颜色" in wave_lb, ("真实水波页缺少高光颜色", wave_lb)
    for lab in ("迎光提亮", "背光压暗", "浪尖高光", "光照方向", "余波时长"):
        assert lab in wave_lb, (f"真实水波页缺少「{lab}」", wave_lb)
    assert "涟漪颜色" not in wave_lb, ("真实水波页混进了涟漪的控件", wave_lb)
    assert "迎光提亮" not in ripple_lb, ("涟漪页混进了水波的控件", ripple_lb)
    # 两套的配置键集合不能有交集（前缀不同），否则 apply_preset 会互相踩
    rk = {k for k in d.DEFAULTS if k.startswith("ripple_")}
    wk = {k for k in d.DEFAULTS if k.startswith("wave_")}
    assert rk and wk and not (rk & wk), (rk & wk)
    assert "wave_enabled" in d.DEFAULTS and d.DEFAULTS["wave_enabled"] is False, \
        "真实水波必须默认关闭（与颜料盘一样，需用户手动打开）"

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
