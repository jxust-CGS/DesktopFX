# -*- coding: utf-8 -*-
"""UI 运行时冒烟：在真实 Tk 下实例化面板，遍历分组页 / 应用预设 / 就地刷新，
确认侧栏导航 + 可滚动内容 + 预设就地刷新这条新代码路径不抛异常。"""
import queue
import time
import tkinter as tk
import desktop_fx as d
import fx_effects
import fx_ui

# 素材来源的三个选项名（用来在控件树里认出哪一个是选中的那个）
fx_kinds_sources = set(fx_effects.CLICK_SOURCES)

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
                    "点击音效", "窗口水波", "其它"], seen
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

    # 2d2) 「点击表情」页：来源 / 两个路径行 / 单次选择 / 图片方向都注册了，
    #      路径行的状态文案跟着**后台**加载走（不跟就会像"选了没反应"）。
    #      语音相关已经全部搬到「点击音效」页，这里**不该**再有它们。
    for k in ("click_emoji_source", "click_emoji_custom_dir", "click_emoji_files",
              "click_emoji_pick", "click_emoji_align"):
        assert k in p._ctrls, f"点击表情页缺少控件 {k}"
    for k in ("click_voice_enabled", "click_voice_dir", "click_text_voice_dir"):
        assert k not in p._ctrls, f"{k} 已并入「点击音效」，不该还留在点击表情页"
        assert k not in d.DEFAULTS, f"{k} 已并入 click_sfx_*，不该还在 DEFAULTS"
    assert p._ctrls["click_emoji_source"]["kind"] == "choices", \
        p._ctrls["click_emoji_source"]["kind"]
    assert p._ctrls["click_emoji_pick"]["kind"] == "choices"
    assert p._ctrls["click_emoji_align"]["kind"] == "choices"
    for k in ("click_emoji_custom_dir", "click_emoji_files"):
        assert p._ctrls[k]["kind"] == "path", (k, p._ctrls[k]["kind"])
        # 路径行必须跟着窗口宽度折行 —— 否则长路径会把页面顶到最小宽度之外
        assert p._ctrls[k]["label"] in p._hints, f"{k} 的路径行没注册折行宽度"

    # 2d2b) 图片素材行：一行服务两种来源，文案跟着来源走
    _st = p._ctrls["click_emoji_files"]["label"]
    app.cfg["click_emoji_source"] = "内置图案"
    p._ctrls["click_emoji_files"]["refresh"]()
    assert "不需要素材" in _st.cget("text"), _st.cget("text")
    _img = _tf.mkdtemp(prefix="fx_ui_asset_")
    _p1 = _os2.path.join(_img, "a.png")
    _p2 = _os2.path.join(_img, "b.gif")
    for _f in (_p1, _p2):
        with open(_f, "wb") as _fh:
            _fh.write(b"\x00")
    app.cfg["click_emoji_source"] = "自选图片"
    app.cfg["click_emoji_files"] = _p1 + "\n" + _p2
    p._ctrls["click_emoji_files"]["refresh"]()
    assert "已选 2 张图片" in _st.cget("text"), _st.cget("text")
    # 删掉一个之后要少一张（而不是整批作废）。列表带 2 秒 TTL，这里用显式
    # 失效模拟"用户动作之后立刻生效" —— 界面上的选择/清除按钮就是这么做的，
    # 否则刚点完"清除"还显示旧张数，看着像没生效。
    _os2.remove(_p1)
    fx_effects.invalidate_media_cache()
    for _k in ("click_emoji_files", "click_emoji_custom_dir"):
        # 两个键共用这一行，任一刷新都应给出同一份视图
        p._ctrls[_k]["refresh"]()
    assert "还没选图片" in _st.cget("text") or "已选 1 张" in _st.cget("text"), \
        _st.cget("text")
    # 旧配置里的「自定义图片」必须高亮到「图片文件夹」，而不是三个选项全灰
    app.cfg["click_emoji_source"] = "自定义图片"
    p._ctrls["click_emoji_source"]["refresh"]()
    _sel = []

    def _walk_btn(w):
        for _c in w.winfo_children():
            try:
                if _c.winfo_class() == "Button" and \
                        str(_c.cget("relief")) == "solid" and \
                        str(_c.cget("text")) in fx_kinds_sources:
                    _sel.append(str(_c.cget("text")))
            except Exception:
                pass
            _walk_btn(_c)

    _walk_btn(p._pages["点击表情"])
    assert _sel == ["图片文件夹"], \
        f"旧值「自定义图片」没有高亮到「图片文件夹」：{_sel}"
    app.cfg["click_emoji_source"] = "内置图案"
    app.cfg["click_emoji_files"] = ""

    # 2d3) 音效文件夹行的回填：非音频文件不算数，长目录名从**尾部**保留
    _aud = _tf.mkdtemp(prefix="fx_ui_voice_")
    for _nm in ("a.wav", "b.wav", "note.txt"):
        with open(_os2.path.join(_aud, _nm), "wb") as _fh:
            _fh.write(b"\x00")
    app.cfg["click_sfx_source"] = "音效文件夹"
    app.cfg["click_sfx_dir"] = _aud
    p._sync_from_cfg()
    _lb = p._ctrls["click_sfx_dir"]["label"].cget("text")
    assert "找到 2 个音频" in _lb, _lb
    assert "note.txt" not in _lb, _lb
    _deep = _os2.path.join(_aud, "一二三四五六七八九十" * 3)
    _os2.makedirs(_deep, exist_ok=True)
    app.cfg["click_sfx_dir"] = _deep
    p._ctrls["click_sfx_dir"]["refresh"]()
    _lb2 = p._ctrls["click_sfx_dir"]["label"].cget("text")
    assert _lb2.startswith("…"), _lb2
    assert _deep[-10:] in _lb2, (_lb2, _deep[-10:])
    app.cfg["click_sfx_dir"] = _aud

    # 2d4) 「语音与文字统一」：目录来自音效来源、开着时自动启用「点击文字」、
    #      文字框**自动填入音频名并禁止编辑**，且**不能顺手把用户自己写的
    #      文字从 cfg 里抹掉**（关掉统一之后得原样回来）—— 这条最容易
    #      实现成"直接覆盖配置"。
    assert "click_text_voice_dir" not in p._ctrls, \
        "语音包目录已并入 click_sfx_dir，点击文字页不该再有一份"
    assert "click_text_voice_dir" not in d.DEFAULTS
    assert p._ctrls["click_text_voice_match"]["kind"] == "toggle"
    _pack = _tf.mkdtemp(prefix="fx_ui_pack_")
    for _nm in ("01-你好.wav", "02_再见.mp3", "note.txt"):
        with open(_os2.path.join(_pack, _nm), "wb") as _fh:
            _fh.write(b"\x00")
    _box = p._ctrls["click_text_list"]["widget"]
    _custom = str(app.cfg["click_text_list"])
    assert str(_box.cget("state")) == "normal", "默认应当可编辑"
    app.cfg["click_sfx_source"] = "音效文件夹"
    app.cfg["click_sfx_dir"] = _pack
    app.cfg["click_text_enabled"] = False
    app.cfg["click_emoji_enabled"] = True
    p._sync_from_cfg()
    p._ctrls["click_text_voice_match"]["on"](True)   # 走真实开关路径
    p.root.update_idletasks()
    assert app.cfg["click_text_enabled"] is True, \
        "开启统一后没有自动启用「点击文字」"
    # 「点击文字 / 点击表情」同类互斥会顺带关掉点击表情 —— 那是 App.set 的
    # 职责（这个桩不实现互斥），由 _verify_click 的互斥用例守着，这里不重复。
    _shown = _box.get("1.0", "end-1c")
    assert str(_box.cget("state")) == "disabled", \
        f"统一开启后文字框仍可编辑（state={_box.cget('state')}）"
    assert _shown == "你好\n再见", f"文字框没自动填入音频名：{_shown!r}"
    assert app.cfg["click_text_list"] == _custom, \
        f"统一模式改写了用户的文字配置：{app.cfg['click_text_list']!r}"
    assert "找到 2 个音频" in p._ctrls["click_sfx_dir"]["label"].cget("text"), \
        p._ctrls["click_sfx_dir"]["label"].cget("text")
    # 开着统一时，音效来源那一组按钮必须是**禁用**的（锁住）
    for _k in ("click_sfx_source", "click_sfx_builtin", "click_sfx_dir"):
        for _b in p._ctrls[_k].get("btns", ()):
            assert str(_b.cget("state")) == "disabled", \
                f"统一开启后 {_k} 的按钮仍可点（应锁住）"
    # 关掉统一：恢复可编辑、内容回到用户自己写的那份、控件解锁
    p._ctrls["click_text_voice_match"]["on"](False)
    p.root.update_idletasks()
    assert str(_box.cget("state")) == "normal", "关掉统一后文字框仍被锁着"
    assert _box.get("1.0", "end-1c") == _custom, \
        f"关掉统一后没回到用户文字：{_box.get('1.0', 'end-1c')!r}"
    for _k in ("click_sfx_source", "click_sfx_builtin", "click_sfx_dir"):
        for _b in p._ctrls[_k].get("btns", ()):
            assert str(_b.cget("state")) == "normal", \
                f"关掉统一后 {_k} 的按钮仍被锁着"
    # 来源不是「音效文件夹」时统一不成立 -> 开关应当被自动关掉
    app.cfg["click_sfx_source"] = "内置音效"
    p._sync_from_cfg()
    assert app.cfg["click_text_voice_match"] is False, \
        "来源不是音效文件夹时统一仍开着（内部不变量没守住）"
    # 空目录时不锁成空框：仍禁止编辑（统一模式就是这样），但内容应是空
    app.cfg["click_sfx_source"] = "音效文件夹"
    app.cfg["click_sfx_dir"] = _tf.mkdtemp(prefix="fx_ui_pack0_")
    p._sync_from_cfg()
    p._ctrls["click_text_voice_match"]["on"](True)
    p.root.update_idletasks()
    assert _box.get("1.0", "end-1c") == "", "空语音包目录下文字框应为空"
    assert "没找到音频" in p._ctrls["click_sfx_dir"]["label"].cget("text")
    p._ctrls["click_text_voice_match"]["on"](False)
    app.cfg["click_sfx_dir"] = ""
    app.cfg["click_sfx_source"] = "内置音效"

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

    # ---------------------------------------------------------- 8) 新布局
    # 8a) 「点击表情」页按来源条件显隐：内置图案只显示图案/颜色，
    #     自选图片只显示素材行/单次选择。判据用**真的映射到屏幕上**
    #     （winfo_ismapped 会沿父链传：行被 pack_forget 时子 Label 也
    #     算未映射）——只看 pack 状态会分不出"行藏着"和"页没显示"。
    def _find_label(w, text):
        for c in w.winfo_children():
            try:
                if c.winfo_class() == "Label" and str(c.cget("text")) == text:
                    return c
            except Exception:
                pass
            r = _find_label(c, text)
            if r is not None:
                return r
        return None

    def _shown(page, text):
        p._show_page(page)
        p.root.update_idletasks()
        lb = _find_label(p._pages[page], text)
        assert lb is not None, f"{page} 页找不到「{text}」这一行"
        return bool(lb.winfo_ismapped())

    def _walk_labels(w, out=None):
        if out is None:
            out = []
        for c in w.winfo_children():
            try:
                if c.winfo_class() == "Label" and str(c.cget("text")):
                    out.append(c)
            except Exception:
                pass
            _walk_labels(c, out)
        return out

    app.cfg["click_emoji_source"] = "内置图案"
    p._sync_from_cfg()
    p.root.update_idletasks()
    assert _shown("点击表情", "图案"), "内置图案时「图案」行应当可见"
    assert _shown("点击表情", "颜色"), "内置图案时「颜色」行应当可见"
    assert _shown("点击表情", "图片方向"), \
        "「图片方向」是通用选项，内置图案时也该可见"
    assert not _shown("点击表情", "图片素材"), \
        "内置图案时「图片素材」行应当整行藏掉"
    assert not _shown("点击表情", "单次选择"), \
        "「单次选择」只适用于图片文件夹，内置图案时应当藏掉"
    app.cfg["click_emoji_source"] = "自选图片"
    p._sync_from_cfg()
    p.root.update_idletasks()
    assert not _shown("点击表情", "图案"), \
        "自选图片时「图案」行应当整行藏掉"
    assert not _shown("点击表情", "颜色"), \
        "自选图片时「颜色」行应当整行藏掉"
    assert _shown("点击表情", "图片素材"), "自选图片时「图片素材」行应当可见"
    assert _shown("点击表情", "图片方向"), \
        "「图片方向」是通用选项，自选图片时也该可见"
    assert not _shown("点击表情", "单次选择"), \
        "「单次选择」只适用于图片文件夹，自选图片时应当藏掉"
    # 素材行只留当前来源那一个按钮（互斥，另一个 pack_forget）
    _row = _find_label(p._pages["点击表情"], "图片素材")
    _row = _row.master
    _btns = {str(c.cget("text")): c for c in _row.winfo_children()
             if c.winfo_class() == "Button"}
    assert _btns["选图片"].winfo_ismapped(), "自选图片时「选图片」应显示"
    assert not _btns["选文件夹"].winfo_ismapped(), \
        "自选图片时「选文件夹」应藏掉"
    app.cfg["click_emoji_source"] = "图片文件夹"
    p._sync_from_cfg()
    p.root.update_idletasks()
    assert _btns["选文件夹"].winfo_ismapped(), "文件夹时「选文件夹」应显示"
    assert not _btns["选图片"].winfo_ismapped(), "文件夹时「选图片」应藏掉"
    app.cfg["click_emoji_source"] = "内置图案"
    p._sync_from_cfg()

    # 8b) 来源置顶 + 文字页语音包前置：卡片里的**行顺序**必须按登记的
    #     pack 顺序读（行是 Frame 包着 Label，直接翻控件树拿不到顺序）
    def _row_texts(page, n=3):
        p._show_page(page)
        p.root.update_idletasks()
        # 页面的第一个子控件是页头，卡片是**登记过行**的那个 Frame
        cards = [c for c in p._pages[page].winfo_children()
                 if c in p._card_rows]
        assert cards, f"{page} 没有任何登记过行的卡片"
        rows = p._card_rows[cards[0]]
        out = []
        for r in rows:
            lbs = _walk_labels(r["w"])
            if lbs:
                out.append(str(lbs[0].cget("text")))
            if len(out) >= n:
                break
        return out

    assert _row_texts("点击表情", 2) == ["启用点击表情", "图案来源"], \
        f"点击表情前两行顺序不对：{_row_texts('点击表情', 2)}"
    assert _row_texts("点击文字", 2) == ["启用点击文字",
                                        "文字（一行一个，点击时轮换取下一个）"], \
        f"点击文字前两行顺序不对：{_row_texts('点击文字', 2)}"
    assert _row_texts("点击音效", 2) == ["启用点击音效", "音效来源"], \
        f"点击音效前两行顺序不对：{_row_texts('点击音效', 2)}"
    # 语音设置已全部并入「点击音效」：这两个页面里不该再有语音相关的行
    for _pg in ("点击表情", "点击文字"):
        _cards = [c for c in p._pages[_pg].winfo_children()
                  if c in p._card_rows]
        _all = " ".join(str(l.cget("text"))
                        for _c in _cards for r in p._card_rows[_c]
                        for l in _walk_labels(r["w"]))
        for _gone in ("语音文件夹", "语音包文件夹", "点击时播放语音",
                      "语音与文字统一"):
            assert _gone not in _all, f"{_pg} 页仍留着「{_gone}」这一行"
    # 「语音与文字统一」只在「点击音效」页、且只在音效文件夹那一档下出现
    app.cfg["click_sfx_source"] = "内置音效"
    p._sync_from_cfg()
    assert not _shown("点击音效", "语音与文字统一"), \
        "来源不是音效文件夹时「语音与文字统一」也该藏掉"
    app.cfg["click_sfx_source"] = "音效文件夹"
    p._sync_from_cfg()
    assert _shown("点击音效", "语音与文字统一"), \
        "音效文件夹那一档应当显示「语音与文字统一」"
    app.cfg["click_sfx_source"] = "内置音效"
    p._sync_from_cfg()

    # 8c) 悬浮提示：停在名称上 0.4 秒弹出，离开即收；行被藏起来时不弹
    p._show_page("点击表情")
    p.root.update_idletasks()
    _lb = _find_label(p._pages["点击表情"], "数量")
    assert _lb is not None
    _lb.event_generate("<Enter>")
    _t0 = time.time()
    _up = False
    while time.time() - _t0 < 2.0:
        p.root.update()
        if p._tip_win is not None and p._tip_win.winfo_ismapped():
            _up = True
            break
        time.sleep(0.02)
    assert _up, "鼠标停在配置项名称上 0.4 秒后应当弹出提示"
    assert "一次点击炸出多少颗" in p._tip_lb.cget("text"), \
        p._tip_lb.cget("text")
    _lb.event_generate("<Leave>")
    p.root.update()
    assert not p._tip_win.winfo_ismapped(), "离开后提示应当收起"
    # 8c2) 提示浮层必须**压在面板之上**，而且面板每 1 秒抢一次置顶位之后仍然
    #      压得住 —— 用户报的就是"提示一闪就沉到窗口下层看不见了"。
    #      只读窗口样式/可见性查不出这件事（窗口状态全正常），所以直接查 z 序：
    #      从面板往上走（GW_HWNDPREV）应当能碰到提示窗。
    _lb.event_generate("<Enter>")
    _t0 = time.time()
    while time.time() - _t0 < 2.0:
        p.root.update()
        if p._tip_win is not None and p._tip_win.winfo_ismapped():
            break
        time.sleep(0.02)
    from _win32 import hwnd_int, user32, HWND, top_hwnd
    _tip_h = top_hwnd(p._tip_win)
    assert user32.GetWindowLongPtrW(HWND(_tip_h), -8) == p._hwnd(), \
        ("提示浮层没有成为面板的从属窗口 —— 面板抢置顶位时会把它挤下去"
         f"（owner={user32.GetWindowLongPtrW(HWND(_tip_h), -8)}，"
         f"面板={p._hwnd()}）")
    p._raise_top()          # 面板抢一次置顶位（每 1 秒都会发生）
    p.root.update()
    GW_HWNDPREV = 3
    _above = []
    _cur = p._hwnd()
    for _ in range(40):
        _cur = hwnd_int(user32.GetWindow(HWND(_cur), GW_HWNDPREV))
        if not _cur:
            break
        _above.append(_cur)
    assert _tip_h in _above, (
        f"面板抢完置顶位后提示被压到下面（上面只有 {_above[:4]}…，"
        f"提示 hwnd={_tip_h}）")
    assert p._tip_win.winfo_ismapped(), "抢位之后提示仍然可见"
    p._tip_hide()
    # 行藏了（来源=自选图片）时不该弹：否则提示会漂在页面上没有主人
    app.cfg["click_emoji_source"] = "自选图片"
    p._sync_from_cfg()
    p.root.update_idletasks()
    _lb2 = _find_label(p._pages["点击表情"], "图案")
    assert _lb2 is not None and not _lb2.winfo_ismapped()
    p._tip_show(_lb2, "这一行正藏着，不该弹")
    p.root.update()
    assert not p._tip_win.winfo_ismapped(), "行被藏起来时不该弹提示"
    app.cfg["click_emoji_source"] = "内置图案"
    p._sync_from_cfg()

    # 8d) 侧栏分组可折叠 + 滚轮可滚 + 悬浮操作条永远在窗口内
    _grp = "鼠标拖动特效"
    assert p._nav_groups[_grp]["open"] is True
    _items_before = [n for n in ("聚光灯", "颜料盘", "宠物跟随", "涟漪", "真实水波")
                     if p._nav[n]["frame"].winfo_ismapped()]
    assert len(_items_before) == 5, _items_before
    p._toggle_nav_group(_grp)
    p.root.update_idletasks()
    assert p._nav_groups[_grp]["open"] is False
    assert "▶" in p._nav_groups[_grp]["label"].cget("text"), \
        p._nav_groups[_grp]["label"].cget("text")
    for n in ("聚光灯", "颜料盘", "宠物跟随", "涟漪", "真实水波"):
        assert not p._nav[n]["frame"].winfo_ismapped(), f"折叠后 {n} 应藏起来"
    assert _grp in p._ui.get("nav_collapsed", []), "折叠状态应落盘"
    p._toggle_nav_group(_grp)
    p.root.update_idletasks()
    assert all(p._nav[n]["frame"].winfo_ismapped()
               for n in ("聚光灯", "颜料盘", "宠物跟随", "涟漪", "真实水波")), \
        "再点一次应整组回来"
    assert p._ui.get("nav_collapsed") == [], "展开后落盘应清空"

    # 活动页所在分组被折叠时，_show_page 要自动展开（重启恢复上次页会踩到）
    p._toggle_nav_group(_grp)
    p._show_page("宠物跟随")
    p.root.update_idletasks()
    assert p._nav_groups[_grp]["open"] is True, "活动页的分组应被自动展开"
    assert p._nav["宠物跟随"]["frame"].winfo_ismapped()

    # 压矮窗口：侧栏要能滚（否则下面的组永远点不到）
    p.root.geometry(f"{p.W}x{p.H_MIN}")
    p.root.update()
    p.root.update_idletasks()
    assert p._nav_need_scroll(), "压矮后侧栏应当需要滚动"
    _before = p._nav_cv.yview()[0]
    p._nav["真实水波"]["frame"].event_generate("<MouseWheel>", delta=-120)
    p.root.update()
    assert p._nav_cv.yview()[0] > _before, "侧栏滚轮应当生效"
    # 滚到底后最后一项必须整个落在悬浮操作条**上方**（条子压在侧栏左下角，
    # 不预留的话「其它设置」正好躲到它后面 —— 看得见标题点不着）
    for _ in range(40):
        p._nav["其它"]["frame"].event_generate("<MouseWheel>", delta=-120)
        p.root.update()
    _last = p._nav["其它"]["frame"]
    assert p._nav_cv.yview()[0] > 0.5, \
        f"侧栏滚不到底：yview={p._nav_cv.yview()}"
    # 坐标系必须换算到**屏幕**：导航项在 canvas 里，winfo_y() 是相对 inner 的，
    # 与 fab.winfo_y()（相对 root）不是一个坐标系，直接比会得到荒谬的差值。
    _last_bottom = _last.winfo_rooty() + _last.winfo_height()
    assert _last_bottom <= p._fab.winfo_rooty() + 1, \
        (f"滚到底后最后一项仍被操作条压住：项底 {_last_bottom} > "
         f"条顶 {p._fab.winfo_rooty()}")
    # 侧栏滚轮不能把内容区一起滚走（bind_all 上只挂了一个分派器）
    _cb = p._canvas.yview()[0]
    p._nav["真实水波"]["frame"].event_generate("<MouseWheel>", delta=-120)
    p.root.update()
    assert p._canvas.yview()[0] == _cb, "侧栏滚轮不应带动内容区"

    # 悬浮操作条：最小尺寸下仍在窗口内、贴右下角、与内容区同宽、且不盖住缩放手柄
    p._set_size(win_w=p.W_MIN, win_h=p.H_MIN)
    p.root.update()
    fx, fy = p._fab.winfo_x(), p._fab.winfo_y()
    fw, fh = p._fab.winfo_width(), p._fab.winfo_height()
    assert p._fab.winfo_manager() == "place", p._fab.winfo_manager()
    assert p._fab.place_info().get("anchor") == "se", \
        f"操作条应贴在右下角：{p._fab.place_info()}"
    assert 0 <= fx and 0 <= fy and fx + fw <= p.W + 1 and fy + fh <= p.H + 1, \
        f"操作条越界：{fx},{fy} {fw}x{fh} 窗口 {p.W}x{p.H}"
    # 宽度 = 内容区（窗口 − 侧栏 120），且与实际内容区宽度一致
    assert fw == p.W - 120, \
        f"操作条宽 {fw} 与内容区 {p.W - 120} 不同宽（拖宽窗口后要跟着变）"
    _cont = p._inner.master
    assert abs(int(_cont.winfo_width()) - fw) <= 1, \
        f"内容区宽 {_cont.winfo_width()} 与操作条 {fw} 不一致"
    assert fy + fh <= p.H - p.GRIP + 1, \
        f"操作条压住了底边手柄：底 {fy + fh} > {p.H - p.GRIP}"
    assert fx + fw <= p.W - p.GRIP_CORNER + 1, \
        f"操作条压住了右下角手柄：右 {fx + fw} > {p.W - p.GRIP_CORNER}"
    # 两行布局：暂停独占一行，下面「隐藏到托盘 / 退出」并排
    assert p.pause_btn.winfo_ismapped(), "暂停按钮应可见"
    assert str(p.pause_btn.cget("state")) == "normal"
    _fab_kids = [k for k in p._fab.winfo_children() if k.winfo_class() == "Frame"]
    assert len(_fab_kids) == 1, f"第二行容器不对：{_fab_kids}"
    assert int(_fab_kids[0].winfo_y()) > int(p.pause_btn.winfo_y()), \
        "「隐藏到托盘 / 退出」那一行应当在暂停按钮**下面**"
    # 拖宽窗口后宽度必须跟着内容区走
    p._set_size(win_w=900, win_h=560)
    p.root.update()
    assert int(p._fab.winfo_width()) == 900 - 120, \
        f"拖宽后操作条宽 {p._fab.winfo_width()}（应为 {900 - 120}）"
    p._fit_height()
    p.root.update_idletasks()

    # 8e) 「点击音效」页：来源三档的条件显隐 + 键都注册上了
    for k in ("click_sfx_enabled", "click_sfx_source", "click_sfx_builtin",
              "click_sfx_file", "click_sfx_dir", "click_sfx_keyboard"):
        assert k in p._ctrls, f"点击音效页缺少控件 {k}"
    assert p._ctrls["click_sfx_source"]["kind"] == "choices"
    assert p._ctrls["click_sfx_keyboard"]["kind"] == "toggle"
    assert p._ctrls["click_sfx_source"]["refresh"] is not None
    for k in ("click_sfx_file", "click_sfx_dir"):
        assert p._ctrls[k]["kind"] == "path", (k, p._ctrls[k]["kind"])
        assert p._ctrls[k]["label"] in p._hints, f"{k} 的路径行没注册折行宽度"
    app.cfg["click_sfx_source"] = "内置音效"
    p._sync_from_cfg()
    p.root.update_idletasks()
    assert _shown("点击音效", "内置音效"), "内置档应当显示「内置音效」选择行"
    assert not _shown("点击音效", "音效素材"), "内置档不该显示素材行"
    app.cfg["click_sfx_source"] = "单个音效"
    p._sync_from_cfg()
    p.root.update_idletasks()
    assert _shown("点击音效", "音效素材"), "单个音效档应当显示素材行"
    assert not _shown("点击音效", "内置音效"), "单个音效档不该显示内置选择行"
    app.cfg["click_sfx_source"] = "音效文件夹"
    p._sync_from_cfg()
    p.root.update_idletasks()
    assert _shown("点击音效", "音效素材"), "文件夹档应当显示素材行"
    app.cfg["click_sfx_source"] = "内置音效"
    p._sync_from_cfg()
    # 三个来源都要有对应的配置键占位（键名与 fx_effects 的实现一致）
    for v in fx_effects.SFX_SOURCES:
        assert v in fx_effects.SFX_SOURCES
    assert fx_effects.builtin_sfx_names(), "内置音效扫不到（assets/sfx 没打包？）"

    # 8f) 配置项名称上挂了提示：随便抽查几个跨页面的项
    _tip_hosts = 0
    for page in ("下雨", "点击表情", "点击音效", "其它"):
        for c in _walk_labels(p._pages[page]):
            if "all" in c.bindtags():
                _tip_hosts += 1
    # 提示是绑在控件上的（<Enter> + <Leave>），Tk 里 listbox 会列出来
    _probe = _find_label(p._pages["其它"], "帧率上限")
    assert _probe is not None, "其它页找不到「帧率上限」"
    _bound = p.root.tk.call("bind", str(_probe), "<Enter>")
    assert _bound, "「帧率上限」的名称上没有挂提示（<Enter> 无绑定）"
    _bound2 = p.root.tk.call("bind", str(_probe), "<Leave>")
    assert _bound2, "「帧率上限」的名称上没有挂收起提示的绑定（<Leave>）"

    # 8g) 套用预设**不该**触发条件行重排（那正是"点预设时整页闪一下"的根因）
    _repacked = []
    _orig_repack = fx_ui.ControlPanel._repack

    def _spy_repack(self, entries):
        _repacked.append(len(entries))
        return _orig_repack(self, entries)

    app.cfg["click_emoji_source"] = "图片文件夹"
    p._sync_from_cfg()
    p._show_page("点击表情")
    p.root.update_idletasks()
    _h_before = int(p._pages["点击表情"].winfo_reqheight())
    fx_ui.ControlPanel._repack = _spy_repack
    try:
        for _n in ("爱心雨", "彩带庆祝", "音符跳动"):
            p._preset(_n)
            p.root.update_idletasks()
    finally:
        fx_ui.ControlPanel._repack = _orig_repack
    assert not _repacked, \
        f"套用预设触发了 {len(_repacked)} 次行重排（预设会闪烁）"
    assert int(p._pages["点击表情"].winfo_reqheight()) == _h_before, \
        "套用预设不该改变页面高度（变了窗口就会跟着抖）"
    assert app.cfg["click_emoji_source"] == "图片文件夹", \
        f"套用预设把来源改成了 {app.cfg['click_emoji_source']}"
    # 但来源真的变了仍然要重排（短路不能把正常显隐也吃掉）
    app.cfg["click_emoji_source"] = "内置图案"
    p._sync_from_cfg()
    p.root.update_idletasks()
    assert _shown("点击表情", "图案"), "改了来源之后「图案」行应当显示出来"

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
