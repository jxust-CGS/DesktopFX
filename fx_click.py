# -*- coding: utf-8 -*-
"""点击管线：图案与文字精灵 / 字体表 / 素材扫描 / 图片池 / MCI 音频 / 爆发引擎。

从 `fx_effects.py` 拆出（2026-10-15）。依赖方向单向下行，本模块**不认识**
雨雪樱流星那些氛围特效，只负责"点一下发生什么"：

    fx_image  <-  fx_click  <-  fx_effects

- 图案与文字都画成**预乘 RGBA 精灵**再叠到屏幕缓冲上（`_over_blend` 在 fx_image）。
- 图片池 / 音频扫描全在后台 daemon 线程，完成后**整体替换**，读取端免锁。
- 播放走 Windows 自带 MCI；`play_path(path, overlap=)` 分两种占坑策略
  （语音包独占、点击音效可叠着播）。
"""
import ctypes
import math
import os
import random
import re
import threading
import time

import numpy as np

from _paths import res_path as _res_path

from fx_image import *          # noqa: F401,F403  图像工具 + PIL 可用性（_PImage 等）

# ------------------------------------------------------------------ 点击特效
# 图案精灵的进程级缓存。
# 键 = (图案, 颜色, 尺寸, 旋转步) -> 预乘 RGBA 数组。
# 放在模块级而不是实例里：两个点击特效（表情 / 文字里的装饰）会用到同几种
# 图案，各存一份纯属浪费；而且图案只取决于配置，与是哪个特效实例无关。
_SHAPE_CACHE = {}
_SHAPE_CACHE_MAX = 400          # 超了就整体清空（配置驱动，重建很便宜）

# 彩带用一整套调色板而不是单色 —— 单色彩带看着像一堆纸片，多彩才有"彩带"感。
_CONFETTI_COLORS = ("#FF5D73", "#FFC145", "#4ADE80", "#38BDF8", "#A78BFA",
                    "#FB7185", "#FDE68A", "#5EEAD4")

# 「彩色」是一个**哨兵值**而不是颜色：选中它表示"逐粒子从调色板取色"，
# 于是任何图案都能做多色（不只是彩带）。它不是合法 hex，所以下游必须先
# 判断它、不能直接丢给 `hex_to_bgr`（那会静默回退成浅蓝）。
COLOR_RAINBOW = "彩色"

_SHAPE_NAMES = ("爱心", "星星", "彩带", "雪花", "气泡", "火焰", "花瓣", "音符")

# 每个图案的惯用色。选图案时面板会自动套上对应的一条（见 fx_ui 的图案选择），
# 省得每次都要自己去配颜色。彩带默认就是彩色 —— 它是这个特效的门面。
_SHAPE_DEFAULT_COLORS = {
    "爱心": "#FF5D73",
    "星星": "#FFC145",
    "彩带": COLOR_RAINBOW,
    "雪花": "#FFFFFF",
    "气泡": "#8FD8FF",
    "火焰": "#FF8A3D",
    "花瓣": "#FFA6C9",
    "音符": "#A78BFA",
}


def _shape_sprite(shape, color_hex, size, rot_step=0, rot_steps=12):
    """画一个图案精灵（预乘 RGBA），带缓存。

    `rot_step` 是预渲染的旋转档位：旋转不能在每帧对每个粒子现算（要重采样，
    几十个粒子根本扛不住），所以**按固定档位预渲染一批**，绘制时取最近的档。
    12 档在视觉上已经看不出跳变。
    """
    size = max(8, int(size))
    key = (shape, color_hex, size, rot_step, rot_steps)
    hit = _SHAPE_CACHE.get(key)
    if hit is not None:
        return hit
    if len(_SHAPE_CACHE) > _SHAPE_CACHE_MAX:
        _SHAPE_CACHE.clear()
    spr = _build_shape(shape, color_hex, size, rot_step, rot_steps)
    _SHAPE_CACHE[key] = spr
    return spr


def _build_shape(shape, color_hex, size, rot_step, rot_steps):
    """真正画图案。坐标一律归一化到 0~1，与最终像素尺寸无关。"""
    SS = 3                                  # 超采样倍数
    W = size * SS
    img = _PImage.new("RGBA", (W, W), (0, 0, 0, 0))
    dr = _PDraw.Draw(img)

    bgr = hex_to_bgr(color_hex)
    rgb = (int(bgr[2]), int(bgr[1]), int(bgr[0]))
    fill = rgb + (255,)
    light = tuple(int(v + (255 - v) * 0.55) for v in rgb) + (255,)
    dark = tuple(int(v * 0.55) for v in rgb) + (255,)

    def ell(cx, cy, rx, ry, col):
        dr.ellipse([(cx - rx) * W, (cy - ry) * W,
                    (cx + rx) * W, (cy + ry) * W], fill=col)

    def poly(pts, col):
        dr.polygon([(x * W, y * W) for x, y in pts], fill=col)

    def line(pts, col, wd):
        dr.line([(x * W, y * W) for x, y in pts], fill=col,
                width=max(1, int(wd)), joint="curve")

    if shape == "爱心":
        # 两个圆瓣 + 一个倒三角拼出心形（与宠物那套同样的构造）
        ell(0.355, 0.365, 0.205, 0.205, fill)
        ell(0.645, 0.365, 0.205, 0.205, fill)
        poly([(0.155, 0.435), (0.845, 0.435), (0.500, 0.880)], fill)
        ell(0.300, 0.305, 0.062, 0.045, light)      # 高光，否则整块颜色太平
    elif shape == "星星":
        pts = []
        for j in range(10):
            rr = 0.46 if j % 2 == 0 else 0.185
            aa = -math.pi / 2 + j * math.pi / 5
            pts.append((0.5 + rr * math.cos(aa), 0.5 + rr * math.sin(aa)))
        poly(pts, fill)
        ell(0.5, 0.52, 0.10, 0.10, light)
    elif shape == "彩带":
        # 一条**弯曲**的飘带。之前是两段直线矩形拼的，方方正正像硬纸片 ——
        # 纸屑和彩带的区别就在"卷"上。
        # 做法：先取一条 S 形中心线，再沿每个点的法向往两侧平移出上下边界，
        # 逐段围成四边形。宽度沿程收放 = 缎带在翻卷（被扭到侧面时变窄），
        # 直线段做不到这一点。
        NJ = 12
        cpts, wid = [], []
        for j in range(NJ + 1):
            t = j / NJ
            cpts.append((0.13 + 0.74 * t,
                         0.50 + 0.26 * math.sin((t - 0.5) * math.pi * 1.5)))
            # 中段被扭窄、两端舒展；系数不取 0 是为了任何一处都有可见宽度
            wid.append(0.088 * (0.42 + 0.58 * abs(math.cos(t * math.pi))))
        for j in range(NJ):
            (ax, ay), (bx, by) = cpts[j], cpts[j + 1]
            dx, dy = bx - ax, by - ay
            m = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / m, dx / m
            wa, wb = wid[j], wid[j + 1]
            poly([(ax + nx * wa, ay + ny * wa), (bx + nx * wb, by + ny * wb),
                  (bx - nx * wb, by - ny * wb), (ax - nx * wa, ay - ny * wa)],
                 fill)
        # 一道沿中心线的高光：缎带的丝面反射，也让"卷"的方向看得出来
        line(cpts, light, max(1, int(W * 0.016)))
    elif shape == "雪花":
        # 六条主枝 + 每枝一对小叉。只画细线，雪花才是"结晶"而不是"六角星"
        wd = max(1, int(W * 0.055))
        for j in range(6):
            aa = j * math.pi / 3
            dx, dy = math.cos(aa), math.sin(aa)
            line([(0.5 - 0.44 * dx, 0.5 - 0.44 * dy),
                  (0.5 + 0.44 * dx, 0.5 + 0.44 * dy)], fill, wd)
            for s in (-1, 1):
                bx, by = 0.5 + 0.30 * dx, 0.5 + 0.30 * dy
                aa2 = aa + s * 0.85
                line([(bx, by), (bx + 0.16 * math.cos(aa2),
                                 by + 0.16 * math.sin(aa2))], fill, wd * 0.8)
        ell(0.5, 0.5, 0.075, 0.075, light)
    elif shape == "气泡":
        # 空心圆 + 左上一点高光；内部留透，叠在桌面上才像泡
        wd = max(1, int(W * 0.07))
        dr.ellipse([0.09 * W, 0.09 * W, 0.91 * W, 0.91 * W],
                   outline=fill, width=wd)
        ell(0.34, 0.31, 0.115, 0.085, light)
        ell(0.68, 0.72, 0.055, 0.055, light)
    elif shape == "火焰":
        # 泪滴形：下半圆 + 上半收成尖，再点一个亮芯
        poly([(0.50, 0.06), (0.80, 0.52), (0.20, 0.52)], fill)
        ell(0.50, 0.62, 0.30, 0.30, fill)
        ell(0.50, 0.70, 0.145, 0.185, light)
    elif shape == "花瓣":
        # 与「落樱」同一套花瓣轮廓，整体缩到 0.1~0.9
        pts = [(5.5, 5.5), (7.5, 3.0), (9.0, 5.0), (10.5, 3.0), (12.5, 5.5),
               (12.5, 10.5), (9.0, 15.0), (5.5, 10.5), (5.5, 5.5)]
        poly([((x - 1.0) / 16.0, (y - 0.5) / 16.0) for x, y in pts], fill)
        ell(0.42, 0.42, 0.07, 0.055, light)
    else:   # 音符
        ell(0.335, 0.735, 0.185, 0.135, fill)               # 符头
        line([(0.505, 0.720), (0.505, 0.215)], fill, max(2, W * 0.075))
        line([(0.505, 0.215), (0.700, 0.290), (0.760, 0.430)],
             fill, max(2, W * 0.070))

    # 旋转：在超采样画布上转，再降采样 —— 先缩后转会把锯齿一起转进去
    if rot_step:
        img = img.rotate(-360.0 * rot_step / rot_steps, resample=_BICUBIC,
                         expand=False)
    img = img.resize((size, size), _LANCZOS)
    arr = np.asarray(img).astype(np.float32) * (1.0 / 255.0)
    arr[..., :3] *= arr[..., 3:4]           # 直通 alpha -> 预乘
    return np.ascontiguousarray(arr)


def _text_sprite(text, font_name, size, color_hex, scale=1.0, angle=0.0):
    """把一行文字渲染成预乘 RGBA（带描边，保证在任何桌面底色上都读得清）。

    字号按 `scale` 直接**重新渲染**而不是缩放位图 —— 文字是矢量轮廓，重渲染
    比放大位图清楚得多，而且这里只在"弹出"的几个档位上各渲一次，成本可忽略。

    `angle`：整体倾斜角度（度）。**先按 textbbox 精确裁剪、再 `expand=True`
    旋转** —— 反过来（先旋转再裁）会把旋转后多出来的边角裁掉。
    """
    px = max(8, int(round(size * scale)))
    font = _font(font_name, px)
    bgr = hex_to_bgr(color_hex)
    rgb = (int(bgr[2]), int(bgr[1]), int(bgr[0]), 255)

    # 先量尺寸（含描边留白），再按量到的尺寸建画布
    tmp = _PImage.new("RGBA", (1, 1))
    box = _PDraw.Draw(tmp).textbbox((0, 0), text, font=font,
                                    stroke_width=max(1, px // 14))
    pad = max(2, px // 6)
    w = max(1, box[2] - box[0]) + pad * 2
    h = max(1, box[3] - box[1]) + pad * 2
    img = _PImage.new("RGBA", (w, h), (0, 0, 0, 0))
    dr = _PDraw.Draw(img)
    # 描边用半透明黑：亮字在浅色桌面上会糊掉，一圈描边把它托起来
    dr.text((pad - box[0], pad - box[1]), text, font=font, fill=rgb,
            stroke_width=max(1, px // 14), stroke_fill=(0, 0, 0, 190))

    if angle:
        img = img.rotate(angle, resample=_BICUBIC, expand=True)

    arr = np.asarray(img).astype(np.float32) * (1.0 / 255.0)
    arr[..., :3] *= arr[..., 3:4]
    return np.ascontiguousarray(arr)


# 字体表：文件相对 `C:/Windows/Fonts`，找不到就逐级回退（见 `_font`）。
# 刻意保留「微软雅黑 / 雅黑粗体 / 黑体」这类清晰的基础款，其余全换成有笔锋的
# 花样款 —— 之前那几个（宋体 / 等线 / 楷体）太像正文，弹出来没有记忆点。
# 只列**系统自带**的：华文 / 方正 / 微软这几家随 Windows 分发的都在，用户
# 不用另外下载；"魏碑 / 方正黑体"这类不一定装的则不列（列了也只是静默回退
# 成雅黑，面板上却多一个点不出效果的选项）。
_FONT_FILES = {
    "微软雅黑": "msyh.ttc",
    "雅黑粗体": "msyhbd.ttc",
    "黑体": "simhei.ttf",
    "幼圆": "SIMYOU.TTF",       # 圆润
    "行楷": "STXINGKA.TTF",     # 手写连笔
    "隶书": "SIMLI.TTF",        # 古风扁平
    "琥珀": "STHUPO.TTF",       # 圆胖醒目
    "彩云": "STCAIYUN.TTF",     # 空心描边，很花
    "舒体": "FZSTK.TTF",        # 毛笔
}
_FONT_CACHE = {}


def _font(name, px):
    """按名字取字体。文件缺失/损坏就回退雅黑 —— 用户机器上不一定装全，
    少一款字体不该让文字整个弹不出来。"""
    key = (name, px)
    hit = _FONT_CACHE.get(key)
    if hit is not None:
        return hit
    for cand in (_FONT_FILES.get(name), _FONT_FILES["微软雅黑"],
                 "simhei.ttf", "simsun.ttc"):
        if not cand:
            continue
        try:
            f = _PImageFont.truetype(cand, px)
            break
        except Exception:
            continue
    else:
        f = _PImageFont.load_default()
    if len(_FONT_CACHE) > 160:
        _FONT_CACHE.clear()
    _FONT_CACHE[key] = f
    return f


# ------------------------------------------------------------ 点击特效的素材
# 「点击表情」的图案来源，三种：
#   内置图案   —— 代码画的 8 种形状，不依赖任何文件
#   自选图片   —— 直接挑若干张图片文件（选一张就是"只用这一张"）
#   图片文件夹 —— 整个目录，随机取图
CLICK_SOURCE_BUILTIN = "内置图案"
CLICK_SOURCE_FILES = "自选图片"
CLICK_SOURCE_FOLDER = "图片文件夹"
CLICK_SOURCES = (CLICK_SOURCE_BUILTIN, CLICK_SOURCE_FILES, CLICK_SOURCE_FOLDER)
# 早期版本只有"自定义图片"（= 从文件夹取图）。旧配置里存的这个值必须当成
# 文件夹处理，否则升级后用户会看到设置莫名其妙回到"内置图案"。
CLICK_SOURCE_LEGACY = "自定义图片"

# 一次点击里怎么用这些素材
CLICK_PICK_MULTI = "随机多图"       # 每颗粒子各自随机
CLICK_PICK_ONE = "整簇同图"         # 一次点击只用同一张
CLICK_PICKS = (CLICK_PICK_MULTI, CLICK_PICK_ONE)

# 用图片当图案时，精灵朝哪边。
#   竖直       = 保持图片原朝向（默认）；
#   随扩散方向 = 图片跟着自己飞出去的方向转。
CLICK_ALIGN_VERTICAL = "竖直"
CLICK_ALIGN_SPREAD = "随扩散方向"
CLICK_ALIGNS = (CLICK_ALIGN_VERTICAL, CLICK_ALIGN_SPREAD)
# 旋转角的量化档数。**必须量化**：旋转一张位图要走一次重采样，逐帧逐粒子
# 现转的话（40 颗粒子 × 60 帧）光这一项就能吃掉半帧；量化成 12 档之后，
# 同一次爆发里各颗粒子最多只多认 12 张缓存，第二次点击起就全是命中。
CLICK_ALIGN_ROT_STEPS = 12


def normalize_source(value):
    """来源值归一化（含把旧的「自定义图片」认成「图片文件夹」）。"""
    s = str(value or "")
    if s == CLICK_SOURCE_FILES:
        return CLICK_SOURCE_FILES
    if s in (CLICK_SOURCE_FOLDER, CLICK_SOURCE_LEGACY):
        return CLICK_SOURCE_FOLDER
    return CLICK_SOURCE_BUILTIN


def click_source_target(cfg):
    """配置 -> `(素材种类, 素材内容)`。

    种类为空串表示"用内置图案"（也是任何未知取值的兜底）。把这层判断收在
    一处：解码池要它、面板要它、测试要它，散在三个地方迟早对不上。
    """
    src = normalize_source(cfg.get("click_emoji_source", CLICK_SOURCE_BUILTIN))
    if src == CLICK_SOURCE_FILES:
        return "files", str(cfg.get("click_emoji_files", "") or "")
    if src == CLICK_SOURCE_FOLDER:
        return "folder", str(cfg.get("click_emoji_custom_dir", "") or "")
    return "", ""


_IMG_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
# 音频只认 MCI 自带设备能放的那几种。flac / ogg / opus 在多数机器上装不上
# 解码器，列进来只会得到"选了文件夹却一声不响"。
_AUDIO_EXTS = (".wav", ".mp3", ".wma", ".m4a")


def _scan_files(folder, exts):
    """列出文件夹里指定扩展名的文件（完整路径 + 文件名排序）。扫不动给空表。

    **不要用 `listdir` + `isfile`**：那是对每个条目再补一次 stat。`scandir`
    的 `is_file()` 读的是目录项自带的属性，整目录一趟扫完 —— 这个函数会被
    面板每 200ms 跟一次，差别是能感觉到的。
    """
    folder = str(folder or "")
    if not folder:
        return []
    out = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                try:
                    if e.is_file() and e.name.lower().endswith(exts):
                        out.append(os.path.join(folder, e.name))
                except OSError:
                    continue        # 单个条目读不了就跳过，不废掉整个文件夹
    except Exception:
        return []
    out.sort()
    return out


_FILES_TTL = 2.0                # 自选图片列表的缓存时长（秒，与音频同一套理由）
_FILES_CACHE = {}


def scan_image_files(raw):
    """把「自选图片」存的多行路径解析成**真实存在**的图片路径（不缓存）。

    过滤掉已被删掉/改名的文件：用户存了 20 张、事后删了两张，剩下的 18 张
    应该照常能用，而不是整批作废。

    **解码池走这条不带缓存的路**：它在后台线程里跑，几十次 `isfile` 无所谓，
    但要的是"用户刚删掉的那张下一批就不再出现"。带缓存的那份留给面板 ——
    它每 200ms 问一次，不缓存就是每 200ms 几十次 stat。
    """
    raw = str(raw or "")
    if not raw:
        return []
    out = []
    for line in raw.replace("\r", "\n").split("\n"):
        p = line.strip().strip('"').strip()
        if p and p.lower().endswith(_IMG_EXTS):
            try:
                if os.path.isfile(p):
                    out.append(p)
            except OSError:
                continue
    return out


def list_image_files(raw):
    """同上，但带 2 秒缓存（面板每 200ms 一查，显示"已选 N 张图片"）。"""
    raw = str(raw or "")
    if not raw:
        return []
    now = time.monotonic()
    hit = _FILES_CACHE.get(raw)
    if hit is not None and now - hit[0] < _FILES_TTL:
        return hit[1]
    out = scan_image_files(raw)
    if len(_FILES_CACHE) > 32:
        _FILES_CACHE.clear()
    _FILES_CACHE[raw] = (now, out)
    return out


def _frame_indexes(n, cap):
    """从一个 n 帧的动图里挑出最多 `cap` 帧的下标（等间隔，**含首尾**）。

    步长取 `(n-1)/(cap-1)` 而不是 `n/cap`：后者的最后一帧会落在 `n - n/cap`
    上（24 帧抽 12 帧时是第 22 帧），**尾帧永远取不到** —— 而 GIF 的尾帧常常
    正是"回到起始姿势"的那一帧，丢了会让循环看起来缺一拍。
    """
    n, cap = max(1, int(n)), max(1, int(cap))
    if n <= cap:
        return list(range(n))
    if cap == 1:
        return [0]                 # 只留一帧：取首帧（步长公式会除零）
    step = (n - 1) / float(cap - 1)
    out = []
    for i in range(cap):
        k = min(n - 1, int(round(i * step)))
        if not out or out[-1] != k:
            out.append(k)
    return out


def _frame_ms(im):
    """取当前帧的显示时长（毫秒），夹到合理区间。

    很多 GIF 把 `duration` 写成 0（含义是"越快越好"）；浏览器按 100ms 处理，
    这里跟着这个约定 —— 否则一帧 0 秒会让动画瞬间跑完、看着像静态。
    """
    try:
        d = float(im.info.get("duration", 0) or 0)
    except Exception:
        d = 0.0
    if d <= 0.0:
        d = 100.0
    return min(max(d, 40.0), 1000.0)


def _load_click_frames(path, px, max_frames=1):
    """读一张图 -> `(帧列表, 每帧秒数)`，每帧都是预乘 RGBA float32。

    **动图（GIF / WebP）会取出多帧**，所以返回值是列表而不是单张：静态图就是
    长度为 1 的列表，调用方不必分支。

    长边缩到 `px` 且**保持比例** —— 表情包里的图多是矩形，硬拉成正方形会变形。
    任何一步失败都返回 None：文件夹里混进一个 0 字节文件、一个改了后缀的网页图
    都是常事，不该让整池子作废。
    """
    if _PImage is None:
        return None
    try:
        im = _PImage.open(path)
        n = int(getattr(im, "n_frames", 1) or 1)
        frames, durs = [], []
        for k in _frame_indexes(n, max_frames):
            # seek 之后再 convert：PIL 的 GIF 解码器在 seek 时就完成了帧合成
            # （局部更新 / dispose 都已处理），所以 convert 拿到的是完整画面。
            im.seek(k)
            durs.append(_frame_ms(im))
            fr = im.convert("RGBA")
            w, h = fr.size
            if w < 1 or h < 1:
                return None
            sc = float(max(8, int(px))) / float(max(w, h))
            fr = fr.resize((max(1, int(round(w * sc))),
                            max(1, int(round(h * sc)))), _LANCZOS)
            arr = np.asarray(fr).astype(np.float32) * (1.0 / 255.0)
            arr[..., :3] *= arr[..., 3:4]       # 直通 alpha -> 预乘
            frames.append(np.ascontiguousarray(arr))
    except Exception:
        return None
    if not frames:
        return None
    # 抽帧必须**补偿帧时长**：抽掉一半的帧、剩下的就该慢一倍，整段动画的总
    # 时长才与原来的 GIF 一致。漏掉这一步的表现是"大动图播起来快得看不清"
    # —— 而小动图（帧数没超上限、没抽帧）完全正常，很容易被当成"那张图本身
    # 就快"。所以按 `原始帧数 / 实抽帧数` 放大。
    step = n / float(len(frames)) if frames else 1.0
    return frames, max(0.04, sum(durs) / len(durs) / 1000.0 * step)


class ClickSpriteSet:
    """一套可上屏的表情包素材：把"每张图可能有若干帧"统一成**扁平帧表**。

    做成对象而不是 list，是因为绘制端每颗粒子每帧都要问"我这张图现在该画第
    几帧"：扁平表 + 三个定长数组，取帧就是两次数组索引；换成 list-of-dict 会
    变成每帧几十次字典查找（40 颗粒子 × 60 帧/秒）。

    动画**整簇共用一个时间轴**（`_BurstEngine._anim_t` 一路累加），各资源按
    自己的帧数取模 —— 于是"8 帧的 GIF"和"20 帧的 GIF"混在同一簇里，也能各按
    各的节奏循环。

    `__len__` 是**资源数**（一张图算一个），不是帧数：粒子的 `spr_id` 指的是
    "第几张图"。空集合时 `len() == 0`，因此 `if sprites:` 照常可用。
    """

    def __init__(self, res=()):
        """`res` = `[(帧列表, 每帧秒数), ...]`，每个元素是一张图。"""
        self.frames = []
        n = len(res)
        self.starts = np.zeros(n, np.int32)
        self.counts = np.ones(n, np.int32)
        # **必须是 float64。** 帧号是 `int(t / dur)`，而 float32 存不下 0.1
        # （最接近的是 0.10000000149）：`0.1 / float32(0.1)` = 0.99999998，
        # `int()` 截断成 0 —— 每一帧的边界处都会少走一帧。单次看只差一个
        # 浮点 epsilon，但 `t` 是长时间累加的，误差会随循环次数一点点累积
        # （跑到几千秒时动画节拍会明显不齐）。
        self.durs = np.ones(n, np.float64)
        for j, (frames, dur) in enumerate(res):
            self.starts[j] = len(self.frames)
            self.counts[j] = len(frames)
            self.durs[j] = max(0.04, float(dur))
            self.frames.extend(frames)
        self.animated = int((self.counts > 1).sum())

    def __len__(self):
        return int(self.starts.size)

    def __bool__(self):
        return bool(self.frames)

    def nbytes(self):
        return sum(f.nbytes for f in self.frames)

    def frame_at(self, j, t):
        """第 `j` 张图在时刻 `t`（秒）该显示的那一帧。"""
        st = int(self.starts[j])
        n = int(self.counts[j])
        if n <= 1:
            return self.frames[st]
        return self.frames[st + (int(t / float(self.durs[j])) % n)]


class ClickImagePool:
    """自定义表情包的图库：拿到路径 -> 解码 -> 存成能直接上屏的精灵。

    两条硬约束决定了它的形状：

    * **解码绝不能进渲染线程。** 一张 2000×2000 的 JPG 解码 + 缩放要几十
      毫秒，动图还要乘以帧数，放在 `update()` 里就是"每次点击掉一帧"。所以
      扫描与解码跑在一条后台线程上，渲染端只读**已经就绪**的那一份精灵表。
    * **素材可能很多。** 文件夹可能有几千张，动图每张又有多帧：前者先按
      文件名排序、再**随机打乱**取前 `MAX_FILES` 张（不洗牌的话用户永远只
      见到文件名靠前的那几十张），后者按 `MAX_ANIM_FRAMES` 抽帧、整池子再
      受 `MAX_TOTAL_BYTES` 约束。

    两种来源走同一条流水线，只是"候选路径怎么来"不同：
    `folder` = 扫目录（顺序打乱），`files` = 用户自己挑的那几张（**顺序保留**，
    那是他一张张选出来的意图）。

    每张图存 `SIZE_STEPS` 档尺寸，粒子直接从池里随机取条目 —— "图随机"与
    "大小随机"于是统一成一件事（不需要再给粒子单独做位图缩放）。

    线程协议：`configure()` 只登记需求并唤醒 worker；worker 解码完成后
    **整体替换**精灵表（从不就地改）。读取方拿到的永远是完整快照，这正是
    它能不加锁地面对渲染线程的原因。
    """

    MAX_FILES = 64
    SIZE_STEPS = (0.74, 1.0)
    # 单个动图最多抽这么多帧：帧数直接乘进内存与解码时间，而点击特效的粒子
    # 只有二三十像素，超过十几帧的差别肉眼已经看不出来。
    MAX_ANIM_FRAMES = 12
    # 整池子的帧数据预算（24MB）。按**字节**而不是按帧数封顶：同一张图在
    # 64px 目标尺寸下的单帧内存是 40px 的 2.6 倍，"多少帧"这个数字单独看
    # 没有意义。
    MAX_TOTAL_BYTES = 24 << 20

    def __init__(self):
        self._lock = threading.Lock()
        self._want = None           # 待处理的 (kind, src, px)
        self._done = None           # 已完成的 (kind, src, px)
        self._sprites = ClickSpriteSet()    # 只整体替换，从不就地改
        self._files = 0             # 上次可用的图片数
        self._failed = 0            # 解码失败个数（面板会显示，别静默）
        self._rng = random.Random(0x5F3C14)
        self._thread = None
        self._wake = threading.Event()

    # ---------------------------------------------------------- 渲染线程侧
    @staticmethod
    def _job(kind, src, px):
        return (str(kind or ""), str(src or ""), max(8, int(px)))

    def configure(self, kind, src, px):
        """登记"我要用这批素材、这个尺寸"。重复登记是**零成本**的。

        每次 `update` 都会调它（拖动"大小"滑块时每帧都在变），所以这里
        只做一次比较、一次赋值 —— 真正的重建由 worker 去合并执行。
        """
        job = self._job(kind, src, px)
        with self._lock:
            if job == self._want:
                return
            self._want = job
        self._start()
        self._wake.set()

    def sprites(self):
        """当前可用的素材快照（`ClickSpriteSet`）。空 = 还没就绪。"""
        with self._lock:
            return self._sprites

    def stats(self):
        """(素材条目数, 可用图片数, 解码失败数)。"""
        with self._lock:
            return len(self._sprites), self._files, self._failed

    def state(self, kind, src, px):
        """给面板的一句话状态。

        `done` 必须比较**整个 job**（种类 + 来源 + 尺寸）而不是只看路径：用户
        拖「大小」滑块时尺寸每帧都在变，"路径对了"并不代表这一批就是用当前
        尺寸做的。
        """
        job = self._job(kind, src, px)
        with self._lock:
            sp, files, failed = self._sprites, self._files, self._failed
            done = (self._done == job)
        if not kind or not str(src or "").strip():
            return ""
        if not done:
            return "读取中…"
        if files == 0:
            return "没找到图片"
        out = "已载入 %d 张" % len(sp)
        if sp.animated:
            out += "（%d 张动图）" % sp.animated
        if failed:
            out += "，%d 张读不了" % failed
        return out

    def clear(self):
        """丢掉现有素材并停止装载（换来源时由 configure 直接覆盖，这是
        给"清空"和测试用的）。"""
        with self._lock:
            self._want = None
            self._done = None
            self._sprites = ClickSpriteSet()
            self._files = 0
            self._failed = 0
        self._wake.set()

    # ---------------------------------------------------------- 后台线程
    def _start(self):
        t = self._thread
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=self._loop, name="ClickImagePool",
                             daemon=True)
        self._thread = t
        t.start()

    def _loop(self):
        while True:
            self._wake.wait(0.5)
            self._wake.clear()
            with self._lock:
                job, done = self._want, self._done
            if job is None or job == done:
                continue
            # **绝不能把异常漏出去**：pythonw 下后台线程死掉连 traceback 都
            # 没有（没有 stderr），池子会永远停在"正在读取"。宁可这次放弃，
            # 下一次点击或下一次 configure 再来一遍。
            try:
                self._load(job)
            except Exception:
                pass

    def _load(self, job):
        kind, src, px = job
        if kind == "files":
            # 不走面板那份缓存：用户刚删掉的那张，下一批就不该再出现
            paths = scan_image_files(src)
        else:
            paths = _scan_files(src, _IMG_EXTS)
            # 目录里的东西是"随机素材"，洗牌才有意义；自选的那几张保持原序。
            self._rng.shuffle(paths)
        out, used = [], 0
        for p in paths[:self.MAX_FILES]:
            if not self._wanted(job):
                return              # 用户改过素材/尺寸，这一批作废
            if used >= self.MAX_TOTAL_BYTES:
                break               # 预算用尽：保留已经装好的那部分
            ok = False
            for sc in self.SIZE_STEPS:
                got = _load_click_frames(p, int(round(px * sc)),
                                         self.MAX_ANIM_FRAMES)
                if got is None:
                    continue
                frames, dur = got
                need = sum(f.nbytes for f in frames)
                if used + need > self.MAX_TOTAL_BYTES and out:
                    break
                used += need
                out.append((frames, dur))
                ok = True
            if not ok:
                # 按**文件**计数而不是按档位：同一个坏文件会在两档上各失败
                # 一次，显示成"2 张读不了"会让用户去找并不存在的第二张。
                with self._lock:
                    self._failed += 1
        with self._lock:
            self._done = job
            self._sprites = ClickSpriteSet(out)
            self._files = min(len(paths), self.MAX_FILES)

    def _wanted(self, job):
        with self._lock:
            return self._want == job


# 模块级单例：同一个文件夹只解码一次，两个点击特效实例（或反复重建的实例）
# 共用同一份素材。
CLICK_IMAGE_POOL = ClickImagePool()

# ---------------------------------------------------------------- 点击语音
_VOICE_ALIAS = "fxclickvoice"
# 「点击音效」的别名池：MCI 的每个 alias 是一条**独立设备**，所以给音效单独
# 一组、一个并发播放占一条 —— 共用一条的话第二个 open 会把第一条正在播的
# 顶掉，听到的是一声被截断的"咔"。语音那边仍独占 _VOICE_ALIAS（长句叠起来
# 没法听）。
#
# 池子有上限：狂点时开几十条设备会把媒体层拖垮（每条都占一个 wave 句柄）。
# 满池之后新的点击**直接丢弃**、绝不排队 —— 排队等于把"哒"拖成滞后的长串。
_SFX_VOICES = 6
_SFX_ALIASES = tuple("fxclicksfx%d" % i for i in range(_SFX_VOICES))
_AUDIO_TTL = 2.0                # 目录列表的缓存时长（秒）
_AUDIO_CACHE = {}               # folder -> (monotonic, [路径...])


def list_audio(folder):
    """文件夹里可播放的音频路径，**已排序**（2 秒内复用结果）。

    缓存是必须的：面板每 200ms 要一次（显示"找到 N 个音频"、语音包模式下还要
    把名字填进文字栏），点击时也要一次。音频文件夹动辄几百首，每 200ms 扫一趟
    盘纯属白烧。
    """
    folder = str(folder or "")
    if not folder:
        return []
    now = time.monotonic()
    hit = _AUDIO_CACHE.get(folder)
    if hit is not None and now - hit[0] < _AUDIO_TTL:
        return hit[1]
    paths = _scan_files(folder, _AUDIO_EXTS)
    if len(_AUDIO_CACHE) > 32:
        _AUDIO_CACHE.clear()
    _AUDIO_CACHE[folder] = (now, paths)
    return paths


def count_audio(folder):
    """文件夹里有多少个可播放的音频。"""
    return len(list_audio(folder))


def invalidate_media_cache():
    """让"目录/文件列表"的缓存立刻失效。

    面板上的**选择 / 清除动作**之后要调它：列表带 2 秒 TTL（那是为了别每
    200ms 扫一次盘），但用户刚点完"清除"却还看到"已选 3 张"，会以为按钮
    没生效。人主动做的事应当立刻看到结果，只有"他在资源管理器里改了文件、
    程序自己去发现"那种情况才值得等 TTL。
    """
    _FILES_CACHE.clear()
    _AUDIO_CACHE.clear()


# 文件名开头的音轨号：「01 你好」「12_再见」「3. 走起」-> 去掉序号。
# **只在数字后面真的还有内容时才剥**（"1.mp3" 这种名字本身就是数字，
# 剥掉就等于没有文字可显示）。
_TRACK_NO = re.compile(r"^\s*\d+\s*[\s._\-—、,，]+(.*)$")


def audio_label(path):
    """音频文件名 -> 显示文字（去扩展名、去开头的音轨号）。

    语音包的"文字与音频统一"模式直接拿它当弹出文字，所以文件名就是文案 ——
    这也是把清洗做在这一层的原因：用户不需要为了好看再改一遍文件名。
    """
    base = os.path.basename(str(path or ""))
    stem = os.path.splitext(base)[0].strip()
    m = _TRACK_NO.match(stem)
    if m and m.group(1).strip():
        stem = m.group(1).strip()
    return stem or base


def audio_labels(folder):
    """文件夹里每条音频的显示文字（去重、保持排序）。"""
    out, seen = [], set()
    for p in list_audio(folder):
        t = audio_label(p)
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def pick_audio(folder, rng=None):
    """随机挑一条音频路径（**只挑不播**）。

    独立于 `VoicePlayer` 而存在，是因为「点击文字」的语音包要先定下"这一次是
    哪一条"，才能把它的名字当成弹出文字 —— 先挑后播，文字和声音才可能对上。
    """
    items = list_audio(folder)
    if not items:
        return None
    return (rng or random).choice(items)

try:
    _winmm = ctypes.WinDLL("winmm")
except Exception:                       # pragma: no cover - 非 Windows
    _winmm = None


def _mci_send(cmd):
    """发一条 MCI 命令，返回 (错误码, 返回串)。错误码 0 = 成功。

    独立成函数是为了**能被测试替换** —— 真去开声卡放一遍，在自动化里既
    听不见也没法判定，而"上一段没放完不响应下一次点击"这条规则必须验。
    """
    if _winmm is None:
        return 1, ""
    buf = ctypes.create_unicode_buffer(128)
    try:
        r = _winmm.mciSendStringW(ctypes.c_wchar_p(cmd), buf, 128, None)
    except Exception:
        return 1, ""
    return int(r), buf.value


# ------------------------------------------------- waveOut 软件音量池（wav 专用）
# 为什么需要它：**MCI 的 waveaudio 设备实测不支持 `setaudio volume`** ——
# winmm 对该命令直接返回错误 261（"Unrecognized command"），发 0~1000、0~65535、
# 双声道打包值统统被拒（本机 winmm 实测，2026-10-16）。而 winmm 又没有
# per-stream 的音量 API（waveOutSetVolume 是全局的，会污染别的程序的声音），
# 所以「音量」对 wav 只能**自己混音**实现。
#
# - mp3 / wma 不走这里：它们走 MCI mpegvideo，`setaudio volume` 0~1000 实测
#   正常（500 回读 501、32767 报超范围错误 282）。
# - 读 PCM 用标准库 `wave`（只认未压缩 PCM；别的格式/读不出来返回 None，
#   由调用方退回 MCI 播放 —— 至少响，只是那条没音量）。
# - 音量 = 软件混音：int16 样本乘系数后 clip。1.0 = 当前系统音量，0 = 静音。
# - 每条流一条 waveOut 句柄（同一设备可多开，各写各的 WAVEHDR），保住「点击
#   音效可叠着播」的语义；句柄懒打开、常驻复用，不随每次播放开关。
# - 完成判定：守护线程轮询 WAVEHDR.dwFlags & WHDR_DONE，**不用回调** ——
#   pythonw 下没有消息循环，回调穿越 GIL 太脆。守护线程入口整段 try/except：
#   pythonw 没有 stderr，异常绝不能漏出去把线程弄死。
# - 池满 -> 返回 False，调用方退回 MCI（与 MCI 别名池"满则丢弃"同一思路）。

_WHDR_DONE = 0x00000001
_WAVE_MAPPER = 0xFFFFFFFF     # UINT(-1)：让系统挑默认输出设备
_WAV_POOL_SIZE = 6            # 与 MCI 音效别名池（_SFX_VOICES）一致


class _WAVEHDR(ctypes.Structure):
    """winmm WAVEHDR（64 位布局）。字段顺序即 SDK 原序，勿重排。"""

    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", ctypes.c_uint),
        ("dwBytesRecorded", ctypes.c_uint),
        ("dwUser", ctypes.c_void_p),
        ("dwFlags", ctypes.c_uint),
        ("dwLoops", ctypes.c_uint),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_void_p),
    ]


class _WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", ctypes.c_ushort),
        ("nChannels", ctypes.c_ushort),
        ("nSamplesPerSec", ctypes.c_uint),
        ("nAvgBytesPerSec", ctypes.c_uint),
        ("nBlockAlign", ctypes.c_ushort),
        ("wBitsPerSample", ctypes.c_ushort),
        ("cbSize", ctypes.c_ushort),
    ]


if _winmm is not None:
    _winmm.waveOutGetNumDevs.restype = ctypes.c_uint
    _winmm.waveOutOpen.restype = ctypes.c_uint
    _winmm.waveOutOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p),
                                   ctypes.c_uint, ctypes.c_void_p,
                                   ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_uint]
    _winmm.waveOutPrepareHeader.restype = ctypes.c_uint
    _winmm.waveOutPrepareHeader.argtypes = [ctypes.c_void_p,
                                            ctypes.POINTER(_WAVEHDR),
                                            ctypes.c_uint]
    _winmm.waveOutWrite.restype = ctypes.c_uint
    _winmm.waveOutWrite.argtypes = [ctypes.c_void_p,
                                    ctypes.POINTER(_WAVEHDR), ctypes.c_uint]
    _winmm.waveOutUnprepareHeader.restype = ctypes.c_uint
    _winmm.waveOutUnprepareHeader.argtypes = [ctypes.c_void_p,
                                              ctypes.POINTER(_WAVEHDR),
                                              ctypes.c_uint]
    _winmm.waveOutReset.restype = ctypes.c_uint
    _winmm.waveOutReset.argtypes = [ctypes.c_void_p]
    _winmm.waveOutClose.restype = ctypes.c_uint
    _winmm.waveOutClose.argtypes = [ctypes.c_void_p]


def wav_pcm16_mono_44k(path):
    """读 wav -> float32 mono 44.1kHz 样本数组；读不了返回 None。

    只接 **16-bit 未压缩 PCM**（内置音效全是；8/24/32-bit、float、ADPCM
    这类交给 MCI 兜底，不在这里猜格式）。多声道先下混成 mono，非 44.1k
    的用线性插值重采样 —— 统一成一种格式，waveOut 句柄才能常驻复用
    （句柄按格式打开，播不同格式的文件就得反复开合）。
    """
    import wave
    try:
        w = wave.open(str(path), "rb")
    except Exception:
        return None
    try:
        if w.getcomptype() != "NONE" or w.getsampwidth() != 2:
            return None
        n = w.getnframes()
        ch = w.getnchannels()
        fr = w.getframerate()
        if n <= 0 or ch <= 0 or fr <= 0:
            return None
        raw = w.readframes(n)
    except Exception:
        return None
    finally:
        try:
            w.close()
        except Exception:
            pass
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)      # 立体声/多声道 -> mono
    if fr != 44100:
        n_out = int(round(len(a) * 44100.0 / fr))
        if n_out <= 0:
            return None
        if n_out != len(a):
            xs = np.linspace(0.0, len(a) - 1.0, n_out)
            a = np.interp(xs, np.arange(len(a), dtype=np.float32), a)
    return a


def mix_pcm16_stereo(a, volume):
    """float32 mono 样本 × 音量 -> 16-bit **立体声**小端 bytes。

    软件音量的落点：乘系数后 clip 回 int16 防溢出，再复制成立体声
    （播放格式统一 44.1k/2ch/16bit）。`volume` 1.0 = 原样（= 当前系统
    音量），0 = 静音。
    """
    v = max(0.0, min(1.0, float(volume)))
    pcm = np.clip(a * v, -32768.0, 32767.0).astype("<i2")
    return np.repeat(pcm[:, None], 2, axis=1).reshape(-1).tobytes()


class _WavOutPool:
    """waveOut 播放池：wav 的软件音量 + 多流并发（见上方大段注释）。"""

    def __init__(self, size=_WAV_POOL_SIZE):
        self._lock = threading.Lock()
        self._free = list(range(size))       # 空闲槽号（栈）
        self._h = [None] * size              # 槽 -> waveOut 句柄；False=开过且失败
        self._playing = []                   # [(槽, hdr, buf, on_free)]
        fmt = _WAVEFORMATEX()
        fmt.wFormatTag = 1                   # WAVE_FORMAT_PCM
        fmt.nChannels = 2
        fmt.nSamplesPerSec = 44100
        fmt.nAvgBytesPerSec = 44100 * 2 * 2
        fmt.nBlockAlign = 4
        fmt.wBitsPerSample = 16
        fmt.cbSize = 0
        self._fmt = fmt
        self._reaper = threading.Thread(target=self._reap_loop,
                                        name="WavReaper", daemon=True)
        self._reaper_started = False

    # ------------------------------------------------------------ 调用方接口
    def playing(self):
        """当前有几条流在播（0 = 全空闲）。"""
        with self._lock:
            return len(self._playing)

    def play(self, path, volume, on_free=None):
        """起播一条 wav；成功返回 True（失败/池满/无设备 False）。"""
        a = wav_pcm16_mono_44k(path)
        if a is None:
            return False
        data = mix_pcm16_stereo(a, volume)
        with self._lock:
            if not self._free:
                return False
            if _winmm is None or _winmm.waveOutGetNumDevs() == 0:
                return False
            slot = self._free.pop()
            if not self._open(slot):
                self._free.append(slot)
                return False
            hwo = self._h[slot]
            hdr = _WAVEHDR()
            buf = ctypes.create_string_buffer(data)
            hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
            hdr.dwBufferLength = len(data)
            hdr.dwFlags = 0
            if _winmm.waveOutPrepareHeader(hwo, ctypes.byref(hdr),
                                           ctypes.sizeof(hdr)):
                self._free.append(slot)
                return False
            if _winmm.waveOutWrite(hwo, ctypes.byref(hdr),
                                   ctypes.sizeof(hdr)):
                try:
                    _winmm.waveOutUnprepareHeader(hwo, ctypes.byref(hdr),
                                                  ctypes.sizeof(hdr))
                except Exception:
                    pass
                self._free.append(slot)
                return False
            self._playing.append((slot, hdr, buf, on_free))
            if not self._reaper_started:
                self._reaper_started = True
                self._reaper.start()
        return True

    def stop(self):
        """关掉所有句柄（进程退出时调；不调也会随进程退出回收）。"""
        with self._lock:
            for i, h in enumerate(self._h):
                if h and h is not False:
                    try:
                        _winmm.waveOutReset(h)
                    except Exception:
                        pass
                    try:
                        _winmm.waveOutClose(h)
                    except Exception:
                        pass
                self._h[i] = None

    # ------------------------------------------------------------ 内部
    def _open(self, slot):
        """（持锁）懒打开槽对应的 waveOut 句柄。开过且失败的不再重试。"""
        cur = self._h[slot]
        if cur is False:
            return False
        if cur is not None:
            return True
        h = ctypes.c_void_p()
        r = _winmm.waveOutOpen(ctypes.byref(h), _WAVE_MAPPER,
                               ctypes.byref(self._fmt), None, None, 0)
        if r or not h.value:
            self._h[slot] = False
            return False
        self._h[slot] = h
        return True

    def _reap_loop(self):
        while True:
            time.sleep(0.012)
            try:
                self._reap_once()
            except Exception:
                pass        # pythonw 没有 stderr，守护线程绝不能死

    def _reap_once(self):
        """（持锁）回收已放完（WHDR_DONE）的流：拆头、还槽、通知。"""
        done = [i for i, (_, hdr, _, _) in enumerate(self._playing)
                if hdr.dwFlags & _WHDR_DONE]
        for i in reversed(done):
            slot, hdr, buf, on_free = self._playing.pop(i)
            h = self._h[slot]
            if h and h is not False:
                try:
                    _winmm.waveOutUnprepareHeader(h, ctypes.byref(hdr),
                                                  ctypes.sizeof(hdr))
                except Exception:
                    pass
            self._free.append(slot)
            if on_free is not None:
                try:
                    on_free()
                except Exception:
                    pass


_WAV_POOL = _WavOutPool()


class VoicePlayer:
    """点一下播一段音频（语音包与点击音效共用）。

    播放后端按文件类型分流，两条路都不引第三方库：

    * wav + 要控音量 -> **waveOut 软件混音池**（`_WavOutPool`）。MCI 的
      waveaudio 设备实测不认 `setaudio volume`，而 winmm 没有 per-stream
      音量 API，所以 wav 只能自己混音（详见池上方注释）。
    * 其余（mp3 / wma / wav 兜底）-> Windows 自带 MCI（`winmm`）。

    整段播放跑在后台线程里，渲染线程只负责"占坑"。

    **两种占坑策略**（`play_path(..., overlap=)` 选）：

    * `overlap=False`（默认）：**同时只响一个**，独占 `_VOICE_ALIAS`。上一段
      没放完时新的点击**立刻返回 False**、一秒都不等 —— 排队等着放会让连点
      变成"越来越滞后的一串拖音"，听感像卡带；语音是长句，叠几句更没法听。
    * `overlap=True`（「点击音效」用）：从 `_SFX_ALIASES` 借一条空闲设备，
      **允许叠着播**。音效本来就一秒内的短促一声，连点时叠起来才有连击手感；
      串行反而像卡顿。池满（`_SFX_VOICES` 条都在响）时丢弃，绝不排队。

    两种策略共同的硬规则：

    * 结束判定用 `status ... mode` **轮询**，**不用 `play ... wait`**：后者在
      某些设备上会直接挂住不返回，那条线程就永远消失在等待里。
    * 600 秒硬上限兜底 —— 设备抽风时 `status` 可能永远回 "playing"。
    * **无论怎么结束（含设备抛异常）都释放占的坑**，否则这辈子只响一次。
    """

    MAX_SECONDS = 600.0

    def __init__(self):
        self._lock = threading.Lock()
        self._busy = False
        # 空闲的音效别名。栈（pop 从尾部）而不是集合：优先复用最早放完的那条，
        # 让同时在响的几条尽量散在不同设备上。
        self._sfx_free = list(_SFX_ALIASES)
        self._rng = random.Random(0x51CE07)
        self._played = 0
        self._failed = 0

    # ---------------------------------------------------------- 渲染线程侧
    def playing(self):
        """语音（独占那条设备）是否正在播。音效不计入 —— 它们本来就允许并发。"""
        return self._busy

    def voices_busy(self):
        """当前有几条音效设备被占着（0 = 还能叠新的）。含 waveOut 池。"""
        with self._lock:
            return (len(_SFX_ALIASES) - len(self._sfx_free)
                    + _WAV_POOL.playing())

    def play_random(self, folder):
        """随机播一个；正在播 -> 放弃这次（返回 False）。"""
        return self.play_path(pick_audio(folder, self._rng))

    def pick(self, folder):
        """随机挑一条（不播）。给"文字与音频统一"用。"""
        return pick_audio(folder, self._rng)

    def play_path(self, path, overlap=False, volume=None):
        """播**指定**的这一条，返回是否真的起了播。

        `overlap=True` -> 从音效别名池借一条空闲设备，允许与正在响的**叠着播**；
        `overlap=False` -> 独占语音设备，正在播就放弃这次（不排队）。

        与 `play_random` 拆开，是因为语音包的"统一"模式必须是**先挑后播**：
        文字已经按这条音频定下来了，播的就必须是同一条。
        """
        if not path:
            return False
        path = str(path)
        # wav + 要控音量 -> waveOut 软件混音池。为什么绕开 MCI：MCI 的
        # waveaudio 设备实测**不认** `setaudio volume`（winmm 回错误 261），
        # winmm 也没有 per-stream 音量 API，详见 _WavOutPool 上方注释。
        # 池起播失败（非 16-bit PCM / 无音频设备 / 池满）时往下走 MCI 兜底：
        # 至少响，只是那条不跟着音量滑块。
        if volume is not None and os.path.splitext(path)[1].lower() == ".wav":
            if self._pool_play(path, overlap, volume):
                return True
        if overlap:
            with self._lock:
                if not self._sfx_free:      # 设备全占满：丢弃，不排队
                    return False
                alias = self._sfx_free.pop()
                self._played += 1
            threading.Thread(target=self._worker, args=(path, alias, volume),
                             name="VoiceSfx", daemon=True).start()
            return True
        if self._busy:
            return False
        with self._lock:
            if self._busy:              # 同帧两次点击抢跑，让先到的那个播
                return False
            self._busy = True
            self._played += 1
        threading.Thread(target=self._worker, args=(path, _VOICE_ALIAS, volume),
                         name="VoicePlayer", daemon=True).start()
        return True

    def _pool_play(self, path, overlap, volume):
        """wav 走 waveOut 池起播，返回是否真的起了播。

        `overlap=False`（语音独占语义）时**任一**后端在播都放弃 —— 池和 MCI
        两条路都查，避免"池里在播语音、MCI 又播一条"变成同时两段。
        `_busy` 由 `_on_pool_free` 在流放完时清。
        """
        with self._lock:
            if not overlap and (self._busy or _WAV_POOL.playing()):
                return False
            ok = _WAV_POOL.play(path, volume, on_free=self._on_pool_free)
            if ok:
                self._played += 1
                if not overlap:
                    self._busy = True
        return ok

    def _on_pool_free(self):
        """池里一条流放完：清独占播放标记（overlap=True 的也会走到，那时
        `_busy` 本来就是 False，无害）。"""
        with self._lock:
            self._busy = False

    def stats(self):
        with self._lock:
            return self._played, self._failed

    # ---------------------------------------------------------- 播放线程
    def _worker(self, path, alias=_VOICE_ALIAS, volume=None):
        """在 `alias` 这条设备上放完 `path`，然后把它还回池子。

        **每条线程只碰自己那条 alias** —— 这是并发能成立的前提：共用一条的话，
        第二个 `open` 会把第一条正在播的顶掉（听到的是一声被截断的"咔"）。
        """
        try:
            # 上一条如果因为异常没关干净，别名会占着媒体设备 -> 先清一次
            _mci_send("close %s" % alias)
            ext = os.path.splitext(path)[1].lower()
            kind = "waveaudio" if ext == ".wav" else "mpegvideo"
            r, _ = _mci_send('open "%s" type %s alias %s'
                             % (path, kind, alias))
            if r:
                with self._lock:
                    self._failed += 1
                return
            # 点击音效音量：相对系统主音量的比例（0~1）。**只对 mpegvideo
            # （mp3/wma）有效** —— 0~1000 是它的合法区间（实测 setaudio 500
            # 后 status 回读 501、发 32767 报超范围错误 282）。
            # waveaudio（.wav）走不到这里：winmm 对它压根不认
            # `setaudio volume`（实测错误 261 "Unrecognized command"），wav
            # 已在 play_path 上游分流到 waveOut 软件混音池；能走到这里的 wav
            # 只剩一种 —— 读不出 PCM 或池满时的 MCI 兜底播放，那时没有音量
            # 可设，照常响。
            # 语音包（volume=None）不设置，保持原样。
            if volume is not None and kind != "waveaudio":
                v = max(0.0, min(1.0, float(volume)))
                _mci_send("setaudio %s volume to %d" % (alias, int(v * 1000.0)))
            _mci_send("play %s" % alias)
            t0 = time.monotonic()
            while time.monotonic() - t0 < self.MAX_SECONDS:
                time.sleep(0.1)
                r, mode = _mci_send("status %s mode" % alias)
                if r or mode.strip().lower() != "playing":
                    break
        except Exception:
            pass
        finally:
            try:
                _mci_send("close %s" % alias)
            finally:
                # 无论怎么结束（含设备抛异常）都要还坑 —— 少了这一步就是
                # "这个音效这辈子只响一次，之后永远占着设备"。
                with self._lock:
                    if alias == _VOICE_ALIAS:
                        self._busy = False
                    elif alias not in self._sfx_free:
                        self._sfx_free.append(alias)


# ---------------------------------------------------------------- 点击音效
# 「鼠标点击音效」是一个**独立的特效分类**：它不画任何像素，只负责"点一下
# 响一声"。与「点击表情 / 点击文字」里那两个语音来源的区别在于它跟画什么
# 无关 —— 想只要声音、不要任何图案时用它。
#
# 音效来源三档，与图案来源同一套语义（内置 / 单文件 / 文件夹）：
#   内置音效  -> assets/sfx 里的那几个（随程序打包，开箱就能响）
#   单个音效  -> 用户挑的某一个文件，每次点击都响它
#   音效文件夹 -> 每次点击从整个文件夹里随机挑一条
SFX_SOURCE_BUILTIN = "内置音效"
SFX_SOURCE_FILE = "单个音效"
SFX_SOURCE_FOLDER = "音效文件夹"
SFX_SOURCES = (SFX_SOURCE_BUILTIN, SFX_SOURCE_FILE, SFX_SOURCE_FOLDER)
# 旧取值 / 界面改名前留下的写法，归一化时一并认掉
SFX_SOURCE_LEGACY = {"内置": SFX_SOURCE_BUILTIN,
                     "自定义音效": SFX_SOURCE_FOLDER,
                     "自定义声音": SFX_SOURCE_FOLDER}


def normalize_sfx_source(value):
    """音效来源归一化；未知取值 -> 内置音效。"""
    s = str(value or "")
    if s in SFX_SOURCES:
        return s
    return SFX_SOURCE_LEGACY.get(s, SFX_SOURCE_BUILTIN)


def builtin_sfx_dir():
    """内置音效目录（`assets/sfx`）。

    `res_path` 会优先取 PyInstaller 解包目录，所以打包后照样找得到。
    """
    return _res_path(os.path.join("assets", "sfx"))


def list_builtin_sfx():
    """内置音效的路径列表（按文件名排序）。"""
    return _scan_files(builtin_sfx_dir(), _AUDIO_EXTS)


def sfx_target(cfg):
    """配置 -> `(要用的音频列表, 是否随机取)`。

    与 `click_source_target` 同一个套路：把这层判断收在一处，场景、面板、
    测试共用。列表为空 = 这一档没配到东西（调用方回退内置音效）。
    """
    src = normalize_sfx_source(cfg.get("click_sfx_source", SFX_SOURCE_BUILTIN))
    if src == SFX_SOURCE_FILE:
        p = str(cfg.get("click_sfx_file", "") or "")
        return ([p] if p and os.path.isfile(p) else []), False
    if src == SFX_SOURCE_FOLDER:
        return list_audio(cfg.get("click_sfx_dir", "")), True
    # 内置：挑定的那一个优先；没挑（或那个文件被删了）就全部随机
    items = list_builtin_sfx()
    want = str(cfg.get("click_sfx_builtin", "") or "")
    if want:
        for p in items:
            if os.path.basename(p) == want:
                return [p], False
    return items, True


def sfx_label(path):
    """音效路径 -> 界面上显示的名字（去扩展名、去音轨号）。"""
    return audio_label(path)


def voice_pack_dir(cfg):
    """「语音与文字统一」用的音频目录 —— 只认**音效文件夹**那一档。

    统一模式需要"一个装着多条音频的目录"才能把文件名一行行变成弹出文字：
    内置音效（数量固定、且是程序自带的那几个）与单个音效都给不出多行文字，
    所以这里对那两档直接返回空串（调用方据此退回自定义文字）。

    这条也决定界面：**统一开关只在来源＝音效文件夹时可用**，开着时来源那一组
    控件被锁住 —— 用户没法一边开着统一、一边把来源换成内置音效。
    """
    if normalize_sfx_source(cfg.get("click_sfx_source")) != SFX_SOURCE_FOLDER:
        return ""
    return str(cfg.get("click_sfx_dir", "") or "")


def voice_match_on(cfg):
    """「语音与文字统一」当前是否真的生效（开着**且**有目录）。"""
    return bool(cfg.get("click_text_voice_match", False)) \
        and bool(voice_pack_dir(cfg))


def builtin_sfx_names():
    """内置音效的显示名列表（界面上那排按钮用）。"""
    return [audio_label(p) for p in list_builtin_sfx()]


class ClickSfx:
    """鼠标点击音效：**不画任何像素**，只在"点一下"时响一声。

    做成独立的特效（独立分类、独立开关）而不是塞进「点击表情」页，是因为
    它跟画什么完全无关 —— 只想要声音、不要图案/文字是常见需求。

    播放复用与两个点击特效**同一个 `VoicePlayer`**，但走 `overlap=True`：
    **允许叠着播** —— 音效是短促的一声，连点时叠起来才有连击手感；串行反而
    像卡顿（上一声没完就把这次吞掉）。同时在响的条数有上限
    （`_SFX_VOICES`），满池之后的新点击丢弃、不排队。
    """

    def __init__(self, player, seed=89):
        self.player = player
        self._rng = random.Random(seed)
        self._played = 0

    def reset(self):
        """本特效没有任何可残留的视觉状态 —— 留着是为了与别的特效同签名。"""

    def active(self):
        """永远返回 False：它不上屏，不该让鼠标层白提交一帧。"""
        return False

    def trigger(self, cfg):
        """一次点击（鼠标或键盘）到了，按来源放一段。返回是否真的播了。"""
        items, _rand = sfx_target(cfg)
        if not items:
            return False
        # overlap=True：音效之间允许重叠。语音那边仍独占 —— 一次点击里
        # 「文字与音频统一」挑中的那条优先（见 fx_scene 的播放分支）。
        # 音量走 MCI setaudio：相对系统主音量的比例（默认 1.0 = 系统音量）。
        vol = cfg.get("click_sfx_volume", 1.0)
        if self.player.play_path(self._rng.choice(items), overlap=True,
                                 volume=vol):
            self._played += 1
            return True
        return False

    def stats(self):
        return self._played


# 旋转过的精灵缓存：{(id(原帧), 档位): (原帧, 旋转后)}。
# 键里带着**原帧对象本身**，是为了让 id() 复用不会撞车（原帧只要还在缓存里
# 就不会被回收，id 也就不会被别的对象占用）。
_ROT_CACHE = {}
_ROT_CACHE_MAX = 512


def _rot_step(ang, steps=CLICK_ALIGN_ROT_STEPS):
    """弧度 -> 旋转档下标（四舍五入并取模）。

    与内置图案那条路径（`round(rot / 2π * ROT_STEPS)`）**必须是同一个式子**：
    两条路径各算一次、算得不一样的话，同一个「图片方向」选项在换素材来源时会
    悄悄差一档。
    """
    return int(round(float(ang) / (2.0 * math.pi) * int(steps))) % int(steps)


def _rotate_premul(spr, step, steps=CLICK_ALIGN_ROT_STEPS):
    """把预乘 RGBA float32 精灵旋转 `step/steps` 圈（顺时针），带缓存。

    顺时针而不是逆时针：屏幕坐标 y 朝下，"飞向右边"的粒子要把它原本朝上的
    图头转成朝右，在视觉上就是顺时针转 90°。

    预乘数据**可以直接**交给 PIL 逐通道重采样：预乘表示本来就是线性的，
    通道插值恰好等于"先合成再插值"的正确结果；换成直通 alpha 反而会在边缘
    渗出半透明的错色。
    """
    if _PImage is None or spr is None:
        return spr
    key = (id(spr), int(step) % int(steps))
    hit = _ROT_CACHE.get(key)
    if hit is not None:
        return hit[1]
    ang = 360.0 * (int(step) % int(steps)) / float(steps)
    if ang <= 0.0:
        out = spr
    else:
        if len(_ROT_CACHE) > _ROT_CACHE_MAX:
            _ROT_CACHE.clear()
        u8 = np.clip(spr * 255.0 + 0.5, 0, 255).astype(np.uint8)
        im = _PImage.fromarray(u8, "RGBA").rotate(-ang, resample=_BICUBIC,
                                                  expand=True)
        out = np.ascontiguousarray(
            np.asarray(im).astype(np.float32) * (1.0 / 255.0))
    _ROT_CACHE[key] = (spr, out)
    return out


class _BurstEngine:
    """点击粒子爆发的物理与上屏（不含配置解析）。
    参数模型参考 pizzazz 那个点击特效库：一次点击炸开 `count` 个粒子，
    速度方向随机（决定"散得多开"）、可加重力（像彩纸往下飘）、可自旋、
    寿命内淡出。

    做成独立的"引擎"而不是直接写进某个特效里，是因为**两个点击特效都要用**：
    「点击表情」整个就是一次爆发，「点击文字」的彩带/星光装饰也是。
    两个特效互斥（同时只开一个），但代码没有必要抄两份。
    """

    MAX = 200                   # 粒子池上限（一次爆发最多这么多）
    ROT_STEPS = 12

    def __init__(self, seed=71):
        n = self.MAX
        self.rng = np.random.default_rng(seed)
        self.px = np.zeros(n, np.float32)
        self.py = np.zeros(n, np.float32)
        self.vx = np.zeros(n, np.float32)
        self.vy = np.zeros(n, np.float32)
        self.t = np.zeros(n, np.float32)
        self.life = np.zeros(n, np.float32)
        self.size = np.zeros(n, np.float32)
        self.rot = np.zeros(n, np.float32)
        self.spin = np.zeros(n, np.float32)
        self.rgb = np.zeros((n, 3), np.float32)
        # 自定义表情包模式下每颗粒子指向外部精灵表的下标（内置图案模式恒 0）
        self.spr_id = np.zeros(n, np.int32)
        # 外部素材（动图）共用的动画时间轴。它在有粒子时一路累加，各张图按
        # 自己的帧数取模 —— 于是"8 帧的 GIF"和"20 帧的 GIF"混在一簇里也能
        # 各按各的节奏循环。不做成"每颗粒子各自的相位"是因为那样得为每个
        # 粒子再加一个时间数组；而同一次点击的粒子本来就该看起来整齐。
        self._anim_t = 0.0
        self._gravity = 0.0
        self._shape = "爱心"        # 内置图案路径当前用的形状（由 spawn 的调用方设）
        # 自定义素材路径是否要按飞行方向旋转精灵（「图片方向随扩散方向」）。
        # **实例级的开关而不是参数**：它只在 draw 里用一次，而 draw 每帧都被
        # 调用、spawn 只在点击那一帧调用 —— 放在这里不必给 draw 再加参数，
        # 也不必每帧从 cfg 里重新解析。
        self._img_align = False
        self.count = 0              # 已用槽位数（紧凑存放，死掉的会被换到前面）

    def active(self):
        return self.count > 0

    def reset(self):
        self.count = 0

    def spawn(self, x, y, shape, colors, count, size, spread, gravity,
              rotate, life, up_bias=0.0, size_jitter=0.45, spr_ids=None,
              align=False):
        """在 (x, y) 炸开一簇粒子。

        `colors` 是 RGB 三元组列表（0~255）：单色图案传一个，彩带传一整盘，
        每个粒子随机取一个。`up_bias` > 0 时整体偏向上方 —— 「点击文字」的
        装饰要往文字那边飘，而不是均匀四散。

        `spr_ids` 给定（长度 = count）时这簇改用**外部精灵表**上屏（自定义
        表情包），`shape` / `colors` 都不再参与绘制，只为保持调用形状一致。

        `align=True` 时把每个粒子的 `rot` 写成**它自己飞出去的方向**的量化档
        （而不是随机角），上屏时据此把精灵转过去。"图片方向随扩散方向"靠它。
        spin 在这条路径上恒为 0，所以 `rot` 写进去是多少就一直是那个值，不必
        每帧重算方向。
        """
        count = max(0, min(int(count), self.MAX))
        if count == 0 or size <= 0:
            return
        # 池满时先让老粒子让位：点击是新事件，比正在淡出的旧粒子重要
        if self.count + count > self.MAX:
            self._drop_oldest(self.count + count - self.MAX)

        idx = slice(self.count, self.count + count)
        k = count
        rng = self.rng
        ang = rng.uniform(0.0, 2.0 * math.pi, k)
        # 速度按 sqrt(uniform) 分布：均匀取半径会让粒子在中心堆成一坨，
        # 开方之后才像"从中心均匀铺开的一张圆盘"
        spd = spread * np.sqrt(rng.uniform(0.12, 1.0, k))
        self.vx[idx] = (np.cos(ang) * spd).astype(np.float32)
        self.vy[idx] = (np.sin(ang) * spd - up_bias * spread * 0.75).astype(np.float32)
        self.px[idx] = float(x)
        self.py[idx] = float(y)
        self.t[idx] = 0.0
        self.life[idx] = (life * rng.uniform(0.75, 1.25, k)).astype(np.float32)
        self.size[idx] = (size * rng.uniform(1.0 - size_jitter,
                                             1.0 + size_jitter, k)).astype(np.float32)
        if align:
            # 直接存**弧度**（角度 + 90°），而不是量化档下标 —— 两条上屏路径
            # 读 `rot` 的方式不同：内置图案那边本来就是
            # `round(rot / 2π * ROT_STEPS)`，图片那边要的是档下标。让存的值
            # 保持"角度"这个物理量，两边各自换算，才不会一边转对一边差一档。
            self.rot[idx] = ((ang + math.pi * 0.5)
                             % (2.0 * math.pi)).astype(np.float32)
        else:
            self.rot[idx] = rng.uniform(0.0, 2.0 * math.pi, k)
        self.spin[idx] = ((rng.uniform(-1.0, 1.0, k) * 7.5) if rotate
                          else np.zeros(k))
        pal = np.asarray(colors, np.float32).reshape(-1, 3)
        pick = rng.integers(0, len(pal), k)
        self.rgb[idx] = pal[pick]
        if spr_ids is None:
            self.spr_id[idx] = 0
        else:
            sid = np.asarray(spr_ids, np.int32).reshape(-1)
            # 长度不足时用 `resize` 循环填满而不是让它抛错：这条路径在渲染
            # 线程上，宁可画得难看一点也不能把一帧打断。
            self.spr_id[idx] = np.resize(sid, k) if sid.size else 0
        self._gravity = float(gravity)
        self.count += count

    def _drop_oldest(self, need):
        """丢掉最老的 need 个粒子，给新粒子腾位置（把存活的后移压实）。"""
        need = min(need, self.count)
        if need <= 0:
            return
        keep = self.count - need
        src = slice(need, self.count)
        for arr in (self.px, self.py, self.vx, self.vy, self.t, self.life,
                    self.size, self.rot, self.spin, self.spr_id):
            arr[:keep] = arr[src]
        self.rgb[:keep] = self.rgb[src]
        self.count = keep

    def update(self, dt):
        if self.count == 0:
            return
        n = self.count
        sl = slice(0, n)
        # 取模是为了长时间运行后不让浮点精度掉到帧间隔以下（连开几天才会遇到，
        # 但那时症状是"动图突然卡成静帧"，极难查）。
        self._anim_t = (self._anim_t + dt) % 3600.0
        self.t[sl] += dt
        if getattr(self, "_gravity", 0.0):
            self.vy[sl] += 260.0 * dt
        # 空气阻力：让粒子末段慢下来，比匀速飞出去自然（也顺手把速度压住，
        # 免得长时间不点击后速度累积成天文数字）
        damp = max(0.0, 1.0 - 1.6 * dt)
        self.vx[sl] *= damp
        self.vy[sl] *= damp
        self.px[sl] += self.vx[sl] * dt
        self.py[sl] += self.vy[sl] * dt
        self.rot[sl] += self.spin[sl] * dt

        dead = np.nonzero(self.t[sl] >= self.life[sl])[0]
        if dead.size:
            alive = np.nonzero(self.t[sl] < self.life[sl])[0]
            m = alive.size
            for arr in (self.px, self.py, self.vx, self.vy, self.t, self.life,
                        self.size, self.rot, self.spin, self.spr_id):
                arr[:m] = arr[alive]
            self.rgb[:m] = self.rgb[alive]
            self.count = m

    def draw(self, u32, w, h, opacity=1.0, tint=None, imgs=None):
        """上屏。

        `tint` 非空时整簇用同一个颜色（点击表情的"主色"模式）。
        `imgs` 非空时改用**外部精灵表**（自定义表情包）：每颗粒子按
        `spr_id` 取自己那一张，图案与颜色都不再参与 —— 这条路径下精灵是
        矩形（用户图片保持原比例），所以裁剪必须**分别**取宽与高。若它是
        `ClickSpriteSet`（有动图），还会按 `_anim_t` 取当前该显示的那一帧。
        """
        if self.count == 0 or opacity <= 0.004:
            return
        anim = imgs if isinstance(imgs, ClickSpriteSet) else None
        n_img = len(imgs) if imgs is not None else 0
        for i in range(self.count):
            t = float(self.t[i])
            life = float(self.life[i])
            if life <= 0.0:
                continue
            u = t / life
            # 生灭包络：开场极快亮起、尾段渐渐淡出。两端都留一点缓冲，
            # 否则粒子会"啪"地出现又"啪"地消失。
            env = min(1.0, t / 0.06) * min(1.0, (1.0 - u) / 0.42)
            a = opacity * max(0.0, env)
            if a <= 0.01:
                continue
            if n_img:
                j = int(self.spr_id[i])
                if j < 0 or j >= n_img:
                    continue
                # 动图按共用时间轴取帧；静态素材就是它本身（`frame_at` 里
                # `counts == 1` 那条分支，等于直接返回）。
                spr = anim.frame_at(j, self._anim_t) if anim is not None \
                    else imgs[j]
                # 「图片方向随扩散方向」：按粒子飞出去的方向把图转过去。
                # `rot` 存的是**弧度**（spawn 时算好的），这里换成旋转档下标
                # 去查缓存 —— 与内置图案那条 `round(rot / 2π * STEPS)` 同一个式子。
                if self._img_align:
                    spr = _rotate_premul(
                        spr, _rot_step(float(self.rot[i])),
                        CLICK_ALIGN_ROT_STEPS)
            else:
                r, g, b = (self.rgb[i] if tint is None else tint)
                hexc = "#%02X%02X%02X" % (int(r), int(g), int(b))
                shape = getattr(self, "_shape", "爱心")
                step = _rot_step(self.rot[i], self.ROT_STEPS)
                size = int(max(8, round(float(self.size[i]))))
                # 颜色进缓存键：彩带逐粒子不同色，单色图案则整簇共用一条缓存
                spr = _shape_sprite(shape, hexc, size, step, self.ROT_STEPS)
            # 高与宽**分开取**。内置图案是正方形（两者永远相等），但自定义
            # 表情包的图是矩形 —— 拿高当边长会让墨迹只落进左上角一块，
            # 也就是"图被切掉大半"（与 ClickTextField._blit 同一个坑）。
            sh, sw = spr.shape[0], spr.shape[1]
            x0 = int(round(float(self.px[i]) - sw * 0.5))
            y0 = int(round(float(self.py[i]) - sh * 0.5))
            gx0, gy0 = max(0, x0), max(0, y0)
            gx1, gy1 = min(int(w), x0 + sw), min(int(h), y0 + sh)
            if gx1 <= gx0 or gy1 <= gy0:
                continue
            sub = spr[gy0 - y0:gy1 - y0, gx0 - x0:gx1 - x0]
            aa = sub[:, :, 3] * a
            if not (aa > 0.004).any():
                continue
            dst = u32.reshape(int(h), int(w))[gy0:gy1, gx0:gx1]
            _over_blend(dst, sub, aa, 255.0 * a)


# 「弹出」的几个缩放档位（直接按不同字号重渲染，不是缩放位图）。
# 中段 1.08 是一次**过冲** —— 正是它给出"啵"地弹出来的手感，线性放大到 1
# 会显得很平。
_TEXT_POP_STEPS = (0.55, 0.78, 0.95, 1.08, 1.00)
_TEXT_CACHE = {}
_TEXT_CACHE_MAX = 400           # 多了"倾斜档"这一维，上限跟着放大

# 倾斜角度档位（度）。做量化档而不是连续值，是为了让精灵缓存键有限 ——
# 每次点击的角度是**固定的**（点击那一下定下来，之后不再变），所以 5° 的
# 粒度只影响"这次看起来偏多少度"，不会有动画跳动。
_TEXT_TILT_STEPS = (-30.0, -25.0, -20.0, -15.0, -10.0, -5.0, 0.0,
                    5.0, 10.0, 15.0, 20.0, 25.0, 30.0)
_TEXT_TILT_ZERO = 6             # 上表里 0.0 的下标


def _tilt_choices(amount):
    """按倾斜幅度（0~1）挑出允许的角度档下标。

    幅度 0 → 只有 0°，1 → 全部 13 档。文字歪得太狠会读不出来，所以上限
    就取表里的 ±30°。
    """
    if amount <= 0.0:
        return (_TEXT_TILT_ZERO,)
    lim = 30.0 * min(1.0, amount)
    ids = tuple(i for i, d in enumerate(_TEXT_TILT_STEPS) if abs(d) <= lim + 1e-6)
    return ids or (_TEXT_TILT_ZERO,)


def _text_sprites(text, font_name, size, color_hex, tilt_idx=_TEXT_TILT_ZERO):
    """取一行文字的 5 档缩放精灵（预乘 RGBA 列表），带缓存。

    `tilt_idx` 是 `_TEXT_TILT_STEPS` 的下标：同一次点击的 5 档共用同一个
    倾斜角，所以一档角度只多 5 个缓存条目。
    """
    key = (text, font_name, int(size), color_hex, int(tilt_idx))
    hit = _TEXT_CACHE.get(key)
    if hit is not None:
        return hit
    if len(_TEXT_CACHE) > _TEXT_CACHE_MAX:
        _TEXT_CACHE.clear()
    ang = _TEXT_TILT_STEPS[int(tilt_idx) % len(_TEXT_TILT_STEPS)]
    out = [_text_sprite(text, font_name, int(size), color_hex, s, ang)
           for s in _TEXT_POP_STEPS]
    _TEXT_CACHE[key] = out
    return out


class ClickEmojiField:
    """点击表情：每点一下在光标处炸开一簇图案。

    与「鼠标拖动特效」的区别在于触发方式 —— 它不是跟着光标连续铺开，而是
    **一次点击一个瞬时爆发**。参数模型参考 pizzazz 那个点击特效库
    （数量 / 尺寸 / 扩散 / 重力 / 自旋）。

    图案有两个来源：内置图案（`_build_shape` 用代码画，不依赖任何文件）
    与自定义表情包（`ClickImagePool` 从用户给的文件夹里随机取图，**在后台
    线程**解码）。后者的素材还没就绪时自动回退到内置图案 —— 点击永远
    有反馈，不会出现"点了没动静"。

    粒子物理与上屏在 `_BurstEngine` 里（「点击文字」的装饰也用它）。
    """

    def __init__(self, w, h, seed=73):
        self.rng = np.random.default_rng(seed)
        self.w, self.h = w, h
        self._mx = w * 0.5
        self._my = h * 0.5
        self._clicked = 0
        # 自定义表情包时的精灵快照（None = 这一簇走内置图案）。持有的是
        # 池子交出来的**不可变快照**，后台换素材不会影响正在飞的那一簇。
        self._imgs = None
        self.engine = _BurstEngine(seed=seed)

    def set_pointer(self, x, y, clicked=0):
        self._mx = float(x)
        self._my = float(y)
        if clicked:
            self._clicked = int(clicked)

    def resize(self, w, h):
        self.w, self.h = w, h

    def reset(self):
        self.engine.reset()
        self._imgs = None
        self._clicked = 0

    def active(self):
        return self.engine.active()

    def _spawn_builtin(self, cfg):
        """内置图案：颜色 / 图案 / 自旋全都听配置的。

        「图片方向」对内置图案同样有效 —— 爱心、彩带这类图形本身就有朝向，
        让它跟着飞行方向转，甩出来的是一条条轨迹而不是一堆随机朝向的贴纸。
        所以这里**不区分来源**：`align` 只看那一个配置键。
        """
        shape = str(cfg.get("click_emoji_shape", _SHAPE_NAMES[0]))
        if shape not in _SHAPE_NAMES:
            shape = _SHAPE_NAMES[0]
        # 颜色缺键时按**该图案的惯用色**兜底（爱心红、星星黄……），
        # 而不是所有图案都退回同一个粉。
        color = str(cfg.get("click_emoji_color",
                            _SHAPE_DEFAULT_COLORS[shape]))
        if color == COLOR_RAINBOW:
            # 逐粒子从调色板取色。哨兵值必须在 hex_to_bgr 之前拦住 ——
            # 它对非法串是"静默回退浅蓝"，不拦就成了"选了彩色却全蓝"。
            colors = [_hex_rgb(c) for c in _CONFETTI_COLORS]
        else:
            colors = [_hex_rgb(color)]
        align = (str(cfg.get("click_emoji_align", CLICK_ALIGN_VERTICAL))
                 == CLICK_ALIGN_SPREAD)
        self.engine._shape = shape
        # 内置图案自带 12 档旋转精灵缓存（`_shape_sprite` 自己按档取），
        # 所以走的是 draw 里那条"角度 → 档位"的换算，**不是**图片那条位图旋转。
        self.engine._img_align = False
        self.engine.spawn(
            self._mx, self._my, shape, colors,
            count=int(cfg.get("click_emoji_count", 12)),
            size=float(cfg.get("click_emoji_size", 22)),
            spread=float(cfg.get("click_emoji_spread", 90)),
            gravity=float(cfg.get("click_emoji_gravity", 1.0)),
            # 勾了"随扩散方向"就必须关掉自旋：spin 会把 rot 逐帧累加，
            # 方向定下来的那一瞬间就散了，等于这个选项不起作用。
            rotate=bool(cfg.get("click_emoji_rotate", True)) and not align,
            life=float(cfg.get("click_emoji_life", 1.1)),
            align=align)

    def _spawn_custom(self, cfg, sprites):
        """自定义表情包：按「单次选择」决定这一簇怎么取图。

        * 随机多图 —— 每颗粒子各自随机（热闹，但一次点击里花样多）；
        * 整簇同图 —— 一次点击只用同一张（看得清这张图本身，动图尤其明显：
          十几个粒子同步播同一段动画，而不是各演各的）。

        不自旋、不做尺寸抖动 —— 图片精灵不是矢量，两者都要重采样；而"图
        随机 × 池子里两档尺寸"本来就提供了同样的视觉变化，白付那笔账不划算。
        例外是「图片方向随扩散方向」：那个旋转是**用户明确要的**，量化成
        12 档 + 缓存之后，一次爆发最多多认 12 张图，第二次点击起全是命中。
        """
        count = max(0, min(int(cfg.get("click_emoji_count", 12)),
                           self.engine.MAX))
        if count <= 0:
            return
        n = len(sprites)
        if str(cfg.get("click_emoji_pick", CLICK_PICK_MULTI)) == CLICK_PICK_ONE:
            ids = np.full(count, int(self.rng.integers(0, n)), np.int32)
        else:
            ids = self.rng.integers(0, n, count)
        align = (str(cfg.get("click_emoji_align", CLICK_ALIGN_VERTICAL))
                 == CLICK_ALIGN_SPREAD)
        self.engine._img_align = align
        self.engine.spawn(
            self._mx, self._my, "", [(255, 255, 255)],
            count=count,
            size=float(cfg.get("click_emoji_size", 22)),
            spread=float(cfg.get("click_emoji_spread", 90)),
            gravity=float(cfg.get("click_emoji_gravity", 1.0)),
            rotate=False,
            life=float(cfg.get("click_emoji_life", 1.1)),
            size_jitter=0.0,
            spr_ids=ids,
            align=align)
        self._imgs = sprites

    def update(self, dt, cfg):
        dt = min(max(float(dt), 0.0), 0.05)
        clicked = self._clicked
        self._clicked = 0
        kind, src = click_source_target(cfg)
        custom = bool(kind)
        if custom:
            # 只登记需求：解码在后台线程做，这里是一次比较的代价。
            CLICK_IMAGE_POOL.configure(kind, src,
                                       float(cfg.get("click_emoji_size", 22)))
        if clicked:
            sprites = CLICK_IMAGE_POOL.sprites() if custom else None
            if sprites:
                self._spawn_custom(cfg, sprites)
            else:
                # 素材还没就绪（首次配置后要解码几十张图）或文件夹是空的：
                # 回退内置图案，让**点击始终有反馈**。
                self._imgs = None
                self._spawn_builtin(cfg)
        self.engine.update(dt)

    def draw(self, u32, w, h, cfg):
        op = float(np.clip(float(cfg.get("click_emoji_opacity", 1.0)), 0.0, 1.0))
        self.engine.draw(u32, w, h, op, imgs=self._imgs)


class ClickTextField:
    """点击文字：点击处弹出文字并上飘淡出，可内嵌彩带 / 星光。

    * **多个文字**：配置里一行一个，每次点击**轮换**取下一个 —— 轮换而不是
      随机，是为了让"我配了三条"能一眼确认三条都生效了。
    * **弹出**：开头按 5 档字号递进（中段有轻微过冲），不是缩放位图 ——
      文字是矢量轮廓，按字号重渲染才清楚。
    * **可读性**：文字带半透明描边，否则白字落在浅色桌面上会糊掉。
    * **装饰**：彩带 / 星光复用 `_BurstEngine`，`up_bias` 让它们整体偏上，
      朝着文字飘而不是均匀四散。
    * **语音包（文字与音频统一）**：开 `click_text_voice_match` 时，文字不再
      来自配置，而是**先随机挑一条音频、再拿它的文件名当文字** —— 看到的
      那行字与听到的那段声音永远是同一条。挑中的路径通过 `take_voice()`
      交给场景去播（播放归 `VoicePlayer`，这里只负责"是哪一条"）。
    """

    def __init__(self, w, h, seed=79):
        self.rng = np.random.default_rng(seed)
        self.w, self.h = w, h
        self._mx = w * 0.5
        self._my = h * 0.5
        self._clicked = 0
        self.pops = []              # [{x, y, t, life, idx, text}]
        self._next_idx = 0          # 多条文字轮换用的游标
        self._voice_q = None        # 这次点击要播的音频（统一模式）
        self.deco = _BurstEngine(seed=seed + 1)
        self._failed = False

    def take_voice(self):
        """取走并清空"这次点击要播的音频"（没有则 None）。"""
        p = self._voice_q
        self._voice_q = None
        return p

    def set_pointer(self, x, y, clicked=0):
        self._mx = float(x)
        self._my = float(y)
        if clicked:
            self._clicked = int(clicked)

    def resize(self, w, h):
        self.w, self.h = w, h

    def reset(self):
        self.pops = []
        self.deco.reset()
        self._clicked = 0
        self._next_idx = 0
        self._voice_q = None

    def active(self):
        return bool(self.pops) or self.deco.active()

    @staticmethod
    def texts_of(cfg):
        """把配置里的多行文字解析成列表（去空行、去首尾空白）。"""
        raw = str(cfg.get("click_text_list", ""))
        return [s.strip() for s in raw.replace("\r", "\n").split("\n")
                if s.strip()]

    def _resolve(self, cfg):
        """定下这次点击显示的文字，以及（统一模式下）要同步播的音频。

        返回 `(文字 或 None, 音频路径 或 None, 轮换下标)`。

        统一模式下**没选目录 / 文件夹里没有音频时不报错、也不吞掉这次点击**，
        而是退回自定义文字 —— 换文件夹的间隙、空目录都算常见状态，让点击
        变成"什么都不发生"比显示一句自己写的字难懂得多。目录来自
        `voice_pack_dir()`（即「点击音效」的音效文件夹），不再单开一个键。
        """
        if voice_match_on(cfg):
            path = pick_audio(voice_pack_dir(cfg))
            if path is not None:
                return audio_label(path), path, 0
        texts = self.texts_of(cfg)
        if not texts:
            return None, None, 0
        idx = self._next_idx % len(texts)
        self._next_idx = (self._next_idx + 1) % len(texts)
        return texts[idx], None, idx

    def update(self, dt, cfg):
        if self._failed:
            return
        dt = min(max(float(dt), 0.0), 0.05)
        clicked = self._clicked
        self._clicked = 0
        life = max(0.2, float(cfg.get("click_text_life", 1.4)))

        if clicked:
            text, voice, idx = self._resolve(cfg)
            if text:
                # 倾斜与横向漂移在**点击这一下**定下来，之后不再变 ——
                # 每帧重随机会让文字疯狂抖动。
                spin = float(np.clip(float(cfg.get("click_text_spin", 0.75)),
                                     0.0, 1.0))
                tilt = int(self.rng.choice(_tilt_choices(spin)))
                drift = float(self.rng.uniform(-1.0, 1.0)) * 0.45
                # 文字在这一刻**定死进弹幕条目**（而不是到 draw 时再按下标
                # 去查）：统一模式下这一次是哪条音频是随机的，到 draw 时重查
                # 就会查到另一条 —— 那正是"字与声音对不上"的来源。
                self.pops.append({
                    "x": float(self._mx), "y": float(self._my), "t": 0.0,
                    "life": life, "text": text, "idx": idx,
                    "tilt": tilt, "drift": drift,
                })
                self._voice_q = voice
            # 装饰：与文字同一个爆发点，整体偏上
            deco = str(cfg.get("click_text_deco", "彩带"))
            if deco != "无":
                if deco == "星光":
                    shape, colors, grav, rot = (
                        "星星", [_hex_rgb(str(cfg.get("click_text_color",
                                                      "#FFFFFF")))], 0.0, True)
                else:
                    shape, colors, grav, rot = (
                        "彩带", [_hex_rgb(c) for c in _CONFETTI_COLORS], 1.0,
                        True)
                self.deco._shape = shape
                self.deco.spawn(
                    self._mx, self._my, shape, colors,
                    count=14, size=float(cfg.get("click_text_size", 34)) * 0.62,
                    spread=95.0, gravity=grav, rotate=rot, life=life,
                    up_bias=0.45)

        if self.pops:
            for pp in self.pops:
                pp["t"] += dt
            self.pops = [pp for pp in self.pops if pp["t"] < pp["life"]]
        self.deco.update(dt)

    def draw(self, u32, w, h, cfg):
        if self._failed:
            return
        op = float(np.clip(float(cfg.get("click_text_opacity", 1.0)), 0.0, 1.0))
        if self.pops and op > 0.004:
            try:
                texts = self.texts_of(cfg)
                font_name = str(cfg.get("click_text_font", "微软雅黑"))
                size = float(cfg.get("click_text_size", 34))
                color = str(cfg.get("click_text_color", "#FFFFFF"))
                rise = float(cfg.get("click_text_rise", 90))
                for pp in self.pops:
                    # 文字优先取**点击那一刻定下的**那份（统一模式下是随机挑中
                    # 的那条音频的名字）；老数据没有该字段时才回退按下标查。
                    txt = pp.get("text")
                    if txt is None:
                        if pp["idx"] >= len(texts):
                            continue
                        txt = texts[pp["idx"]]
                    steps = _text_sprites(txt, font_name,
                                          size, color, pp.get("tilt",
                                                              _TEXT_TILT_ZERO))
                    u = pp["t"] / max(pp["life"], 1e-4)
                    # 选缩放档：弹出段走 5 档递进，之后固定最大档
                    si = min(len(steps) - 1,
                             int(pp["t"] / 0.055)) if pp["t"] < 0.22 else len(steps) - 1
                    spr = steps[max(0, si)]
                    # 上飘用 ease-out：开头冲上去、末段慢下来，比匀速自然；
                    # 再叠一点**点击时定好的**横向漂移，让几行字散开而不是
                    # 叠在同一条竖线上。
                    ease = 1.0 - (1.0 - min(1.0, u)) ** 2
                    dx = pp.get("drift", 0.0) * rise * ease
                    alpha = min(1.0, pp["t"] / 0.08) \
                        * min(1.0, (1.0 - u) / 0.35)
                    a = op * max(0.0, alpha)
                    if a <= 0.01:
                        continue
                    self._blit(u32, w, h, spr, pp["x"] + dx,
                               pp["y"] - rise * ease, a)
            except Exception:
                # 只报一次：反复抛会耗光"连续失败"配额把整个渲染线程熔断，
                # 一个文字效果画不出来不该让雨也停。
                self._failed = True
                raise
        self.deco.draw(u32, w, h, op)

    def _blit(self, u32, w, h, img, cx, cy, a):
        """把精灵以 (cx, cy) 为中心叠加到缓冲上。

        ⚠️ **必须分别取高与宽**：文字精灵宽高不等（"太好了" 是 116×46），
        拿 `shape[0]` 当正方形边长会只取到左边一个 46px 宽的方形区域 ——
        横向被切掉大半（实测墨迹 104px 只落屏 41px），表现为"文字显示
        不完整"。图案精灵（`_shape_sprite`）恰好是正方形，所以这个错误在
        那边不会被发现。
        """
        sh, sw = img.shape[:2]
        x0 = int(round(cx - sw * 0.5))
        y0 = int(round(cy - sh * 0.5))
        gx0, gy0 = max(0, x0), max(0, y0)
        gx1, gy1 = min(int(w), x0 + sw), min(int(h), y0 + sh)
        if gx1 <= gx0 or gy1 <= gy0:
            return
        sub = img[gy0 - y0:gy1 - y0, gx0 - x0:gx1 - x0]
        aa = sub[:, :, 3] * a
        if not (aa > 0.004).any():
            return
        dst = u32.reshape(int(h), int(w))[gy0:gy1, gx0:gx1]
        _over_blend(dst, sub, aa, 255.0 * a)


__all__ = [
    "CLICK_ALIGNS", "CLICK_ALIGN_ROT_STEPS", "CLICK_ALIGN_SPREAD",
    "CLICK_ALIGN_VERTICAL", "CLICK_IMAGE_POOL", "CLICK_PICKS",
    "CLICK_PICK_MULTI", "CLICK_PICK_ONE", "CLICK_SOURCES",
    "CLICK_SOURCE_BUILTIN", "CLICK_SOURCE_FILES", "CLICK_SOURCE_FOLDER",
    "CLICK_SOURCE_LEGACY", "COLOR_RAINBOW", "ClickEmojiField",
    "ClickImagePool", "ClickSfx", "ClickSpriteSet", "ClickTextField",
    "SFX_SOURCES", "SFX_SOURCE_BUILTIN", "SFX_SOURCE_FILE",
    "SFX_SOURCE_FOLDER", "SFX_SOURCE_LEGACY", "VoicePlayer",
    "_AUDIO_CACHE", "_AUDIO_EXTS", "_AUDIO_TTL", "_BurstEngine",
    "_CONFETTI_COLORS", "_FILES_CACHE", "_FILES_TTL", "_FONT_CACHE",
    "_FONT_FILES", "_IMG_EXTS", "_ROT_CACHE", "_ROT_CACHE_MAX",
    "_SFX_ALIASES", "_SFX_VOICES", "_SHAPE_CACHE", "_SHAPE_CACHE_MAX",
    "_SHAPE_DEFAULT_COLORS", "_SHAPE_NAMES", "_TEXT_CACHE",
    "_TEXT_CACHE_MAX", "_TEXT_POP_STEPS", "_TEXT_TILT_STEPS",
    "_TEXT_TILT_ZERO", "_TRACK_NO", "_VOICE_ALIAS", "_build_shape",
    "_font", "_frame_indexes", "_frame_ms", "_load_click_frames",
    "_mci_send", "_rot_step", "_rotate_premul", "_scan_files",
    "_shape_sprite", "_text_sprite", "_text_sprites", "_tilt_choices",
    "_winmm", "audio_label", "audio_labels", "builtin_sfx_dir",
    "builtin_sfx_names", "click_source_target", "count_audio",
    "invalidate_media_cache", "list_audio", "list_builtin_sfx",
    "list_image_files", "normalize_sfx_source", "normalize_source",
    "pick_audio", "scan_image_files", "sfx_label", "sfx_target",
    "voice_match_on", "voice_pack_dir",
]
