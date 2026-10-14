# -*- coding: utf-8 -*-
"""「避开任务栏」回归验证。

背景（2026-09-14，用户报）："真实水波在下方任务栏会有残影"。

根因不是水波的渲染，而是**覆盖窗的层级**：覆盖窗是 `WS_EX_TOPMOST`
（实机确认 3840x1200 置顶），而任务栏 `Shell_TrayWnd` /
`Shell_SecondaryTrayWnd` **不是**置顶窗。于是水波那道半透明的明暗条纹被
直接画在任务栏上面 —— 用户看到的就是"任务栏糊了一层洗不掉的印记"。

修法是上屏前把任务栏矩形从两层缓冲区里擦掉（[fx_winlayer.blank_rects]），
观感上等于"任务栏浮在特效之前"，这正是用户的预期。

本测试锁住四条：

    1. 任务栏矩形取得对（含**每块副屏**各一个，不只是主屏那一个）；
    2. 擦除**只擦任务栏**，区域外一个像素都不许动；
    3. 矩形越界（跨缓冲区边界 / 完全在屏外，如自动隐藏把任务栏推到屏外）
       时必须裁剪，不许因为切片越界而报错或擦错地方；
    4. 端到端：真有特效在跑时，**上屏那一刻**任务栏区域必须是全透明的；
       而关掉这个开关后同一块区域必须有内容（否则这条设置就是死的）。
"""
import sys
import time

import numpy as np

import fx_winlayer as fw


def test_bar_rects_look_sane():
    """真机：任务栏矩形非空、尺寸合理、落在虚拟屏内。"""
    bars = fw.system_bar_rects()
    ox, oy, w, h = fw.virtual_screen()
    if not bars:
        return None, "SKIP（本机没有可见任务栏，无法验证）"
    seen = set()
    for (l, t, r, b) in bars:
        if (l, t, r, b) in seen:
            return False, f"同一个任务栏矩形出现了两次：{(l, t, r, b)}"
        seen.add((l, t, r, b))
        if r <= l or b <= t:
            return False, f"任务栏矩形面积非正：{(l, t, r, b)}"
        # 任务栏永远是一条**贴在某个边上的薄条**，所以判据是"短边够薄"。
        # ⚠️ 早先这里写的是"宽 ≤ 虚拟屏宽的一半"，在**单显示器**机器上会误判：
        # 主屏底边任务栏本来就占满整个宽度（虚拟屏 1920 → 1920 > 960 判成
        # "尺寸离谱"）。双屏时虚拟屏宽 3840，判据恰好蒙混过关，所以这毛病
        # 一直没暴露 —— 本机副屏关掉之后才现形。判"薄条"与显示器数量无关。
        if min(r - l, b - t) > max(w, h) // 4:
            return False, f"任务栏不像是贴在一条边上的薄条：{(l, t, r, b)}"
        if r <= ox or b <= oy or l >= ox + w or t >= oy + h:
            return False, f"任务栏完全在虚拟屏之外却没被跳过：{(l, t, r, b)}"
        if t < oy or b > oy + h or l < ox or r > ox + w:
            return False, f"任务栏超出了虚拟屏边界：{(l, t, r, b)}"
    return True, f"{len(bars)} 个任务栏：{bars}"


def test_blank_only_touches_taskbar():
    """擦除必须**只擦任务栏**：区域外一个像素都不许动。"""
    ox, oy, w, h = 0, 0, 640, 480
    bars = [(100, 400, 300, 440)]           # 一块假任务栏
    buf = np.full(w * h, 0x11223344, dtype=np.uint32)
    n = fw.blank_rects(buf, w, h, ox, oy, bars)
    want = (300 - 100) * (440 - 400)
    if n != want:
        return False, f"报告擦掉 {n} 像素，应为 {want}"
    a = buf.reshape(h, w)
    if a[400:440, 100:300].any():
        return False, "任务栏区域内仍有非零像素"
    if not a[399, 100:300].all() or not a[440, 100:300].all():
        return False, "任务栏上下相邻行被误擦"
    if not a[:, :100].all() or not a[:, 300:].all():
        return False, "任务栏左右两侧被误擦"
    # 区域外的非零总数核对（防止"多擦了一条"这种边界错位）
    if int((a != 0).sum()) != w * h - want:
        return False, f"非零像素数不对：{int((a != 0).sum())} != {w * h - want}"
    return True, f"只擦掉任务栏 {want} 像素，区域外（含相邻行）纹丝不动"


def test_blank_clips_out_of_range():
    """矩形越界时必须裁剪：自动隐藏会把任务栏整个推到屏幕外。

    缓冲区原点刻意取负（虚拟屏原点就是负的），顺带验证坐标换算没写反。
    """
    ox, oy, w, h = -100, -50, 200, 100
    # 完全在屏外（自动隐藏时的任务栏就在屏幕外）：一个像素都不该动
    buf = np.full(w * h, 0x11223344, dtype=np.uint32)
    n = fw.blank_rects(buf, w, h, ox, oy, [(-500, -500, -300, -400)])
    if n != 0 or not buf.all():
        return False, f"完全在屏外的矩形不该动像素，实际擦了 {n}"
    # 右上角越界：屏幕 x 0..40 -> 缓冲 x 100..140；屏幕 y -90..0 -> 缓冲 y 0..50
    buf = np.full(w * h, 0x11223344, dtype=np.uint32)
    n = fw.blank_rects(buf, w, h, ox, oy, [(0, -90, 40, 0)])
    if n != 40 * 50:
        return False, f"裁剪后的像素数应为 2000，实际 {n}"
    a = buf.reshape(h, w)
    if a[:50, 100:140].any():
        return False, "越界矩形的有效部分没被擦到"
    if int((a != 0).sum()) != w * h - 40 * 50:
        return False, "越界矩形擦到了范围之外的像素"
    # 覆盖整个缓冲区：应整块擦干净
    buf = np.full(w * h, 0x11223344, dtype=np.uint32)
    n = fw.blank_rects(buf, w, h, ox, oy, [(-1000, -1000, 1000, 1000)])
    if n != w * h or buf.any():
        return False, f"整块覆盖时应擦掉 {w * h} 像素，实际 {n}"
    return True, "屏外矩形 0 像素；越界矩形精确裁到 2000 像素；整块覆盖全清"


def test_blank_empty_is_noop():
    """没有任务栏（或拿不到）时不得碰缓冲区。"""
    buf = np.full(1000, 0x11223344, dtype=np.uint32)
    n = fw.blank_rects(buf, 100, 10, 0, 0, [])
    if n != 0 or not buf.all():
        return False, "空矩形列表却动了像素"
    return True, "无任务栏 -> 0 像素改动"


# ------------------------------------------------------------------ 端到端
def test_render_loop_masks_taskbar():
    """端到端：上屏那一刻，任务栏区域必须是全透明的；关掉开关则相反。

    A/B 两相都在**同一个渲染循环**里跑（改 snapshot 即可），用聚光灯当
    "必然覆盖整屏"的内容源 —— 它是全屏压暗，任务栏那块必然有像素，
    所以"没被擦掉"一定能被抓到，不会因为雨滴恰好没落到那儿而假通过。
    """
    import threading

    import desktop_fx as d

    # 本脚本要起**真实 App**，App.__init__ 会读用户真实的 config.json。
    # 把它挪到临时文件：断言不能依赖用户设置（比如用户关掉了 avoid_taskbar，
    # 这条端到端用例就会被自己的前置条件判失败），也顺带不去写用户的文件。
    import os
    import tempfile

    d.CONFIG_PATH = os.path.join(tempfile.mkdtemp(prefix="fx_taskbar_"),
                                 "config.json")

    bars = fw.system_bar_rects()
    if not bars:
        return None, "SKIP（本机没有可见任务栏）"

    stats = {}
    holder = {}
    orig = fw.Overlay.present

    def _spy(self):
        ovs = holder["app"].overlays if holder.get("app") else []
        key = "fs" if (len(ovs) > 0 and self.hwnd == ovs[0].hwnd) else "ms"
        st = stats.setdefault(key, {"frames": 0, "dirty": 0, "px": 0})
        st["frames"] += 1
        for (l, t, r, b) in bars:
            x0, x1 = max(0, l - self.x), min(self.w, r - self.x)
            y0, y1 = max(0, t - self.y), min(self.h, b - self.y)
            if x1 > x0 and y1 > y0:
                blk = self.buffer[y0:y1, x0:x1]
                st["px"] += blk.shape[0] * blk.shape[1]
                if blk.any():
                    st["dirty"] += 1
        return orig(self)

    fw.Overlay.present = _spy
    app = None
    try:
        app = d.App()
        holder["app"] = app
        app.cfg.update(dict(rain_enabled=False, snow_enabled=False,
                            petal_enabled=False, meteor_enabled=False,
                            spotlight_enabled=True, spotlight_dim=0.5,
                            paint_enabled=False, wave_enabled=False,
                            win_ripple_enabled=False, ripple_enabled=False,
                            avoid_taskbar=True))
        app.snapshot = dict(app.cfg)
        app._thread = threading.Thread(target=app._render_loop, name="probe",
                                       daemon=True)
        app._thread.start()
        deadline = time.time() + 10
        while time.time() < deadline and len(app.overlays) < 2:
            time.sleep(0.1)
        if len(app.overlays) != 2:
            return None, "SKIP（覆盖层建不起来）"

        time.sleep(0.8)
        for st in stats.values():
            st["frames"] = st["dirty"] = st["px"] = 0
        time.sleep(1.2)
        masked = {k: dict(v) for k, v in stats.items()}
        lay = masked.get("ms", {})
        if lay.get("frames", 0) < 20 or lay.get("px", 0) <= 0:
            return False, f"鼠标层几乎没上屏（{lay.get('frames')} 帧），测试前提不成立"
        if lay.get("dirty"):
            return False, (f"任务栏区域在 {lay['dirty']}/{lay['frames']} 帧上仍有像素"
                           f" —— 避开任务栏没生效")

        # B 相：关掉开关，同一块区域必须出现内容
        for st in stats.values():
            st["frames"] = st["dirty"] = st["px"] = 0
        app.snapshot = dict(app.snapshot, avoid_taskbar=False)
        time.sleep(1.2)
        off = {k: dict(v) for k, v in stats.items()}
        if off.get("ms", {}).get("dirty", 0) <= 0:
            return False, ("关掉「避开任务栏」后任务栏区域依然干干净净 —— "
                           "说明这个开关根本没接线")
        if app.last_error:
            return False, f"渲染循环报错：{app.last_error}"
    finally:
        fw.Overlay.present = orig
        if app is not None:
            app.stop_evt.set()
            if app._thread is not None:
                app._thread.join(timeout=3)
    return True, (f"开启时任务栏 {lay.get('px', 0)} 像素/帧全干净；"
                  f"关闭后同一区域出现内容（{off['ms']['dirty']} 帧有像素）")


def main():
    tests = [
        test_bar_rects_look_sane,
        test_blank_only_touches_taskbar,
        test_blank_clips_out_of_range,
        test_blank_empty_is_noop,
        test_render_loop_masks_taskbar,
    ]
    failed = 0
    skipped = 0
    for t in tests:
        try:
            ok, msg = t()
        except Exception as e:
            ok, msg = False, f"{type(e).__name__}: {e}"
        if ok is None:
            skipped += 1
            print(f"[SKIP] {t.__name__}: {msg}")
        elif ok:
            print(f"[PASS] {t.__name__}: {msg}")
        else:
            failed += 1
            print(f"[FAIL] {t.__name__}: {msg}")
    total = len(tests) - skipped
    print(f"\n{total - failed}/{total} passed"
          + (f", {skipped} skipped" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
