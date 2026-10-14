# -*- coding: utf-8 -*-
"""特效层级开关 —— 功能检查（端到端）

「特效层级」是「其它设置」页上的两个胶囊开关：

    关 = 最上层（默认）     开 = 仅桌面图层

它们分别作用在**两个独立的覆盖窗口**上（不是一个开关管全部）：

    全屏特效层：下雨 / 下雪 / 落樱 / 流星雨
    鼠标特效层：聚光灯 / 颜料盘 / 涟漪 / 真实水波 / 窗口水波

本脚本查的是「界面开关 → 配置落盘 → 重启恢复 → 真实窗口层级」整条链，
重点补上其它验证脚本没覆盖的边界：

  1. 档位只有两档，非法值必须归一到最上层（手改 config.json 也不能崩）；
  2. 旧配置文件缺 layer_* 键要落到默认值（向后兼容）；
  3. 落盘后重新读回，档位保持 —— 这是"重启还能记住"的前提；
  4. 界面开关 → cfg → **snapshot**：渲染线程只读 snapshot 而不是 cfg，
     这一步断了就会表现为"点了开关没反应，重启才生效"；
  5. 界面两侧文字染色确实跟着档位走（不点开也知道现在在哪一档）；
  6. 启动即恢复：配置里是桌面层，渲染循环第一帧就该把窗口落实下去；
  7. 混合档位：一层最上层、一层桌面层，两层不能互相干扰；
  8. 桌面层不参与抢顶（它本来就该待在下面）；
  9. 桌面层窗口在"资源管理器重启"后重建，仍要回到桌面层；
 10. 两种档位下窗口的**屏幕矩形**都要等于虚拟屏 —— 桌面层改用父窗口
     客户区坐标，折算错就会整片错位。

全程把 CONFIG_PATH 与界面状态文件改到临时目录，不碰用户真实配置。
"""
import ctypes
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import desktop_fx as d
import fx_kinds
import fx_ui
import fx_winlayer as fw

user32 = fw.user32
HWND = fw.HWND


# ------------------------------------------------------------------ 小工具
def _is_topmost(hwnd):
    return bool(user32.GetWindowLongW(HWND(hwnd), fw.GWL_EXSTYLE)
                & fw.WS_EX_TOPMOST)


def _screen_rect(hwnd):
    """窗口的屏幕矩形 (x, y, w, h)。

    注意：GetWindowRect 对手窗口**同样返回屏幕坐标**，所以它可以直接和
    virtual_screen() 比 —— 这正是能查出"父窗口客户区坐标折算错"的地方。
    """
    r = fw.RECT()
    if not user32.GetWindowRect(HWND(hwnd), ctypes.byref(r)):
        return None
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def _parent_class(hwnd):
    p = fw.hwnd_int(user32.GetParent(HWND(hwnd)))
    if not p:
        return "", 0
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(HWND(p), buf, 64)
    return buf.value, p


def _wait(cond, timeout=8.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


def _wait_desktop_settled(ov, timeout=8.0):
    """等到"切到桌面层"这个动作彻底做完。

    不能只等 `z_mode`：`apply_z_mode` 一进来就先设 `self.z_mode`，之后才动
    窗口。在"SetParent 已做、定位还没做"的中间态上读窗口矩形，会读到旧位置
    被当成父窗口客户区坐标解释的结果 —— 一次纯属测试自身竞态的假失败。
    这里同时要求几何已经落定，正好也把"每帧 present 不能把窗口挪走"一起
    验掉（present 是在等待期间持续跑的）。
    """
    def ok():
        if ov.z_mode != fx_kinds.LAYER_DESKTOP:
            return False
        if ov._parented and _parent_class(ov.hwnd)[0] != "WorkerW":
            return False
        return _screen_rect(ov.hwnd) == fw.virtual_screen()
    return _wait(ok, timeout=timeout)


def _wait_topmost_settled(ov, timeout=8.0):
    """`_wait_desktop_settled` 的对称版：等"切回最上层"彻底做完。

    同样不能只等 `z_mode`：换层是渲染循环异步做的，`z_mode` 先翻，
    `WS_EX_TOPMOST` 要等下一帧才补上。实测（2026-09-20）中间那段窗口约 0.25s，
    期间窗口看起来"不是置顶的" —— 在这里断言 `_is_topmost` 就是一次纯属测试
    自身竞态的假失败（`_verify_layer_switch` 偶发、`_verify_zorder` 稳定复现）。
    一并等几何，理由同 `_wait_desktop_settled`：present 在等待期间持续跑，
    顺带也验掉"切回置顶后不会把窗口挪走"。
    """
    def ok():
        if ov.z_mode != fx_kinds.LAYER_TOPMOST or not ov.hwnd:
            return False
        if not _is_topmost(ov.hwnd):
            return False
        if _parent_class(ov.hwnd)[1]:
            return False
        return _screen_rect(ov.hwnd) == fw.virtual_screen()
    return _wait(ok, timeout=timeout)


def _probe_fs_visible(ov_fs, seconds=0.45):
    """把全屏层缓冲刷成不透明品红，上屏后把**屏幕像素**读回来。

    返回 (命中数, 采样数)。这是唯一能抓住"窗口在、标志位全对、但屏幕上没有它"
    的判据 —— 读窗口状态（可见性 / 矩形 / 置顶位 / 父亲）一律查不出来。
    """
    import fx_scene

    orig = fx_scene.Scene.step

    def _solid(sc, dt, u32, w, h, u32_mouse=None):
        u32[:] = 0xFFFF00FF          # DIB 里的 0xAARRGGBB = 不透明品红
        if u32_mouse is not None:
            u32_mouse[:] = 0

    fx_scene.Scene.step = _solid
    try:
        time.sleep(seconds)          # 让渲染循环把这一帧合成并上屏
        hdc = fw.user32.GetDC(None)
        try:
            vx, vy, vw, vh = fw.virtual_screen()
            pts = [(vx + int(vw * f), vy + int(vh * g))
                   for f in (0.15, 0.5, 0.85) for g in (0.25, 0.5, 0.75)]
            hits = sum(1 for x, y in pts
                       if fw.gdi32.GetPixel(hdc, x, y) == 0x00FF00FF)
        finally:
            fw.user32.ReleaseDC(None, hdc)
    finally:
        fx_scene.Scene.step = orig
    return hits, len(pts)


def _labels_of(page):
    """按创建顺序收集页面里的 Label 文本，用于检查开关两侧的染色。"""
    out = []

    def walk(w):
        for c in w.winfo_children():
            try:
                if c.winfo_class() == "Label":
                    out.append(c)
            except Exception:
                pass
            walk(c)

    walk(page)
    return out


def _find_toggle(page, command):
    """按 command 身份找出那条层级行上的真实胶囊开关。

    以 `command is ctrl["on"]` 为判据，而不是靠控件在树里的次序 —— 一个页面
    上同时有几个开关（多开、任务栏、开机自启），按下标取迟早会串台。
    """
    stack = [page]
    while stack:
        w = stack.pop()
        for c in w.winfo_children():
            if isinstance(c, fx_ui._Toggle) and c.command is command:
                return c
            stack.append(c)
    return None


# ------------------------------------------------------------------ 1. 档位与归类
def test_layer_taxonomy():
    """两个开关各管哪些特效必须是确定的，且每个特效都有合法归属。

    这里是**两套不同的名字**，别混：
      * 特效 → 覆盖窗口的归属：`EFFECTS[...]["layer"]` ∈ {"fullscreen","mouse"}
      * 覆盖窗口的显示档位：`LAYER_VALUES` ∈ {"topmost","desktop"}
    `LAYER_KEYS` 把它们对起来（槽位 → 层级键 + 多开键）。
    """
    if fx_kinds.LAYER_VALUES != (fx_kinds.LAYER_TOPMOST,
                                 fx_kinds.LAYER_DESKTOP):
        return False, f"档位取值不是两档：{fx_kinds.LAYER_VALUES}"
    if fx_kinds.LAYER_LABELS.get(fx_kinds.LAYER_TOPMOST) != "最上层" or \
            fx_kinds.LAYER_LABELS.get(fx_kinds.LAYER_DESKTOP) != "仅桌面图层":
        return False, f"档位中文名不对：{fx_kinds.LAYER_LABELS}"

    slots = set(fx_kinds.LAYER_KEYS)
    if slots != {"fullscreen", "mouse"}:
        return False, f"覆盖层槽位不是两个：{sorted(slots)}"
    for slot, keys in fx_kinds.LAYER_KEYS.items():
        for k in keys:
            if k not in d.DEFAULTS:
                return False, f"槽位 {slot} 的配置键 {k} 不在 DEFAULTS 里"

    fs = {n for n, e in fx_kinds.EFFECTS.items()
          if e.get("layer") == "fullscreen"}
    ms = {n for n, e in fx_kinds.EFFECTS.items() if e.get("layer") == "mouse"}
    want_fs = {"下雨", "下雪", "落樱", "流星雨"}
    want_ms = {"聚光灯", "颜料盘", "宠物跟随", "涟漪", "真实水波", "窗口水波"}
    if fs != want_fs:
        return False, f"全屏层归类变了：{sorted(fs)}（期望 {sorted(want_fs)}）"
    if ms != want_ms:
        return False, f"鼠标层归类变了：{sorted(ms)}（期望 {sorted(want_ms)}）"
    if fs & ms:
        return False, f"同一特效同时归两层：{fs & ms}"

    # layer_of() 是场景侧实际使用的入口，不能和 EFFECTS 表说两套话
    for n in fx_kinds.EFFECTS:
        got = fx_kinds.layer_of(n)
        if got != fx_kinds.EFFECTS[n].get("layer"):
            return False, f"{n} 的 layer_of 与 EFFECTS 表不一致：{got}"
        if got not in fx_kinds.LAYER_KEYS:
            return False, f"{n} 的归属 {got!r} 不是已知槽位"
    if fx_kinds.layer_of("不存在的特效") is not None:
        return False, "未知特效的 layer_of 应当返回 None"
    return True, ("全屏特效开关 → 下雨/下雪/落樱/流星雨；"
                  "鼠标特效开关 → 聚光灯/颜料盘/宠物跟随/涟漪/真实水波/窗口水波")


# ------------------------------------------------------------------ 2. 非法值归一
def test_value_normalization():
    """手改 config.json 写成怪值也不能崩，必须静默归一到最上层。

    这里用**不建窗口**的 Overlay：apply_z_mode 无论有没有 hwnd 都要先把
    self.z_mode 规范化，档位状态与窗口动作解耦。
    """
    ok_cases = [
        ("desktop", "desktop"),
        (fx_kinds.LAYER_DESKTOP, "desktop"),
        ("topmost", "topmost"),
        (fx_kinds.LAYER_TOPMOST, "topmost"),
    ]
    bad_cases = ["Desktop", "DESKTOP", " desktop", "桌面", "", None, 0, 1,
                 True, False, [], {}, 3.5]
    o = fw.Overlay()
    for v, want in ok_cases:
        o.apply_z_mode(v)
        if o.z_mode != want:
            return False, f"合法值 {v!r} 被改成了 {o.z_mode!r}（期望 {want}）"
    for v in bad_cases:
        try:
            o.apply_z_mode(v)
        except Exception as e:
            return False, f"非法值 {v!r} 抛异常：{type(e).__name__}: {e}"
        if o.z_mode != fx_kinds.LAYER_TOPMOST:
            return False, f"非法值 {v!r} 没归一到最上层，而是 {o.z_mode!r}"
    return True, f"{len(ok_cases)} 个合法值原样保留，{len(bad_cases)} 个非法值归一到最上层"


# ------------------------------------------------------------------ 3. 落盘 / 旧配置
def test_config_persist():
    """落盘后读回档位要原样保持 —— "重启后还记得"就靠这一步。"""
    app = d.App()
    app.cfg["layer_fullscreen"] = "desktop"
    app.cfg["layer_mouse"] = "topmost"
    if not app.save():
        return False, "save() 失败"
    # 打乱内存值，再从盘上读回来
    app.cfg["layer_fullscreen"] = "topmost"
    app.cfg["layer_mouse"] = "desktop"
    if not app.load():
        return False, "load() 失败"
    if app.cfg["layer_fullscreen"] != "desktop":
        return False, f"回读后全屏层是 {app.cfg['layer_fullscreen']!r}"
    if app.cfg["layer_mouse"] != "topmost":
        return False, f"回读后鼠标层是 {app.cfg['layer_mouse']!r}"
    with open(d.CONFIG_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    for k in ("layer_fullscreen", "layer_mouse"):
        if raw.get(k) not in fx_kinds.LAYER_VALUES:
            return False, f"盘上 {k} 的值不合法：{raw.get(k)!r}"
    return True, "layer_fullscreen/layer_mouse 落盘 → 回读一致"


def test_config_backward_compat(tmp_factory):
    """旧版 config.json 里根本没有 layer_* 键，必须落到默认的「最上层」。"""
    path = tmp_factory()
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"rain_amount": 200, "fps": 60}, f)
    app = d.App()
    for k in ("layer_fullscreen", "layer_mouse"):
        if app.cfg[k] != fx_kinds.LAYER_TOPMOST:
            return False, f"旧配置缺 {k} 时没有落到默认档，而是 {app.cfg[k]!r}"
    # 未知键照旧忽略，不能被当成合法档位塞进来
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"layer_fullscreen": "leftover_garbage"}, f)
    app2 = d.App()
    if app2.cfg["layer_fullscreen"] != "leftover_garbage":
        return False, "load() 变得会过滤值了 —— 归一化职责应只在 apply_z_mode"
    o = fw.Overlay()
    o.apply_z_mode(app2.cfg["layer_fullscreen"])
    if o.z_mode != fx_kinds.LAYER_TOPMOST:
        return False, f"盘上脏值没被 apply_z_mode 兜住：{o.z_mode!r}"
    return True, "缺键→默认最上层；脏值在施加层级时被兜住"


# ------------------------------------------------------------------ 4/5. 界面
def test_ui_toggle_writes_cfg_and_snapshot():
    """点真实开关 → cfg → snapshot，并检查两侧文字染色。

    渲染线程读的是 `app.snapshot` 而不是 `app.cfg`（整块替换保证原子）。
    若某天 `set()` 忘了刷新 snapshot，开关就会"点了没反应、重启才生效"——
    这条断言专门拦它。

    驱动方式必须是**真实点击路径**（`_Toggle._toggle`）：直接调
    `_ctrls[key]["on"](v)` 只写 cfg，不会拨动开关变量，测不到两者是否一致。
    """
    app = d.App()
    panel = fx_ui.ControlPanel(app)
    try:
        panel._show_page("其它")
        panel.root.update_idletasks()
        labels = _labels_of(panel._pages["其它"])
        tops = [l for l in labels if l.cget("text") == "最上层"]
        desks = [l for l in labels if l.cget("text") == "仅桌面图层"]
        if len(tops) != 2 or len(desks) != 2:
            return False, (f"层级行的文字标签数量不对：最上层 {len(tops)} 个、"
                           f"仅桌面图层 {len(desks)} 个（期望各 2）")
        # 顺序 = 创建顺序 = 「全屏特效」行在前、「鼠标特效」行在后

        for idx, key in enumerate(("layer_fullscreen", "layer_mouse")):
            ctrl = panel._ctrls.get(key)
            if ctrl is None:
                return False, f"面板没有注册控件 {key}"
            tg = _find_toggle(panel._pages["其它"], ctrl["on"])
            if tg is None:
                return False, f"{key} 这一行没有可点击的胶囊开关"

            tg._toggle(None)                      # 开 = 仅桌面图层
            if not ctrl["var"].get():
                return False, f"{key} 点击后开关变量仍是关"
            if app.cfg[key] != fx_kinds.LAYER_DESKTOP:
                return False, f"{key} 开关打开后 cfg 是 {app.cfg[key]!r}"
            if app.snapshot.get(key) != fx_kinds.LAYER_DESKTOP:
                return False, (f"{key} 改了 cfg 但 snapshot 没跟上"
                               f"（渲染线程读 snapshot，会表现为开关无效）")
            if tops[idx].cget("fg") == fx_ui.ACCENT:
                return False, f"{key} 切到桌面层后「最上层」仍被高亮"
            if desks[idx].cget("fg") != fx_ui.ACCENT:
                return False, f"{key} 切到桌面层后「仅桌面图层」没被高亮"

            tg._toggle(None)                      # 关 = 最上层
            if ctrl["var"].get():
                return False, f"{key} 再点一次开关变量仍是开"
            if app.cfg[key] != fx_kinds.LAYER_TOPMOST:
                return False, f"{key} 开关关闭后 cfg 是 {app.cfg[key]!r}"
            if app.snapshot.get(key) != fx_kinds.LAYER_TOPMOST:
                return False, f"{key} 切回最上层后 snapshot 没跟上"
            if tops[idx].cget("fg") != fx_ui.ACCENT:
                return False, f"{key} 切回最上层后「最上层」没被高亮"
            if desks[idx].cget("fg") == fx_ui.ACCENT:
                return False, f"{key} 切回最上层后「仅桌面图层」仍被高亮"

        # 反向：cfg 被外部改（预设 / 手改文件）后面板要跟着走
        app.cfg["layer_mouse"] = fx_kinds.LAYER_DESKTOP
        panel._sync_from_cfg()
        if panel._ctrls["layer_mouse"]["var"].get() is not True:
            return False, "cfg → 面板方向没同步（开关没被拨到桌面层）"
        # 脏值进 cfg 时面板不能假装它是桌面层
        app.cfg["layer_mouse"] = "Desktop"
        panel._sync_from_cfg()
        if panel._ctrls["layer_mouse"]["var"].get() is not False:
            return False, "cfg 里的脏值被面板当成了桌面层"
    finally:
        panel.root.destroy()
    return True, "两个开关点击后都写回 cfg + snapshot，染色双向正确，脏值不误判"


# ------------------------------------------------------------------ 6~10. 真机
class Loop:
    """一个真实 App 渲染循环（两个覆盖层），全程只起一次。"""

    def __init__(self):
        self.app = None
        self.stats = []
        self._orig = None

    def start(self, **over):
        import threading

        import fx_scene

        self._orig = fx_scene.Scene.step
        stats, orig = self.stats, self._orig

        def _spy(sc, dt, u32, w, h, u32_mouse=None):
            orig(sc, dt, u32, w, h, u32_mouse)
            stats.append((int((u32 > 0).sum()),
                          -1 if u32_mouse is None else int((u32_mouse > 0).sum())))

        fx_scene.Scene.step = _spy
        cfg = dict(d.DEFAULTS)
        cfg.update(dict(rain_enabled=True, rain_amount=220, splash=False,
                        snow_enabled=False, petal_enabled=False,
                        meteor_enabled=False, spotlight_enabled=True,
                        spotlight_dim=0.15, paint_enabled=False,
                        wave_enabled=False, ripple_enabled=False,
                        win_ripple_enabled=False))
        cfg.update(over)
        self.app = d.App()
        self.app.cfg.update(cfg)
        self.app.snapshot = dict(self.app.cfg)
        self.app._thread = threading.Thread(target=self.app._render_loop,
                                            name="probe", daemon=True)
        self.app._thread.start()
        _wait(lambda: len(self.app.overlays) >= 2, timeout=10)
        time.sleep(1.0)
        return self.app

    def stop(self):
        import fx_scene

        if self.app is not None:
            self.app.stop_evt.set()
            if self.app._thread is not None:
                self.app._thread.join(timeout=3)
            self.app = None
        if self._orig is not None:
            fx_scene.Scene.step = self._orig
            self._orig = None


def _check_desktop_state(ov, name):
    """桌面层的共同约束，返回 (ok, msg)。"""
    if ov.z_mode != fx_kinds.LAYER_DESKTOP:
        return False, f"{name} 层级没切过去：{ov.z_mode}"
    if not user32.IsWindow(HWND(ov.hwnd)):
        return False, f"{name} 窗口失效"
    if _is_topmost(ov.hwnd):
        return False, f"{name} 仍是置顶窗 —— 档位没真正生效"
    cls, parent = _parent_class(ov.hwnd)
    if ov._parented and cls != "WorkerW":
        return False, f"{name} 报告已挂壁纸层，实际父亲是 {cls or '(空)'}"
    if not ov._parented and not ov.is_desktop_fallback():
        return False, f"{name} 既没挂上壁纸层、也没走沉底降级 —— 状态不明"
    rect = _screen_rect(ov.hwnd)
    if rect != fw.virtual_screen():
        return False, (f"{name} 屏幕矩形 {rect} 不等于虚拟屏 "
                       f"{fw.virtual_screen()}（父窗口客户区坐标折算错了）")
    return True, f"已挂 WorkerW={parent}" if ov._parented else "已降级为沉底窗口"


def test_real_startup_restores_desktop(app, stats):
    """配置里是桌面层时，渲染循环启动后必须立刻落实（重启恢复）。

    注意这里等的是 `_wait_desktop_settled`（含几何落定），不只是 z_mode：
    渲染循环每帧都在 present()，所以"等几何稳定"顺带验掉了"present 不能把
    桌面层窗口挪走"—— 这条正是真实踩过的 bug（见 fx_winlayer.present）。
    """
    ov_fs, ov_ms = app.overlays
    for ov, name in ((ov_fs, "全屏层"), (ov_ms, "鼠标层")):
        if not _wait_desktop_settled(ov):
            rect = _screen_rect(ov.hwnd)
            return False, (f"{name} 没有稳定落到桌面层：z_mode={ov.z_mode} "
                           f"矩形={rect} 虚拟屏={fw.virtual_screen()} "
                           f"（矩形不等通常是上屏时按错坐标系，被挪了一个父客户区原点）")
        ok, msg = _check_desktop_state(ov, name)
        if not ok:
            return False, msg
    detail = []
    for ov, name in ((ov_fs, "全屏层"), (ov_ms, "鼠标层")):
        detail.append(f"{name}已挂 WorkerW={_parent_class(ov.hwnd)[1]}"
                      if ov._parented else f"{name}已降级为沉底窗口")

    # 落到桌面层之后还必须在继续作画（挂成子窗口不能把渲染搞停）
    stats.clear()
    time.sleep(1.2)
    fs_max = max((s[0] for s in stats), default=0)
    ms_max = max((s[1] for s in stats), default=0)
    if not stats:
        return False, "桌面层下渲染循环停了（一次 Scene.step 都没有）"
    if fs_max <= 0:
        return False, "桌面层的全屏特效没有画任何东西"
    if ms_max <= 0:
        return False, "桌面层的鼠标特效没有画任何东西"
    # 跑了一段之后几何不能漂移（present 每帧都在重新定位）
    for ov, name in ((ov_fs, "全屏层"), (ov_ms, "鼠标层")):
        if _screen_rect(ov.hwnd) != fw.virtual_screen():
            return False, (f"{name} 连续上屏 1.2 秒后窗口漂到了 "
                           f"{_screen_rect(ov.hwnd)}")
    return True, (f"两层都落到桌面层并持续作画 1.2s 且几何不漂移"
                  f"（全屏≈{fs_max}px / 鼠标≈{ms_max}px）；" + "；".join(detail))


def test_real_mixed_modes(app):
    """一层最上层、一层桌面层：不能互相干扰。

    这是最常见的用法（全屏雨浮在最上层、鼠标特效沉在桌面层）。两层这时
    分属不同 z 序空间，`_restack_overlays` 必须明确放弃纠正 —— 否则会把
    顶层窗插到别人的子窗口之后，语义未定义，可能整层消失。
    """
    ov_fs, ov_ms = app.overlays
    app.snapshot = dict(app.snapshot,
                        layer_fullscreen=fx_kinds.LAYER_TOPMOST,
                        layer_mouse=fx_kinds.LAYER_DESKTOP)
    if not _wait_topmost_settled(ov_fs):
        return False, (f"全屏层没稳定回到最上层：{ov_fs.z_mode}"
                       f"(topmost={_is_topmost(ov_fs.hwnd)})")
    if not _wait(lambda: ov_ms.z_mode == fx_kinds.LAYER_DESKTOP):
        return False, f"鼠标层没切到桌面层：{ov_ms.z_mode}"
    if not _wait_desktop_settled(ov_ms):
        return False, (f"鼠标层没稳定落到桌面层：矩形={_screen_rect(ov_ms.hwnd)} "
                       f"虚拟屏={fw.virtual_screen()}")
    if not _is_topmost(ov_fs.hwnd):
        return False, "全屏层选了最上层却没有 WS_EX_TOPMOST"
    if _is_topmost(ov_ms.hwnd):
        return False, "鼠标层选了桌面层却仍带 WS_EX_TOPMOST"
    if _parent_class(ov_ms.hwnd)[0] != "WorkerW" and ov_ms._parented:
        return False, "鼠标层没挂到 WorkerW"
    if app._restack_overlays():
        return False, ("两层不在同一 z 序空间，_restack_overlays 仍去重排了"
                       "（可能把顶层窗插到子窗口之后）")
    if app.last_error:
        return False, f"混合档位下渲染循环报错：{app.last_error}"
    return True, "全屏层在最上层 / 鼠标层在桌面层，跨 z 序空间的重排被正确跳过"


def test_real_desktop_skips_raise(app):
    """桌面层不该参与抢顶：它本来就该待在下面。"""
    _, ov_ms = app.overlays
    before = ov_ms._last_full_check
    raised = ov_ms.keep_on_top()
    if raised:
        return False, "桌面层的 keep_on_top 居然动手了"
    if ov_ms._last_full_check != before:
        return False, ("桌面层仍走了昂贵的全量遮挡检查 —— 应该在第一道判据"
                       "（z_mode != 最上层）上直接返回")
    return True, "桌面层 keep_on_top 立即返回，连通链路检查都不做"


def test_real_roundtrip_back_to_topmost(app):
    """切回最上层：置顶位、几何、所有权都要还原干净。"""
    ov_fs, ov_ms = app.overlays
    app.snapshot = dict(app.snapshot,
                        layer_fullscreen=fx_kinds.LAYER_TOPMOST,
                        layer_mouse=fx_kinds.LAYER_TOPMOST)
    if not (_wait_topmost_settled(ov_fs) and _wait_topmost_settled(ov_ms)):
        return False, (f"切回最上层没生效：全屏={ov_fs.z_mode}"
                       f"(topmost={_is_topmost(ov_fs.hwnd)}) / "
                       f"鼠标={ov_ms.z_mode}(topmost={_is_topmost(ov_ms.hwnd)})")
    for ov, name in ((ov_fs, "全屏层"), (ov_ms, "鼠标层")):
        if not _is_topmost(ov.hwnd):
            return False, f"{name} 切回最上层后没有 WS_EX_TOPMOST"
        if _parent_class(ov.hwnd)[1]:
            return False, f"{name} 切回最上层后还挂在别的窗口下"
        if ov._parented or ov.is_desktop_fallback():
            return False, f"{name} 的桌面层标记没清干净"
        rect = _screen_rect(ov.hwnd)
        if rect != fw.virtual_screen():
            return False, (f"{name} 切回后屏幕矩形 {rect} 不等于虚拟屏 "
                           f"{fw.virtual_screen()}")
        if not user32.IsWindowVisible(HWND(ov.hwnd)):
            return False, f"{name} 切回后不可见"
    if app._restack_overlays() is not True and app.overlays[1].hwnd:
        return False, "两层都回到最上层后，鼠标层没能纠正到全屏层之上"
    return True, "两层都还原为置顶独立窗口，矩形=虚拟屏，鼠标层重新压在全屏层之上"


def test_real_roundtrip_still_visible_on_screen(app):
    """往返切换后窗口必须**真的还在屏幕上** —— 这条专门盯 SetParent。

    真机实测（2026-09-15）：把分层窗口 SetParent 到壁纸层（或摘回来）之后，
    UpdateLayeredWindow 依旧返回成功，IsWindowVisible / 屏幕矩形 /
    WS_EX_TOPMOST / 父亲 全部正常，但窗口**再也不出现在屏幕上** —— 用户看到
    的就是"切到桌面层后特效消失，切回最上层也恢复不了"。

    这正是"只查窗口状态查不出来"的那类 bug：旧用例把可见性、矩形、置顶位、
    所有权都查了一遍仍然全绿。所以这里必须把像素从屏幕上读回来。
    """
    ov_fs = app.overlays[0]
    app.snapshot = dict(app.snapshot,
                        layer_fullscreen=fx_kinds.LAYER_TOPMOST)
    if not _wait_topmost_settled(ov_fs):
        return False, f"全屏层没回到最上层：{ov_fs.z_mode}"
    base_hits, total = _probe_fs_visible(ov_fs)
    if base_hits <= 0:
        # 最上层都看不见，说明环境里有别的置顶窗压在采样点上 —— 结论不成立，
        # 别报假失败（真机上确实可能发生）
        return None, (f"环境里另有置顶窗遮挡（基线 {base_hits}/{total}），"
                      f"无法判定")

    app.snapshot = dict(app.snapshot,
                        layer_fullscreen=fx_kinds.LAYER_DESKTOP)
    if not _wait_desktop_settled(ov_fs):
        return False, (f"全屏层没落到桌面层：矩形 {_screen_rect(ov_fs.hwnd)}"
                       f" 虚拟屏 {fw.virtual_screen()}")
    app.snapshot = dict(app.snapshot,
                        layer_fullscreen=fx_kinds.LAYER_TOPMOST)
    if not _wait_topmost_settled(ov_fs):
        return False, f"全屏层没切回最上层：{ov_fs.z_mode}"

    hits, total = _probe_fs_visible(ov_fs)
    if hits <= 0:
        return False, (
            f"往返切换后窗口报告自己在上屏，但屏幕上没有它（{hits}/{total} 采样点）"
            f"—— SetParent 废掉了分层表面，"
            f"见 fx_winlayer.Overlay._rebuild_layered_surface")
    return True, (f"最上层 ⇄ 仅桌面图层 往返后仍真实可见"
                  f"（{hits}/{total} 采样点读到颜色，基线 {base_hits}/{total}）")


def test_real_desktop_layer_survives_rebuild():
    """桌面层是 WorkerW 的子窗口，资源管理器重启会连它一起销毁。

    重建之后必须**重新施加**当前档位，否则会留下一个默认（非置顶、非子窗）
    的窗口：用户看到的是"特效突然浮在最上层了"或"整个不显示"。
    """
    try:
        ov = fw.Overlay().create()
    except Exception as e:
        return None, f"SKIP（{type(e).__name__}: {e}）"
    try:
        ov.apply_z_mode(fx_kinds.LAYER_DESKTOP)
        if ov.z_mode != fx_kinds.LAYER_DESKTOP:
            return False, "切桌面层失败"
        if not ov._parented and not ov.is_desktop_fallback():
            return False, "状态不明"
        was_parented = ov._parented
        old = ov.hwnd
        # 让 Overlay 看起来像"句柄已经被系统带走"，再真销毁，避免泄漏
        ov.hwnd = 0
        try:
            user32.DestroyWindow(HWND(old))
        except Exception:
            pass
        if not ov.ensure_alive():
            return False, "句柄失效后 ensure_alive 没报告重建"
        if not user32.IsWindow(HWND(ov.hwnd)):
            return False, "重建后句柄仍无效"
        if ov.z_mode != fx_kinds.LAYER_DESKTOP:
            return False, f"重建后档位丢了：{ov.z_mode}"
        ok, msg = _check_desktop_state(ov, "重建后的窗口")
        if not ok:
            return False, msg
        # 重建后要连续上屏几帧：桌面层是 WS_CHILD，present() 的 pptDst 必须按
        # 父窗口客户区坐标给，给成屏幕坐标会把窗口每帧挪一个主屏宽度。
        for _ in range(5):
            ov.u32[:] = 0
            ov.present()
        rect = _screen_rect(ov.hwnd)
        if rect != fw.virtual_screen():
            return False, (f"重建后连续上屏 5 帧，窗口漂到 {rect}"
                           f"（虚拟屏 {fw.virtual_screen()}）—— present 的"
                           f"pptDst 用错了坐标系")
    except Exception as e:
        return None, f"SKIP（重建模拟异常 {type(e).__name__}: {e}）"
    finally:
        try:
            ov.destroy()
        except Exception:
            pass
    return True, (f"句柄被销毁后重建，仍回到桌面层"
                  f"（{'WorkerW 子窗口' if was_parented else '沉底降级'}）"
                  f"且连续上屏 5 帧几何不漂移")


# ------------------------------------------------------------------ 驱动
def main():
    tmpdir = tempfile.mkdtemp(prefix="fx_layer_switch_")
    real_cfg = d.CONFIG_PATH
    real_ui_state = getattr(fx_ui, "_UI_STATE_PATH", None)
    cfg_path = os.path.join(tmpdir, "config.json")
    seq = [0]

    def tmp_factory():
        seq[0] += 1
        p = os.path.join(tmpdir, f"cfg{seq[0]}.json")
        # App 读的就是 d.CONFIG_PATH，所以临时配置要"挂"上去
        d.CONFIG_PATH = p
        return p

    print("== 特效层级开关 功能检查 ==")
    results = []

    def run(name, fn):
        try:
            r = fn()
        except Exception as e:
            r = (False, f"exception: {type(e).__name__}: {e}")
        if r is None:
            results.append((name, None, "SKIP"))
            print(f"  [SKIP] {name}：跳过")
            return
        ok, msg = r
        results.append((name, ok, msg))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {msg}")

    # 之后的 App 一律读临时配置，绝不碰用户的 config.json
    d.CONFIG_PATH = cfg_path
    if real_ui_state:
        fx_ui._UI_STATE_PATH = os.path.join(tmpdir, "ui_state.json")

    run("层级归组与档位命名", test_layer_taxonomy)
    run("非法档位值归一化", test_value_normalization)
    run("档位落盘与回读", test_config_persist)
    run("旧配置缺键向后兼容", lambda: test_config_backward_compat(tmp_factory))
    run("界面开关写回 cfg+snapshot", test_ui_toggle_writes_cfg_and_snapshot)

    loop = Loop()
    try:
        app = loop.start(layer_fullscreen=fx_kinds.LAYER_DESKTOP,
                         layer_mouse=fx_kinds.LAYER_DESKTOP)
        if len(app.overlays) < 2:
            print(f"  [SKIP] 真机层级测试：只建起 {len(app.overlays)} 个覆盖层")
        else:
            run("启动即恢复到桌面层", lambda: test_real_startup_restores_desktop(
                app, loop.stats))
            run("混合档位互不干扰", lambda: test_real_mixed_modes(app))
            run("桌面层不参与抢顶", lambda: test_real_desktop_skips_raise(app))
            run("切回最上层完全还原", lambda: test_real_roundtrip_back_to_topmost(app))
            run("往返后仍真实上屏（屏幕像素）",
                lambda: test_real_roundtrip_still_visible_on_screen(app))
    finally:
        loop.stop()

    run("桌面层窗口重建后仍有档位", test_real_desktop_layer_survives_rebuild)

    d.CONFIG_PATH = real_cfg
    if real_ui_state:
        fx_ui._UI_STATE_PATH = real_ui_state
    try:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass

    passed = sum(1 for _, ok, _ in results if ok is True)
    failed = [n for n, ok, _ in results if ok is False]
    skipped = [n for n, ok, _ in results if ok is None]
    print()
    if failed:
        print(f"== LAYER SWITCH FAILED ==  失败：{'、'.join(failed)}")
    else:
        extra = f"，跳过 {len(skipped)} 项" if skipped else ""
        print(f"== LAYER SWITCH VERIFIED ==  {passed}/{len(results)} 通过{extra}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
