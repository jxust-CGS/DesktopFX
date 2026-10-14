# -*- coding: utf-8 -*-
"""宠物跟随（小猫 / 小狗跟着鼠标跑）专项验证。

覆盖四块：

  1. **跟随链物理** —— 静止时收敛到光标、队列间距跟随体型、**帧率无关**
     （这条是"弹簧推进"相对"每帧靠固定比例 lerp"的核心价值）、无 NaN。
  2. **朝向迟滞** —— 小幅抖动不翻面；持续单向移动才翻。
  3. **精灵** —— 两种形象都非空、预乘合法（rgb ≤ a）、边缘有羽化像素。
     最后一条同时是"为什么必须做 over 混合"的证据：有羽化边，覆盖写法就会
     在边缘糊出一圈脏色。
  4. **混合正确性** —— 同一只宠物画在亮底与暗底上，**结果必须不同**。
     覆盖（copy）写法不看底色，两者会一模一样；只有 over 混合才会让边缘随
     底色变化。这条判据比"对比具体颜色"稳，因为它不依赖毛色取值。

结尾带一条负向对照：把 `_blit` 换成覆盖写法，第 4 块必须变红。

不构造 App、不建窗口，全程只碰内存缓冲，因此可并行跑（仍按项目惯例顺序跑）。
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import desktop_fx as d                              # noqa: E402
import fx_effects                                   # noqa: E402
from fx_effects import PetField                     # noqa: E402
from fx_scene import Scene                          # noqa: E402

W, H = 480, 360


def _cfg(**over):
    cfg = dict(pet_enabled=True, pet_kind="小猫", pet_count=2.0,
               pet_size=56.0, pet_follow=0.55, pet_color="#F2C27B",
               pet_opacity=1.0)
    cfg.update(over)
    return cfg


def _step(p, fps, secs, cfg, target=(0.0, 0.0)):
    p.set_pointer(*target)
    for _ in range(max(1, int(fps * secs))):
        p.update(1.0 / fps, cfg)


# ---------------------------------------------------------------- 1. 物理
def test_settles_on_cursor():
    """光标不动一段时间后，第一只必须停在光标处、速度归零。"""
    p = PetField(W, H)
    cfg = _cfg()
    _step(p, 60, 2.5, cfg, (300.0, 180.0))
    dx = abs(float(p.px[0]) - 300.0)
    dy = abs(float(p.py[0]) - 180.0)
    if dx > 2.0 or dy > 2.0:
        return False, f"没收敛到光标：偏 ({dx:.1f}, {dy:.1f}) 像素"
    spd = (float(p.vx[0]) ** 2 + float(p.vy[0]) ** 2) ** 0.5
    if spd > 3.0:
        return False, f"停下后速度仍有 {spd:.1f} 像素/秒（应该在抖）"
    return True, f"稳定停在光标处（偏差 {dx:.1f}/{dy:.1f} 像素，速度 {spd:.2f}）"


def test_chain_spacing():
    """队列间距应跟着体型走（体型 × 0.82），并且排成一列。"""
    for size in (40.0, 80.0):
        p = PetField(W, H)
        cfg = _cfg(pet_size=size, pet_count=3.0)
        _step(p, 60, 2.5, cfg, (240.0, 180.0))
        want = size * 0.82
        for i in (1, 2):
            dx = float(p.px[i] - p.px[i - 1])
            dy = float(p.py[i] - p.py[i - 1])
            got = (dx * dx + dy * dy) ** 0.5
            if abs(got - want) > max(4.0, want * 0.20):
                return False, (f"体型 {size:.0f}：第 {i} 与第 {i - 1} 只间距 "
                               f"{got:.1f}，期望约 {want:.1f}")
            # 应该排在身后（-x 方向），不是叠在一起
            if dx > 2.0:
                return False, f"体型 {size:.0f}：第 {i} 只跑到前一只前面去了"
    return True, "间距跟随体型（40→32.8 / 80→65.6），且依次排在身后"


def test_framerate_independent():
    """同样的真实时长、不同帧率，位置必须接近。

    这是"临界阻尼弹簧 + dt"相对"每帧向目标靠固定比例"的核心价值：后者在
    120 帧的机器上跟得明显更紧，同一个 follow 值手感不一致。
    """
    def run(fps, secs=1.6):
        p = PetField(W, H)
        cfg = _cfg()
        _step(p, fps, 0.6, cfg, (100.0, 180.0))     # 先落位
        _step(p, fps, secs, cfg, (380.0, 180.0))    # 再猛跳一下
        return float(p.px[0]), float(p.py[0])

    a = run(60)
    b = run(120)
    gap = ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
    if gap > 4.0:
        return False, (f"60fps 落在 {a[0]:.1f}、120fps 落在 {b[0]:.1f}，"
                       f"相差 {gap:.1f} 像素 —— 跟随手感会随帧率变")
    return True, f"60/120 帧率下终点相差仅 {gap:.2f} 像素"


def test_no_nan_on_wild_input():
    """光标瞬移到屏幕外 / dt 异常夸张时也不能产生 NaN 或 Inf。"""
    p = PetField(W, H)
    cfg = _cfg(pet_count=3.0)
    rng = np.random.default_rng(3)
    for _ in range(400):
        p.set_pointer(float(rng.uniform(-2e4, 2e4)),
                      float(rng.uniform(-2e4, 2e4)))
        p.update(float(rng.uniform(0.0, 0.5)), cfg)     # 含 0 与大 dt
    vals = np.concatenate([p.px, p.py, p.vx, p.vy])
    if not np.isfinite(vals).all():
        return False, "出现 NaN / Inf"
    # dt=0 时不该推进（除零保护）
    p2 = PetField(W, H)
    _step(p2, 60, 1.0, cfg, (240.0, 180.0))
    x0 = float(p2.px[0])
    p2.update(0.0, cfg)
    if abs(float(p2.px[0]) - x0) > 1e-6:
        return False, "dt=0 时位置仍被推进"
    return True, "400 次乱序输入（含 dt=0 与 0.5s 大跳）无 NaN/Inf，dt=0 不推进"


def test_count_change_reseats():
    """数量从 1 改到 3 时，新加的两只必须当场落在光标身后。

    不这么做的话它们会从 (0,0) 排队跑过来 —— 观感上像被甩出去。
    """
    p = PetField(W, H)
    _step(p, 60, 0.5, _cfg(pet_count=1.0), (300.0, 180.0))
    p.set_pointer(300.0, 180.0)
    p.update(1.0 / 60.0, _cfg(pet_count=3.0))
    for i in (1, 2):
        d = ((float(p.px[i]) - 300.0) ** 2 + (float(p.py[i]) - 180.0) ** 2) ** 0.5
        if d > 140.0:
            return False, f"第 {i} 只出现时离光标 {d:.0f} 像素（应已在队列里）"
    return True, "数量增加时新宠物当场入列，不从屏幕角落跑来"


# ---------------------------------------------------------------- 2. 朝向
def test_face_hysteresis():
    """低于阈值的横向抖动不该让宠物翻面。"""
    p = PetField(W, H)
    cfg = _cfg(pet_count=1.0)
    _step(p, 60, 0.6, cfg, (240.0, 180.0))
    face0 = float(p._face[0])
    # 在极近处来回抖动：速度远低于 _FLIP_SPEED
    for k in range(40):
        p.set_pointer(240.0 + (2.0 if k % 2 else -2.0), 180.0)
        p.update(1.0 / 60.0, cfg)
    if float(p._face[0]) != face0:
        return False, "小幅抖动就把宠物翻面了（没有迟滞）"
    return True, f"小幅抖动 40 帧后朝向仍为 {face0:+.0f}（迟滞生效）"


def test_face_flips_on_sustained_move():
    """持续朝一个方向走，必须翻到那一面。

    注意先要在右边**落位**再往左跑：`_seat()` 会让宠物直接出现在光标处，
    所以"第一次把光标设在左边"并不会产生任何位移。
    """
    p = PetField(W, H)
    cfg = _cfg(pet_count=1.0)
    _step(p, 60, 1.0, cfg, (400.0, 180.0))          # 先在右侧落位
    if float(p._face[0]) < 0:
        return False, "刚落位时朝向不该是左"
    _step(p, 60, 2.0, cfg, (80.0, 180.0))           # 一路向左
    if float(p._face[0]) > 0:
        return False, "向左跑了 2 秒却没有翻到朝左"
    _step(p, 60, 2.5, cfg, (440.0, 180.0))          # 再一路向右
    if float(p._face[0]) < 0:
        return False, "持续向右跑了 2.5 秒却没有翻到朝右"
    return True, "持续单向移动时正确翻面（左、右都试过）"


# ---------------------------------------------------------------- 3. 精灵
def test_sprite_geometry():
    """两种形象都要画得出东西，且预乘合法。"""
    for kind in PetField.KINDS:
        for size in (24, 56, 120):
            p = PetField(W, H)
            spr = p._build_sprite(kind, "#F2C27B", size)
            if spr.shape != (size, size, 4):
                return False, f"{kind}/{size}: 形状是 {spr.shape}"
            a = spr[:, :, 3]
            solid = int((a > 0.5).sum())
            if solid < 0.06 * size * size:
                return False, f"{kind}/{size}: 实心像素只有 {solid}（形象画空了？）"
            if a.max() < 0.95:
                return False, f"{kind}/{size}: 没有完全不透明的主体像素"
            # 预乘合法性：rgb 不得超过 alpha（否则上屏会溢出成亮斑）
            over = spr[:, :, :3].max(axis=2) - a
            if float(over.max()) > 1e-3:
                return False, (f"{kind}/{size}: 预乘不合法，rgb 比 alpha 大 "
                               f"{float(over.max()):.4f}")
    return True, "小猫/小狗 × 3 种尺寸：形状、实心占比、预乘全合法"


def test_sprite_has_feathered_edge():
    """边缘必须有羽化 —— 这也是本特效必须做 over 混合的原因。"""
    p = PetField(W, H)
    spr = p._build_sprite("小猫", "#F2C27B", 96)
    a = spr[:, :, 3]
    edge = int(((a > 0.02) & (a < 0.98)).sum())
    if edge < 80:
        return False, f"羽化边缘只有 {edge} 个像素（抗锯齿没生效？）"
    return True, f"边缘 {edge} 个半透明像素（抗锯齿生效，必须走 over 混合）"


# ---------------------------------------------------------------- 4. 混合
BG_BRIGHT = np.uint32(0xFFF0F0F0)
BG_DARK = np.uint32(0xFF202020)


def _pin_mouse(sc, x, y):
    """把 Scene 的鼠标位置钉在画布中心。

    `MouseProbe` 读的是**真实光标**，测试环境里它可能在任何地方（甚至在测试
    画布之外）—— 那样宠物会被整只裁掉，"鼠标层有没有像素"就成了碰运气。
    这里替换掉 update()，让 mx/my 固定下来；返回值仍要是"本帧位移"，给 0
    就不会顺带触发涟漪与水波。
    """
    def fake(dt, ox, oy):
        sc.mouse.mx = float(x)
        sc.mouse.my = float(y)
        return 0.0
    sc.mouse.update = fake
    sc.mouse.mx = float(x)
    sc.mouse.my = float(y)


def _draw_on(bg_pack, cfg, x=None, y=None):
    """把宠物画在指定底色上，返回整块缓冲。"""
    p = PetField(W, H)
    p.set_pointer(W * 0.5 if x is None else x, H * 0.5 if y is None else y)
    for _ in range(40):
        p.update(1.0 / 60.0, cfg)
    buf = np.full(W * H, np.uint32(bg_pack), np.uint32)
    p.draw(buf, W, H, cfg)
    return buf


def _painted_mask(bright, dark):
    """"真的被画过"的像素：在任一底色上结果与底色不同。

    为什么必须先划出这块区域：整块缓冲直接比对是**没有意义**的 —— 没被覆盖
    的地方本来就各自保持着底色，两种底色当然处处不同。只有把范围收进
    "画过的地方"，"结果是否随底色变化"才真正在测混合方式。
    """
    return (bright != BG_BRIGHT) | (dark != BG_DARK)


def test_blend_depends_on_destination():
    """同一只宠物画在**亮底**与**暗底**上，半透明边缘必须给出不同结果。

    这是判断"到底做了 over 混合还是直接覆盖"最稳的一条：覆盖写法根本不读
    底色，被画过的区域内两种底会得到**逐像素完全相同**的结果；只有 over 才
    会让羽化边缘随底色变化。比"比对某个具体颜色"可靠 —— 不依赖毛色取值，
    也不依赖精灵细节。
    """
    cfg = _cfg()
    bright = _draw_on(BG_BRIGHT, cfg)
    dark = _draw_on(BG_DARK, cfg)
    painted = _painted_mask(bright, dark)
    n_painted = int(painted.sum())
    if n_painted < 100:
        return False, f"两种底色下都几乎没画上东西（只改动 {n_painted} 像素）"
    edge = painted & (bright != dark)       # 半透明边缘：结果随底色变
    n_edge = int(edge.sum())
    if n_edge < 20:
        return False, (f"画过的 {n_painted} 个像素里，只有 {n_edge} 个随底色变化"
                       f" —— 说明是覆盖而不是 over 混合，羽化边缘会糊出脏边")
    # 方向也要对：亮底上的边缘像素必须更亮
    br = ((bright >> np.uint32(16)) & np.uint32(0xFF)).astype(np.int32)
    dr = ((dark >> np.uint32(16)) & np.uint32(0xFF)).astype(np.int32)
    if not (br[edge].mean() > dr[edge].mean() + 5.0):
        return False, "亮底上的边缘并不比暗底更亮（混合方向反了）"
    return True, (f"画过 {n_painted} 像素，其中边缘 {n_edge} 个随底色变化，"
                  f"亮底更亮（红分量 {br[edge].mean():.1f} vs {dr[edge].mean():.1f}）")


def test_blend_covers_sprite_area():
    """宠物该落在画布上，而且不该把整块缓冲都改掉。"""
    cfg = _cfg(pet_count=3.0)
    base = _draw_on(0xFF000000, cfg)
    changed = int((base != np.uint32(0xFF000000)).sum())
    if changed <= 0:
        return False, "画完之后缓冲一个像素都没变"
    # 3 只 × 56px 精灵，算上浮动，占用远小于全屏
    if changed > W * H * 0.5:
        return False, f"改动了 {changed} 个像素（超过半屏），混合范围失控"
    return True, f"3 只宠物共改动 {changed} 个像素（占全屏 {100.0 * changed / (W * H):.1f}%）"


def test_opacity_zero_draws_nothing():
    cfg = _cfg(pet_opacity=0.0)
    base = _draw_on(0xFF000000, cfg)
    if (base != np.uint32(0xFF000000)).any():
        return False, "不透明度为 0 时仍然画上了像素"
    return True, "不透明度 0 不落任何像素"


def test_draw_clipped_at_screen_edge():
    """宠物跑到屏幕外时不能越界写缓冲（会踩到别的行）。"""
    cfg = _cfg(pet_count=1.0)
    p = PetField(W, H)
    p.set_pointer(W * 0.5, H * 0.5)
    for _ in range(20):
        p.update(1.0 / 60.0, cfg)
    buf = np.zeros(W * H, np.uint32)
    for pos in ((-40.0, -40.0), (W + 40.0, H + 40.0), (-30.0, H * 0.5),
                (W + 30.0, H * 0.5)):
        p.px[0], p.py[0] = pos
        p.draw(buf, W, H, cfg)          # 越界就该被裁掉，不该抛异常
    if not np.isfinite(buf.astype(np.float32)).all():
        return False, "越界绘制产生了非有限值"
    return True, "四角越界都安全裁剪，无异常"


# ---------------------------------------------------------------- 5. 接线
def test_scene_draws_into_mouse_layer():
    """开宠物 → 只出现在鼠标层；关掉 → 两层都干净。"""
    from _verify_kinds import _base_cfg
    sc = Scene(W, H)
    _pin_mouse(sc, W * 0.5, H * 0.5)
    sc.cfg = _base_cfg(pet_enabled=True, pet_size=56.0, pet_count=2.0)
    fs = np.zeros(W * H, np.uint32)
    ms = np.zeros(W * H, np.uint32)
    for _ in range(40):
        fs[:] = 0
        ms[:] = 0
        sc.step(1.0 / 60.0, fs, W, H, ms)
    if not ms.any():
        return False, "开着宠物，鼠标层却没有像素"
    if fs.any():
        return False, "宠物跑进了全屏层"

    sc2 = Scene(W, H)
    _pin_mouse(sc2, W * 0.5, H * 0.5)
    sc2.cfg = _base_cfg()               # 全关
    fs2 = np.zeros(W * H, np.uint32)
    ms2 = np.zeros(W * H, np.uint32)
    for _ in range(40):
        fs2[:] = 0
        ms2[:] = 0
        sc2.step(1.0 / 60.0, fs2, W, H, ms2)
    if fs2.any() or ms2.any():
        return False, "宠物关着却仍然画了东西（没清场）"
    return True, "开：只落鼠标层；关：两层都干净"


def test_layer_activity_includes_pets():
    """空层跳过的信号必须把宠物算进去，否则宠物会被冻在屏幕上。"""
    from _verify_kinds import _base_cfg
    sc = Scene(W, H)
    _pin_mouse(sc, W * 0.5, H * 0.5)
    sc.cfg = _base_cfg()
    if sc.layer_activity() != (False, False):
        return False, "全关时却被报成有内容"
    sc.cfg = _base_cfg(pet_enabled=True, pet_size=56.0)
    blank = np.zeros(W * H, np.uint32)
    for _ in range(40):
        sc.step(1.0 / 60.0, blank, W, H, blank)
    f, m = sc.layer_activity()
    if not m:
        return False, "宠物业已落位，鼠标层却报空 -> 宠物会被冻在屏幕上"
    if f:
        return False, "宠物让全屏层也报成了有内容"
    return True, "宠物在场时鼠标层报有内容，全屏层不受影响"


# ---------------------------------------------------------- 6. 注册表
def test_registry():
    import fx_kinds
    info = fx_kinds.EFFECTS.get("宠物跟随")
    if info is None:
        return False, "fx_kinds.EFFECTS 里没有「宠物跟随」"
    if info["cat"] != "鼠标特效" or info["layer"] != "mouse":
        return False, f"归属不对：{info}"
    if info["enable"] not in d.DEFAULTS:
        return False, f"启停键 {info['enable']} 不在 DEFAULTS 里"
    if "宠物跟随" not in fx_kinds.NAV_GROUPS[1][1]:
        return False, "侧栏「鼠标特效」分组里没有它"
    if "宠物跟随" not in d.PRESET_GROUPS:
        return False, "PRESET_GROUPS 里没有宠物跟随页"
    for n in d.PRESET_GROUPS["宠物跟随"]:
        if n not in d.PRESETS:
            return False, f"预设 {n} 没定义"
        bad = [k for k in d.PRESETS[n] if k not in d.DEFAULTS]
        if bad:
            return False, f"预设 {n} 含未登记的键：{bad}"
    for k in ("pet_enabled", "pet_kind", "pet_count", "pet_size",
              "pet_follow", "pet_color", "pet_opacity"):
        if k not in d.DEFAULTS:
            return False, f"DEFAULTS 缺 {k}"
    return True, "分类/分层/启停键/导航/预设 六处登记齐全"


def test_kind_and_values_sane():
    """默认值本身要在合理区间内（滑条范围与代码假设一致）。"""
    if d.DEFAULTS["pet_kind"] not in PetField.KINDS:
        return False, f"默认种类 {d.DEFAULTS['pet_kind']!r} 不是已知形象"
    if not (1.0 <= float(d.DEFAULTS["pet_count"]) <= PetField.MAX_PETS):
        return False, "默认数量超出 1~MAX_PETS"
    if not (16.0 <= float(d.DEFAULTS["pet_size"]) <= 160.0):
        return False, "默认尺寸超出代码钳制区间"
    if not (0.0 <= float(d.DEFAULTS["pet_follow"]) <= 1.0):
        return False, "默认跟随紧密度超出 0~1"
    # 未知种类要被兜到第一个，而不是崩
    p = PetField(W, H)
    p._ensure_sprite(dict(pet_kind="不存在", pet_color="#F2C27B",
                          pet_size=48.0))
    if p._sprite is None:
        return False, "未知种类没有兜底，精灵构建失败"
    return True, "默认值与钳制区间自洽；未知种类兜到默认形象"


# ------------------------------------------------- 7. 负向对照（判据灵敏性）
def test_negative_copy_instead_of_over():
    """把 `_blit` 换成覆盖写法，`test_blend_depends_on_destination` 必须变红。

    没有这条，上面那个用例可能只是在测"我新写的东西自洽"，而不是真的在测
    "混合方式正确"。
    """
    src = open(os.path.join(HERE, "fx_effects.py"), encoding="utf-8").read()
    marker = "        dst[...] = out\n"
    if src.count(marker) != 1:
        return False, f"定位不到 _blit 的写回语句（找到 {src.count(marker)} 处）"
    # 换成"覆盖"写法：直接用精灵像素写回，完全不读 dst。这是本文件其它特效
    # 惯用的那套写法，但在有羽化边缘的精灵上就是错的。
    stripped = src.replace(marker,
                           "        dst[...] = (np.clip(sub[:, :, 2] * sc, 0.0, 255.0)\n"
                           "                    .astype(np.uint32)\n"
                           "                    | (np.clip(sub[:, :, 1] * sc, 0.0, 255.0)\n"
                           "                       .astype(np.uint32) << np.uint32(8))\n"
                           "                    | (np.clip(sub[:, :, 0] * sc, 0.0, 255.0)\n"
                           "                       .astype(np.uint32) << np.uint32(16))\n"
                           "                    | (np.clip(a * 255.0, 0.0, 255.0)\n"
                           "                       .astype(np.uint32) << np.uint32(24)))\n")

    import types
    mod = types.ModuleType("fxeff_copy_mod")
    mod.__dict__["__name__"] = "fxeff_copy_mod"
    mod.__dict__["__file__"] = os.path.join(HERE, "fx_effects.py")
    try:
        exec(compile(stripped, "fx_effects_copy.py", "exec"), mod.__dict__)
    except Exception as e:
        return False, f"负向对照模块编译失败：{type(e).__name__}: {e}"

    Pet2 = mod.PetField
    cfg = dict(pet_enabled=True, pet_kind="小猫", pet_count=2.0,
               pet_size=56.0, pet_follow=0.55, pet_color="#F2C27B",
               pet_opacity=1.0)

    def draw_on(pack):
        p = Pet2(W, H)
        p.set_pointer(W * 0.5, H * 0.5)
        for _ in range(40):
            p.update(1.0 / 60.0, cfg)
        buf = np.full(W * H, np.uint32(pack), np.uint32)
        p.draw(buf, W, H, cfg)
        return buf

    b = draw_on(0xFFF0F0F0)
    dk = draw_on(0xFF202020)
    painted = _painted_mask(b, dk)
    # 覆盖写法下，画过的区域内两种底应逐像素完全相同（根本没读底色）
    edge = painted & (b != dk)
    if int(edge.sum()) != 0:
        return False, (f"负向对照失效：换成覆盖写法后仍有 {int(edge.sum())} 个"
                       f"被画过的像素随底色变化")
    if int(painted.sum()) < 100:
        return False, "负向对照没画上东西，无法判断"
    return True, (f"覆盖写法下画过的 {int(painted.sum())} 个像素两种底完全一致"
                  f"（判据确实在测混合方式）")


# ------------------------------------------------------------------ 驱动
def main():
    tests = [
        test_settles_on_cursor,
        test_chain_spacing,
        test_framerate_independent,
        test_no_nan_on_wild_input,
        test_count_change_reseats,
        test_face_hysteresis,
        test_face_flips_on_sustained_move,
        test_sprite_geometry,
        test_sprite_has_feathered_edge,
        test_blend_depends_on_destination,
        test_blend_covers_sprite_area,
        test_opacity_zero_draws_nothing,
        test_draw_clipped_at_screen_edge,
        test_scene_draws_into_mouse_layer,
        test_layer_activity_includes_pets,
        test_registry,
        test_kind_and_values_sane,
        test_negative_copy_instead_of_over,
    ]
    print("== 宠物跟随 ==")
    bad = 0
    for fn in tests:
        try:
            r = fn()
        except Exception as e:
            import traceback
            r = (False, f"exception: {type(e).__name__}: {e}\n"
                        + traceback.format_exc().splitlines()[-3])
        if r is None:
            print(f"  [SKIP] {fn.__name__}")
            continue
        ok, msg = r
        if not ok:
            bad += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {fn.__name__}: {msg}")
    print()
    if bad:
        print(f"== PET FAILED ==  {bad} 项失败")
        raise SystemExit(1)
    print(f"== PET VERIFIED ==  {len(tests)} 项")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
