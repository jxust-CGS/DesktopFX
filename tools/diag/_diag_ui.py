# -*- coding: utf-8 -*-
"""在真实显示器上打开控制面板并抓图保存为 _ui_screenshot.png。

headless / 远程会话里无法显示窗口，此脚本会失败；在本地 Windows
桌面上运行可用来确认界面是否按设计稿渲染。
"""
import os
import queue
import sys
import time

# 本脚本住在 tools/ 子目录里：`sys.path[0]` 与 `__file__` 的父目录都不再是项目根。
# 向上找到含 desktop_fx.py 的目录（= 项目根）当基准，import 与输出路径才成立。
ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import desktop_fx as d
import fx_ui

try:
    from PIL import ImageGrab
except ImportError:
    print("缺少 PIL，无法抓图")
    sys.exit(1)


class _StubApp:
    def __init__(self):
        self.cfg = dict(d.DEFAULTS)
        self.events = queue.Queue()
        self.fps = 60.0
        self.cost_ms = 9.0
        self.paused = False
        self.user_presets = {}

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


def main():
    app = _StubApp()
    p = fx_ui.ControlPanel(app)
    p.root.deiconify()
    p._show_page("下雨")
    p.root.update_idletasks()
    time.sleep(0.5)

    x, y = p.root.winfo_x(), p.root.winfo_y()
    w, h = p.root.winfo_width(), p.root.winfo_height()
    print(f"窗口位置: {x},{y} 尺寸: {w}x{h}")

    try:
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        path = os.path.join(ROOT, "_ui_screenshot.png")
        img.save(path)
        print(f"已保存截图: {path}")
    except Exception as e:
        print(f"抓图失败: {e}")

    p.root.destroy()


if __name__ == "__main__":
    main()
