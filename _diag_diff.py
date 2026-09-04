# -*- coding: utf-8 -*-
"""把 A/B 帧差做成增强图，方便肉眼判断是否真有雨在动。"""
import os, numpy as np
from PIL import Image
ROOT = os.path.dirname(os.path.abspath(__file__))
A = np.asarray(Image.open(os.path.join(ROOT, "_diag_A.png")).convert("RGB")).astype(np.int16)
B = np.asarray(Image.open(os.path.join(ROOT, "_diag_B.png")).convert("RGB")).astype(np.int16)
diff = np.abs(A - B).astype(np.uint8)
# 增强：任何通道>0 都标成亮青色，便于看见
enh = np.zeros_like(diff)
chg = diff.sum(axis=2)
enh[chg>0] = [0, 230, 255]
Image.fromarray(enh).save(os.path.join(ROOT, "_diag_diff.png"))
print("saved _diag_diff.png ; 变化像素:", int((chg>0).sum()))
