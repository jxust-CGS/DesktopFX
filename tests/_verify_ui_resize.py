# -*- coding: utf-8 -*-
"""窗口可缩放 + 底部提示迁移 的专项验证。

要守住的性质（都是"看着对、其实错"的重灾区）：

  ① 底部那行提示真的没了，内容真的进了「其他设置」（两侧都查，避免只删没加）；
  ② 八个边/角手柄齐备、都在窗口范围内、光标是缩放形状；
  ③ 拖角放大 → 尺寸跟上、左上角不动；
  ④ 拖边缩小到底 → 被夹在最小尺寸，不会缩成一条缝；
  ⑤ 拖左上角时**右下角必须钉住**（对边不动，否则看着像在拖窗口而不是改大小）；
  ⑥ 尺寸落盘 + 重启还原；
  ⑦ 「按内容自适应高度」只放开高度，宽度仍记着；
  ⑧ 「恢复默认尺寸」两个方向都回默认；
  ⑨ 宽度变化时说明文字的折行宽度跟着变，且各页都不再被挤（这是最小宽度的依据）；
  ⑩ 没自定义过尺寸时，_fit_height 仍是"按内容自适应"（老行为不能坏）；
  ⑪ 缩小到装不下时滚动仍然可用（不能把画面冻住）。

驱动方式：手柄走真正绑定的 <ButtonPress-1>/<B1-Motion>/<ButtonRelease-1>
（用 event_generate 带 rootx/rooty），滑块/按钮走同一套回调，不直接改 W/H。
"""
import json
import os
import queue
import tempfile
import time
import types

import sys

# 本脚本住在 tests/ 子目录里：`sys.path[0]` 与 `__file__` 的父目录都不再是项目根。
# 向上找到含 desktop_fx.py 的目录（= 项目根）：产品模块从那里 import；脚本里已有的
# HERE 也一律以它为基准（读 desktop_fx.py / fx_effects.py / .gitattributes /
# 启动桌面特效.bat、写临时配置都按项目根算）。
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = _TESTS_DIR
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
# tests/ 自己也要进 path：有几个脚本互相 import（_verify_click / _verify_pet ->
# _verify_kinds、_verify_tray -> _verify_launcher）。
for _p in (ROOT, _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import desktop_fx as d
import fx_ui
import fx_kinds

# 隔离 UI 状态文件：这里有 4 个面板被反复构造/销毁，绝不能碰到用户真实的
# ui_state.json（那是"上次页面 + 窗口尺寸"的家）。
_TMP = tempfile.mkdtemp(prefix="fx_uiresize_")
_STATE = os.path.join(_TMP, "ui_state.json")
fx_ui._UI_STATE_PATH = _STATE

FAILS = []


def check(ok, msg):
    """msg 写"被验证的性质本身"（中性陈述），通过/失败都按原样打印。"""
    print(("   [OK]   " if ok else "   [FAIL] ") + msg)
    if not ok:
        FAILS.append(msg)
    return ok


def _close(p):
    """停掉定时器再销毁。

    面板里的 _poll / _raise_top 是"自己给自己续期"的定时器。只设 _quitting
    不够：已经排进队列的那一次仍会触发，而那时窗口已销毁 —— Tcl 会把它当作
    "未知命令"打到 stderr（连开几个面板时刷一屏）。这里直接把队列里的 after
    全撤掉。
    """
    p._quitting = True
    try:
        for aid in p.root.tk.call("after", "info"):
            try:
                p.root.after_cancel(aid)
            except Exception:
                pass
    except Exception:
        pass
    p.root.destroy()


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


def _texts(w, out=None):
    """递归收集一棵控件树里所有 Label/Button 的文字。"""
    if out is None:
        out = []
    for c in w.winfo_children():
        try:
            if c.winfo_class() in ("Label", "Button"):
                t = str(c.cget("text"))
                if t:
                    out.append(t)
        except Exception:
            pass
        _texts(c, out)
    return out


def _drag(grip, sx, sy, dx, dy, steps=1):
    """在某个手柄上走真实的三段事件：按下 -> 拖动 -> 松开。

    用 event_generate 而不是直接调 _rz_*，是为了连"绑定有没有挂上"一起验到。
    """
    grip.event_generate("<ButtonPress-1>", rootx=sx, rooty=sy)
    for i in range(1, steps + 1):
        grip.event_generate("<B1-Motion>",
                            rootx=sx + dx * i // steps,
                            rooty=sy + dy * i // steps)
    grip.event_generate("<ButtonRelease-1>", rootx=sx + dx, rooty=sy + dy)


def _geo(p):
    return (int(p.root.winfo_x()), int(p.root.winfo_y()),
            int(p.W), int(p.H))


def _settle(p, want_xy=None, timeout=3.0):
    """等到窗口的**实际位置**稳定下来（可选：等到等于 want_xy）。

    为什么需要：`_rz_begin` 记的是 `winfo_x()`，`_rz_motion` 据此算"对边钉住"。
    若这个读数发生在上一次 `geometry()` 真正被 WM 落实之前，钉住的就是一个
    已经不存在的旧位置 —— 表现为"尺寸照长、位置不动"，右下角跟着长，
    断言报 `(r0,b0) -> (r0+dx, b0+dy)`。

    这条断言历史上就"时红时绿"（满负载跑整套时更容易碰上）。**不变量本身没
    错，脆的是读数时机** —— 所以在这里等，而不是放宽判据。
    """
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        p.root.update()
        cur = (int(p.root.winfo_x()), int(p.root.winfo_y()))
        if want_xy is not None and cur == tuple(want_xy):
            return cur
        if cur == last:
            return cur          # 连续两轮没动 = 稳了
        last = cur
        time.sleep(0.02)
    return last


def _read_state():
    try:
        with open(_STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    app = _StubApp()
    p = fx_ui.ControlPanel(app)
    p.root.update()

    # ---------------------------------------------------------- ① 提示迁移
    print("\n1) 底部提示条 -> 其他设置")
    bottom = [t for t in _texts(p.root) if "托盘图标右键可唤出菜单" in t]
    check(not bottom, f"窗口里已无老的底部提示条（找到 {len(bottom)} 条）")
    other = _texts(p._pages["其它"])
    check("托盘图标右键" in other and "唤出菜单" in other,
          "「其他设置」页含「托盘图标右键 / 唤出菜单」")
    check("Ctrl+Alt+R" in other and "快速暂停" in other,
          "「其他设置」页含「Ctrl+Alt+R / 快速暂停」")
    check(not any("快速暂停" in t and "Ctrl" in t and len(t) > 20
                  for t in _texts(p.root)),
          "老的「两件事合并成一行」提示串已移除")
    cards = [w for w in p._pages["其它"].winfo_children()]
    check(any("窗口尺寸" in _texts(c) for c in cards),
          "「其他设置」页含「窗口尺寸」卡片")
    check(any("快捷操作" in _texts(c) for c in cards),
          "「其他设置」页含「快捷操作」卡片")

    # ---------------------------------------------------------- ② 手柄
    print("\n2) 缩放手柄")
    grips = getattr(p, "_grip", {})
    check(set(grips) == {"n", "s", "w", "e", "nw", "ne", "sw", "se"},
          f"八个手柄齐备：{sorted(grips)}")
    bad = []
    for k, g in grips.items():
        x, y = int(g.winfo_x()), int(g.winfo_y())
        w, h = int(g.winfo_width()), int(g.winfo_height())
        if x < 0 or y < 0 or x + w > p.W + 1 or y + h > p.H + 1:
            bad.append((k, x, y, w, h))
    check(not bad, f"所有手柄都落在窗口范围内（越界 {len(bad)} 个）")
    cur = {k: str(g.cget("cursor")) for k, g in grips.items()}
    check(all(cur.values()), "每个手柄都设了缩放光标")
    check(cur["se"] in ("size_nw_se", "sizing") and
          cur["e"] in ("sb_h_double_arrow", "sizing") and
          cur["n"] in ("sb_v_double_arrow", "sizing"),
          f"边/角手柄光标形状正确：{cur['n']} / {cur['e']} / {cur['se']}")
    check(int(grips["se"].winfo_width()) == p.GRIP_CORNER and
          int(grips["e"].winfo_width()) == p.GRIP,
          f"手柄厚度符合预期（角落 {p.GRIP_CORNER}、边 {p.GRIP}）")

    # ---------------------------------------------------------- ③ 拖角放大
    print("\n3) 拖右下角放大")
    # 位移必须落在**没被钳制**的区间里，否则断言测的是 clamp 而不是拖拽：
    # 窗口高度封顶在屏高×0.92，而"按内容自适应"会把默认高度顶到接近上限
    # （最长页一变高就更近），再往上拖就只是原地撞顶。先把窗口摆到中间，
    # 下面两个方向的位移才真的落到尺寸上。前提本身也断言一次 —— 哪天页面
    # 再长高一截，这里会先给出"起点不合法"，而不是让人去猜钳制。
    p._set_size(win_w=600, win_h=760)
    p.root.update()
    check(p.W_MIN < p.W < p._cap_w() and p.H_MIN < p.H < p._cap_h(),
          f"拖拽起点在上下限之内：{p.W}x{p.H}"
          f"（{p.W_MIN}~{p._cap_w()} × {p.H_MIN}~{p._cap_h()}）")
    x0, y0, w0, h0 = _geo(p)
    _drag(grips["se"], x0 + w0 - 3, y0 + h0 - 3, 120, 90, steps=3)
    p.root.update()
    check(p.W == w0 + 120 and p.H == h0 + 90,
          f"拖右下角 +120/+90 后尺寸 {w0}x{h0} -> {p.W}x{p.H}")
    check((p.root.winfo_x(), p.root.winfo_y()) == (x0, y0),
          f"拖右下角时左上角不动 ({x0},{y0}) -> "
          f"({p.root.winfo_x()},{p.root.winfo_y()})")
    check(p._rz is None, "松手后 _rz 已清空")

    # ---------------------------------------------------------- ⑤ 对边钉住
    print("\n4) 拖左上角时右下角必须钉住")
    # **先把窗口摆到离屏幕边缘足够远的地方。** 拖左上角会让窗口往左上移，
    # 起点若就贴着屏幕边，`_clamp_pos` 会把位置夹住不动、而尺寸照长 ——
    # 右下角于是"跟着长"，看着像没钉住。
    vx, vy = p.root.winfo_vrootx(), p.root.winfo_vrooty()
    want = (vx + 240, vy + 200)
    p.root.geometry("+%d+%d" % want)
    # **必须等位置真的落实**再读锚点：`_rz_begin` 记的是 `winfo_x()`，读到的
    # 若是旧位置，"对边钉住"就会钉在一个已经不存在的点上 —— 尺寸照长、位置
    # 不动，右下角跟着长（实测报 `(r0,b0) -> (r0+40, b0+30)`）。
    got = _settle(p, want)
    # 锚点取**实际位置**（与 `_rz_begin` 读的是同一个来源），尺寸取 p.W/p.H
    x0, y0 = got
    w0, h0 = p.W, p.H
    check(got == want,
          f"窗口已摆到 ({want[0]},{want[1]})（实测 {got}）—— "
          "位置没落实就读锚点会让后面的钉住断言时红时绿")
    check(x0 - vx >= 40 and y0 - vy >= 30,
          f"拖拽起点离屏幕边缘足够远（左 {x0 - vx} / 上 {y0 - vy}）")
    r0, b0 = x0 + w0, y0 + h0
    _drag(grips["nw"], x0 + 3, y0 + 3, -40, -30, steps=2)
    p.root.update()
    check((p.root.winfo_x() + p.W, p.root.winfo_y() + p.H) == (r0, b0),
          f"拖左上角时右下角钉住不动 ({r0},{b0}) -> "
          f"({p.root.winfo_x() + p.W},{p.root.winfo_y() + p.H})")
    check(p.W == w0 + 40 and p.H == h0 + 30,
          f"拖左上角 -40/-30 后尺寸 {w0}x{h0} -> {p.W}x{p.H}")

    # ---------------------------------------------------------- ④ 下限
    print("\n5) 缩到极限必须被夹住")
    x0, y0, w0, h0 = _geo(p)
    _drag(grips["se"], x0 + w0 - 3, y0 + h0 - 3, -8000, -8000, steps=2)
    p.root.update()
    check(p.W == p.W_MIN and p.H == p.H_MIN,
          f"缩到极限被夹在 {p.W}x{p.H}（最小 {p.W_MIN}x{p.H_MIN}）")
    check(p.root.winfo_width() == p.W and p.root.winfo_height() == p.H,
          f"Tk 实际尺寸与 W/H 一致：{p.root.winfo_width()}x"
          f"{p.root.winfo_height()}")
    check(p.root.winfo_x() >= p.root.winfo_vrootx() and
          p.root.winfo_y() >= p.root.winfo_vrooty(),
          "窗口没有跑到虚拟桌面之外")

    # ---------------------------------------------------------- ⑪ 缩小后仍能滚
    print("\n6) 缩到最小后滚动仍然可用（画面不能被冻住）")
    p._show_page("真实水波")
    p.root.update_idletasks()
    check(p._need_scroll(), "缩到最小后长页面需要滚动")
    before = p._canvas.yview()[0]
    deep = p._pages["真实水波"]
    for _ in range(6):
        kids = deep.winfo_children()
        if not kids:
            break
        deep = kids[0]
    deep.event_generate("<MouseWheel>", delta=-120)
    p.root.update()
    check(p._canvas.yview()[0] > before,
          f"缩小后滚轮仍生效：yview {before:.3f} -> "
          f"{p._canvas.yview()[0]:.3f}")

    # ---------------------------------------------------------- ⑥ 落盘
    print("\n7) 尺寸落盘")
    p._set_size(win_w=700, win_h=660)
    p.root.update()
    saved = _read_state()
    check(saved.get("win_w") == 700 and saved.get("win_h") == 660,
          f"ui_state 记住了尺寸：{saved}")
    check(bool(saved.get("last_page")),
          f"ui_state 同时保留 last_page：{saved.get('last_page')!r}")

    # ---------------------------------------------------------- ⑨ 折行宽度
    print("\n8) 宽度变化时说明文字折行宽度跟着变，且各页不再被挤")
    p._set_size(win_w=p.W_MIN)
    p.root.update_idletasks()
    wl = sorted({int(h.cget("wraplength")) for h in p._hints})
    check(wl == [p.W_MIN - 192],
          f"wraplength 跟随最小宽度：{wl}（期望 {p.W_MIN - 192}）")
    avail = p.W - 120
    tight = [(n, int(p._pages[n].winfo_reqwidth()))
             for n in fx_kinds.nav_order()
             if int(p._pages[n].winfo_reqwidth()) > avail]
    check(not tight, f"最小宽度下无页面被挤（可用 {avail}，被挤 {tight}）")
    p._set_size(win_w=900)
    p.root.update_idletasks()
    wl2 = sorted({int(h.cget("wraplength")) for h in p._hints})
    check(wl2 == [900 - 192],
          f"wraplength 跟随加宽：{wl2}（期望 {900 - 192}）")

    # ---------------------------------------------------------- ⑦ 自适应高度
    print("\n9) 按内容自适应高度")
    p._show_page("下雨")
    p.root.update_idletasks()
    p._size_auto_h()
    p.root.update_idletasks()
    saved = _read_state()
    check("win_h" not in saved, f"自适应后 ui_state 里没有 win_h：{saved}")
    check(saved.get("win_w") == p.W,
          f"自适应高度不改宽度：{saved.get('win_w')} == {p.W}")
    check(p.H == p._clamp_h(max(p.H_MIN, p._content_h + p._chrome)),
          f"自适应高度 {p.H} = 内容 {p._content_h} + chrome {p._chrome}")
    sr = p._size_rows["win_h"]
    check(int(sr["var"].get()) == p.H and sr["label"].cget("text") == f"{p.H} 像素",
          f"自适应后「窗口高度」显示 {sr['label'].cget('text')!r}"
          f"（实际 {p.H}）—— 构造期它是按默认 742 建的，必须跟着改")
    p._show_page("下雨")
    p.root.update_idletasks()
    check(not p._need_scroll(), "自适应后「下雨」页无需滚动")

    # ---------------------------------------------------------- ⑧ 恢复默认
    print("\n10) 恢复默认尺寸")
    p._size_reset()
    p.root.update_idletasks()
    saved = _read_state()
    check("win_w" not in saved and "win_h" not in saved,
          f"恢复默认后 ui_state 无尺寸键：{saved}")
    check(p.W == p.W_DEF, f"宽度回默认：{p.W}（默认 {p.W_DEF}）")

    # 记下最终状态，供"重启还原"用
    p._set_size(win_w=777, win_h=666)
    p.root.update()
    _close(p)

    # ---------------------------------------------------------- ⑥b 重启还原
    print("\n11) 重启后按记忆尺寸还原")
    p2 = fx_ui.ControlPanel(_StubApp())
    p2.root.update()
    check((p2.W, p2.H) == (777, 666),
          f"重启还原尺寸：{p2.W}x{p2.H}（期望 777x666）")
    check(p2.root.winfo_width() == 777 and p2.root.winfo_height() == 666,
          f"窗口实际尺寸：{p2.root.winfo_width()}x"
          f"{p2.root.winfo_height()}")
    check(sorted({int(h.cget("wraplength")) for h in p2._hints}) == [777 - 192],
          "还原后折行宽度也跟着还原")
    check(int(p2._size_rows["win_w"]["var"].get()) == 777,
          f"宽度滑块同步为 {p2._size_rows['win_w']['var'].get():.0f}")
    check(int(p2._size_rows["win_h"]["var"].get()) == 666,
          f"高度滑块同步为 {p2._size_rows['win_h']['var'].get():.0f}")
    for k, want in (("win_w", 777), ("win_h", 666)):
        lb = p2._size_rows[k]["label"].cget("text")
        check(lb == f"{want} 像素", f"{k} 数值标签显示 {lb!r}（期望 {want}）")
    check(p2._content_h > 0, f"内容高度已测出：{p2._content_h}")
    _close(p2)

    # ---------------------------------------------------------- ⑩ 老行为
    print("\n12) 没自定义过尺寸时仍是按内容自适应（老行为）")
    fresh = os.path.join(_TMP, "fresh.json")
    fx_ui._UI_STATE_PATH = fresh
    p3 = fx_ui.ControlPanel(_StubApp())
    p3.root.update()
    expect_h = p3._clamp_h(max(p3.H_MIN, p3._content_h + p3._chrome))
    check(p3.W == p3.W_DEF, f"默认宽度 {p3.W}（期望 {p3.W_DEF}）")
    check(p3.H == expect_h,
          f"默认高度按内容自适应 {p3.H}（期望 {expect_h}）")
    sr = p3._size_rows["win_h"]
    check(int(sr["var"].get()) == p3.H,
          f"自适应后高度滑块 {sr['var'].get():.0f} == 实际 {p3.H}")
    check(sr["label"].cget("text") == f"{p3.H} 像素",
          f"自适应后高度标签 {sr['label'].cget('text')!r} == {p3.H} 像素")
    check(p3.root.winfo_height() >= p3._content_h,
          f"窗口装得下最长页：{p3.root.winfo_height()} >= {p3._content_h}")
    try:
        with open(fresh, "r", encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = {}
    check("win_w" not in st and "win_h" not in st,
          f"首次运行没把尺寸写死：{st}")
    _close(p3)

    print("\n" + "=" * 72)
    if FAILS:
        print(f"UI RESIZE FAILED  -> {len(FAILS)} 项未通过：")
        for m in FAILS:
            print("   ✗", m)
        raise SystemExit(1)
    print("UI RESIZE VERIFIED  -> 12 组断言全部通过")


if __name__ == "__main__":
    main()
