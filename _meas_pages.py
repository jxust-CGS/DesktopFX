# -*- coding: utf-8 """量各页自然高度/宽度，用于布局预算核对。"""
import os
import queue
import tempfile

import desktop_fx as d
import fx_ui
import fx_kinds

fx_ui._UI_STATE_PATH = os.path.join(tempfile.mkdtemp(prefix="fx_meas_"),
                                    "ui_state.json")


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
        return list(d.PRESETS)

    def presets_for_page(self, page):
        return [n for n in d.PRESET_GROUPS.get(page, []) if n in d.PRESETS]

    def is_user_preset(self, n):
        return False

    def save_preset(self, name, page):
        return True, "ok"

    def delete_preset(self, name):
        return True, "ok"

    def toggle_pause(self):
        return False

    def quit(self):
        pass

    def report_error(self, *a):
        pass


def main():
    app = _StubApp()
    p = fx_ui.ControlPanel(app)
    p.root.update()
    print(f"窗口 {p.W}x{p.H}  chrome={p._chrome}  cap_h={p._cap_h()}"
          f"  内容上限={p._cap_h() - p._chrome}")
    rows = []
    for n in fx_kinds.nav_order():
        h = int(p._pages[n].winfo_reqheight())
        w = int(p._pages[n].winfo_reqwidth())
        rows.append((h, w, n))
    for h, w, n in sorted(rows, reverse=True):
        print(f"  {n:<8} 高 {h:>5}  宽 {w:>4}")
    # 点击表情在三种来源下的高度
    for src in ("内置图案", "自选图片", "图片文件夹"):
        app.cfg["click_emoji_source"] = src
        p._sync_from_cfg()
        p.root.update_idletasks()
        print(f"  点击表情[{src}] 高 "
              f"{int(p._pages['点击表情'].winfo_reqheight())}")
    p.root.destroy()


if __name__ == "__main__":
    main()
