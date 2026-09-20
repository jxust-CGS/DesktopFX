# -*- coding: utf-8 -*-
"""长跑探针：真实 App（含两个覆盖窗、每帧上屏、抢顶、托盘）跑 N 分钟，
回答"长时间运行会不会越来越卡"。观测四类会随时间恶化的量：

  1. RSS 内存        —— numpy 大数组/字典是否泄漏
  2. GDI / USER 句柄 —— Win32 桌面进程最隐蔽的卡顿源（默认上限 10000，
                         爆掉之后不是本程序卡，是整机桌面都卡）
  3. 帧耗时与帧间隔   —— 看是缓慢漂移还是突发
  4. 窗口活塞事件驱动 —— 周期性注入 change 事件，让新的水波层真的在动

过程完全不碰用户数据：CONFIG_PATH / USER_PRESETS_PATH / ERROR_LOG_PATH
在导入后改到临时副本；不创建控制面板（也就不涉及 ui_state.json）。
"""
import ctypes
import os
import shutil
import statistics
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import desktop_fx as d                      # noqa: E402
d._set_dpi_awareness()

# ---- 数据隔离：绝不读写用户的配置文件
_ISOLATE = (("CONFIG_PATH", "_longrun_config.json"),
            ("USER_PRESETS_PATH", "_longrun_presets.json"),
            ("ERROR_LOG_PATH", "_longrun_error.log"))
_tmps = []
for attr, tmp_name in _ISOLATE:
    src = getattr(d, attr)
    tmp = os.path.join(HERE, tmp_name)
    if os.path.exists(src):
        shutil.copy2(src, tmp)
    setattr(d, attr, tmp)
    _tmps.append(tmp)

import fx_scene                               # noqa: E402

try:
    import psutil
    _PROC = psutil.Process()
    HAS_PS = True
except Exception:
    _PROC = None
    HAS_PS = False

_kernel32 = ctypes.windll.kernel32
_user32 = ctypes.windll.user32
GR_GDIOBJECTS, GR_USEROBJECTS = 0, 1


def _gdi():
    """进程当前持有的 GDI / USER 对象数。

    注意 ctypes 必须显式给 GetGuiResources 设 argtypes/restype：默认把
    HANDLE 当 32 位 int 传，64 位下句柄被截断，函数直接失败返回 0 —— 那样
    采样会恒为 0，"句柄没泄漏"就变成一句空话。
    """
    _user32.GetGuiResources.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    _user32.GetGuiResources.restype = ctypes.c_uint
    h = _kernel32.GetCurrentProcess()
    return (int(_user32.GetGuiResources(h, GR_GDIOBJECTS)),
            int(_user32.GetGuiResources(h, GR_USEROBJECTS)))


def _rss_mb():
    return _PROC.memory_info().rss / 1048576.0 if HAS_PS else 0.0


# ---- 挂钩 Scene.step：采帧间隔 + 周期性注入窗口活塞事件
FRAME_TS = []
STATE = {"frames": 0, "inject": 0}

_orig_step = fx_scene.Scene.step


def _step(self, dt, *a, **k):
    FRAME_TS.append(time.perf_counter())
    n = STATE["frames"] = STATE["frames"] + 1
    # 模拟"拖一下、停一下"的真实节奏：每 90 帧（约 1.5s）连拖 3 帧
    try:
        if n % 90 in (1, 2, 3):
            i = STATE["inject"] = STATE["inject"] + 1
            x = 420.0 + (i % 24) * 14.0
            self._win_events = [("change", (x, 210.0, x + 360.0, 560.0),
                                 (x + 14.0, 210.0, x + 374.0, 560.0),
                                 max(float(dt), 1e-3))]
        elif self._win_events:
            self._win_events = []
    except Exception:
        pass
    return _orig_step(self, dt, *a, **k)


fx_scene.Scene.step = _step

DUR = float(os.environ.get("LONGRUN_SECS", "240"))
MID = DUR * 0.5

app = d.App()
app._thread = threading.Thread(target=app._render_loop, name="fx-render",
                               daemon=True)
app._thread.start()

print(f"长跑 {DUR:.0f} 秒（真实 App：两个覆盖窗 + 每帧上屏 + 抢顶 + 托盘）")
print(f"{'时刻':>6}  {'RSS MB':>9}  {'GDI':>5}  {'USER':>5}  {'句柄':>5}  "
      f"{'帧/秒':>6}  {'ms/帧':>7}  {'活动特效'}")
sys.stdout.flush()

rows = []
t0 = time.time()
next_tick = 0.0
switched = False
while True:
    el = time.time() - t0
    if el >= DUR:
        break
    time.sleep(min(1.0, DUR - el))
    if el >= next_tick:
        next_tick += 10.0
        fps, cost = app.fps, app.cost_ms
        rss = _rss_mb()
        gdi, usr = _gdi()
        nh = _PROC.num_handles() if HAS_PS else 0
        cfg = dict(app.cfg)
        on = [k.replace("_enabled", "") for k, v in cfg.items()
              if k.endswith("_enabled") and v]
        rows.append({"t": el, "rss": rss, "gdi": gdi, "usr": usr, "nh": nh,
                     "fps": fps, "cost": cost, "on": on})
        print(f"{el:6.0f}  {rss:9.1f}  {gdi:5d}  {usr:5d}  {nh:5d}  "
              f"{fps:6.1f}  {cost:7.2f}  {','.join(on)}")
        sys.stdout.flush()
    # 中段切到「水面」预设：覆盖共用水波的完整参数
    if not switched and el >= MID:
        switched = True
        try:
            if "水面" in app.preset_names():
                app.apply_preset("水面")
                print(f"[{el:.0f}s] 已切换到预设「水面」（真实水波 + 窗口水波）")
                sys.stdout.flush()
        except Exception as exc:
            print("切换预设失败:", exc)

frames_before = len(FRAME_TS)
app.stop_evt.set()
app._thread.join(timeout=8.0)
try:
    if app.tray is not None:
        app.tray.stop()
except Exception:
    pass

# ---- 汇总
print("\n" + "=" * 78)
if rows:
    r0, r1 = rows[0], rows[-1]
    print(f"内存   {r0['rss']:.1f} MB -> {r1['rss']:.1f} MB  "
          f"(增长 {r1['rss'] - r0['rss']:+.1f} MB"
          + (f", {100.0 * (r1['rss'] - r0['rss']) / max(r0['rss'], 1):.1f}%)" if r0['rss'] else "") + ")")
    print(f"GDI 对象 {r0['gdi']} -> {r1['gdi']}    USER 对象 {r0['usr']} -> {r1['usr']}"
          f"    Win32 句柄 {r0['nh']} -> {r1['nh']}（上限 10000 指 GUI 对象）")
    print(f"帧耗时   {r0['cost']:.2f} -> {r1['cost']:.2f} ms/帧   "
          f"帧率  {r0['fps']:.1f} -> {r1['fps']:.1f}")
    # 分段趋势：区分"启动瞬态"与"持续退化"。首尾两点最容易被启动瞬间骗到，
    # 所以再把中位段与末段比一次。
    q = max(1, len(rows) // 3)
    mid, end = rows[q], rows[-q]
    if mid["cost"] > 0:
        drift = (end["cost"] - mid["cost"]) / mid["cost"] * 100
        print(f"分段趋势（剔掉头尾各 {q} 个采样）：中段 {mid['cost']:.2f} ms/帧"
              f" -> 末段 {end['cost']:.2f} ms/帧（{drift:+.0f}%）")

ivs = [(FRAME_TS[i + 1] - FRAME_TS[i]) * 1000.0
       for i in range(len(FRAME_TS) - 1)]
if ivs:
    s = sorted(ivs)
    print(f"\n帧间隔（共 {len(ivs)} 帧）：平均 {statistics.mean(ivs):.2f}  "
          f"p50 {s[len(s) // 2]:.2f}  p95 {s[int(len(s) * .95)]:.2f}  "
          f"p99 {s[int(len(s) * .99)]:.2f}  最大 {max(ivs):.2f} ms")

# ---- 判定
print()
ok = True
if r1["rss"] - r0["rss"] > 40:
    print("✗ 内存增长超过 40 MB —— 有泄漏嫌疑")
    ok = False
else:
    print("✓ 内存基本平稳（增长 <= 40 MB，含解释器与 numpy 的自然抖动）")
if r1["gdi"] > r0["gdi"] + 200 or r1["usr"] > r0["usr"] + 200 or \
        r1["nh"] > r0["nh"] + 200:
    print(f"✗ 句柄增长 GDI {r1['gdi'] - r0['gdi']:+d} / USER {r1['usr'] - r0['usr']:+d}"
          f" / Win32 {r1['nh'] - r0['nh']:+d} —— 有泄漏")
    ok = False
else:
    print("✓ GDI / USER / Win32 句柄均无净增长")
if len(rows) >= 3 and mid["cost"] > 0 and \
        (end["cost"] - mid["cost"]) / mid["cost"] > 0.5:
    print(f"✗ 帧耗时持续漂移（中段 -> 末段 {drift:+.0f}%），不只是启动瞬态")
    ok = False
elif r0["cost"] > 0 and (r1["cost"] - r0["cost"]) / r0["cost"] > 0.5:
    print(f"✗ 帧耗时漂移 {(r1['cost'] - r0['cost']) / r0['cost'] * 100:+.0f}%")
    ok = False
else:
    print("✓ 帧耗时无持续漂移（若有变化多来自启动铺满与切换预设，非累积退化）")

for f in _tmps:
    try:
        if os.path.exists(f):
            os.remove(f)
    except Exception:
        pass
print("\n" + ("== LONGRUN OK ==" if ok else "== LONGRUN SUSPECT =="))
# 给个退出码，方便脚本化调用：
#   LONGRUN_SECS=600 python _probe_longrun.py && echo 可以发版
sys.exit(0 if ok else 1)
