# -*- coding: utf-8 -*-
"""生成"流星雨覆盖整屏"的模型对照图（docs/images/meteor-full-screen.png）。

上：旧写法（燃尽距离 = 尾长 × 1.3~2.3，与屏幕尺寸无关）—— 1920×1200 下十段
    瞬时密度 41/42/35/26/17/11/9/7/7/7，顶部 20% 是底部 20% 的 5.7 倍，
    密度从 40% 高度起断崖式掉下去。
下：新写法（燃尽距离 = 出生点沿飞行方向到背风边的距离 × [0.40, 1.35]，>1 表示
    允许飞过屏边再燃尽）—— 十段 30/30/29/27/25/23/21/18/14/8，整屏都有流星。

做法：把 N 秒内每一帧画上去的像素**累积**成一张"长曝光"图（白 = 亮过），
单帧像素太少看不出覆盖差异；同时统计每段的**瞬时密度**（眼睛真正看到的东西）。
两条横线是 50% / 80% 高度参考线。

注意"底部有没有像素"是个会骗人的判据：旧实现在底部一直有零星像素（左边缘低处
出生的那几颗），只是稀到肉眼看不见 —— 必须比**密度**，不能只看"有没有"。

用法：python _shot_meteor_coverage.py
"""
import os
os.environ.setdefault("DESKTOP_FX_NOWINDOW", "1")

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

import desktop_fx as d
from fx_effects import MeteorField

HERE = Path(__file__).resolve().parent
OUT = HERE / "docs" / "images" / "meteor-full-screen.png"
W, H = 1920, 1200
SECS = float(os.environ.get("SHOT_SECS", "45"))
FPS = 60.0
STRIDE = int(os.environ.get("SHOT_STRIDE", "3"))   # 每 N 帧渲一次（累积图够用）


def _load_head_meteor():
    """从 HEAD 取旧版 fx_effects，拿到它的 MeteorField 当对照组。

    不手写"旧公式" —— 旧实现里出生点、流量加权、抖动一大堆，抄一半就会得出
    一个谁也不是的中间版本（窗口水波那边就吃过这个亏）。
    """
    src = subprocess.run(["git", "show", "HEAD:fx_effects.py"],
                         cwd=HERE, capture_output=True, text=True, check=True).stdout
    tmp = Path(tempfile.mkdtemp(prefix="fxhead_")) / "fx_effects_head.py"
    tmp.write_text(src, encoding="utf-8")
    sys.path.insert(0, str(HERE))
    spec = importlib.util.spec_from_file_location("fx_effects_head", tmp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MeteorField


def run(field_cls, cfg):
    wf = field_cls(W, H, seed=31)
    wf.set_amount(int(cfg["meteor_amount"]), W, H)
    acc = np.zeros((H, W), np.uint8)     # 长曝光：这行有没被点亮过
    dens = np.zeros(H, np.int64)         # 瞬时密度：每行平均点亮多少像素
    n = 0
    for i in range(int(SECS * FPS)):
        wf.update(1.0 / FPS, cfg)
        if i % STRIDE:
            continue
        buf = np.zeros((H, W, 4), np.uint8)
        wf.draw(buf.view(np.uint32).reshape(-1), W, H, cfg)
        lit = (buf[:, :, 3] > 0)
        acc |= lit.astype(np.uint8)
        dens += lit.sum(axis=1)
        n += 1
    return acc, dens / max(1, n)


def render(acc):
    img = np.zeros((H, W, 3), np.uint8)
    img[..., 0] = img[..., 1] = img[..., 2] = acc * 235
    for fy in (0.5, 0.8):
        j = int(H * fy)
        img[j:j + 2, :] = (70, 130, 255)      # 参考线（蓝）
    return img


def report(label, acc, dens):
    rows = acc.sum(axis=1)          # 每行"被流星点亮过"的像素数（长曝光）
    nz = np.nonzero(rows)[0]
    print(f"{label}：长曝光覆盖到最低行 {nz.max() if nz.size else -1}/{H}")
    print(f"    {'高度':>9}  {'瞬时密度(像素/行)':>16}  {'长曝光覆盖/行':>13}")
    for i in range(10):
        a, b = int(H * i / 10), int(H * (i + 1) / 10)
        d = dens[a:b].mean()
        c = rows[a:b].mean()
        bar = "#" * int(58.0 * d / max(1e-9, dens.max()))
        print(f"    {i*10:3d}-{(i+1)*10:3d}%  {d:16.1f}  {c:13.0f}  {bar}")


def main():
    Old = _load_head_meteor()
    cfg = dict(d.DEFAULTS)
    cfg.update(meteor_enabled=True, meteor_amount=14, meteor_stars=0.0)
    old, d_old = run(Old, cfg)
    new, d_new = run(MeteorField, cfg)
    report("旧", old, d_old)
    print()
    report("新", new, d_new)
    a, b = render(old), render(new)
    sep = np.full((8, W, 3), 90, np.uint8)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.concatenate([a, sep, b], axis=0)).save(OUT)
    print(f"已写出 {OUT}（上=旧、下=新；蓝线 = 50% / 80% 高度参考线）")


if __name__ == "__main__":
    main()
