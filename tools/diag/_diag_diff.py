# -*- coding: utf-8 -*-
"""把 A/B 帧差做成增强图，方便肉眼判断是否真有雨在动。"""
import os, numpy as np
from PIL import Image
# 本脚本住在 tools/ 子目录里：`sys.path[0]` 与 `__file__` 的父目录都不再是项目根。
# 向上找到含 desktop_fx.py 的目录（= 项目根）当基准，import 与输出路径才成立。
ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
A = np.asarray(Image.open(os.path.join(ROOT, "_diag_A.png")).convert("RGB")).astype(np.int16)
B = np.asarray(Image.open(os.path.join(ROOT, "_diag_B.png")).convert("RGB")).astype(np.int16)
diff = np.abs(A - B).astype(np.uint8)
# 增强：任何通道>0 都标成亮青色，便于看见
enh = np.zeros_like(diff)
chg = diff.sum(axis=2)
enh[chg>0] = [0, 230, 255]
Image.fromarray(enh).save(os.path.join(ROOT, "_diag_diff.png"))
print("saved _diag_diff.png ; 变化像素:", int((chg>0).sum()))
