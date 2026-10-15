# -*- coding: utf-8 -*-
"""托盘图标可见性验证（纯 numpy，不依赖 Win32/GDI）。

headless / 远程会话里 build_icon 可能因无法创建 DC 而返回 None，但图标
的 numpy 绘制路径必须始终产出可见、非空、有正确 alpha 通道的像素 ——
否则即使上屏也看不到图标。本脚本检查这一点并落盘预览图。
"""
import os
import sys

# 本脚本住在 tests/ 子目录里：`sys.path[0]` 与 `__file__` 的父目录都不再是项目根。
# 向上找到含 desktop_fx.py 的目录（= 项目根）：产品模块从那里 import；脚本里已有的
# HERE 也一律以它为基准（读 desktop_fx.py / fx_effects.py / .gitattributes /
# 启动桌面特效.bat、写临时配置都按项目根算）。
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = _TESTS_DIR
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
# tests/ 自己也要进 path：有几个脚本互相 import（_verify_click / _verify_pet ->
# _verify_kinds、_verify_tray -> _verify_launcher）。
for _p in (ROOT, _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import numpy as np
from PIL import Image
from fx_tray import _icon_rgba

APP_DIR = ROOT       # 与搬家前一致：预览图仍落在项目根


def _bgra_to_rgba(px):
    """numpy BGRA -> RGBA，供 PNG 预览使用。Windows DIB 用 BGRA，但 PIL PNG 要 RGBA。"""
    out = px.copy()
    out[:, :, [0, 2]] = px[:, :, [2, 0]]
    return out


def _save_previews():
    sizes = [16, 32, 48]
    pad = 6
    grid_w = sum(s + 2 * pad for s in sizes) + 8
    grid_h = max(sizes) + 2 * pad + 8

    # 透明背景（PNG 需要 RGBA，所以先 swap）
    canvas = Image.new("RGBA", (grid_w, grid_h), (0, 0, 0, 0))
    x = 8
    for s in sizes:
        px = _bgra_to_rgba(_icon_rgba(s))
        im = Image.fromarray(px, "RGBA")
        y = (grid_h - s) // 2
        canvas.alpha_composite(im, (x, y))
        x += s + 2 * pad
    canvas.save(os.path.join(APP_DIR, "_icon_preview_transparent.png"))

    # 棋盘格背景（凸显 alpha）
    cell = 8
    checker = np.zeros((grid_h, grid_w, 3), np.uint8)
    yy, xx = np.mgrid[0:grid_h, 0:grid_w]
    mask = ((xx // cell + yy // cell) % 2 == 0)
    checker[mask] = 200
    checker[~mask] = 120

    oa = np.asarray(canvas.convert("RGBA")).astype(np.float32) / 255.0
    bg = checker.astype(np.float32) / 255.0
    a = oa[:, :, 3:4]
    rgb = bg * (1 - a) + oa[:, :, :3] * a
    Image.fromarray((rgb * 255).astype(np.uint8), "RGB").save(
        os.path.join(APP_DIR, "_icon_preview_checker.png"))


def test_icon_not_blank():
    print("== test_icon_not_blank ==")
    for size in (16, 32, 48):
        px = _icon_rgba(size)
        assert px.shape == (size, size, 4), f"size={size} 形状错误: {px.shape}"
        visible = int((px[:, :, 3] > 16).sum())
        total = size * size
        ratio = visible / total
        print(f"  {size}px: 可见像素 {visible}/{total} = {ratio*100:.1f}%")
        assert ratio > 0.15, f"{size}px 可见像素太少: {ratio*100:.1f}%"
    print("  [OK] 各尺寸图标都有足够可见像素")


def test_alpha_channel_valid():
    print("== test_alpha_channel_valid ==")
    px = _icon_rgba(32)
    assert px[:, :, 3].max() > 200, f"alpha 峰值不足: {px[:, :, 3].max()}"
    assert px[:, :, 3].mean() > 30, f"alpha 均值过低: {px[:, :, 3].mean():.1f}"
    print(f"  alpha max={px[:, :, 3].max()} mean={px[:, :, 3].mean():.1f}")
    print("  [OK] alpha 通道范围正常")


def test_color_not_gray():
    print("== test_color_not_gray ==")
    px = _bgra_to_rgba(_icon_rgba(32))
    rgb = px[:, :, :3].astype(np.float32)
    # 非透明区域的平均色应为 WorkBuddy 品牌青绿色（绿通道占主导）
    mask = px[:, :, 3] > 16
    assert mask.any(), "没有非透明像素"
    avg = rgb[mask].mean(axis=0)
    print(f"  非透明区域平均 RGB: ({avg[0]:.1f}, {avg[1]:.1f}, {avg[2]:.1f})")
    assert avg[1] >= avg[0] and avg[1] >= avg[2], "图标应偏青绿色而非灰/红"
    print("  [OK] 图标颜色符合 WorkBuddy 品牌青绿色")


def test_preview_saved():
    print("== test_preview_saved ==")
    _save_previews()
    for name in ("_icon_preview_transparent.png", "_icon_preview_checker.png"):
        path = os.path.join(APP_DIR, name)
        assert os.path.exists(path), f"未生成预览图: {path}"
        size = os.path.getsize(path)
        print(f"  {name}: {size} bytes")
        assert size > 0, f"预览图为空: {path}"
    print("  [OK] 预览图已生成")


def main():
    print("== tray icon pixel verification ==")
    print()
    for fn in (test_icon_not_blank, test_alpha_channel_valid,
               test_color_not_gray, test_preview_saved):
        fn()
        print()
    print("== ICON PREVIEW VERIFIED ==")


if __name__ == "__main__":
    main()
