# -*- coding: utf-8 -*-
"""把一个方形图（AI 生成 / 手绘 / 从 SVG 导出都行）做成托盘图标资源。

用法
----
    python tools/_make_icon.py 新图标.png                 # 只体检，不写任何文件
    python tools/_make_icon.py 新图标.png --apply         # 装进 assets/
    python tools/_make_icon.py 新图标.png --drop-bg --apply
    python tools/_make_icon.py --check                    # 只体检当前 assets/ 里的图标

产物（`--apply` 时）
------------------
    assets/tray_icon.png   256×256 RGBA —— `fx_tray._icon_rgba()` 运行时读的就是它
    assets/tray_icon.ico   16/32/48/64/128/256 六档 —— `_load_ico_icon()` 的首选路径

为什么要有这个脚本
----------------
1. **`.ico` 不是改个扩展名就行**：托盘图标必须内嵌多档尺寸，Windows 才会按实际
   DPI 挑最清晰的那一档。只塞一张 256 会被系统硬缩，小尺寸下糊成一团。
2. **托盘图标要在浅色与深色任务栏上都看得清**，这是最容易翻车的地方：一张
   深蓝底的图在深色任务栏上等于隐形。脚本会把图标分别合成到浅色/深色底上量
   对比度（见 `_contrast_vs`）—— 这个量法**只有实测才能发现**，肉眼看 PNG
   是看不出来的（PNG 自己在透明背景上看永远很清楚）。
3. **边缘留白要统一**：内容顶到边会被任务栏切掉一圈；留白太多 16px 下就剩几个像素。
   脚本按内容包围盒自动裁切 + 补正方形留白。

`--drop-bg` 的作用
----------------
多数 AI 出图给的是**实底**而不是透明 PNG。这个开关从四角向内泛洪，把与四角同色
且**与边缘连通**的区域变透明（只看连通性是为了不误伤图形内部的同色区域，比如
白色眼睛）。留白与描边不要用和背景一样的颜色，否则会被一起抠掉。

体检项的阈值都是**实测标定**出来的，不是拍的：
    · 可见占比 > 15%（沿用 `tests/_verify_icon_preview.py` 的判据）
    · alpha 峰值 > 200、均值 > 30（同上）
    · 浅色/深色底对比度 ≥ 35/255 —— 现有品牌图标 81/134、回退自绘云雨图标 41/132，
      取 35 是"比已知可用里最弱的那个再低一点"，只拦真正隐形的图。
"""
import argparse
import io
import os
import shutil
import struct
import sys
from datetime import datetime

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) \
        and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)

ASSETS = os.path.join(ROOT, "assets")
PNG_PATH = os.path.join(ASSETS, "tray_icon.png")
ICO_PATH = os.path.join(ASSETS, "tray_icon.ico")

ICO_SIZES = (16, 32, 48, 64, 128, 256)
PNG_SIZE = 256
MARGIN = 0.06          # 内容四周留白占最终边长的比例
TOL_BG = 26            # --drop-bg 的颜色容差（0~255，每通道最大差）

MIN_VISIBLE_RATIO = 0.15
MIN_ALPHA_MAX = 200
MIN_ALPHA_MEAN = 30
MIN_CONTRAST = 35.0    # 对浅色底与深色底都要满足
LIGHT_BG, DARK_BG = 248.0, 32.0


# ------------------------------------------------------------------ 处理
def drop_background(im, tol=TOL_BG):
    """从四角泛洪，把与角同色且与边缘连通的区域抠成透明。

    只认**与边缘连通**的同色区域：图形内部若有与背景同色的部分（白眼睛、
    高光），不该被一起抠掉。做法是"受限膨胀"—— 反复从边缘向四邻扩张，
    但每步都和 `near`（与角同色的像素）取交集，所以扩不出同色区域之外。
    """
    rgb = np.asarray(im.convert("RGB")).astype(np.int16)
    h, w, _ = rgb.shape
    corners = np.stack([rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]])
    ref = np.median(corners, axis=0)
    near = (np.abs(rgb - ref).max(axis=2) <= tol)

    mask = np.zeros((h, w), bool)
    mask[0, :] |= near[0, :]
    mask[-1, :] |= near[-1, :]
    mask[:, 0] |= near[:, 0]
    mask[:, -1] |= near[:, -1]

    for _ in range(12):                 # 行扫两趟就能收敛，12 是安全上限
        before = int(mask.sum())
        for y in range(h):              # 正向
            row = mask[y]
            if y:
                row |= mask[y - 1]
            row[1:] |= row[:-1]
            row &= near[y]
        for y in range(h - 1, -1, -1):  # 反向
            row = mask[y]
            if y < h - 1:
                row |= mask[y + 1]
            row[:-1] |= row[1:]
            row &= near[y]
        if int(mask.sum()) == before:
            break

    out = np.asarray(im.convert("RGBA")).copy()
    out[mask, 3] = 0
    return Image.fromarray(out, "RGBA"), int(mask.sum())


def fit_square(im, size=PNG_SIZE, margin=MARGIN):
    """按内容包围盒裁切，再补成正方形（内容四周留出 margin 的边）。"""
    bbox = im.getbbox()                 # 基于 alpha 的包围盒
    if bbox is None:
        raise SystemExit("源图整张都是透明的（抠背景是不是把主体也抠掉了？）")
    im = im.crop(bbox)
    w, h = im.size
    inner = max(1, int(round(size * (1.0 - 2.0 * margin))))
    scale = inner / float(max(w, h))
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    im = im.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(im, ((size - nw) // 2, (size - nh) // 2))
    return canvas


def build_ico(im256, sizes=ICO_SIZES):
    """手工组装多档 ICO（每档 PNG 压缩，与项目原来那份一致）。

    不用 `PIL.Image.save(..., sizes=...)` 是为了**自己控制每档的缩放滤镜**，
    并在写盘前就能回读验证档数与尺寸。
    """
    blobs = []
    for s in sizes:
        buf = io.BytesIO()
        im256.resize((s, s), Image.LANCZOS).save(buf, format="PNG", optimize=True)
        blobs.append((s, buf.getvalue()))

    n = len(blobs)
    header = struct.pack("<HHH", 0, 1, n)
    offset = 6 + 16 * n
    entries, data = b"", b""
    for s, blob in blobs:
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32,
                               len(blob), offset)
        data += blob
        offset += len(blob)
    return header + entries + data


def parse_ico(raw):
    """回读 ICO，返回 [(边长, 字节数)]。"""
    _, _, n = struct.unpack("<HHH", raw[:6])
    out = []
    for i in range(n):
        w, h, _, _, _, _, size, _ = struct.unpack(
            "<BBBBHHII", raw[6 + i * 16:6 + (i + 1) * 16])
        out.append((w or 256, size))
    return out


# ------------------------------------------------------------------ 体检
def visible_ratio(px):
    return float((px[:, :, 3] > 16).mean())


def contrast_vs(px, bg):
    """图标合成到给定底色上后，与底色的平均差（只算图标占的像素）。

    这个量法能抓住"透明背景上看很清楚、贴到任务栏上却看不见"的情况 ——
    本质上是问：把图标放到那块底色上，肉眼还能分出形状吗。
    """
    rgb = px[:, :, :3].astype(np.float32)
    a = px[:, :, 3].astype(np.float32) / 255.0
    foot = a > (16.0 / 255.0)
    if not foot.any():
        return 0.0
    comp = rgb * a[:, :, None] + bg * (1.0 - a[:, :, None])
    return float(np.abs(comp - bg).mean(axis=2)[foot].mean())


def check(px, label):
    """返回 (问题列表, 打印行列表)。"""
    bad, lines = [], []
    lines.append("  %s" % label)
    lines.append("    alpha      峰值 %3d  均值 %5.1f"
                 % (px[:, :, 3].max(), px[:, :, 3].mean()))

    if px[:, :, 3].max() < MIN_ALPHA_MAX:
        bad.append("alpha 峰值 %d < %d（图标太透明，任务栏上会糊）"
                   % (px[:, :, 3].max(), MIN_ALPHA_MAX))
    if px[:, :, 3].mean() < MIN_ALPHA_MEAN:
        bad.append("alpha 均值 %.1f < %d（整体太淡）"
                   % (px[:, :, 3].mean(), MIN_ALPHA_MEAN))

    cl = contrast_vs(px, LIGHT_BG)
    cd = contrast_vs(px, DARK_BG)
    lines.append("    对浅色底   %5.1f/255%s" % (cl, "" if cl >= MIN_CONTRAST else "  << 偏低"))
    lines.append("    对深色底   %5.1f/255%s" % (cd, "" if cd >= MIN_CONTRAST else "  << 偏低"))
    if cl < MIN_CONTRAST:
        bad.append("浅色底对比度 %.1f < %.0f —— 浅色任务栏上会看不清" % (cl, MIN_CONTRAST))
    if cd < MIN_CONTRAST:
        bad.append("深色底对比度 %.1f < %.0f —— 深色任务栏上会看不清" % (cd, MIN_CONTRAST))
    return bad, lines


def check_sizes(im256):
    """按最终上屏尺寸逐档体检小图（缩放是在这一档最容易看出问题）。"""
    bad, lines = [], []
    for s in (16, 32, 48):
        px = np.asarray(im256.resize((s, s), Image.LANCZOS)).astype(np.float32)
        r = visible_ratio(px)
        lines.append("    %2dpx  可见占比 %5.1f%%" % (s, r * 100))
        if r <= MIN_VISIBLE_RATIO:
            bad.append("%dpx 可见占比 %.1f%% ≤ %.0f%%（小尺寸下太单薄）"
                       % (s, r * 100, MIN_VISIBLE_RATIO * 100))
    # 对比度也按 32px（任务栏实际大小）算一遍
    px32 = np.asarray(im256.resize((32, 32), Image.LANCZOS)).astype(np.float32)
    cl, cd = contrast_vs(px32, LIGHT_BG), contrast_vs(px32, DARK_BG)
    lines.append("    32px 上屏时的对比度：浅色底 %.1f / 深色底 %.1f" % (cl, cd))
    if min(cl, cd) < MIN_CONTRAST:
        bad.append("32px 上屏时对比度不足（浅 %.1f / 深 %.1f < %.0f）"
                   % (cl, cd, MIN_CONTRAST))
    return bad, lines


def save_preview(im256, path):
    """把几档尺寸并排 + 浅/深底对照画成一张预览，方便肉眼确认。"""
    sizes = (16, 32, 48, 64)
    pad, gap = 10, 14
    w = sum(s + gap for s in sizes) + gap
    h = max(sizes) + 2 * pad
    canvas = Image.new("RGBA", (w * 2 + gap, h), (0, 0, 0, 0))
    for col, bg in enumerate(((248, 248, 248), (32, 32, 32))):
        plate = Image.new("RGBA", (w, h), bg + (255,))
        x = gap
        for s in sizes:
            fr = im256.resize((s, s), Image.LANCZOS)
            plate.alpha_composite(fr, (x, (h - s) // 2))
            x += s + gap
        canvas.alpha_composite(plate, (col * (w + gap), 0))
    canvas.convert("RGB").save(path)
    return path


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("source", nargs="?", help="源图（方形 PNG，越大越好，建议 ≥512）")
    ap.add_argument("--apply", action="store_true", help="写进 assets/（会先备份）")
    ap.add_argument("--drop-bg", action="store_true", help="先抠掉四角同色的实底")
    ap.add_argument("--tol", type=int, default=TOL_BG, help="抠背景容差，默认 26")
    ap.add_argument("--check", action="store_true", help="只体检当前 assets/ 里的图标")
    args = ap.parse_args()

    if args.check or not args.source:
        if not os.path.exists(PNG_PATH):
            raise SystemExit("assets/tray_icon.png 不存在，没法体检")
        im = Image.open(PNG_PATH).convert("RGBA")
        print("== 当前图标体检 ==")
        print("  源文件 %s  %dx%d" % (os.path.relpath(PNG_PATH, ROOT), *im.size))
        if os.path.exists(ICO_PATH):
            tiers = parse_ico(open(ICO_PATH, "rb").read())
            print("  ico    %d 档：%s"
                  % (len(tiers), "、".join("%dpx(%dB)" % t for t in tiers)))
        bad1, l1 = check(np.asarray(im).astype(np.float32), "全尺寸")
        bad2, l2 = check_sizes(im)
        for l in l1 + l2:
            print(l)
        if bad1 + bad2:
            print("\n  ✗ " + "\n  ✗ ".join(bad1 + bad2))
            return 1
        print("\n  全部通过。")
        return 0

    src = args.source
    if not os.path.exists(src):
        raise SystemExit("找不到源图：%s" % src)

    print("== 处理 ==")
    im = Image.open(src).convert("RGBA")
    print("  源图 %s  %dx%d" % (src, *im.size))
    if min(im.size) < 128:
        print("  ⚠️  源图偏小（%dpx），16/32px 档会糊；建议 ≥512" % min(im.size))

    if args.drop_bg:
        im, n = drop_background(im, args.tol)
        print("  抠背景：%d 个像素变透明（容差 %d）" % (n, args.tol))
        if n == 0:
            print("  ⚠️  一个像素都没抠掉 —— 四角不是纯色？试试调大 --tol")

    im256 = fit_square(im)
    print("  归一化到 %dx%d（内容留白 %.0f%%）"
          % (PNG_SIZE, PNG_SIZE, MARGIN * 200))

    print()
    print("== 体检 ==")
    bad1, l1 = check(np.asarray(im256).astype(np.float32), "全尺寸 256px")
    bad2, l2 = check_sizes(im256)
    for l in l1 + l2:
        print(l)

    preview = save_preview(im256, os.path.join(ROOT, "_icon_new_preview.png"))
    print()
    print("  预览图（左=浅色底 / 右=深色底，各 16/32/48/64px）：")
    print("    %s" % preview)

    problems = bad1 + bad2
    if problems:
        print()
        print("  ✗ 有 %d 项不达标：" % len(problems))
        for p in problems:
            print("     · %s" % p)
        print()
        print("  这些是**任务栏上能不能看清**的问题，不是审美问题 ——")
        print("  建议回去调图（加深主体 / 加描边 / 减少留白）而不是硬装。")
        if args.apply:
            print("\n  未写盘（--apply 遇到不达标会停手）。")
            return 1
        return 1

    ico_raw = build_ico(im256)
    back = parse_ico(ico_raw)
    if [s for s, _ in back] != list(ICO_SIZES):
        raise SystemExit("内部错误：组装的 ico 档位不对 %s" % back)

    print()
    print("== 产物 ==")
    print("  assets/tray_icon.png   %dx%d RGBA" % im256.size)
    print("  assets/tray_icon.ico   %d 档：%s"
          % (len(back), "、".join("%dpx(%dB)" % t for t in back)))
    print("  ico 合计 %.1f KB" % (len(ico_raw) / 1024.0))

    if not args.apply:
        print()
        print("（预演：只体检不写盘。加 --apply 装进 assets/。）")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for p in (PNG_PATH, ICO_PATH):
        if os.path.exists(p):
            bak = p + ".bak-" + stamp
            shutil.copy2(p, bak)
            print("  备份 %s -> %s" % (os.path.relpath(p, ROOT),
                                      os.path.basename(bak)))
    os.makedirs(ASSETS, exist_ok=True)
    im256.save(PNG_PATH, format="PNG", optimize=True)
    with open(ICO_PATH, "wb") as fh:
        fh.write(ico_raw)
    print("  已写入 assets/tray_icon.png 与 assets/tray_icon.ico")

    print()
    print("== 接下来 ==")
    print("  1. 退出正在运行的程序（托盘图标是启动时加载的，不重启看不到新的）")
    print("  2. 源码方式看效果： python tools/_shot_panel.py  或直接跑 启动桌面特效.bat")
    print("  3. 验证： python tests/_verify_icon_preview.py")
    print("           python tests/_verify_tray.py")
    print("  4. 打包进 exe： rebuild（见 README「开发调试」）")
    print()
    print("  ⚠️ tests/_verify_icon_preview.py 里有一条 test_color_not_gray 写死了")
    print("     「绿通道占主导」（那是 WorkBuddy 品牌青绿）。换成别的配色它会红 ——")
    print("     那是**判据本身过时**，不是你的图有问题；改判据即可，别改图。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
