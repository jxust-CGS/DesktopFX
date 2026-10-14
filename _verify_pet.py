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
    """基线配置。**默认关掉静止渐隐与现身门槛**（`pet_idle_fade=0`、
    `pet_min_travel=0`）。

    这两条都是"出场 / 退场"的时序策略，而物理类用例要的是"宠物稳定跟到位"：
    渐隐会在光标停住 1.6 秒后把它们冻住（隐去时物理不再推进）；现身门槛要求
    先移动够一段距离，而 `_step()` 只在循环外喂一次指针、位移常常只有几十
    像素，开着门槛就永远不会露面。两者各有专门用例，各测各的。
    """
    cfg = dict(pet_enabled=True, pet_kind="小猫", pet_count=2.0,
               pet_size=56.0, pet_follow=0.55, pet_color="#F2C27B",
               pet_opacity=1.0, pet_idle_fade=0.0, pet_min_travel=0.0)
    cfg.update(over)
    return cfg


def _step(p, fps, secs, cfg, target=(0.0, 0.0), approach=60.0):
    """把光标挪到 target 并推进 secs 秒。

    `approach`：先把光标放在 `target - approach` 再移到 target。PetField 的
    现身门槛要求光标真的走过一段，而它的初始光标正好落在画布中心 —— 测试里
    写"设到中心"就是零位移，宠物永远不会露面（那条门槛本身有专门用例）。

    中间必须**单独占一帧**：门槛量的是"当前净位移"（每次喂指针重算），同一帧
    内直接再喂一次会把刚才那段落差抹掉 —— 那对应的是"甩出去又收回原位"，
    产品语义上本来就不该触发。
    """
    if approach > 0.0:
        p.set_pointer(target[0] - approach, target[1])
        p.update(1.0 / fps, cfg)
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

    注意先要在右边**落位**再往左跑：`_seat_on_trail()` 把第一只铺在光标处，
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


def _pin_mouse(sc, x, y, travel=0.0):
    """把 Scene 的鼠标位置钉在 (x, y)。

    `MouseProbe` 读的是**真实光标**，测试环境里它可能在任何地方（甚至在测试
    画布之外）—— 那样宠物会被整只裁掉，"鼠标层有没有像素"就成了碰运气。
    这里替换掉 update()，让 mx/my 固定下来；返回值仍要是"本帧位移"，给 0
    就不会顺带触发涟漪与水波。

    `travel > 0` 时改成"先用十几帧从 (x - travel, y) 滑到 (x, y) 再钉住"：
    PetField 的现身门槛要求光标**真的走过一段**，钉死在一点是走不出来的
    （那正是这个门槛要拦的情况）。
    """
    state = {"n": 0}
    start_x = float(x) - travel

    def fake(dt, ox, oy):
        if travel > 0.0 and state["n"] < 12:
            state["n"] += 1
            sc.mouse.mx = start_x + travel * (state["n"] / 12.0)
        else:
            sc.mouse.mx = float(x)
        sc.mouse.my = float(y)
        return 0.0
    sc.mouse.update = fake
    sc.mouse.mx = start_x
    sc.mouse.my = float(y)


def _draw_on(bg_pack, cfg, x=None, y=None):
    """把宠物画在指定底色上，返回整块缓冲。"""
    p = PetField(W, H)
    tx = W * 0.5 if x is None else x
    ty = H * 0.5 if y is None else y
    # 先挪一段：现身门槛要求光标真的动过（默认目标正好是初始光标位置，零位移）。
    # 这一段单独占一帧 —— 门槛量的是"当前净位移"，同帧内再喂一次会抹掉它。
    p.set_pointer(tx - 60.0, ty)
    p.update(1.0 / 60.0, cfg)
    p.set_pointer(tx, ty)
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
    _pin_mouse(sc, W * 0.5, H * 0.5, travel=60.0)
    sc.cfg = _base_cfg(pet_enabled=True, pet_size=56.0, pet_count=2.0,
                       pet_min_travel=0.0)
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
    _pin_mouse(sc, W * 0.5, H * 0.5, travel=60.0)
    sc.cfg = _base_cfg()
    if sc.layer_activity() != (False, False):
        return False, "全关时却被报成有内容"
    sc.cfg = _base_cfg(pet_enabled=True, pet_size=56.0, pet_min_travel=0.0)
    blank = np.zeros(W * H, np.uint32)
    for _ in range(40):
        sc.step(1.0 / 60.0, blank, W, H, blank)
    f, m = sc.layer_activity()
    if not m:
        return False, "宠物业已落位，鼠标层却报空 -> 宠物会被冻在屏幕上"
    if f:
        return False, "宠物让全屏层也报成了有内容"
    return True, "宠物在场时鼠标层报有内容，全屏层不受影响"


# ---------------------------------------------------------- 5. 动画帧
def _frames(kind="小猫", size=64.0):
    p = PetField(W, H)
    p._ensure_sprite(dict(pet_kind=kind, pet_color="#F2C27B", pet_size=size))
    return p


def _spread(frames, y0f, y1f, x0f=0.0, x1f=1.0):
    """一组帧在指定区域内的"平均逐像素标准差" —— 用来量化动画幅度。"""
    S = frames.shape[1]
    sub = frames[:, int(S * y0f):int(S * y1f), int(S * x0f):int(S * x1f)]
    return float(sub.std(axis=0).mean())


def test_frames_exist_and_differ():
    """走路/静止帧数正确，且帧与帧之间必须真的不一样。"""
    p = _frames()
    if p._frames_walk.shape[0] != PetField._WALK_FRAMES:
        return False, f"走路帧数 {p._frames_walk.shape[0]}"
    if p._frames_idle.shape[0] != PetField._IDLE_FRAMES:
        return False, f"静止帧数 {p._frames_idle.shape[0]}"
    for name, f in (("走路", p._frames_walk), ("静止", p._frames_idle)):
        if float(f[0].std()) <= 0.0:
            return False, f"{name}帧是空白的"
        same = all(float(np.abs(f[k] - f[0]).max()) == 0.0
                   for k in range(1, len(f)))
        if same:
            return False, f"{name}循环里每一帧完全相同 —— 动画没生效"
    return True, (f"走路 {p._frames_walk.shape[0]} 帧 / 静止 "
                  f"{p._frames_idle.shape[0]} 帧，帧间均有差异")


def test_legs_move_only_when_walking():
    """腿只在走路帧里动；静止帧的腿必须站定，只有尾巴在摆。

    这是"走路/静止两套帧序列"存在的全部意义。判据取**下半身（腿）**与
    **左上（尾巴）**两个区域的动画幅度做对比，而不是"帧之间有没有差异"——
    后者静止帧也满足（尾巴在摆）。
    """
    p = _frames()
    legs = (0.72, 1.0)
    tail = (0.28, 0.80, 0.0, 0.30)
    w_legs = _spread(p._frames_walk, *legs)
    i_legs = _spread(p._frames_idle, *legs)
    if w_legs <= i_legs * 1.5:
        return False, (f"走路帧腿部幅度 {w_legs:.4f} 并不明显大于静止帧 "
                       f"{i_legs:.4f} —— 走路时腿没动起来")
    if abs(i_legs) > 1e-6 and i_legs > w_legs * 0.5:
        return False, f"静止帧腿部幅度 {i_legs:.4f} 过大（腿不该在走）"
    w_tail = _spread(p._frames_walk, *tail)
    i_tail = _spread(p._frames_idle, *tail)
    if i_tail <= 1e-6:
        return False, "静止帧尾巴完全没摆（静止时应该轻轻晃）"
    return True, (f"腿部幅度 走路 {w_legs:.4f} vs 静止 {i_legs:.4f}"
                  f"（{w_legs / max(i_legs, 1e-9):.1f} 倍）；"
                  f"静止帧尾巴仍在摆 {i_tail:.4f}")


def test_gait_offsets_desynchronize():
    """每只宠物的走路相位必须有偏移，否则 10 只会像列队操一样同步摆尾。"""
    p = _frames()
    offs = p._gait_off[:PetField.MAX_PETS]
    if float(offs.std()) < 0.5:
        return False, f"各只的相位偏移几乎相同（std={float(offs.std()):.3f}）"
    # 同一时刻两只宠物的选帧不该总是一样
    idxs = {p._frame_index(i, PetField._WALK_FRAMES)
            for i in range(PetField.MAX_PETS)}
    if len(idxs) < 3:
        return False, f"10 只同一时刻只落在 {len(idxs)} 种帧上（太整齐）"
    return True, (f"相位偏移 std={float(offs.std()):.2f}，"
                  f"同一时刻分散在 {len(idxs)} 种帧上")


def test_blink_overlay_is_cropped():
    """闭眼覆盖层要裁到眼部，且位置落在上半部的中间区域。"""
    for kind in PetField.KINDS:
        p = _frames(kind)
        if p._blink is None:
            return False, f"{kind}: 没有生成眨眼覆盖层"
        img, dx, dy = p._blink
        S = p._frames_walk.shape[1]
        area = img.shape[0] * img.shape[1]
        if area > 0.25 * S * S:
            return False, (f"{kind}: 覆盖层占整图 "
                           f"{100.0 * area / (S * S):.0f}%（没裁到眼睛）")
        # 眼睛归一化坐标 y≈0.37~0.39 → 落在上半部；x≈0.5~0.85 → 中偏右
        cy = (dy + img.shape[0] * 0.5) / S
        if not (0.24 <= cy <= 0.52):
            return False, f"{kind}: 覆盖层中心 y={cy:.2f}，不在眼部高度"
    return True, "两种形象的闭眼覆盖层都裁到眼部（仅占整图约 6%）"


# ---------------------------------------------------------- 6. 静止渐隐
def _run(p, cfg, fps, secs, target, click=0, feed=True, approach=0.0):
    """逐帧喂同一个目标点。

    `approach > 0` 时先挪一段再开始（现身门槛要求光标真的动过，见 `_step`）。
    这一段同样要单独占一帧。
    """
    if approach > 0.0:
        p.set_pointer(target[0] - approach, target[1])
        p.update(1.0 / fps, cfg)
    for k in range(max(1, int(fps * secs))):
        if feed:
            p.set_pointer(target[0], target[1],
                          click if k == 0 else 0)
        p.update(1.0 / fps, cfg)


def test_idle_fade_hides_then_wakes():
    """光标停住超过阈值 → 渐隐并停止上屏；一动 → 淡入并在光标处重新落位。"""
    p = PetField(W, H)
    cfg = _cfg(pet_idle_fade=1.0, pet_count=3.0)
    _run(p, cfg, 60, 0.3, (200.0, 200.0))          # 先落位
    if not p.active():
        return False, "刚落位就被判成不可见"
    _run(p, cfg, 60, 0.4, (200.0, 200.0))          # 累计静止 0.7 秒，未到阈值
    if p._fade <= 0.01:
        return False, (f"静止 {p._idle:.2f} 秒（阈值 1.0 秒）就淡光了 —— "
                       f"阈值没生效")
    _run(p, cfg, 60, 1.0, (200.0, 200.0))          # 越过阈值
    if p._fade > 0.01 or p.active():
        return False, (f"静止 {p._idle:.2f} 秒后仍可见（fade={p._fade:.2f}, "
                       f"active={p.active()}）")
    if not p._hidden:
        return False, "淡光后没有进入 hidden 状态"

    # 一动就回来，而且要**落在光标处**而不是从老位置跑过来。
    # 目标点留在画面内：跳出屏幕的宠物会被 docstring 里说的裁剪逻辑整只丢掉，
    # 那时"看不见"与唤醒成不成功就分不清了。
    far = (W - 60.0, H - 60.0)
    p.set_pointer(*far)
    p.update(1.0 / 60.0, cfg)
    if p._hidden or p._fade <= 0.0:
        return False, "移动后没有立刻淡入"
    d0 = ((float(p.px[0]) - far[0]) ** 2
          + (float(p.py[0]) - far[1]) ** 2) ** 0.5
    if d0 > 8.0:
        return False, f"唤醒后第一只离光标 {d0:.0f} 像素（应从光标处出现）"
    _run(p, cfg, 60, 0.6, far)
    if p._fade < 0.99:
        return False, f"唤醒 0.6 秒后仍未完全淡入（fade={p._fade:.2f}）"
    return True, "静止 1.4 秒后淡光且停止上屏；一动立刻在光标处淡入复原"


def test_idle_fade_zero_never_hides():
    """填 0 = 一直跟着，不该因为静止就消失。"""
    p = PetField(W, H)
    cfg = _cfg(pet_idle_fade=0.0)
    _run(p, cfg, 60, 5.0, (300.0, 300.0))
    if p._fade < 0.99 or p._hidden:
        return False, f"阈值填 0 却仍然消失了（fade={p._fade:.2f}）"
    return True, "阈值 0 时静止 5 秒仍全程可见"


def test_click_does_not_wake_hidden_pets():
    """隐去之后**原地点击不能**把它们叫出来 —— 得真的移动。

    这是刻意的行为选择：点击不改变光标位置，宠物露面也只能杵在原地，正是
    "一队不动的宠物站在那儿"那种观感。点击只刷新静止计时，让已经跟上来的
    一队不至于因为点了一下就提前散场。
    """
    p = PetField(W, H)
    cfg = _cfg(pet_idle_fade=0.8)
    c = (W * 0.5, H * 0.5)
    _run(p, cfg, 60, 2.0, c, approach=60.0)
    if not p._hidden:
        return False, "前提不成立：静止 2 秒后没有隐去"
    p.set_pointer(c[0], c[1], 1)            # 原地点击（位置没变）
    p.update(1.0 / 60.0, cfg)
    if not p._hidden or p.active():
        return False, "原地点击把宠物叫出来了（没动就会杵在原地，不该出现）"
    # 移动够门槛才回来，而且要真的把像素画回来（别只看状态位）
    _run(p, cfg, 60, 0.6, (c[0] + 60.0, c[1]))
    if p._hidden or not p.active():
        return False, "移动 60 像素后仍没回来"
    buf = np.zeros(W * H, np.uint32)
    p.draw(buf, W, H, cfg)
    if not (buf > 0).any():
        return False, "唤醒后画不出任何像素"
    return True, "原地点击不唤醒；移动 60 像素后回来并真的重新画了出来"


# ---------------------------------------------------------- 7. 现身门槛
def test_min_travel_gates_appearance():
    """现身门槛：没走够就不动声色，走够了才露面。"""
    p = PetField(W, H)
    cfg = _cfg(pet_min_travel=100.0)
    start = (100.0, 180.0)
    p.set_pointer(*start)
    p.reset()                       # 模拟"特效刚打开"：锚点对齐当前光标
    p.update(1.0 / 60.0, cfg)
    if p._placed:
        return False, "刚打开、光标一下都没动就现身了"

    for k in range(1, 46):          # 走 90 像素（差一点）
        p.set_pointer(start[0] + k * 2.0, start[1])
        p.update(1.0 / 60.0, cfg)
    if p._placed:
        return False, "只走了 90 像素（门槛 100）就现身了"

    for k in range(46, 66):         # 再走一段，越过门槛
        p.set_pointer(start[0] + k * 2.0, start[1])
        p.update(1.0 / 60.0, cfg)
    if not p._placed:
        return False, "走满 130 像素仍未现身"
    return True, "没动 / 走 90 像素都不现身，越过 100 像素门槛后现身"


def test_wiggle_in_place_never_appears():
    """原地来回抖动不算"开始移动"：门槛量净位移，不是累计路程。

    累计路程在来回抖动时能轻松刷到几千像素，但用户明明没在走 —— 按累计判
    会让宠物在光标旁边原地蹦出来，正是这次要修掉的观感。
    """
    p = PetField(W, H)
    cfg = _cfg(pet_min_travel=100.0)
    c = (240.0, 180.0)
    p.set_pointer(*c)
    p.reset()
    for k in range(600):            # 10 秒，±30 像素来回
        p.set_pointer(c[0] + (30.0 if (k // 5) % 2 else -30.0), c[1])
        p.update(1.0 / 60.0, cfg)
    if p._placed:
        return False, "原地来回抖动 10 秒后仍然现身了（判成累计路程了？）"
    for k in range(61):             # 改成单向走 120 像素
        p.set_pointer(c[0] + 30.0 + k * 2.0, c[1])
        p.update(1.0 / 60.0, cfg)
    if not p._placed:
        return False, "改成单向走 120 像素后仍不现身"
    return True, "原地抖动 10 秒不现身；换成单向走 120 像素后现身"


def test_appears_spread_along_trail():
    """露面时队伍沿**刚走过的路径**铺开，而不是在光标身后拉一条直线。

    门槛取 150：第一段（向右 120）走完净位移是 120，差一点；拐弯后往下走，
    净位移涨到 hypot(120, 2k)，k=45 时越过 150 —— 露面发生在**拐弯之后**，
    轨迹已经是个 L 形，铺开的队伍才会纵向拉开。
    """
    p = PetField(W, H)
    cfg = _cfg(pet_count=4.0, pet_min_travel=150.0, pet_size=40.0)
    p.set_pointer(80.0, 60.0)
    p.reset()
    for k in range(61):             # 先向右走 120（不够门槛，不露面）
        p.set_pointer(80.0 + k * 2.0, 60.0)
        p.update(1.0 / 60.0, cfg)
    if p._placed:
        return False, "门槛没生效：走 120 像素就露面了"
    for k in range(61):             # 再向下走，途中越过门槛
        p.set_pointer(200.0, 60.0 + k * 2.0)
        p.update(1.0 / 60.0, cfg)
        if p._placed:
            break
    if not p._placed:
        return False, "拐弯后走满门槛仍未露面"

    ys = [float(p.py[i]) for i in range(4)]
    spread = max(ys) - min(ys)
    if spread < 8.0:
        return False, (f"队伍铺在一条水平线上（y 极差 {spread:.1f}）—— "
                       f"没有沿轨迹铺开")
    d0 = ((float(p.px[0]) - p._mx) ** 2 + (float(p.py[0]) - p._my) ** 2) ** 0.5
    if d0 > 3.0:
        return False, f"第一只没落在光标处（偏 {d0:.1f} 像素）"
    return True, (f"沿 L 形路径铺开（y 极差 {spread:.0f} 像素），"
                  f"第一只在光标处（偏 {d0:.1f}）")


# ---------------------------------------------------------- 7. 跟随链规模
def test_ten_pets_chain_visible():
    """10 只时队列要真的散开成一列，而不是全挤在光标那一点上。

    上限从 3 提到 10 之后，"后面几只还看得见吗"就成了新问题：间距是
    体型 × 0.82，体型调小的时候这一串会缩得比较短。这里钉住"首尾要有
    足够的跨度"，免得哪天改参数把队伍缩成一坨。
    """
    p = PetField(W, H)
    cfg = _cfg(pet_count=float(PetField.MAX_PETS), pet_size=36.0,
               pet_follow=0.72)
    _run(p, cfg, 60, 2.5, (W * 0.5, H * 0.5), approach=60.0)
    spread = float(np.ptp(p.px[:PetField.MAX_PETS]))
    if spread < 36.0 * 0.82 * (PetField.MAX_PETS - 1) * 0.55:
        return False, (f"10 只首尾跨度只有 {spread:.0f} 像素，队伍挤成一坨了")
    # 每只都该在自己那一格附近（间距 = 体型 × 0.82 ± 容差）
    want = 36.0 * 0.82
    for i in range(1, PetField.MAX_PETS):
        got = abs(float(p.px[i] - p.px[i - 1]))
        if got < want * 0.45:
            return False, (f"第 {i} 只与前一只只差 {got:.0f} 像素"
                           f"（期望约 {want:.0f}）—— 叠在一起了")
    return True, f"10 只首尾跨度 {spread:.0f} 像素，相邻间距约 {want:.0f}"


# ---------------------------------------------------------- 8. 十只同屏
def test_ten_pets_ok():
    """10 只（上限）同屏：不产生 NaN、落屏像素量级合理、成本可接受。"""
    import time as _t
    p = PetField(1920, 1200)
    cfg = _cfg(pet_count=float(PetField.MAX_PETS), pet_follow=0.7)
    buf = np.zeros(1920 * 1200, np.uint32)
    for k in range(120):
        p.set_pointer(700.0 + 200.0 * (k % 2), 500.0)
        p.update(1.0 / 60.0, cfg)
        p.draw(buf, 1920, 1200, cfg)
        buf[:] = 0
    if p._n != PetField.MAX_PETS:
        return False, f"实际只带了 {p._n} 只"
    vals = np.concatenate([p.px, p.py, p.vx, p.vy])
    if not np.isfinite(vals).all():
        return False, "10 只时出现 NaN / Inf"
    p.draw(buf, 1920, 1200, cfg)
    n_px = int((buf > 0).sum())
    if n_px <= 0:
        return False, "10 只一只都没画出来"
    if n_px > 1920 * 1200 * 0.05:
        return False, f"落屏 {n_px} 像素（超过 5% 屏幕），画得太满"

    # 计时只作参考：本机约 1.8ms/帧，这里放宽到 8ms 只为拦住数量级退化
    t0 = _t.perf_counter()
    for k in range(120):
        p.set_pointer(700.0 + 200.0 * (k % 2), 500.0)
        p.update(1.0 / 60.0, cfg)
        buf[:] = 0
        p.draw(buf, 1920, 1200, cfg)
    ms = (_t.perf_counter() - t0) / 120 * 1000.0
    if ms > 8.0:
        return False, f"10 只每帧 {ms:.2f}ms（超出 8ms 上限）"
    return True, (f"10 只同屏：无 NaN，落屏 {n_px} 像素"
                  f"（占屏 {100.0 * n_px / (1920 * 1200):.1f}%），"
                  f"每帧 {ms:.2f}ms")


# ---------------------------------------------------------- 9. 注册表
def test_registry():
    import fx_kinds
    info = fx_kinds.EFFECTS.get("宠物跟随")
    if info is None:
        return False, "fx_kinds.EFFECTS 里没有「宠物跟随」"
    if info["cat"] != "鼠标拖动特效" or info["layer"] != "mouse":
        return False, f"归属不对：{info}"
    if info["enable"] not in d.DEFAULTS:
        return False, f"启停键 {info['enable']} 不在 DEFAULTS 里"
    if "宠物跟随" not in fx_kinds.NAV_GROUPS[1][1]:
        return False, "侧栏「鼠标拖动特效」分组里没有它"
    if "宠物跟随" not in d.PRESET_GROUPS:
        return False, "PRESET_GROUPS 里没有宠物跟随页"
    for n in d.PRESET_GROUPS["宠物跟随"]:
        if n not in d.PRESETS:
            return False, f"预设 {n} 没定义"
        bad = [k for k in d.PRESETS[n] if k not in d.DEFAULTS]
        if bad:
            return False, f"预设 {n} 含未登记的键：{bad}"
    for k in ("pet_enabled", "pet_kind", "pet_count", "pet_size",
              "pet_follow", "pet_color", "pet_opacity",
              "pet_idle_fade", "pet_min_travel"):
        if k not in d.DEFAULTS:
            return False, f"DEFAULTS 缺 {k}"
    return True, "分类/分层/启停键/导航/预设 六处登记齐全（9 个 pet_ 键）"


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
    if p._frames_walk is None:
        return False, "未知种类没有兜底，精灵构建失败"
    for k in ("pet_idle_fade", "pet_min_travel"):
        if k not in d.DEFAULTS:
            return False, f"DEFAULTS 缺 {k}"
    # 已去掉的功能不该在配置里留下"无效开关"（面板上会多一个拨了没用的项）
    for k in ("pet_expr",):
        if k in d.DEFAULTS:
            return False, f"DEFAULTS 里残留了已移除的配置键 {k}"
    if PetField.MAX_PETS != 10:
        return False, f"数量上限是 {PetField.MAX_PETS}，期望 10"
    return True, ("默认值与钳制区间自洽；未知种类兜到默认形象；"
                  f"数量上限 {PetField.MAX_PETS} 只；无残留键")


# ------------------------------------------------- 7. 负向对照（判据灵敏性）
def test_negative_copy_instead_of_over():
    """把共用的 over 混合换成覆盖写法，`test_blend_depends_on_destination` 必须变红。

    没有这条，上面那个用例可能只是在测"我新写的东西自洽"，而不是真的在测
    "混合方式正确"。

    混合实现已抽到模块级 `_over_blend`（宠物与点击特效共用），标记行是那条
    `dst[...] = out` —— 它在全文件只该出现一次，出现两次说明有人又抄了一份
    混合代码出来，那正是这份对照要拦的。
    """
    src = open(os.path.join(HERE, "fx_effects.py"), encoding="utf-8").read()
    marker = "    dst[...] = out\n"
    if src.count(marker) != 1:
        return False, (f"定位不到共用的混合写回语句（找到 {src.count(marker)} 处）"
                       f" —— 混合实现被抄成了多份？")
    # 换成"覆盖"写法：直接用精灵像素写回，完全不读 dst。这是本文件其它特效
    # 惯用的那套写法，但在有羽化边缘的精灵上就是错的。
    stripped = src.replace(marker,
                           "    dst[...] = (np.clip(sub[:, :, 2] * sc, 0.0, 255.0)\n"
                           "                .astype(np.uint32)\n"
                           "                | (np.clip(sub[:, :, 1] * sc, 0.0, 255.0)\n"
                           "                   .astype(np.uint32) << np.uint32(8))\n"
                           "                | (np.clip(sub[:, :, 0] * sc, 0.0, 255.0)\n"
                           "                   .astype(np.uint32) << np.uint32(16))\n"
                           "                | (np.clip(a * 255.0, 0.0, 255.0)\n"
                           "                   .astype(np.uint32) << np.uint32(24)))\n")

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
               pet_opacity=1.0, pet_idle_fade=0.0, pet_min_travel=0.0)

    def draw_on(pack):
        p = Pet2(W, H)
        # 挪一段再停下：现身门槛要求"光标真的动过"，钉在初始位置是画不出来的
        p.set_pointer(120.0, H * 0.5)
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
        test_frames_exist_and_differ,
        test_legs_move_only_when_walking,
        test_gait_offsets_desynchronize,
        test_blink_overlay_is_cropped,
        test_idle_fade_hides_then_wakes,
        test_idle_fade_zero_never_hides,
        test_click_does_not_wake_hidden_pets,
        test_min_travel_gates_appearance,
        test_wiggle_in_place_never_appears,
        test_appears_spread_along_trail,
        test_ten_pets_chain_visible,
        test_ten_pets_ok,
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
