# -*- coding: utf-8 -*-
"""顺序跑完整套验证脚本并汇总（**必须顺序**，不要并行）。

为什么强调顺序：并行跑会互相抢 CPU 与 z 序窗口，之前因此出过假失败 ——
单实例/启动器这类靠子进程启动时序的脚本在大负载下会误报（详见
`_verify_single_instance.py` / `_verify_launcher.py` 里改成事件驱动的原因）。
桌面特效脚本大多要建真窗口、动真 z 序，同样经不起并行。

用法：
    python _run_all_verify.py            # 跑全部 _verify_*.py
    python _run_all_verify.py --fast     # 只跑与 z 序/分层/任务栏/UI 相关的几个
"""
import glob
import os
import subprocess
import sys
import time

FAST = [
    "_verify_zorder.py",
    "_verify_kinds.py",
    "_verify_layers.py",
    "_verify_taskbar.py",
    "_verify_ui_runtime.py",
    "_verify_visibility.py",
    "_verify_smoke.py",
]


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    py = sys.executable
    if "--fast" in sys.argv:
        scripts = FAST
    else:
        scripts = sorted(os.path.basename(p)
                         for p in glob.glob(os.path.join(here, "_verify_*.py")))
    rows = []
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
    print(f"\n==== {len(rows) - len(bad)}/{len(rows)} 个脚本通过 ====")
    for s, rc, dt, _ in bad:
        print(f"  未通过：{s} (exit={rc})")


if __name__ == "__main__":
    main()
