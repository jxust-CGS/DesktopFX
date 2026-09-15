# -*- coding: utf-8 -*-
"""启动无闪动：构造期间窗口必须全程不可见，__init__ 末尾才一次性显示。

机制：ControlPanel.__init__ 一开始 withdraw()，所有 _build / _fit_height 的
flush（逐页测高度等）都发生在窗口隐藏期；只有 __init__ 末尾才 deiconify + lift。
本脚本在导入期给 tk.Misc.update_idletasks 打桩，记录每次 flush 时顶层窗口的
mapped 状态；构造完成后断言：
  ① 构造期间有 flush（证明拦到了构建过程）且全程不可见；
  ② 构造结束后窗口可见（deiconify 生效）；
  ③ 显示后窗口高度等于 _fit_height 算出的最终值（无“先 742 再跳变”）；
  ④ 窗口仍为无边框置顶窗。
"""
import tkinter as tk
import desktop_fx as d
import fx_ui

_FLUSH_STATES = []


def _patched_update_idletasks(self):
    try:
        mapped = self.winfo_toplevel().winfo_ismapped()
    except Exception:
        mapped = None
    _FLUSH_STATES.append(mapped)
    return _orig_update_idletasks(self)


_orig_update_idletasks = tk.Misc.update_idletasks
tk.Misc.update_idletasks = _patched_update_idletasks


class _StubApp:
    def __init__(self):
        self.cfg = dict(d.DEFAULTS)
        self.events = __import__("queue").Queue()
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


def main():
    global _FLUSH_STATES
    _FLUSH_STATES = []
    app = _StubApp()
    p = fx_ui.ControlPanel(app)

    # ① 构造期间必须有 flush（证明拦到了构建），且全程不可见
    if not _FLUSH_STATES:
        raise AssertionError("构造期间没有任何 update_idletasks 调用，无法验证隐藏时机")
    visible_during_build = [s for s in _FLUSH_STATES if s]
    if visible_during_build:
        raise AssertionError(
            f"构造期间窗口竟有 {len(visible_during_build)} 次 flush 发生在可见状态"
            f"（这就是“逐页闪过”的来源）")

    # ② 构造结束后窗口必须可见（deiconify 生效）
    if not p.root.winfo_ismapped():
        raise AssertionError("构造结束后窗口仍未显示（deiconify 没生效）")

    # ③ 显示后窗口高度必须等于 _fit_height 算出的最终值（无“先 742 再跳变”）
    h = p.root.winfo_height()
    if h != p.H:
        raise AssertionError(
            f"显示后窗口高度 {h} 与自适应高度 {p.H} 不一致（可能有跳变）")

    # ④ 窗口仍为无边框（overrideredirect 未被破坏）
    if p.root.wm_overrideredirect() != 1:
        raise AssertionError("窗口不是无边框（overrideredirect 丢失）")

    p.root.destroy()
    try:
        import os as _os
        _os.remove(fx_ui._UI_STATE_PATH)
    except Exception:
        pass
    print(f"STARTUP NO-FLICKER OK  -> 构造期 flush={len(_FLUSH_STATES)} 次全部隐藏，"
          f"最终高度={h}")


if __name__ == "__main__":
    main()
