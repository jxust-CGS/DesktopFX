# -*- coding: utf-8 -*-
"""真机验证：用「初雪」预设启动，确认雪真的画到了桌面上。
抓前后两帧差分，统计「变化的明亮像素」——雪是动态且偏白的，应当大量出现。
结束时删除临时 config.json，恢复默认（雨）启动状态。
"""
import os
import sys
import time
import json
import ctypes

import numpy as np
from PIL import Image, ImageGrab

# 本脚本住在 tools/ 子目录里：`sys.path[0]` 与 `__file__` 的父目录都不再是项目根。
# 向上找到含 desktop_fx.py 的目录（= 项目根）当基准，import 与输出路径才成立。
ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import desktop_fx as d

# 1) 写入「初雪」预设配置
cfg = dict(d.DEFAULTS)
cfg.update(d.PRESETS["初雪"])
with open(d.CONFIG_PATH, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print(f"[diag] wrote config.json with preset 初雪 (snow_enabled={cfg['snow_enabled']}, rain_enabled={cfg['rain_enabled']})")

# 2) 清掉残留进程
os.system("taskkill /F /IM pythonw.exe >nul 2>&1")
time.sleep(1.5)

# 3) 启动
bat = os.path.join(d.APP_DIR, "启动桌面特效.bat")
os.startfile(bat)
print("[diag] launching...")
time.sleep(7)

# 4) 抓虚拟屏幕两帧
try:
    a = np.asarray(ImageGrab.grab().convert("RGB"), dtype=np.int32)
    time.sleep(0.5)
    b = np.asarray(ImageGrab.grab().convert("RGB"), dtype=np.int32)
except Exception as e:
    print("[diag] screenshot failed:", e)
    a = b = None

if a is not None:
    diff = np.abs(a - b).max(axis=2)
    changed = diff > 12
    bright = (b.sum(axis=2) > 400)          # 偏白：雪是淡色
    snowish = int((changed & bright).sum())
    total = b.shape[0] * b.shape[1]
    pct = snowish / total * 100
    print(f"[diag] changed bright (snow-ish) pixels: {snowish} / {total}  ({pct:.3f}%)")
    # 同时统计任意变化像素，区分"有动画"与"雪色"
    any_changed = int(changed.sum())
    print(f"[diag] any changed pixels: {any_changed}  ({any_changed/total*100:.3f}%)")
    ok = snowish > 200
    print("[diag]", "SNOW VISIBLE ON DESKTOP ✔" if ok else "SNOW NOT VISIBLE �’")
else:
    ok = False

# 5) 清理
os.system("taskkill /F /IM pythonw.exe >nul 2>&1")
time.sleep(1)
try:
    os.remove(d.CONFIG_PATH)
    print("[diag] removed temp config.json (restored default rain startup)")
except Exception as e:
    print("[diag] could not remove config.json:", e)

sys.exit(0 if ok else 1)
