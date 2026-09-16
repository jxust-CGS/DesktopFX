# -*- coding: utf-8 -*-
"""性能探针：跑真实渲染循环，把每帧耗时拆到"特效 update/draw / 上屏 / 抢顶"各项。

配置用**副本**（不改用户 config.json），特效组合与用户当前一致（真实水波 + 雪）。
产出：实际帧率、帧间隔 p50/p95/max、掉帧数、各阶段 ms/帧 排行。
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

# ---- 配置隔离：用副本，绝不碰用户的 config.json
tmp_cfg = os.path.join(HERE, "_perf_config.json")
shutil.copy2(d.CONFIG_PATH, tmp_cfg)
d.CONFIG_PATH = tmp_cfg

import fx_winlayer                          # noqa: E402
import fx_scene                             # noqa: E402

STATS = {}          # label -> [总耗时, 次数, 最大单次]
ORDER = []          # Overlay 实例顺序：[0]=全屏层, [1]=鼠标层
FRAME_TS = []


def _accum(label, cost):
    s = STATS.setdefault(label, [0.0, 0, 0.0])
    s[0] += cost
    s[1] += 1
    if cost > s[2]:
        s[2] = cost


def _wrap(obj, name, label, label_fn=None):
    orig = getattr(obj, name)

    def w(*a, **k):
        t = time.perf_counter()
        try:
            return orig(*a, **k)
        finally:
            lab = label_fn(*a, **k) if label_fn else label
            _accum(lab, time.perf_counter() - t)

    setattr(obj, name, w)


# ---- Overlay 层：上屏 / 抢顶 / 消息泵
_orig_create = fx_winlayer.Overlay.create


def _create(self):
    r = _orig_create(self)
    if self not in ORDER:
        ORDER.append(self)
    return r


fx_winlayer.Overlay.create = _create


def _layer_tag(self):
    try:
        return "全屏层" if ORDER and self is ORDER[0] else "鼠标层"
    except Exception:
        return "?"


def _make_label_fn(lab):
    def f(self, *a, **k):
        return f"{lab}[{_layer_tag(self)}]"
    return f


for meth, lab in (("present", "上屏 UpdateLayeredWindow"),
                  ("keep_on_top", "抢顶 keep_on_top"),
                  ("pump", "消息泵 pump")):
    _wrap(fx_winlayer.Overlay, meth, lab, _make_label_fn(lab))

for fn, lab in (("system_bar_rects", "任务栏矩形枚举"),
                ("blank_rects", "擦任务栏")):
    if hasattr(fx_winlayer, fn):
        _wrap(fx_winlayer, fn, lab)

# ---- Scene 层：先量总量，再把内部各特效逐个插桩
_orig_step = fx_scene.Scene.step


def _step(self, dt, *a, **k):
    FRAME_TS.append(time.perf_counter())
    if not getattr(self, "_probe_installed", False):
        self._probe_installed = True
        for nm in ("rain", "snow", "petals", "meteors", "paint", "ripples",
                   "wave", "spot", "win"):
            obj = getattr(self, nm, None)
            if obj is None:
                continue
            for m in ("update", "draw", "step"):
                if hasattr(obj, m) and callable(getattr(obj, m)):
                    _wrap(obj, m, f"特效 {nm}.{m}")
        _wrap(self.mouse, "update", "特效 mouse.update")
    t = time.perf_counter()
    try:
        return _orig_step(self, dt, *a, **k)
    finally:
        _accum("Scene.step 合计", time.perf_counter() - t)


fx_scene.Scene.step = _step

# ---- 跑起来
app = d.App()
app._thread = threading.Thread(target=app._render_loop, name="fx-render",
                               daemon=True)
app._thread.start()

DUR = float(os.environ.get("PERF_SECS", "8"))
print(f"渲染循环运行 {DUR:.0f} 秒 …（特效：见 config）")
sys.stdout.flush()
samples = []
t_end = time.time() + DUR
while time.time() < t_end:
    samples.append((time.perf_counter(), app.fps, app.cost_ms))
    time.sleep(0.1)

frames_snapshot = len(FRAME_TS)
app.stop_evt.set()
app._thread.join(timeout=5.0)

# ---- 汇总
print("\n" + "=" * 72)
cfg = app.cfg
on = [k for k, v in cfg.items() if k.endswith("_enabled") and v]
print("开启特效:", on, "| 目标帧率:", cfg.get("fps"))
print("虚拟屏:", fx_winlayer.virtual_screen())

if len(FRAME_TS) >= 2:
    t0, t1 = FRAME_TS[0], FRAME_TS[-1]
    span = t1 - t0
    n = len(FRAME_TS) - 1
    ivs = [(FRAME_TS[i + 1] - FRAME_TS[i]) * 1000.0 for i in range(n)]
    ivs_sorted = sorted(ivs)
    target_ms = 1000.0 / max(20.0, min(144.0, float(cfg.get("fps", 60.0))))
    jank = [v for v in ivs if v > target_ms * 1.5]
    print(f"\n--- 帧节奏 ---")
    print(f"  帧数 {n}  跨度 {span:.2f}s  →  实际 {n / span:.1f} 帧/秒"
          f"（目标 {1000.0 / target_ms:.0f}）")
    print(f"  帧间隔 ms: 平均 {statistics.mean(ivs):.2f}  "
          f"p50 {ivs_sorted[n // 2]:.2f}  "
          f"p95 {ivs_sorted[int(n * 0.95)]:.2f}  "
          f"p99 {ivs_sorted[int(n * 0.99)]:.2f}  最大 {max(ivs):.2f}")
    print(f"  掉帧(>1.5×目标 {target_ms:.1f}ms): {len(jank)} 次"
          f"（占 {100.0 * len(jank) / max(n, 1):.1f}%）")
    print(f"  app.cost_ms 末值 {app.cost_ms:.2f}  app.fps 末值 {app.fps:.1f}")

print(f"\n--- 各阶段耗时（按 ms/帧 排行）---")
print(f"{'阶段':34s} {'ms/帧':>8s} {'ms/次':>8s} {'最大ms':>8s} {'次数':>7s}")
rows = []
for lab, (tot, cnt, mx) in STATS.items():
    rows.append((tot / max(n, 1), tot / max(cnt, 1), mx, cnt, lab))
rows.sort(reverse=True)
for per_frame, per_call, mx, cnt, lab in rows:
    print(f"{lab:34s} {per_frame * 1000:8.3f} {per_call * 1000:8.3f} "
          f"{mx * 1000:8.3f} {cnt:7d}")

busy = sum(r[0] for r in rows) * 1000.0
print(f"\n  总计插桩耗时 {busy:.2f} ms/帧（未含 u32 清零、z 序重排、帧率等待）")

# ---- 清理（不调 app.save()：绝不让探针写任何配置文件）
for f in (tmp_cfg,):
    if os.path.exists(f):
        os.remove(f)
print("\n完成（已删除临时配置副本，用户 config.json 未改动）")
