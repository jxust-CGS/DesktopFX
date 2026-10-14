# -*- coding: utf-8 -*-
"""渲染线程保护 + 原子写 的回归。

这一组针对 2026-10-14 那次审查的三处"静默失败"：

1. 渲染线程入口裸奔（`Thread(target=self._render_loop)`，没有包装器）。
   症状：特效完全不显示、面板和托盘都正常、error.log 干干净净。
2. 帧循环的 `try` 边界画错：`pump()` 与 `float(cfg["fps"])` 在块外，
   抛异常直接掀翻线程；末尾的 `destroy()` 挂在 while 正常出口上，
   异常栈展开时不执行 → 两个覆盖窗的 GDI/USER 对象泄漏。
3. 三个 JSON 数据文件是 `open(path,"w")` 直接覆盖，非原子。
   断电或强杀留下半截文件，而读取端对损坏的处理是"整体回退出厂值"。

数据隔离：CONFIG_PATH / USER_PRESETS_PATH / ERROR_LOG_PATH 在导入后改到临时目录
（本脚本要反复触发错误上报，绝不能写用户的 error.log）。不构造 ControlPanel，
因此不涉及 ui_state.json。
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import desktop_fx as d                              # noqa: E402

_TMP = tempfile.mkdtemp(prefix="fxguard-")
for _attr, _name in (("CONFIG_PATH", "config.json"),
                     ("USER_PRESETS_PATH", "presets_user.json"),
                     ("ERROR_LOG_PATH", "error.log")):
    setattr(d, _attr, os.path.join(_TMP, _name))

import fx_winlayer                                  # noqa: E402


# --------------------------------------------------------------- 原子写
def _tmp_residue():
    return [f for f in os.listdir(_TMP) if f.startswith(".tmp-")]


def test_atomic_write_json_basics():
    """基本行为：写出内容正确、不留临时文件、覆盖写正常、indent=None 紧凑。"""
    from _paths import atomic_write_json
    p = os.path.join(_TMP, "a.json")
    obj = {"a": 1, "中文": "值"}
    atomic_write_json(p, obj)
    if not os.path.exists(p):
        return False, "文件没写出来"
    if json.load(open(p, encoding="utf-8")) != obj:
        return False, "读回内容不一致"
    if _tmp_residue():
        return False, f"留下了临时文件：{_tmp_residue()}"
    atomic_write_json(p, {"b": 2})
    if json.load(open(p, encoding="utf-8")) != {"b": 2}:
        return False, "覆盖写失败"
    p2 = os.path.join(_TMP, "c.json")
    atomic_write_json(p2, {"k": "v"}, indent=None)
    if open(p2, encoding="utf-8").read() != '{"k": "v"}':
        return False, "indent=None 应写成紧凑单行"
    return True, "写出/覆盖/紧凑格式都对，无残留"


def test_atomic_write_failure_cleans_up():
    """写失败必须抛、且不能把临时文件留在用户目录里。"""
    from _paths import atomic_write_json
    bad = os.path.join(_TMP, "sub")
    os.makedirs(bad, exist_ok=True)          # 目标是目录，replace 必然失败
    before = sorted(os.listdir(_TMP))
    try:
        atomic_write_json(bad, {"x": 1})
    except Exception:
        pass
    else:
        return False, "写到目录上居然成功了"
    left = set(os.listdir(_TMP)) - set(before)
    if left:
        return False, f"失败后留下了垃圾：{left}"
    return True, "失败路径抛出且清理干净"


def test_config_roundtrip():
    """save() → 新 App load() 必须拿回同一个值，且不落临时文件。"""
    app = d.App()
    app.set("fps", 99)
    app.set("rain_amount", 321)
    if not app.save():
        return False, "save 返回 False"
    app2 = d.App()
    if app2.cfg["fps"] != 99 or app2.cfg["rain_amount"] != 321:
        return False, (f"往返不一致：fps={app2.cfg['fps']} "
                       f"rain_amount={app2.cfg['rain_amount']}")
    if _tmp_residue():
        return False, f"配置写入留下了临时文件：{_tmp_residue()}"
    return True, "往返一致，无残留"


def test_presets_roundtrip():
    """用户预设同样走原子写，且能往返。"""
    app = d.App()
    app.user_presets["自测"] = {"rain_amount": 7, "_page": "下雨"}
    if not app._save_user_presets():
        return False, "_save_user_presets 返回 False"
    app2 = d.App()
    if app2.user_presets.get("自测", {}).get("rain_amount") != 7:
        return False, f"预设往返不一致：{app2.user_presets}"
    return True, "预设往返一致"


def test_no_direct_overwrite_left():
    """三处写入点都不许再有 `open(..., "w")` 直接覆盖。

    这条是防回退的"结构守卫"：将来谁图省事改回直接覆盖，脚本会变红。
    """
    import re
    bad = []
    for fn in ("desktop_fx.py", "fx_ui.py"):
        src = open(os.path.join(HERE, fn), encoding="utf-8").read()
        for m in re.finditer(r'open\(\s*(CONFIG_PATH|USER_PRESETS_PATH|'
                             r'_UI_STATE_PATH)\s*,\s*"w"', src):
            bad.append(f"{fn}: {m.group(0)}")
    if bad:
        return False, f"还有直接覆盖的写入点：{bad}"
    return True, "三处写入点都已改为原子写"


# ------------------------------------------------------- 渲染线程的保护
def test_render_loop_reports_on_exception():
    """`_render_frames` 抛异常时：必须上报 error.log **且** 拆掉覆盖窗。

    用假 Overlay，不拉真窗口：只验"保护层做了该做的事"。
    """
    err = os.path.join(_TMP, "error.log")
    if os.path.exists(err):
        os.remove(err)
    app = d.App()

    class FakeOv:
        destroyed = 0

        def destroy(self):
            FakeOv.destroyed += 1

    app.overlays = [FakeOv(), FakeOv()]

    def boom():
        raise RuntimeError("自检注入：渲染帧炸了")

    app._render_frames = boom
    try:
        app._render_loop()
    except RuntimeError:
        pass
    else:
        return False, "异常没有向上传播（被吞了？）"
    if FakeOv.destroyed != 2:
        return False, f"覆盖窗没被拆干净（destroy 调了 {FakeOv.destroyed} 次）"
    if not os.path.exists(err):
        return False, "error.log 里没有任何记录 —— 这就是原来的静默死亡"
    body = open(err, encoding="utf-8").read()
    if "自检注入" not in body:
        return False, f"error.log 没记下原始异常：{body!r}"
    return True, "异常被上报、覆盖窗被拆除、且仍向上传播"


def test_render_loop_finally_is_needed():
    """负向对照：把 `_render_loop` 的 finally 摘掉，上面那条必须失败。

    用改过的源码副本跑一遍同样的场景，证明这条用例真的在测那层保护。
    """
    src = open(os.path.join(HERE, "desktop_fx.py"), encoding="utf-8").read()
    marker = "        finally:\n            for ov in (getattr(self, \"overlays\""
    if marker not in src:
        return False, "找不到 finally 块，源码结构与预期不符"
    stripped = src.replace(marker, "        if False:\n            for ov in (getattr(self, \"overlays\"")
    if stripped == src:
        return False, "负向对照没有真正改到源码"

    ns = {"__name__": "stripped", "__file__": os.path.join(HERE, "desktop_fx.py")}
    code = compile(stripped, "desktop_fx_stripped.py", "exec")
    import types
    mod = types.ModuleType("stripped_mod")
    mod.__dict__["__name__"] = "stripped_mod"
    mod.__dict__["__file__"] = os.path.join(HERE, "desktop_fx.py")
    exec(code, mod.__dict__)
    # 关键：这份副本有自己的模块级常量，**必须重新指向临时目录**。
    # 否则它的 report_error 会写进用户真实的 error.log（负向对照里正好会
    # 主动抛一次异常，等于每次跑测试都往用户日志里塞一条假故障）。
    mod.ERROR_LOG_PATH = os.path.join(_TMP, "error-negctl.log")
    mod.CONFIG_PATH = os.path.join(_TMP, "config-negctl.json")
    mod.USER_PRESETS_PATH = os.path.join(_TMP, "presets-negctl.json")
    App2 = mod.App

    app = App2()

    class FakeOv:
        destroyed = 0

        def destroy(self):
            FakeOv.destroyed += 1

    app.overlays = [FakeOv()]
    app._render_frames = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        app._render_loop()
    except RuntimeError:
        pass
    if FakeOv.destroyed != 0:
        return False, "负向对照失效：摘掉 finally 后覆盖窗仍被拆了，这条测不出来"
    return True, "负向对照成立：摘掉 finally 后确实不再清理"


def _frame_try(fn):
    """取 `_render_frames` 主循环里那层帧级 try 的 **Try 节点**。"""
    for n in ast.walk(fn):
        if isinstance(n, ast.While):
            if n.body and isinstance(n.body[-1], ast.Try):
                return n.body[-1]
    return None


def _frame_try_src():
    tree = ast.parse(open(os.path.join(HERE, "desktop_fx.py"),
                          encoding="utf-8").read())
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == "App":
            for m in n.body:
                if isinstance(m, ast.FunctionDef) and m.name == "_render_frames":
                    return m
    return None


def _pumps_in_try_body(tr):
    """**只**数 try 体里的 pump 调用。

    口径硬性要求：不能用 `ast.walk(tr)` —— 它会连 handlers/finally 一起走，
    于是"把 pump 挪到 except 之后"这种错误照样通过（2026-10-14 真踩过：
    缩进与 except 块相同 → 变成"只在帧失败时才泵消息"，语法合法、运行不报错、
    但正常路径再也不泵消息也不限帧率）。
    """
    hits = []
    for stmt in tr.body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "pump":
                hits.append(n)
    return hits


def test_pump_and_fps_inside_frame_try():
    """`pump()` 与 `cfg["fps"]` 必须落在每帧那层 try 的**体**里（原 bug 的正身）。"""
    fn = _frame_try_src()
    if fn is None:
        return False, "找不到 App._render_frames"
    tr = _frame_try(fn)
    if tr is None:
        return False, ("主循环 while 的最后一条语句不是 try —— "
                       "说明帧内逻辑被挪出了保护范围")
    pumps = _pumps_in_try_body(tr)
    if len(pumps) < 2:
        return False, (f"try 体里只找到 {len(pumps)} 处 pump()（应 >= 2）—— "
                       f"若它们在 except 之后，正常路径就永远不泵消息")
    fps = any(isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
              and n.value.id == "cfg" and isinstance(n.slice, ast.Constant)
              and n.slice.value == "fps"
              for stmt in tr.body for n in ast.walk(stmt))
    if not fps:
        return False, "cfg[\"fps\"] 不在帧内 try 的体里"
    return True, f"pump x{len(pumps)} 与 cfg['fps'] 均在帧内 try 的体内"


def test_ast_check_is_sensitive():
    """负向对照 ①：把两行 pump 从 try 体里删掉，判据必须失败。"""
    src = open(os.path.join(HERE, "desktop_fx.py"), encoding="utf-8").read()
    stripped = src.replace("                ov_fs.pump()\n"
                           "                ov_ms.pump()\n", "")
    if stripped == src:
        return False, "负向对照 ① 没改到源码（行首空格数变了？）"
    tr = _frame_try_from_src(stripped)
    if tr is None:
        return False, "负向对照 ① 改坏了源码结构"
    if len(_pumps_in_try_body(tr)) >= 2:
        return False, "负向对照 ① 失效：删掉两行 pump 后判据仍然通过"
    return True, "负向对照 ① 成立：删掉 pump 后判据确实失败"


def test_ast_check_catches_pump_after_except():
    """负向对照 ②：**复现 2026-10-14 真踩的那个坑** —— 把 pump 挪到 except 之后。

    两行 pump 的缩进与 `time.sleep(0.2)` 相同（即 except 块内部），语法合法、
    编译通过、运行不报错，但正常路径再也不泵消息。判据必须把它判红。
    """
    src = open(os.path.join(HERE, "desktop_fx.py"), encoding="utf-8").read()
    pump_lines = "                ov_fs.pump()\n                ov_ms.pump()\n"
    if pump_lines not in src:
        return False, "找不到那两行 pump（源码结构与预期不符）"
    stripped = src.replace(pump_lines, "").replace(
        "                time.sleep(0.2)\n",
        "                time.sleep(0.2)\n" + pump_lines)
    if stripped == src:
        return False, "负向对照 ② 没有真正改到源码"
    try:
        tr = _frame_try_from_src(stripped)
    except SyntaxError as e:
        return False, f"负向对照 ② 造出了语法错误：{e}"
    if tr is None:
        return False, "负向对照 ② 改坏了源码结构"
    if len(_pumps_in_try_body(tr)) >= 2:
        return False, ("负向对照 ② 失效：pump 挪到 except 之后判据仍然通过 —— "
                       "说明判据口径又错了（是不是用了 ast.walk 走 whole Try？）")
    return True, "负向对照 ② 成立：pump 挪到 except 之后会被判红"


def _frame_try_from_src(src):
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == "App":
            for m in n.body:
                if isinstance(m, ast.FunctionDef) and m.name == "_render_frames":
                    return _frame_try(m)
    return None


def test_real_loop_survives_pump_failure():
    """真机：泵消息抛异常时渲染线程应当**活着**，且错误必须落盘。

    这是原来"pump 失败 = 线程静默死亡"那条路径的正面验证。
    """
    err = os.path.join(_TMP, "error.log")
    for f in os.listdir(_TMP):
        # 全清，包括 error.log —— 上一条用例留的记录会让本条读到陈旧内容
        try:
            os.remove(os.path.join(_TMP, f))
        except OSError:
            pass
    app = d.App()
    app.set("rain_enabled", True)
    orig = fx_winlayer.Overlay.pump
    calls = {"n": 0}

    def bad_pump(self):
        calls["n"] += 1
        if calls["n"] >= 3:
            raise RuntimeError("自检注入：pump 失败")

    fx_winlayer.Overlay.pump = bad_pump
    app._thread = threading.Thread(target=app._render_loop,
                                   name="guard-probe", daemon=True)
    app._thread.start()
    try:
        time.sleep(2.0)
    finally:
        fx_winlayer.Overlay.pump = orig
        app.stop_evt.set()
        app._thread.join(timeout=6.0)
    if calls["n"] < 3:
        return False, f"渲染线程只调了 {calls['n']} 次 pump，注入没生效"
    if not os.path.exists(err):
        return False, "pump 失败却没有留下任何记录"
    body = open(err, encoding="utf-8").read()
    if "自检注入：pump 失败" not in body:
        return False, f"error.log 里没有注入的异常：{body[:300]!r}"
    # 线程退出时覆盖窗必须已拆（destroy 后 hwnd 为 None）
    for i, ov in enumerate(app.overlays):
        if getattr(ov, "hwnd", None) is not None:
            return False, f"第 {i} 层退出后 hwnd 还在（没 destroy）"
    return True, "pump 失败被记录、线程走完正常退出、覆盖窗已释放"


# ----------------------------------------------------------- 行尾属性
def test_bat_eol_attribute():
    """启动器必须是 CRLF，且这条保证要落在仓库里而不是开发机全局 git 配置上。"""
    ga = os.path.join(HERE, ".gitattributes")
    if not os.path.exists(ga):
        return False, "没有 .gitattributes"
    body = open(ga, encoding="utf-8").read()
    if "*.bat text eol=crlf" not in body:
        return False, ".gitattributes 里没有 *.bat text eol=crlf"
    try:
        r = subprocess.run(["git", "check-attr", "eol", "--", "启动桌面特效.bat"],
                           cwd=HERE, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except Exception as exc:
        return None, f"SKIP（{type(exc).__name__}: {exc}）"
    if "crlf" not in (r.stdout or ""):
        return False, f"git check-attr 没认出 crlf：{r.stdout!r}"
    # 工作区那份必须真的是 CRLF（否则改了属性反而把可用文件弄坏）
    b = open(os.path.join(HERE, "启动桌面特效.bat"), "rb").read()
    if b.count(b"\r") != b.count(b"\n") or not b.count(b"\r"):
        return False, "工作区的启动器不是 CRLF"
    return True, "属性已声明且工作区文件确实是 CRLF"


# ----------------------------------------------------------------- 驱动
def main():
    tests = [
        test_atomic_write_json_basics,
        test_atomic_write_failure_cleans_up,
        test_config_roundtrip,
        test_presets_roundtrip,
        test_no_direct_overwrite_left,
        test_render_loop_reports_on_exception,
        test_render_loop_finally_is_needed,
        test_pump_and_fps_inside_frame_try,
        test_ast_check_is_sensitive,
        test_ast_check_catches_pump_after_except,
        test_real_loop_survives_pump_failure,
        test_bat_eol_attribute,
    ]
    print("== 渲染保护 + 原子写 ==")
    bad = 0
    for fn in tests:
        try:
            r = fn()
        except Exception as e:
            r = (False, f"exception: {type(e).__name__}: {e}")
        if r is None:
            print(f"  [SKIP] {fn.__name__}: {r[1]}")
            continue
        ok, msg = r
        if not ok:
            bad += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {fn.__name__}: {msg}")

    try:
        shutil.rmtree(_TMP, ignore_errors=True)
    except Exception:
        pass
    print()
    if bad:
        print(f"== RENDER GUARD FAILED ==  {bad} 项失败")
        raise SystemExit(1)
    print("== RENDER GUARD VERIFIED ==")
    raise SystemExit(0)


if __name__ == "__main__":
    main()