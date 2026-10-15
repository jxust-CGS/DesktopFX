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


MIN_SATURATION = 0.15


def _saturation(px):
    """可见像素的平均 HSV 饱和度：0 = 纯灰，1 = 最鲜艳。"""
    m = px[:, :, 3] > 16
    rgb = px[:, :, :3][m].astype(np.float32)
    mx, mn = rgb.max(axis=1), rgb.min(axis=1)
    return float(np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0).mean())


def test_color_not_gray():
    """画面要有颜色，不是一团灰。

    ⚠️ 这条原来写死了「绿通道占主导」—— 那是在验 **WorkBuddy 品牌青绿**。
    图标改成可替换之后那种绑定就**过时**了：任何非青绿配色的新图标都会让它
    变红，而它其实什么都没测到（一张纯灰图只要偏绿也能过）。
    现在按函数名该有的意思测：量 HSV 饱和度。
    实测标定 —— 品牌青绿 0.61 / 换肤测试图（靛蓝）0.65 / 回退自绘云雨 0.32 /
    去色灰度版 0.00；取 0.15 拦得住灰度，又容得下低饱和的柔和配色。
    真正"在任务栏上看不看得清"由 `tools/_make_icon.py` 的浅/深底对比度负责，
    这里只管通道顺序串没串、图有没有上色。
    """
    print("== test_color_not_gray ==")
    px = _bgra_to_rgba(_icon_rgba(32))
    mask = px[:, :, 3] > 16
    assert mask.any(), "没有非透明像素"
    rgb = px[:, :, :3][mask].astype(np.float32)
    avg_r, avg_g, avg_b = (int(round(rgb[:, i].mean())) for i in range(3))
    dominant = ("红" if avg_r >= avg_g and avg_r >= avg_b
                else "绿" if avg_g >= avg_b else "蓝")
    sat = _saturation(px)
    print(f"  非透明区平均 RGB: ({avg_r}, {avg_g}, {avg_b})  主导通道：{dominant}")
    print(f"  平均饱和度 {sat:.3f}（要求 > {MIN_SATURATION}）")
    assert sat > MIN_SATURATION, (
        f"图标接近灰度（饱和度 {sat:.3f}）—— 通道顺序串了？还是图本身没上色？")
    print("  [OK] 图标有颜色而非灰度图")


def test_gray_would_be_caught():
    """负向对照：把图标去色，上面那条必须变红。

    没有它，`test_color_not_gray` 可能只是在测"我新写的判据自洽"。
    """
    print("== test_gray_would_be_caught（负向对照）==")
    px = _bgra_to_rgba(_icon_rgba(32)).astype(np.float32)
    lum = 0.299 * px[:, :, 0] + 0.587 * px[:, :, 1] + 0.114 * px[:, :, 2]
    gray = px.copy()
    gray[:, :, 0] = gray[:, :, 1] = gray[:, :, 2] = lum
    sat = _saturation(gray)
    print(f"  去色后饱和度 {sat:.3f}")
    assert sat <= MIN_SATURATION, (
        f"负向对照失效：去色后饱和度 {sat:.3f} 仍被判为有色 —— 判据没在测颜色")
    print("  [OK] 灰度图确实会被判出来（判据有效）")



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
               test_color_not_gray, test_gray_would_be_caught,
               test_preview_saved):
        fn()
        print()
    print("== ICON PREVIEW VERIFIED ==")


if __name__ == "__main__":
    main()
