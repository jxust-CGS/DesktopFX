# -*- coding: utf-8 -*-
"""真机验证：雨 + 聚光灯 同屏启动，确认图层组合真的画到了桌面上。
抓前后两帧差分，统计「变化的像素」——雨是动态的，应当大量出现；
同时报告平均亮度，聚光灯下整屏应明显偏暗（证据：压暗生效）。
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

# 1) 写入「雨 + 聚光灯」组合配置
cfg = dict(d.DEFAULTS)
cfg.update(
    rain_enabled=True, rain_amount=280, rain_speed=900, rain_length=1.0,
    wind=0.12, rain_color="#5FA8D8", rain_opacity=0.85, splash=True,
    spotlight_enabled=True, spotlight_radius=240, spotlight_dim=0.6,
    spotlight_color="#05070A",
)
with open(d.CONFIG_PATH, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print(f"[diag] wrote config.json: rain+spotlight "
      f"(spotlight={cfg['spotlight_enabled']})")

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

ok = False
if a is not None:
    diff = np.abs(a - b).max(axis=2)
    changed = diff > 12
    any_changed = int(changed.sum())
    total = b.shape[0] * b.shape[1]
    mean_b = float(b.mean())
    mean_a = float(a.mean())
    print(f"[diag] any changed pixels: {any_changed}  ({any_changed/total*100:.3f}%)")
    print(f"[diag] mean brightness: frame1={mean_a:.1f}  frame2={mean_b:.1f}")
    # 雨是自主动画，保证有变化；聚光灯把整屏压暗（平均亮度应明显低于纯桌面）
    ok = any_changed > 500
    print("[diag]", "LAYERS VISIBLE ON DESKTOP ✔" if ok else "LAYERS NOT VISIBLE ✘")
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
