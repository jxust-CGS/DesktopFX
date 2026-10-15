# -*- coding: utf-8 -*-
"""一次性预处理：把参考图右上角的“豆包”水印抹掉，输出干净 PNG 供 _make_icon.py 用。

水印位于纯色深底上（不与主体光效重叠），所以直接用周边背景色盖掉即可。
"""
import os
import sys

import numpy as np
from PIL import Image

SRC = r"C:\Users\hspcadmin\.workbuddy\clipboard-images\clipboard-2026-10-15T06-14-14-969Z-7b6860e6.jpg"
DST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_icon_ref_clean.png")

im = Image.open(SRC).convert("RGB")
w, h = im.size
print("源图 %dx%d" % (w, h))

px = np.asarray(im).copy()

# 水印区域：右上角。取大一点把光晕残边也盖住，但不动圆环主体。
# 目测水印在 x ∈ [0.88w, 1.0w], y ∈ [0.0h, 0.16h]；该处背景为均匀深色。
x0, x1 = int(w * 0.86), w
y0, y1 = 0, int(h * 0.18)

# 用该区域外一圈的背景中位数填充（比单点采样稳）
ring = np.concatenate([
    px[y1:y1 + 20, x0:x1].reshape(-1, 3),      # 水印下方
    px[y0:y1, max(0, x0 - 60):x0].reshape(-1, 3),  # 水印左侧
])
ref = np.median(ring, axis=0)
print("背景参考色 RGB=%s" % ref.astype(int))

patch = px[y0:y1, x0:x1].astype(np.int16)
diff = np.abs(patch - ref).max(axis=2)
print("覆盖前该区域内与背景色差>26 的像素：%d" % int((diff > 26).sum()))

px[y0:y1, x0:x1] = ref.astype(np.uint8)

out = Image.fromarray(px, "RGB")
out.save(DST)
print("已输出 %s" % DST)
