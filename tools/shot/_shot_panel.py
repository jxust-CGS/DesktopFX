# -*- coding: utf-8 -*-
"""重新生成 README 用的控制面板截图（docs/images/panel-*.png）。

为什么不用窗口截图工具：面板是**自绘**控件（Canvas + PIL 抗锯齿），普通截图会
把桌面背景一起拍进去，README 里不好看。这里只按面板窗口的矩形抓屏，所以
**必须真的把面板显示出来**（先 deiconify + lift + update），不能离屏画。

用法（要有真实桌面）：
    python _shot_panel.py            # 生成 panel-rain / panel-other / panel-paint
"""
import ctypes
import os
import queue
import sys
import tempfile
import time
from pathlib import Path

# 本脚本住在 tools/ 子目录里：`sys.path[0]` 与 `__file__` 的父目录都不再是项目根。
# 向上找到含 desktop_fx.py 的目录（= 项目根）当基准，import 与输出路径才成立。
ROOT = Path(__file__).resolve().parent
while not (ROOT / "desktop_fx.py").exists() and ROOT.parent != ROOT:
    ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import desktop_fx as d          # noqa: E402
import fx_ui                    # noqa: E402
import _shot                    # noqa: E402

OUT_DIR = ROOT / "docs" / "images"

# 隔离 UI 状态：_show_page 会把"上次停留的页面"写回 ui_state.json，那是用户
# 真实在用的文件（里面还记着他调好的窗口尺寸）。截图工具不该改用户的设置。
fx_ui._UI_STATE_PATH = os.path.join(tempfile.mkdtemp(prefix="fx_shot_"),
                                    "ui_state.json")


class _StubApp:
    """面板只需要一个"有 cfg / 能落 set"的后端，不必起渲染线程。"""

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

    def save_preset(self, n, p):
        return True, "ok"

    def delete_preset(self, n):
        return True, "ok"

    def toggle_pause(self):
        return False

    def quit(self):
        pass

    def report_error(self, *a):
        pass


def grab(x, y, w, h, out):
    """按屏幕坐标抓一块并写成 PNG（BGRA -> RGBA 需手动换位）。"""
    sdc = _shot.u32dll.GetDC(0)
    mdc = _shot.g32.CreateCompatibleDC(sdc)
    bmp = _shot.g32.CreateCompatibleBitmap(sdc, w, h)
    _shot.g32.SelectObject(mdc, bmp)
    _shot.g32.BitBlt(mdc, 0, 0, w, h, sdc, x, y, _shot.SRCCOPY)
    bi = _shot.BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(_shot.BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = (ctypes.c_ubyte * (w * h * 4))()
    _shot.g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    mv = memoryview(buf).cast("B")
    rows = []
    stride = w * 4
    for r in range(h):
        row = mv[r * stride:(r + 1) * stride]
        a = bytearray(row)
        a[0::4], a[2::4] = row[2::4], row[0::4]
        rows.append(bytes(a))
    _shot.write_png(str(out), w, h, rows)
    _shot.g32.DeleteObject(bmp)
    _shot.g32.DeleteDC(mdc)
    _shot.u32dll.ReleaseDC(0, sdc)
    print(f"  saved {out.name}  {w}x{h}")


def _shoot(panel, page, out):
    panel._show_page(page)
    panel.root.update()
    time.sleep(0.7)          # 等 DWM 圆角/亚克力画完，否则抓到方角
    panel.root.update()
    # 抓图期间把整体不透明度顶到 1.0：面板平时是 0.97（Tk 用分层窗实现），
    # 那 3% 会把**背后的窗口**透进截图 —— 背后恰好开着文件管理器时，README
    # 图里就会浮出别人的文件夹列表。截图要的是面板本身，不是当时的桌面。
    panel.root.attributes("-alpha", 1.0)
    panel.root.update()
    time.sleep(0.15)
    try:
        grab(panel.root.winfo_x(), panel.root.winfo_y(),
             panel.root.winfo_width(), panel.root.winfo_height(), out)
    finally:
        panel.root.attributes("-alpha", 0.97)
        panel.root.update()


def main():
    if not OUT_DIR.is_dir():
        print(f"找不到输出目录：{OUT_DIR}")
        return 1
    panel = fx_ui.ControlPanel(_StubApp())
    try:
        panel.root.deiconify()
        panel.root.lift()
        panel.root.update()
        time.sleep(1.0)
        panel.root.update()
        print(f"面板几何 {panel.root.winfo_width()}x{panel.root.winfo_height()}")
        _shoot(panel, "下雨", OUT_DIR / "panel-rain.png")
        _shoot(panel, "颜料盘", OUT_DIR / "panel-paint.png")
        _shoot(panel, "其它", OUT_DIR / "panel-other.png")
    finally:
        panel.root.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
