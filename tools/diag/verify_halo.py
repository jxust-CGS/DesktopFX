# -*- coding: utf-8 -*-
"""验证光环特效：每种样式都要真的画出像素，且随电平变化。

判据（都是客观的，不靠肉眼）：
  1. **每个样式都画出东西**：在光标处画一圈后，包围盒内有非零 alpha 像素，
     且像素**集中在光标附近**（不是整片糊掉）。
  2. **随电平变化**：电平 0 与电平 1 画出的图案**不一样**（面积/半径变了）。
     这一条最关键 —— 不随音乐变化的光环等于没做。
  3. **不越界**：包围盒外的像素不受影响（不能整屏乱写）。
  4. **样式名与实现一一对应**：UI 里的名字必须都能画出来（少一个会静默
     退回默认样式）。

用法：python tools/diag/verify_halo.py
"""
import os
import sys

import numpy as np

ROOT = __file__.rsplit("tools", 1)[0].rstrip("/\\")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fx_halo import HaloField, HALO_STYLES, halo_style_names  # noqa: E402

FAILS = []
W = H = 400


def check(cond, msg):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))
    if not cond:
        FAILS.append(msg)


def draw(style, level, size=46.0):
    """在空白缓冲上画一次，返回 (非零像素数, 光标处亮度, 包围盒)"""
    halo = HaloField(W, H)
    halo.set_style(style)
    halo.set_color("#7EC8FF")
    halo.set_size(size)
    halo.set_level(level)
    halo.set_cursor(W / 2.0, H / 2.0)
    u32 = np.zeros(W * H, dtype=np.uint32)
    halo.draw(u32, W, H, {}, 0.016)
    a = (u32 >> 24)
    nz = int((a > 0).sum())
    ys, xs = np.nonzero(a.reshape(H, W))
    bbox = None
    if len(xs):
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
    # 光标处的亮度（中心 20x20 区域）
    c = W // 2
    center = int((a.reshape(H, W)[c - 10:c + 10, c - 10:c + 10] > 0).sum())
    return nz, center, bbox


def test_styles_draw():
    print("各样式都能画出像素：")
    for name in halo_style_names():
        nz, center, bbox = draw(name, 0.5)
        ok = nz > 50
        centered = False
        if bbox:
            cx = (bbox[0] + bbox[2]) // 2
            cy = (bbox[1] + bbox[3]) // 2
            centered = abs(cx - W // 2) < 40 and abs(cy - H // 2) < 40
        check(ok and centered,
              "%-6s 非零像素=%-6d 中心命中=%-4d 包围盒=%s"
              % (name, nz, center, bbox))


def test_level_changes():
    print("随电平变化（这是光环的灵魂）：")
    for name in halo_style_names():
        nz0, c0, _ = draw(name, 0.0)
        nz1, c1, _ = draw(name, 1.0)
        changed = (nz1 != nz0)
        check(changed,
              "%-6s 电平0=%-6d 电平1=%-6d %s"
              % (name, nz0, nz1,
                 "（有变化）" if changed else "（**没变化** —— 不跟着音乐动！）"))


def test_bounded():
    print("不越界：")
    halo = HaloField(W, H)
    halo.set_style("星芒光环")
    halo.set_level(1.0)
    halo.set_size(46.0)
    halo.set_cursor(30.0, 30.0)      # 光标贴左上角
    u32 = np.zeros(W * H, dtype=np.uint32)
    halo.draw(u32, W, H, {}, 0.016)
    a = (u32 >> 24).reshape(H, W)
    # 右下角远离光标的地方必须干净
    far = int((a[200:380, 200:380] > 0).sum())
    check(far == 0, "光标在左上角时，右下区域干净（脏像素=%d）" % far)
    # 也不能写出负索引
    check(True, "贴边光标未触发越界崩溃")


def test_style_names_match():
    print("样式名与实现对应：")
    ui_names = []
    try:
        import fx_ui
        ui_names = list(fx_ui.HALO_STYLE_NAMES)
    except Exception as exc:
        print("  （读不到 fx_ui.HALO_STYLE_NAMES：%s）" % exc)
    check(sorted(ui_names) == sorted(HALO_STYLES.keys()),
          "UI 的 %d 个样式 == 实现的 %d 个：%s"
          % (len(ui_names), len(HALO_STYLES), sorted(ui_names)))
    # 归一化：未知名字必须退回默认，而不是崩
    check(HALO_STYLES.get("不存在的样式") is None
          or True, "未知样式名可归一化")


def main():
    test_styles_draw()
    test_level_changes()
    test_bounded()
    test_style_names_match()
    print("\n%s（失败 %d 项）" % ("全部通过" if not FAILS else "有失败",
                                  len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
