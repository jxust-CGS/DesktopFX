# -*- coding: utf-8 -*-
"""渲染 SnowField 单帧到 PNG，用于肉眼校对雪花大小分档 / 纵深 / 辉光。

用法: python _snow_visual.py <out.png> [snow_size | key=value ...]
  例: python _snow_visual.py a.png 1.0            # 只想改倍率
      python _snow_visual.py b.png snow_glow=0    # 关掉辉光对比
      python _snow_visual.py c.png snow_size=1.4 snow_glow=1.0 snow_amount=560
输出为屏幕中心 640x360 区域放大 2 倍（雪花看得清），深蓝夜空底。
"""
import os
os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")

import sys
import numpy as np
from PIL import Image
import desktop_fx as d
from fx_effects import SnowField

OUT = sys.argv[1] if len(sys.argv) > 1 else "_snow_frame.png"

W, H = 1920, 1080
cfg = dict(d.DEFAULTS)
cfg["snow_enabled"] = True
cfg["snow_amount"] = 320
cfg["snow_color"] = "#FFFFFF"
cfg["snow_opacity"] = 0.95
cfg["snow_size"] = 1.0
FRAMES = 120

# 第三个起：裸数字 = snow_size，k=v = 任意配置键覆盖
for a in sys.argv[2:]:
    if "=" in a:
        k, v = a.split("=", 1)
        k = k.strip()
        if k not in cfg:
            print("未知配置键: %s" % k)
            sys.exit(2)
        cfg[k] = type(cfg[k])(float(v))
    else:
        cfg["snow_size"] = float(a)


def _num(x):
    return "%.2f" % x if isinstance(x, float) else str(x)


print("overrides: " + ", ".join(
    "%s=%s" % (k, _num(cfg[k])) for k in
    ("snow_size", "snow_amount", "snow_glow", "snow_twinkle", "snow_wind")))

snow = SnowField(W, H, seed=11)
snow.set_color(cfg["snow_color"])
snow.set_amount(int(cfg["snow_amount"]), W, H)
for _ in range(FRAMES):
    snow.update(1.0 / 60.0, cfg)

buf = np.zeros((H, W, 4), np.uint8)
u32 = buf.view(np.uint32).reshape(-1)
snow.draw(u32, W, H, cfg)

# 深蓝夜空渐变底
yy = np.linspace(0.0, 1.0, H, dtype=np.float32)[:, None]
bg = np.zeros((H, W, 3), np.float32)
bg[..., 0] = 8.0 + 14.0 * yy       # B
bg[..., 1] = 12.0 + 18.0 * yy      # G
bg[..., 2] = 26.0 + 30.0 * yy      # R

a = buf[..., 3].astype(np.float32) / 255.0
pm = buf[..., [2, 1, 0]].astype(np.float32)      # 预乘 RGB
out = bg * (1.0 - a[..., None]) + pm

crop = out[360:720, 640:1280]
img = Image.fromarray(np.clip(crop, 0, 255).astype(np.uint8), "RGB")
img = img.resize((1280, 720), Image.LANCZOS)
img.save(OUT)

# 量化：点亮面积 + 三档粒子数
lit = int((buf[..., 3] > 8).sum())
lv = np.digitize(snow.depth, SnowField.TIER_EDGES)
print("saved %s  lit=%d (%.2f%% of screen)  depth %.2f~%.2f" % (
    OUT, lit, lit / (W * H) * 100.0, snow.depth.min(), snow.depth.max()))
print("tiers far/mid/near = %d/%d/%d  (glow 只作用于 near 里 vary>=1 的那部分)" % (
    int((lv == 0).sum()), int((lv == 1).sum()), int((lv == 2).sum())))
