# -*- coding: utf-8 -*-
"""特效分类表：侧栏分组、渲染分层、同类互斥三处共用同一份定义。

放在这里而不是各写一份的原因：分类一旦分散在三个文件里，加一个新特效
就要同时改面板、改渲染、改互斥，漏一处就出现"界面归了类、渲染还画在
旧层"这种只有在特定设置下才暴露的错位。

三条分类信息
------------
cat    侧栏分组：全屏特效 / 鼠标特效 / 其他特效
layer  渲染层：fullscreen = 全屏特效层；mouse = 鼠标特效层
       两层的**显示层级各自由用户设定**（最上层 / 仅桌面图层），因此必须是
       两个独立的覆盖窗口，不能合成一张缓冲。
enable 主启停键（配置键名）。同类互斥时把它置 False。
       为 None 表示该特效没有单一主开关（如"涟漪"由 开/关移动、开/关点击
       两个键共同决定），此时用 off 列出需要一起关掉的键。
"""

# 侧栏分组顺序。仅这四组；"设置" 不是特效组。
CATEGORY_ORDER = ["全屏特效", "鼠标特效", "其他特效"]

# 每个特效页的归属。key = 面板页键（必须与 fx_ui._pages 的键一致）
EFFECTS = {
    # ---- 全屏特效：整屏氛围粒子，自己布满屏幕，与鼠标无关 ----
    "下雨":     dict(cat="全屏特效", layer="fullscreen", enable="rain_enabled"),
    "下雪":     dict(cat="全屏特效", layer="fullscreen", enable="snow_enabled"),
    "落樱":     dict(cat="全屏特效", layer="fullscreen", enable="petal_enabled"),
    "流星雨":   dict(cat="全屏特效", layer="fullscreen", enable="meteor_enabled"),
    # ---- 鼠标特效：由光标位置/动作驱动 ----
    # 鼠标层特效全部依赖浮在最上层：聚光灯压暗全屏、真实水波抓屏折射、颜料盘
    # 跟随光标作画、涟漪 / 窗口水波在光标处扩散 —— 沉到桌面层（壁纸之下）会被
    # 所有窗口挡死、等于隐形。故 behind_ok=False：该层不允许「仅桌面图层」档。
    "聚光灯":   dict(cat="鼠标特效", layer="mouse", enable="spotlight_enabled",
                   behind_ok=False),
    "颜料盘":   dict(cat="鼠标特效", layer="mouse", enable="paint_enabled",
                   behind_ok=False),
    # 涟漪有主开关 ripple_enabled（"跟随移动 / 点击扩散"只是它的两种触发方式），
    # 否则它在配置里恒为"开着"，同类互斥会一直把它当占用者、把别的特效顶掉。
    "涟漪":     dict(cat="鼠标特效", layer="mouse", enable="ripple_enabled",
                   behind_ok=False),
    "真实水波": dict(cat="鼠标特效", layer="mouse", enable="wave_enabled",
                   behind_ok=False),
    # ---- 其他特效：既不由鼠标驱动，也不是整屏氛围 ----
    # 窗口水波画进**鼠标层**：它复用的是涟漪池（RippleField），而涟漪池整体
    # 画在鼠标层上 —— 拆成两层会让窗口水波和鼠标涟漪打架（同一池子只能画一处）。
    "窗口水波": dict(cat="其他特效", layer="mouse", enable="win_ripple_enabled",
                   behind_ok=False),
}

# 层 -> (层级配置键, 多开配置键)
LAYER_KEYS = {
    "fullscreen": ("layer_fullscreen", "multi_fullscreen"),
    "mouse": ("layer_mouse", "multi_mouse"),
}

# 层级配置键的取值
LAYER_TOPMOST = "topmost"     # 始终浮在窗口之上（默认）
LAYER_DESKTOP = "desktop"     # 仅桌面图层：被任何窗口盖住
LAYER_VALUES = (LAYER_TOPMOST, LAYER_DESKTOP)
LAYER_LABELS = {LAYER_TOPMOST: "最上层", LAYER_DESKTOP: "仅桌面图层"}

# 面板页键 -> 显示名。"涟漪"页内部键沿用旧名（改键会牵动 ui_state 与
# 用户预设里的 _page 标签），显示成交互更明确的"鼠标涟漪"。
PAGE_TITLES = {"涟漪": "鼠标涟漪"}

# 主启停键 -> 特效名（只有参与互斥的键才在表里）
ENABLE_TO_EFFECT = {v["enable"]: k for k, v in EFFECTS.items() if v.get("enable")}

# 侧栏导航顺序：(分组标题, [页键...])
NAV_GROUPS = [
    ("全屏特效", ["下雨", "下雪", "落樱", "流星雨"]),
    ("鼠标特效", ["聚光灯", "颜料盘", "涟漪", "真实水波"]),
    ("其他特效", ["窗口水波"]),
]

# 非特效页（设置类），单独排在最后
SETTINGS_PAGES = ["其它"]


def page_title(page):
    """页键 -> 界面上显示的名称。"""
    return PAGE_TITLES.get(page, page)


def category_of(page):
    info = EFFECTS.get(page)
    return info["cat"] if info else None


def layer_of(page):
    info = EFFECTS.get(page)
    return info["layer"] if info else None


def effects_of(cat):
    """某分类下的特效名（按声明顺序）。"""
    return [k for k, v in EFFECTS.items() if v["cat"] == cat]


def off_keys(page):
    """关掉某特效要写 False 的配置键。"""
    info = EFFECTS.get(page)
    if not info:
        return []
    if info.get("enable"):
        return [info["enable"]]
    return list(info.get("off") or [])


def is_on(cfg, page):
    """该特效当前是否开着（任一判定键为真即算开）。"""
    keys = off_keys(page)
    if not keys:
        return False
    return any(bool(cfg.get(k, False)) for k in keys)


def multi_key_of(page):
    """该特效所在分类的"允许多开"配置键。"""
    layer = layer_of(page)
    if layer is None:
        return None
    return LAYER_KEYS[layer][1]


def layer_key_of(page):
    layer = layer_of(page)
    if layer is None:
        return None
    return LAYER_KEYS[layer][0]


# 配置键 -> 渲染层槽位（"layer_mouse" -> "mouse"）。UI 拿到的是配置键，
# 判定该层能否沉到桌面层需要槽位，所以这里反向建一张表。
_KEY_TO_SLOT = {v[0]: s for s, v in LAYER_KEYS.items()}


def layer_slot_of_key(key):
    return _KEY_TO_SLOT.get(key)


def layer_supports_desktop(layer):
    """该渲染层是否允许切到「仅桌面图层」。

    仅当该层内**每个**特效都能沉到壁纸之下还看得见才允许。鼠标层特效
    （聚光灯压暗全屏 / 真实水波抓屏折射 / 颜料盘 / 涟漪 / 窗口水波）全部依赖
    浮在最上层，沉下去会被所有窗口挡死、等于隐形 —— 用户一旦切过去就"看不到
    效果"，却找不到原因。所以该层永远不允许这一档，从根上堵死这个无效状态，
    而不是等用户切了再补救。
    """
    return all(info.get("behind_ok", True)
               for _, info in EFFECTS.items() if info.get("layer") == layer)


def nav_order():
    """侧栏从上到下的页键顺序（不含分组标题）。"""
    order = []
    for _, pages in NAV_GROUPS:
        order.extend(pages)
    order.extend(SETTINGS_PAGES)
    return order


def enabled_pages(cfg):
    """当前开着的特效页（按声明顺序）。"""
    return [k for k in EFFECTS if is_on(cfg, k)]
