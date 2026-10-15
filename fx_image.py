# -*- coding: utf-8 -*-
"""像素底层：颜色解析 / 预乘打包 / over 混合 / PIL 可用性探测。

**为什么单独一个模块**：这些是最底层的设施，被两边共用 ——
`fx_effects.py`（所有特效 + 宠物）与 `fx_click.py`（点击管线）。
若把它们留在 `fx_effects.py` 里让 `fx_click` 反向 import，就会构成
`fx_effects -> fx_click -> fx_effects` 环，而且**先 import fx_click 的那条路径
会静默拿到半初始化的模块**（`import *` 只能看到已执行到的定义）—— 那种故障
时好时坏、极难定位。摆成叶子模块，依赖方向就是单向下行的：

    fx_image  <-  fx_click  <-  fx_effects

这一层不认识"特效"，只认识像素：进来一个色值或一块预乘 RGBA，出去一块
可以直接写进 Overlay 的 uint32 视图。
"""
import numpy as np

# 宠物跟随的卡通形象用 PIL 画（超采样 + LANCZOS，与 fx_ui._render_aa 同一套
# 先例）：三角耳朵 / 多段尾巴 / 细胡须这类形状用绘图原语表达要干净得多。
# 形状全部来自代码，不引入任何图片资源 —— 也就不牵扯第三方形象授权。
try:
    from PIL import Image as _PImage
    from PIL import ImageDraw as _PDraw
    from PIL import ImageFont as _PImageFont
    _LANCZOS = _PImage.Resampling.LANCZOS
    _BICUBIC = _PImage.Resampling.BICUBIC
except Exception:                       # pragma: no cover - 打包环境必有 PIL
    _PImage = None
    _PDraw = None
    _PImageFont = None
    _LANCZOS = None
    _BICUBIC = None


# ------------------------------------------------------------------ 工具
def hex_to_bgr(s):
    s = s.lstrip("#").strip()
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    try:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        r, g, b = 191, 227, 255
    return np.array([b, g, r], dtype=np.float32)


def _premul_pack(b, g, r, a):
    """把 4 个 uint32 通道打包成 0xAARRGGBB。"""
    return b | (g << np.uint32(8)) | (r << np.uint32(16)) | (a << np.uint32(24))


def _over_blend(dst, sub, a, sc):
    """把**预乘**精灵 `sub` 以覆盖率 `a` 叠加到 `dst` 上（原地写回 dst 子区域）。

    这是真正的 over 混合，而不是本文件其它特效惯用的"非零覆盖"：

        dst = src + dst·(1 − src_a)

    缓冲本身就是预乘格式，所以 over 不必再除 alpha。**有羽化边缘的精灵必须走
    这里** —— 覆盖写法会把半透明边缘像素写成"实心但偏暗"，叠在涟漪 / 水面上
    就是一圈脏边。不透明粒子（雨滴、雪花的实心核）用覆盖就够了。

    参数：
        dst  目标：`u32.reshape(h, w)[y0:y1, x0:x1]` 的切片视图
        sub  精灵的对应子区域（预乘 RGBA float32，0~1）
        a    每像素覆盖率 = 精灵 alpha × 整体不透明度
        sc   常量缩放 = 255 × 整体不透明度（把预乘色转回 0~255 整数）
    """
    inv = 1.0 - a
    d = dst.astype(np.uint32)
    dr = ((d >> np.uint32(16)) & np.uint32(0xFF)).astype(np.float32)
    dg = ((d >> np.uint32(8)) & np.uint32(0xFF)).astype(np.float32)
    db = (d & np.uint32(0xFF)).astype(np.float32)
    da = ((d >> np.uint32(24)) & np.uint32(0xFF)).astype(np.float32)

    nr = sub[:, :, 0] * sc + dr * inv
    ng = sub[:, :, 1] * sc + dg * inv
    nb = sub[:, :, 2] * sc + db * inv
    na = a * 255.0 + da * inv      # a 已含不透明度

    out = (np.clip(nb, 0.0, 255.0).astype(np.uint32)
           | (np.clip(ng, 0.0, 255.0).astype(np.uint32) << np.uint32(8))
           | (np.clip(nr, 0.0, 255.0).astype(np.uint32) << np.uint32(16))
           | (np.clip(na, 0.0, 255.0).astype(np.uint32) << np.uint32(24)))
    dst[...] = out


def _smooth5(a, k, passes=1):
    """5 点拉普拉斯平滑（原地）。2D / 3D 数组都适用：
    3D 时最后一维是通道，各通道独立平滑。

    `c += k*(邻居均值 - c)` 里 RHS 会先整体算完再累加，所以重叠读写安全。
    """
    k = np.float32(k)
    for _ in range(passes):
        c = a[1:-1, 1:-1]
        c += k * (0.25 * (a[:-2, 1:-1] + a[2:, 1:-1]
                          + a[1:-1, :-2] + a[1:-1, 2:]) - c)


def _rs_table(g, f):
    """整数倍双线性放大的取样表：把长度 g 的轴放大 f 倍。

    返回 (i0, i1, w)，于是 `out = a[i0] + (a[i1] - a[i0]) * w` 就是双线性插值。
    权值以 f 为周期重复，所以整张表只在建网格时算一次，每帧只做两次 take。

    为什么必须有这一步：上屏是**最近邻**采样，模拟网格（短边 ~110、格子 ~10px）
    直接铺到屏幕上，每个格子就是一块 10px 的硬方块 —— 满屏"方格纸"。
    参考实例（WebGL Fluid）的染料纹理是 1024 宽（约半个屏宽）所以丝滑；CPU 上把
    模拟网格提到那个量级要付出 20 倍平流开销，不划算。改成先在**模拟网格上**做
    双线性放大到"显示网格"，再由最近邻铺屏：插值后的曲面是连续的，硬边消失，
    残留的只是格子边界处的一阶不连续（肉眼几乎不可见）。
    """
    t = np.arange(g * f, dtype=np.float32)
    u = (t + 0.5) / float(f) - 0.5          # 目标像素中心 -> 源坐标
    i0 = np.floor(u)
    np.clip(i0, 0.0, float(g - 1), out=i0)
    i1 = np.minimum(i0 + 1.0, float(g - 1))
    w = np.clip(u - i0, 0.0, 1.0)
    return (i0.astype(np.int32), i1.astype(np.int32), w.astype(np.float32))


def _hsv_to_rgb(h, s, v):
    """向量化 HSV->RGB（h/s/v ∈ [0,1]，任意同形状数组）。返回 (r, g, b)。"""
    h = np.mod(h, 1.0) * 6.0
    i = np.floor(h).astype(np.int32)
    f = h - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    i = np.mod(i, 6)
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return r, g, b


# `_hex_rgb` 与 `hex_to_bgr` 是同一件事的两种出口（后者给内部
# 打包用，前者给需要整数三元组的调用方），所以放一起。
def _hex_rgb(hex_str):
    """#RRGGBB -> (R, G, B) 的 0~255 整数三元组。"""
    bgr = hex_to_bgr(hex_str)
    return (int(bgr[2]), int(bgr[1]), int(bgr[0]))


__all__ = [
    "_BICUBIC", "_LANCZOS", "_PDraw", "_PImage", "_PImageFont", "_hex_rgb",
    "_hsv_to_rgb", "_over_blend", "_premul_pack", "_rs_table", "_smooth5",
    "hex_to_bgr",
]
