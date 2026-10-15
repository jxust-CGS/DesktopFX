# -*- coding: utf-8 -*-
"""侧栏分组折叠/展开的专项验证。

要守住的性质（用户实报："点左侧分组收起/放开，总是触发整个左边刷新，
只需要控制子项显示隐藏，不需要整体刷新"）：

  ① 折叠只让**本组子项**消失，组标题还在；
  ② 折叠/展开**不碰其他组**的任何一个控件（不再整栏重排）；
  ③ 展开后**pack 顺序与原顺序完全一致** —— pack 的顺序即视觉顺序，子项若被
     直接 pack 会排到栏尾（所以实现必须 pack 到组后锚点之前，见 _repack_nav_group）；
  ④ `_show_page` 把折叠的活动页分组自动展开，同样只碰该组；
  ⑤ 负向对照：旧的整栏 `_repack(self._nav_entries)` 确实会碰到其他组的控件 ——
     证明 ② 的间谍判据分得清"窄重排"和"整栏重排"，不是怎么测都绿。

驱动方式：走真实点击路径（`_toggle_nav_group`，与组标题 ButtonPress-1 绑的是
同一个函数）；"有没有被碰"用 tk.Misc.pack / pack_forget 的类级间谍记录。
"""
import os
import queue
import sys
import tkinter as tk

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = _TESTS_DIR
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
for _p in (ROOT, _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import desktop_fx as d
import fx_ui
import fx_kinds

import tempfile as _tf
fx_ui._UI_STATE_PATH = os.path.join(_tf.mkdtemp(prefix="fx_navcollapse_"),
                                    "ui_state.json")

FAILS = []


def check(cond, msg):
    if cond:
        print("  [OK] %s" % msg)
    else:
        FAILS.append(msg)
        print("  [✗] %s" % msg)


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


class _PackSpy:
    """类级间谍：记录期间所有 pack / pack_forget 作用到的控件。

    pack/pack_forget 定义在 tkinter 的 `Pack` 混类上（不在 Misc 上）——
    Widget 的 MRO 里才有它，Toplevel/Tk 都没有。
    """

    def __init__(self):
        self.touched = []

    def __enter__(self):
        op, of = tk.Pack.pack, tk.Pack.pack_forget
        self._op, self._of = op, of
        touched = self.touched

        # 注意：函数装在类上，第一个参数是**控件实例**（不是 self）——
        # 原函数必须闭包捕获，不能走 self._of（控件上没有这个属性）。
        def _pack(w, *a, **kw):
            touched.append(w)
            return op(w, *a, **kw)

        def _forget(w, *a, **kw):
            touched.append(w)
            return of(w, *a, **kw)

        tk.Pack.pack, tk.Pack.pack_forget = _pack, _forget
        return self

    def __exit__(self, *a):
        tk.Pack.pack, tk.Pack.pack_forget = self._op, self._of


def main():
    root = tk.Tk()
    root.withdraw()
    p = fx_ui.ControlPanel(_StubApp())
    p.root.update()

    groups = [g for g, _ in fx_kinds.NAV_GROUPS]
    g0, g1 = groups[0], groups[1]          # 全屏特效 / 鼠标拖动特效
    items0 = [n for n in fx_kinds.NAV_GROUPS[0][1]]
    items1 = [n for n in fx_kinds.NAV_GROUPS[1][1]]

    def others_excluding(group):
        """除 group 之外所有分组的侧栏控件（含组标题、子项、"其它"这种无组条目）。"""
        s = set()
        for g, pages in fx_kinds.NAV_GROUPS:
            if g == group:
                continue
            s.add(p._nav_groups[g]["frame"])
            for n in pages:
                s.update([p._nav[n]["frame"], p._nav[n]["label"],
                          p._nav[n]["line"], p._nav[n]["icon"]])
        return s

    order0 = list(p._nav_in.pack_slaves())
    print("== 1) 折叠只收本组子项，且不碰其他组 ==")
    with _PackSpy() as spy:
        p._toggle_nav_group(g0)
        p.root.update()

    check(p._nav_groups[g0]["open"] is False, "折叠后分组状态为关")
    check(not any(p._nav[n]["frame"].winfo_ismapped() for n in items0),
          "%s 的子项全部收起" % g0)
    check(p._nav_groups[g0]["frame"].winfo_ismapped(), "组标题仍在")
    check(all(p._nav[n]["frame"].winfo_ismapped() for n in items1),
          "%s 的子项没被牵连" % g1)

    others = others_excluding(g0)
    hit = [w for w in spy.touched if w in others]
    check(not hit,
          "折叠期间其他组控件零触碰（实际碰到 %d 个）" % len(hit))

    print()
    print("== 2) 展开后顺序与原顺序完全一致（子项不会掉到栏尾）==")
    with _PackSpy() as spy:
        p._toggle_nav_group(g0)
        p.root.update()
    order1 = list(p._nav_in.pack_slaves())
    check(order1 == order0,
          "展开后 pack 顺序与初始一致（%d 项）" % len(order1))
    check(all(p._nav[n]["frame"].winfo_ismapped() for n in items0),
          "%s 的子项全部回来" % g0)
    hit = [w for w in spy.touched if w in others]
    check(not hit,
          "展开期间其他组控件零触碰（实际碰到 %d 个）" % len(hit))

    print()
    print("== 3) _show_page 自动展开折叠的活动页分组，同样只碰该组 ==")
    p._toggle_nav_group(g1)
    p.root.update()
    check(not p._nav_groups[g1]["open"], "%s 已折叠" % g1)
    others = others_excluding(g1)
    with _PackSpy() as spy:
        p._show_page("宠物跟随")       # 宠物跟随 属于 g1
        p.root.update()
    check(p._nav_groups[g1]["open"] is True, "活动页所在分组被自动展开")
    check(p._nav["宠物跟随"]["frame"].winfo_ismapped(), "活动项可见")
    hit = [w for w in spy.touched if w in others]
    check(not hit,
          "自动展开期间其他组控件零触碰（实际碰到 %d 个）" % len(hit))
    order2 = list(p._nav_in.pack_slaves())
    check(order2 == order0, "自动展开后顺序仍与初始一致")

    print()
    print("== 4) 负向对照：旧的整栏 _repack 确实会碰到其他组 ==")
    p._toggle_nav_group(g1)          # 先折起来，让重排有事可做
    p.root.update()
    with _PackSpy() as spy:
        p._repack(p._nav_entries)    # 旧实现走的就是这条路
        p.root.update()
    hit = [w for w in spy.touched if w in others]
    check(len(hit) > 0,
          "整栏 _repack 会碰到其他组控件（实际 %d 个）—— 间谍判据有效"
          % len(hit))
    # 复原：展开 + 顺序归位
    p._toggle_nav_group(g1)
    p.root.update()
    check(list(p._nav_in.pack_slaves()) == order0, "负向对照后顺序已复原")

    print()
    print("== 5) 折叠状态落盘（内存态）与侧栏滚轮 ==")
    p._toggle_nav_group("其他特效")
    check("其他特效" in p._ui.get("nav_collapsed", []),
          "折叠状态记进 _ui（重启后还记得）")
    p._toggle_nav_group("其他特效")
    check(p._ui.get("nav_collapsed") == [], "再点一次展开，记录清空")

    p.root.destroy()

    print()
    if FAILS:
        print("NAV COLLAPSE FAILED  ->  %d 项未通过：" % len(FAILS))
        for m in FAILS:
            print("   ✗", m)
        return 1
    print("NAV COLLAPSE VERIFIED  ->  折叠只动本组、顺序不乱、其他组零触碰")
    return 0


if __name__ == "__main__":
    sys.exit(main())
