# -*- coding: utf-8 -*-
"""悬浮提示的多屏定位专项验证。

要守住的性质（用户实报："窗口在主屏上提示正常，移到副屏后提示还停留在主屏"）：

  ① 夹取边界必须是**虚拟桌面**，不是主屏 —— 副屏在主屏左侧时虚拟原点是负的，
     提示要跟着面板留在副屏；用主屏尺寸夹（max(0, …)）会把负坐标整个弹到主屏；
  ② 提示仍被夹在可见区域内（右缘/下缘不飞出屏幕）；
  ③ `_clamp_pos`（窗口不许整个跑出虚拟桌面）与提示共用同一套夹取，行为不退步；
  ④ 负向对照：把旧的主屏夹法复刻出来跑同一组数据，它必须把副屏上的提示弹到
     主屏（x=0）—— 证明判据分得清新旧行为，不是"怎么测都绿"。

驱动方式：`_clamp_xy` 是纯函数，直接喂合成矩形（不需要真副屏）；端到端那条
把面板挪到负坐标、并把 winfo_vroot* 打成副屏矩形（单屏机器上模拟副屏），
再走真实的 `_tip_show`。
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

# 隔离 UI 状态文件（用户真实的"上次页面 + 窗口尺寸"不能被测试碰）
import tempfile as _tf
fx_ui._UI_STATE_PATH = os.path.join(_tf.mkdtemp(prefix="fx_tipscreen_"),
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


def _old_clamp(x, y, w, h):
    """旧实现的夹法复刻（负向对照用）：拿**主屏**尺寸夹，边界钉在 0。"""
    sw, sh = 1920, 1080
    return (min(max(0, x), max(0, sw - w)),
            min(max(0, y), max(0, sh - h)))


def main():
    print("== 1) _clamp_xy 纯函数：副屏（主屏左侧，原点 -1920）==")
    # 副屏矩形 (-1920, 0) ~ (0, 1080)；提示停在副屏中间，不该被挪去主屏
    rect = (-1920, 0, 0, 1080)
    x, y = fx_ui._clamp_xy(-1800, 500, 100, 30, rect)
    check(x == -1800 and y == 500,
          "副屏中间的提示原地不动（得到 %r，期望 (-1800, 500)）" % ((x, y),))
    ox, oy = _old_clamp(-1800, 500, 100, 30)
    check(ox == 0,
          "负向对照：旧主屏夹法把同一位置弹到主屏 x=0（得到 %r）" % ((ox, oy),))

    # 副屏内贴左缘外：夹回 -1920，而不是 0
    x, y = fx_ui._clamp_xy(-2000, 100, 100, 30, rect)
    check(x == -1920, "副屏左缘外夹回 -1920（得到 %d）" % x)

    # 副屏在主屏右侧（原点 1920）：夹取边界跟着搬，不会掉回主屏右缘
    rect_r = (1920, 0, 3840, 1080)
    x, y = fx_ui._clamp_xy(3800, 100, 100, 30, rect_r)
    check(x == 3740, "右副屏右缘外夹回 3740（得到 %d）" % x)

    # 主屏右缘 / 下缘照旧夹住（行为不退步）
    x, y = fx_ui._clamp_xy(1850, 1060, 100, 30, (0, 0, 1920, 1080))
    check((x, y) == (1820, 1050),
          "主屏右缘/下缘夹回屏内（得到 %r，期望 (1820, 1050)）" % ((x, y),))

    # 提示比屏幕还宽：夹到矩形左上角，不出现负数宽
    x, y = fx_ui._clamp_xy(0, 0, 3000, 30, (-1920, 0, 0, 1080))
    check(x == -1920, "提示宽于副屏时夹到左边界（得到 %d）" % x)

    print()
    print("== 2) 端到端：面板挪到副屏（winfo_vroot* 打成副屏矩形）==")
    root = tk.Tk()
    root.withdraw()
    p = fx_ui.ControlPanel(_StubApp())
    p.root.update()

    # 找页内一个可见的配置项名称 Label（提示就挂在这种 Label 上）
    p._show_page("下雨")
    p.root.update()
    label = None
    stack = list(p._pages["下雨"].winfo_children())
    while stack:
        w = stack.pop(0)
        if isinstance(w, tk.Label) and w.cget("text").strip():
            label = w
            break
        stack.extend(w.winfo_children())
    check(label is not None and label.winfo_ismapped(),
          "找到可见的页内 Label 作为提示宿主")

    # 面板挪到"副屏"：负坐标 + vroot 报副屏矩形（单屏机器上模拟）
    p.root.geometry("%dx%d+%d+%d" % (p.W, p.H, -1800, 120))
    p.root.update()
    p.root.winfo_vrootx = lambda: -1920
    p.root.winfo_vrooty = lambda: 0
    p.root.winfo_vrootwidth = lambda: 1920
    p.root.winfo_vrootheight = lambda: 1080

    p._tip_show(label, "多屏定位测试提示")
    p.root.update()
    tip_x = int(p._tip_win.winfo_x())
    tip_y = int(p._tip_win.winfo_y())
    check(-1920 <= tip_x < 0,
          "提示落在副屏区（x=%d，期望 [-1920, 0)）" % tip_x)
    check(tip_y >= 0 and tip_y <= 1080 - 30,
          "提示纵向仍在副屏内（y=%d）" % tip_y)
    check(p._tip_win.winfo_ismapped(), "提示可见")

    # 宿主 Label 在副屏上 → 提示必须挨着它，不能跑去主屏
    lb_x = int(label.winfo_rootx())
    check(abs(tip_x - (lb_x + 8)) <= 2,
          "提示贴着宿主 Label（提示 x=%d，宿主 rootx+8=%d）" % (tip_x, lb_x + 8))

    # owner 关系不能被这次改动破坏（提示是从属窗，压在面板之上）
    from _win32 import top_hwnd
    import ctypes
    owner = ctypes.windll.user32.GetWindow(top_hwnd(p._tip_win), 4)  # GW_OWNER
    check(int(owner) == int(p._hwnd()),
          "提示仍是面板的从属窗口（owner=%s 面板=%s）"
          % (owner, p._hwnd()))

    p._tip_hide()
    p.root.destroy()

    print()
    print("== 3) _clamp_pos 与提示共用夹取，窗口仍不许跑出虚拟桌面 ==")
    root2 = tk.Tk()
    root2.withdraw()
    p2 = fx_ui.ControlPanel(_StubApp())
    p2.root.update()
    p2.root.winfo_vrootx = lambda: -1920
    p2.root.winfo_vrooty = lambda: 0
    p2.root.winfo_vrootwidth = lambda: 3840
    p2.root.winfo_vrootheight = lambda: 1080
    x, y = p2._clamp_pos(-5000, 2000, 600, 500)
    check(x == -1920, "窗口左侧夹回虚拟桌面左缘 -1920（得到 %d）" % x)
    check(y == 1080 - 500, "窗口下侧夹回虚拟桌面下缘（得到 %d）" % y)
    x, y = p2._clamp_pos(-100, 100, 600, 500)
    check((x, y) == (-100, 100), "虚拟桌面内的位置原样返回（得到 %r）" % ((x, y),))
    p2.root.destroy()

    print()
    if FAILS:
        print("TIP SCREEN FAILED  ->  %d 项未通过：" % len(FAILS))
        for m in FAILS:
            print("   ✗", m)
        return 1
    print("TIP SCREEN VERIFIED  ->  提示按虚拟桌面夹取，副屏上不再弹回主屏")
    return 0


if __name__ == "__main__":
    sys.exit(main())
