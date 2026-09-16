# -*- coding: utf-8 -*-
"""顺序跑完整套验证脚本并汇总（**必须顺序**，不要并行）。

为什么强调顺序：并行跑会互相抢 CPU 与 z 序窗口，之前因此出过假失败 ——
单实例/启动器这类靠子进程启动时序的脚本在大负载下会误报（详见
`_verify_single_instance.py` / `_verify_launcher.py` 里改成事件驱动的原因）。
桌面特效脚本大多要建真窗口、动真 z 序，同样经不起并行。

用法（按改动范围选，别一律跑全量）：
    python _run_all_verify.py --fast     # 核心回归，约 1.5 分钟 —— 日常收尾默认用这个
    python _run_all_verify.py --skip-slow  # 全部但要略过已知慢的，约 3 分钟
    python _run_all_verify.py            # 全部，约 11 分钟（含性能/长跑基准）

为什么要有分层：脚本数量只会增加，全量跑一遍的耗时随之线性增长；而绝大多数
改动（UI / 配置 / 层级 / 单特效）与性能基准、长跑稳定性、全预设烟雾无关。
把它们从日常收尾里摘出去，能省下约 8 分钟。
"""
import glob
import os
import subprocess
import sys
import time

# 核心回归：覆盖"改了容易坏、坏了用户立刻能看出来"的那几块，约 1.5 分钟。
FAST = [
    "_verify_kinds.py",          # 特效分类表自洽（2s）
    "_verify_zorder.py",         # 置顶保活 / 插入锚点（9s）
    "_verify_layer_switch.py",    # 层级开关端到端（34s）
    "_verify_taskbar.py",        # 避让任务栏（4s）
    "_verify_ui_runtime.py",      # 面板运行时冒烟（8s）
    "_verify_ui_resize.py",       # 窗口可缩放 / 尺寸记忆 / 提示迁移（16s）
    "_verify_layer_skip.py",      # 层判空的安全不变量，无窗口（22s）
    "_verify_window_ripple.py",   # 窗口 = 水面活塞（事件流 / 物理 / 接线，无窗口，1s）
    "_verify_wave_draw.py",       # 水面上屏：查表着色 + 直写（1s）
    "_verify_startup_noflicker.py",  # 启动无闪动：构造期全程隐藏（1s）
]

# 已知慢的：性能基准、长跑稳定性、全预设烟雾、以及要真拉起程序的几个。
# 与 UI / 配置 / 层级改动无关，按需再跑。
SLOW = [
    "_verify_smoke.py",          # 全预设 60 帧（155s）
    "_verify_perf_layers.py",    # 4K 双屏最坏情况 + 3600 帧（141s）
    "_verify_stability.py",      # 18000 帧长跑（87s）
    "_verify_meteor.py",         # 39.7s
    "_verify_tray.py",           # 真机拉起程序（39s）
    "_verify_wave.py",           # 32s
    "_verify_launcher.py",       # 真机拉起程序（12s）
    "_verify_render_loop.py",    # 真实渲染循环的阶段耗时拆解（10s，只测不判，人工看）
]


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    py = sys.executable
    all_scripts = sorted(os.path.basename(p)
                         for p in glob.glob(os.path.join(here, "_verify_*.py")))
    if "--fast" in sys.argv:
        scripts = FAST
    elif "--skip-slow" in sys.argv:
        scripts = [s for s in all_scripts if s not in SLOW]
    else:
        scripts = all_scripts
    print(f"共 {len(all_scripts)} 个脚本，本次跑 {len(scripts)} 个", flush=True)
    rows = []
    t_all = time.time()
    for s in scripts:
        t0 = time.time()
        r = subprocess.run([py, s], cwd=here, capture_output=True, text=True,
                           errors="replace")
        dt = time.time() - t0
        tail = [l for l in (r.stdout or "").strip().splitlines() if l.strip()]
        summary = ""
        for line in reversed(tail):
            if ("passed" in line or "PASS" in line or "FAIL" in line
                    or "VERIFIED" in line or "失败" in line):
                summary = line.strip()
                break
        if not summary and tail:
            summary = tail[-1].strip()
        rows.append((s, r.returncode, dt, summary))
        print(f"{'OK ' if r.returncode == 0 else 'ERR'} {s:<30} "
              f"{dt:6.1f}s  {summary[:90]}", flush=True)
        if r.returncode != 0:
            err = [l for l in (r.stderr or "").strip().splitlines()
                   if l.strip() and "shell-runtime-bash-env" not in l]
            for l in err[-6:]:
                print(f"      | {l[:150]}", flush=True)
            for l in tail[-6:]:
                print(f"      > {l[:150]}", flush=True)

    bad = [r for r in rows if r[1] != 0]
    slow = sorted(((r[2], r[0]) for r in rows), reverse=True)[:3]
    print(f"\n==== {len(rows) - len(bad)}/{len(rows)} 个脚本通过"
          f"，总耗时 {time.time() - t_all:.0f}s ====")
    print("最慢的三个：" + "、".join(f"{n} {d:.0f}s" for d, n in slow))
    for s, rc, dt, _ in bad:
        print(f"  未通过：{s} (exit={rc})")


if __name__ == "__main__":
    main()
