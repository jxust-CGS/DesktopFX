# -*- coding: utf-8 -*-
"""离屏自检：跑若干帧并把结果合成为 PNG，用于验证渲染正确性与性能。"""
import math
import os
import struct
import sys
import time
import zlib

import numpy as np

# 本脚本住在 tools/ 子目录里：`sys.path[0]` 与 `__file__` 的父目录都不再是项目根。
# 向上找到含 desktop_fx.py 的目录（= 项目根）当基准，import 与输出路径才成立。
ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from fx_effects import RainField, RippleField, hex_to_bgr  # noqa: E402


def write_png(path, rgb):
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[i].tobytes() for i in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(chunk(b"IEND", b""))


def compose(buf, w, h, bg=(26, 32, 42)):
    a = (buf >> np.uint32(24)).astype(np.float32) / 255.0
    b = (buf & np.uint32(0xFF)).astype(np.float32)
    g = ((buf >> np.uint32(8)) & np.uint32(0xFF)).astype(np.float32)
    r = ((buf >> np.uint32(16)) & np.uint32(0xFF)).astype(np.float32)
    out = np.zeros((h, w, 3), np.uint8)
    for i, (c, ch) in enumerate(zip((r, g, b), bg)):
        out[:, :, i] = np.clip(c.reshape(h, w) + np.float32(ch) * (1.0 - a.reshape(h, w)),
                               0, 255).astype(np.uint8)
    return out


def run(width=1280, height=720, frames=180, seed=3, out="preview.png"):
    np.random.seed(seed)
    buf = np.zeros(height * width, np.uint32)

    rain = RainField(width, height, seed=seed)
    rain.set_amount(300, width, height)
    rain.set_color("#BFE3FF")
    rip = RippleField()
    rc = hex_to_bgr("#7FE7FF")

    cfg = dict(rain_speed=900.0, rain_length=1.0, wind=0.12, rain_opacity=0.75,
               ripple_opacity=0.85, ripple_size=145.0, ripple_life=0.95, splash=True)

    mx, my, acc = 0.0, 0.0, 0.0
    dt = 1.0 / 60.0
    t0 = time.perf_counter()
    for f in range(frames):
        buf[:] = 0
        sx = rain.update(dt, cfg)
        rain.draw(buf, width, height, cfg)
        for k in range(min(5, sx.size)):
            rip.spawn(float(sx[k]), float(height - 2),
                      rmax=float(np.random.uniform(9, 28)),
                      life=float(np.random.uniform(0.30, 0.52)),
                      strength=0.34 * cfg["ripple_opacity"], color_bgr=rc,
                      r0=1.0, squash=0.30)
        t = f * dt
        nx = width * 0.5 + width * 0.30 * math.sin(t * 2.0)
        ny = height * 0.5 + height * 0.28 * math.sin(t * 4.0) * math.cos(t * 1.3)
        acc += ((nx - mx) ** 2 + (ny - my) ** 2) ** 0.5
        mx, my = nx, ny
        step = 24.0
        while acc >= step:
            acc -= step
            rip.spawn(mx, my, float(cfg["ripple_size"] * (0.55 + 0.55 * np.random.random())),
                      cfg["ripple_life"], 0.45, rc, r0=2.0)
        if f == 90:
            rip.spawn(mx, my, cfg["ripple_size"] * 1.9, cfg["ripple_life"] * 1.35,
                      0.9, rc, r0=3.0)
        rip.update(dt)
        rip.draw(buf, width, height)

    elapsed = time.perf_counter() - t0
    nonzero = int((buf != 0).sum())
    alpha_mean = float(((buf >> np.uint32(24)).astype(np.float32) / 255.0)[buf != 0].mean())

    write_png(out, compose(buf, width, height))
    print(f"frames      : {frames}")
    print(f"resolution  : {width}x{height}")
    print(f"render only : {elapsed / frames * 1000:.2f} ms/frame  "
          f"({frames / elapsed:.0f} fps, 不含 UpdateLayeredWindow)")
    print(f"drawn px    : {nonzero}  ({nonzero / (width * height) * 100:.2f}% of screen)")
    print(f"mean alpha  : {alpha_mean:.3f}")
    print(f"preview     : {os.path.abspath(out)}")
    return elapsed / frames * 1000


if __name__ == "__main__":
    run()
