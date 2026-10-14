# -*- coding: utf-8 -*-
"""配置值钳制（load / apply_preset 的输入校验）回归。

`load()` 原先只判 `k in DEFAULTS` —— **键对了不代表值对了**。三类坏值各自有
一个很难查的症状：

  1. `"rain_enabled": "yes"` —— 非空字符串 `bool()` 恒真，"写了关闭却仍然开着"。
     更隐蔽的是反方向：`"snow_enabled": "no"` 同样是 truthy，用户以为关了雪。
  2. `"fps": "true"` —— UI 构造期 `float()` 抛 ValueError，"特效在跑但面板打不开"。
  3. `"fps": NaN` —— Python 的 json 模块接受这个**非标准**字面量，读进来是
     `float('nan')`，下游 `int(cfg[...])` 直接抛错。

以及"手改出天文数字"：`rain_amount: 1e9` 会让 `set_amount` 去分配十亿个粒子。

反过来，这个脚本还必须证明**没有过度过滤**：88 个 DEFAULTS 与全部内置预设的
值都要原样通过；合法但书写不同的值（`"on"` / `"144.0"`）按显然的意图转换。

边界（既有约定，别越界）：钳制只管**类型**，不管取值是否"语义合法" ——
档位归一化归 `apply_z_mode`（见 `_verify_layer_switch.test_config_backward_compat`
里"load() 不该过滤值"那条）。

数据隔离：CONFIG_PATH / USER_PRESETS_PATH / ERROR_LOG_PATH 在导入后全部改到
临时目录，不碰用户真实配置与日志。
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import desktop_fx as d                              # noqa: E402

_TMP = tempfile.mkdtemp(prefix="fxcoerce-")
for _attr, _name in (("CONFIG_PATH", "config.json"),
                     ("USER_PRESETS_PATH", "presets_user.json"),
                     ("ERROR_LOG_PATH", "error.log")):
    setattr(d, _attr, os.path.join(_TMP, _name))

import fx_ui                                        # noqa: E402

# 面板会写"上次打开的页面 + 窗口尺寸"，那是**用户真实在用**的文件，
# 构造 ControlPanel 的用例必须把它也隔离掉。
fx_ui._UI_STATE_PATH = os.path.join(_TMP, "ui_state.json")

_seq = [0]


def _cfg_path():
    """给当前用例挂一个干净的配置路径（App.load 读的就是 d.CONFIG_PATH）。"""
    _seq[0] += 1
    p = os.path.join(_TMP, f"cfg{_seq[0]}.json")
    d.CONFIG_PATH = p
    return p


def _write(obj, raw_text=None):
    p = _cfg_path()
    with open(p, "w", encoding="utf-8") as f:
        if raw_text is not None:
            f.write(raw_text)
        else:
            json.dump(obj, f)
    return p


def _clear_log():
    try:
        os.remove(d.ERROR_LOG_PATH)
    except OSError:
        pass


def _log_text():
    try:
        with open(d.ERROR_LOG_PATH, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


# --------------------------------------------------- 1. 纯函数：类型钳制
def test_bool_accepts_words():
    """布尔键收下各种写法，并按显然的意图转换。"""
    cases = [
        (True, True), (False, False),
        (1, True), (0, False),
        ("yes", True), ("no", False), ("true", True), ("false", False),
        ("on", True), ("off", False), ("开", True), ("关", False),
        ("是", True), ("否", False),
        ("  YES  ", True), ("No", False),
    ]
    for raw, want in cases:
        ok, val = d._coerce_cfg_value("rain_enabled", raw)
        if not ok:
            return False, f"{raw!r} 被拒收了（应转成 {want}）"
        if val is not want or not isinstance(val, bool):
            return False, f"{raw!r} -> {val!r}，期望 {want!r}（且必须是 bool）"
    return True, f"{len(cases)} 种写法都转成了正确的布尔"


def test_bool_rejects_garbage():
    """转不成布尔的（容器、None）当坏值，由调用方回退出厂值。"""
    for raw in ({}, [], None, "maybe", ""):
        ok, _ = d._coerce_cfg_value("rain_enabled", raw)
        if ok:
            return False, f"{raw!r} 竟然被当成了合法布尔值"
    # 空字符串不算真：非空字符串恒真才是原 bug，空串是"没填"
    return True, "容器/None/空串/无意义字符串一律拒收"


def test_float_accepts_numbers_and_numeric_strings():
    cases = [(1, 1.0), (2.5, 2.5), ("3", 3.0), (" 4.25 ", 4.25), (0.0, 0.0),
             (-1.5, -1.5)]
    for raw, want in cases:
        ok, val = d._coerce_cfg_value("fps", raw)
        if not ok or abs(val - want) > 1e-9 or not isinstance(val, float):
            return False, f"{raw!r} -> {val!r}（ok={ok}），期望 float {want}"
    return True, f"{len(cases)} 种数值写法都转成了 float"


def test_float_rejects_nan_inf_and_absurd():
    """NaN / Inf / 天文数字都要拦：前者让 int() 抛错，后者让粒子数爆内存。"""
    for raw in (float("nan"), float("inf"), float("-inf"),
                "nan", "inf", "abc", "", None, {}, [],
                d._ABS_LIMIT * 10, -d._ABS_LIMIT * 10):
        ok, _ = d._coerce_cfg_value("fps", raw)
        if ok:
            return False, f"{raw!r} 竟然被当成了合法数值"
    # 边界内要放行（1e6 是留了三个数量级余量的粗筛，不是精确值域）
    ok, val = d._coerce_cfg_value("fps", d._ABS_LIMIT)
    if not ok:
        return False, f"恰好等于上界 {d._ABS_LIMIT} 的值被误杀"
    return True, "NaN/Inf/文本/容器/超界一律拒收，上界本身放行"


def test_float_rejects_bool():
    """JSON 里 true 不是 1，布尔被当数值用一定是写错了。"""
    for raw in (True, False):
        ok, val = d._coerce_cfg_value("fps", raw)
        if ok:
            return False, f"{raw!r} 被当成了数值 {val!r}"
    return True, "布尔不当数值用"


def test_str_type_only():
    """字符串键只管类型，不管取值是否'语义合法'（后者归 apply_z_mode）。"""
    ok, val = d._coerce_cfg_value("rain_color", "#3F88C0")
    if not ok or val != "#3F88C0":
        return False, f"正常颜色值没通过：{val!r}"
    for raw in (123, None, [], {}, True):
        if d._coerce_cfg_value("rain_color", raw)[0]:
            return False, f"非字符串 {raw!r} 被当成了合法字符串"
    # 档位脏值必须**通过**（load 不过滤值），由 apply_z_mode 归一化
    ok, val = d._coerce_cfg_value("layer_fullscreen", "leftover_garbage")
    if not ok:
        return False, "load 开始过滤档位取值了 —— 与 apply_z_mode 的分工被打破"
    return True, "只管类型；档位脏值照旧放行交给 apply_z_mode"


def test_unknown_key_rejected():
    if d._coerce_cfg_value("not_a_real_key", 1)[0]:
        return False, "未知键被当成合法键"
    return True, "未知键拒收"


# --------------------------------------------- 2. 零误伤：现有配置全部通过
def test_defaults_and_presets_pass_untouched():
    """88 个 DEFAULTS 与全部内置预设的值必须**原样**通过钳制。

    这条是防"钳制过严"的网：哪天有人加了个类型特殊的新键，这里立刻红，
    而不是等用户发现"我的配置被改掉了"。
    """
    for k, v in d.DEFAULTS.items():
        ok, val = d._coerce_cfg_value(k, v)
        if not ok:
            return False, f"DEFAULTS 里的 {k}={v!r} 自己都过不了钳制"
        if type(val) is not type(v):
            return False, f"DEFAULTS 里的 {k} 被改了类型：{type(v)} -> {type(val)}"
        if isinstance(v, float) and abs(val - v) > 1e-9:
            return False, f"DEFAULTS 里的 {k} 值被改动：{v} -> {val}"
    n = 0
    for pname, preset in d.PRESETS.items():
        for k, v in preset.items():
            if k not in d.DEFAULTS:
                continue
            n += 1
            ok, val = d._coerce_cfg_value(k, v)
            if not ok:
                return False, f"内置预设「{pname}」的 {k}={v!r} 过不了钳制"
            if isinstance(v, bool) and val is not v:
                return False, f"内置预设「{pname}」的 {k}={v!r} 被改成了 {val!r}"
    return True, f"{len(d.DEFAULTS)} 个默认值 + {n} 个预设值全部原样通过"


# --------------------------------------------------- 3. 端到端：load()
def test_load_string_bool_is_honored_both_ways():
    """核心用例：`"no"` 必须是**关**。

    原实现 `self.cfg[k] = v` 原样收下 `"no"`，而下游 `bool("no")` 为真 ——
    用户写了关，雪却照下。这条要是红了，说明钳制没接上。
    """
    _clear_log()
    _write({"rain_enabled": "yes", "snow_enabled": "no",
            "petal_enabled": "关", "meteor_enabled": 0})
    app = d.App()
    want = {"rain_enabled": True, "snow_enabled": False,
            "petal_enabled": False, "meteor_enabled": False}
    for k, w in want.items():
        got = app.cfg[k]
        if got is not w:
            return False, f"{k}: {got!r}（期望 {w!r}）—— '关'没能关掉它"
    return True, "'yes'/'no'/'关'/0 都被正确理解（不再出现'写了关却开着'）"


def test_load_bad_fps_keeps_panel_openable():
    """`"fps": "true"` 不能让面板打不开。

    异常发生在 ControlPanel 构造期的 `float()`，用户看到的是"特效在跑、
    但设置面板点开就没了"。所以这里必须**真的**构造一次面板才算证明。
    """
    _clear_log()
    _write({"fps": "true"})
    app = d.App()
    if float(app.cfg["fps"]) != float(d.DEFAULTS["fps"]):
        return False, f"坏 fps 没回退出厂值，而是 {app.cfg['fps']!r}"
    panel = fx_ui.ControlPanel(app)
    try:
        panel.root.update_idletasks()
    finally:
        panel.root.destroy()
    return True, "坏 fps 回退出厂值，面板照常构造出来"


def test_load_nan_literal_falls_back():
    """json 模块接受非标准的 `NaN` 字面量，读进来是 float('nan')。"""
    _clear_log()
    _write(None, raw_text='{"fps": NaN, "rain_amount": Infinity}')
    app = d.App()
    for k in ("fps", "rain_amount"):
        if float(app.cfg[k]) != float(d.DEFAULTS[k]):
            return False, f"{k} 没回退出厂值，而是 {app.cfg[k]!r}"
    return True, "NaN / Infinity 都回退到出厂值"


def test_load_absurd_number_falls_back():
    """手改出 1e9 的话，set_amount 会去分配十亿个粒子。"""
    _clear_log()
    _write({"rain_amount": 1e9, "snow_amount": 99999999})
    app = d.App()
    for k in ("rain_amount", "snow_amount"):
        if float(app.cfg[k]) != float(d.DEFAULTS[k]):
            return False, f"{k} 没被拦下，而是 {app.cfg[k]!r}"
    return True, "天文数字回退出厂值"


def test_load_valid_values_still_apply():
    """别矫枉过正：合法值必须照旧生效（含类型转换）。"""
    _clear_log()
    _write({"rain_amount": 300, "fps": 90, "rain_enabled": False,
            "rain_color": "#123456", "rain_opacity": "0.5"})
    app = d.App()
    if float(app.cfg["rain_amount"]) != 300.0:
        return False, f"rain_amount 没生效：{app.cfg['rain_amount']!r}"
    if float(app.cfg["fps"]) != 90.0:
        return False, f"fps 没生效：{app.cfg['fps']!r}"
    if app.cfg["rain_enabled"] is not False:
        return False, f"rain_enabled 没生效：{app.cfg['rain_enabled']!r}"
    if app.cfg["rain_color"] != "#123456":
        return False, f"颜色没生效：{app.cfg['rain_color']!r}"
    if abs(float(app.cfg["rain_opacity"]) - 0.5) > 1e-9:
        return False, f"字符串数值没转换：{app.cfg['rain_opacity']!r}"
    return True, "合法值原样生效，字符串数值按意图转换"


def test_load_reports_but_does_not_count_as_failure():
    """被忽略的键要留下记录，但**不能**计入连续失败（那是运行期故障的口径）。"""
    _clear_log()
    _write({"fps": "true", "rain_amount": 1e9})
    app = d.App()
    body = _log_text()
    if "config-coerce" not in body:
        return False, f"error.log 里没有钳制记录：{body[:200]!r}"
    if "fps" not in body:
        return False, "记录里没指出是哪个键"
    if getattr(app, "_err_streak", 0) != 0:
        return False, "配置问题被计入了连续失败（30 次会停掉渲染）"
    return True, "写进 error.log 供排查，且不影响渲染熔断计数"


# ------------------------------------------ 4. 端到端：用户预设也要过钳制
def test_user_preset_values_coerced():
    """用户预设来自 presets_user.json —— 手改同一个文件能造成同样的坏值。"""
    _clear_log()
    _write({})
    app = d.App()
    app.user_presets["坏预设"] = {
        "rain_enabled": "no", "fps": "abc", "rain_amount": 1e9,
        "rain_speed": 1234, "_page": "其它",
    }
    app.apply_preset("坏预设")
    if app.cfg["rain_enabled"] is not False:
        return False, f"预设里的 'no' 没关掉：{app.cfg['rain_enabled']!r}"
    if float(app.cfg["fps"]) != float(d.DEFAULTS["fps"]):
        return False, f"预设里的坏数值没被跳过：{app.cfg['fps']!r}"
    if float(app.cfg["rain_amount"]) != float(d.DEFAULTS["rain_amount"]):
        return False, f"预设里的天文数字没被跳过：{app.cfg['rain_amount']!r}"
    if float(app.cfg["rain_speed"]) != 1234.0:
        return False, f"预设里的好值没生效：{app.cfg['rain_speed']!r}"
    if "_page" in app.cfg:
        return False, "内部元数据 _page 被写进了 cfg"
    return True, "坏值跳过、好值生效、元数据不落 cfg"


# ------------------------------------------- 5. 负向对照：旧实现确有该 bug
def test_old_load_really_had_the_bug():
    """把钳制段换回旧的一行 `self.cfg[k] = v`，`"no"` 必须被原样收下。

    没有这条，上面那些用例可能只是在测"我新写的东西自洽"，而不是在测
    "旧的确实坏了"。
    """
    src = open(os.path.join(HERE, "desktop_fx.py"), encoding="utf-8").read()
    new = ("                ok, val = _coerce_cfg_value(k, v)\n"
           "                if ok:\n"
           "                    self.cfg[k] = val\n"
           "                else:\n"
           "                    rejected.append(k)\n")
    old = "                self.cfg[k] = v\n"
    if new not in src:
        return False, "负向对照没改到源码（load 里的钳制段形状变了？）"
    stripped = src.replace(new, old)

    import types
    mod = types.ModuleType("dfx_old_mod")
    mod.__dict__["__name__"] = "dfx_old_mod"
    mod.__dict__["__file__"] = os.path.join(HERE, "desktop_fx.py")
    exec(compile(stripped, "desktop_fx_old.py", "exec"), mod.__dict__)
    # 副本同样不能碰用户文件：重新指向临时目录（与主模块分开的文件名）
    _seq[0] += 1
    mod.CONFIG_PATH = os.path.join(_TMP, f"old{_seq[0]}.json")
    mod.USER_PRESETS_PATH = os.path.join(_TMP, f"old{_seq[0]}_p.json")
    mod.ERROR_LOG_PATH = os.path.join(_TMP, f"old{_seq[0]}_e.log")
    with open(mod.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"snow_enabled": "no", "fps": "true"}, f)

    old_app = mod.App()
    got = old_app.cfg["snow_enabled"]
    if got != "no":
        return False, f"负向对照失效：旧实现收到的是 {got!r}（期望原样的 'no'）"
    if not bool(got):
        return False, "负向对照失效：'no' 竟然被判成了关"
    if old_app.cfg["fps"] != "true":
        return False, f"负向对照失效：旧实现的 fps 是 {old_app.cfg['fps']!r}"
    return True, "旧实现原样收下 'no'（bool 为真 = 雪还在下）与 'true'，bug 属实"


# ----------------------------------------------------------------- 驱动
def main():
    tests = [
        test_bool_accepts_words,
        test_bool_rejects_garbage,
        test_float_accepts_numbers_and_numeric_strings,
        test_float_rejects_nan_inf_and_absurd,
        test_float_rejects_bool,
        test_str_type_only,
        test_unknown_key_rejected,
        test_defaults_and_presets_pass_untouched,
        test_load_string_bool_is_honored_both_ways,
        test_load_bad_fps_keeps_panel_openable,
        test_load_nan_literal_falls_back,
        test_load_absurd_number_falls_back,
        test_load_valid_values_still_apply,
        test_load_reports_but_does_not_count_as_failure,
        test_user_preset_values_coerced,
        test_old_load_really_had_the_bug,
    ]
    print("== 配置值钳制 ==")
    bad = 0
    for fn in tests:
        try:
            r = fn()
        except Exception as e:
            r = (False, f"exception: {type(e).__name__}: {e}")
        if r is None or (isinstance(r, tuple) and r[0] is None):
            print(f"  [SKIP] {fn.__name__}: {r}")
            continue
        ok, msg = r
        if not ok:
            bad += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {fn.__name__}: {msg}")

    import shutil
    shutil.rmtree(_TMP, ignore_errors=True)
    print()
    if bad:
        print(f"== CFG COERCE FAILED ==  {bad} 项失败")
        raise SystemExit(1)
    print("== CFG COERCE VERIFIED ==")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
