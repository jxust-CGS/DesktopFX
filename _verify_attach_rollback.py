# -*- coding: utf-8 -*-
"""`_attach_desktop` 失败路径的清理对称性回归。

把覆盖窗挂到壁纸层要连做三步：改样式为 WS_CHILD → 回读校验 → SetParent。
第三条（SetParent 回读失败）原先做得很完整：`SetParent(hwnd, None)` + 写回原
样式 + 复位 `_parented`/`_origin` + 重建分层表面。

**第二条（样式回读失败）却直接 `return False` 就走了** —— 样式已经改成
WS_CHILD 却没挂上去，留下一个"没有父亲却有 WS_CHILD"的窗口。系统会按孤儿
子窗处理它（不上屏、不参与正常 z 序），而且它**不可自愈**：`_parented` 仍是
False，`_detach_parent()` 开头 `if not self._parented: return` 直接返回，
此后没有任何路径会来修它。

本脚本查三件事：

  1. 源码里回读失败分支确实回写了原样式（AST 判据，并带负向对照）；
  2. 取不到样式（`GetWindowLongW` 返回 0）时**一次都不能写** —— 拿 0 去
     SetWindowLong 会把窗口的全部样式抹光（连 WS_VISIBLE 都没了）；
  3. 真机验证：桩住"第一次样式写不生效"制造回读失败，看回滚是否真的发生、
     失败后状态是否干净。

真机用例会真的建一个全屏覆盖窗（和其它真机脚本一样），用完即销毁。
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fx_winlayer as fw                            # noqa: E402

user32 = fw.user32
HWND = fw.HWND

_SRC_PATH = os.path.join(HERE, "fx_winlayer.py")


def _src():
    with open(_SRC_PATH, encoding="utf-8") as f:
        return f.read()


def _find_method(tree, cls_name, meth_name):
    for n in tree.body:
        if not isinstance(n, ast.ClassDef) or n.name != cls_name:
            continue
        for m in n.body:
            if isinstance(m, ast.FunctionDef) and m.name == meth_name:
                return m
    return None


def _identifiers(node):
    """子树里出现的属性名与裸名字（`WS_CHILD` 是模块级常量，属 ast.Name）。"""
    out = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
    out |= {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    return out


def _has_call(node, func_attr):
    for n in ast.walk(node):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == func_attr):
            return True
    return False


def _style_check_branch(src):
    """返回 `_attach_desktop` 里"回读 WS_CHILD 失败"那个 If 节点，找不到返回 None。"""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    fn = _find_method(tree, "Overlay", "_attach_desktop")
    if fn is None:
        return None
    for n in ast.walk(fn):
        if isinstance(n, ast.If) and "WS_CHILD" in _identifiers(n.test):
            return n
    return None


# ------------------------------------------------------ 1. 源码：回滚必须存在
def test_source_rolls_back_style():
    """回读失败分支里必须有 `SetWindowLongW(..., GWL_STYLE, style)`。"""
    n = _style_check_branch(_src())
    if n is None:
        return False, "找不到 `_attach_desktop` 里回读 WS_CHILD 的分支"
    if not _has_call(n, "SetWindowLongW"):
        return False, ("回读失败时没有回写原样式 —— 会留下"
                       "'没有父亲却有 WS_CHILD'的孤儿窗，且不可自愈")
    return True, "回读失败时回写原样式（与 SetParent 失败分支对称）"


def test_ast_check_is_sensitive():
    """负向对照：把回滚那行删掉，上面那条判据必须变红。"""
    src = _src()
    old = ("            # _rebuild_layered_surface()；_origin 也还是旧值，无需复位。\n"
           "            user32.SetWindowLongW(self.hwnd, GWL_STYLE, style)\n")
    if old not in src:
        return False, "负向对照没改到源码（回滚行前的注释变了？）"
    n = _style_check_branch(src.replace(old, ""))
    if n is None:
        return False, "负向对照失效：删掉回滚后连分支都找不到了"
    if _has_call(n, "SetWindowLongW"):
        return False, "负向对照失效：删掉回滚后判据仍然通过"
    return True, "负向对照成立：删掉回滚，判据确实变红"


def test_source_guards_zero_style():
    """取不到样式（0）时必须提前返回，绝不能拿 0 去回写。"""
    try:
        tree = ast.parse(_src())
    except SyntaxError as e:
        return False, f"语法错误：{e}"
    fn = _find_method(tree, "Overlay", "_attach_desktop")
    if fn is None:
        return False, "找不到 Overlay._attach_desktop"
    for n in fn.body:
        if not isinstance(n, ast.If):
            continue
        t = n.test
        if (isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not)
                and isinstance(t.operand, ast.Name) and t.operand.id == "style"
                and any(isinstance(s, ast.Return) for s in n.body)):
            return True, "style 为 0 时提前返回（不会把窗口样式抹成 0）"
    return False, ("函数开头没有 `if not style: return False` 守卫 —— "
                   "GetWindowLongW 失败时 style 是 0，下面两支回滚都会拿 0 回写")


# ---------------------------------------------------------- 2. 真机：回滚行为
def _new_overlay():
    try:
        return fw.Overlay().create(), None
    except Exception as e:
        return None, f"SKIP（{type(e).__name__}: {e}）"


def test_real_rollback_on_readback_failure():
    """桩住"第一次写样式不生效"，制造回读失败，验证回滚真的发生。"""
    ov, err = _new_overlay()
    if ov is None:
        return None, err
    try:
        if not fw._find_wallpaper_workerw():
            return None, "SKIP（当前桌面没有可挂载的 WorkerW）"
        # 先探一次正常路径：受限会话 / 多屏 WorkerW 不覆盖虚拟屏时
        # `_attach_desktop` 会提前返回，那样就测不到回滚分支。
        if not ov._attach_desktop():
            return None, "SKIP（本机正常路径就挂不上，无法覆盖回滚分支）"
        ov._detach_parent()

        base = user32.GetWindowLongW(HWND(ov.hwnd), fw.GWL_STYLE)
        if not base:
            return None, "SKIP（读不到窗口样式）"

        writes = []
        orig = user32.SetWindowLongW

        def fake_set(hwnd, idx, val):
            if idx == fw.GWL_STYLE:
                writes.append(val)
                if len(writes) == 1:
                    return 0            # 第一次不生效 → 回读读不到 WS_CHILD
            return orig(hwnd, idx, val)

        user32.SetWindowLongW = fake_set
        try:
            ok = ov._attach_desktop()
        finally:
            user32.SetWindowLongW = orig

        if ok:
            return False, "样式没设上却报告挂载成功"
        if len(writes) < 2:
            return False, (f"回读失败后没有回写样式（GWL_STYLE 只写了 {len(writes)} 次）"
                           " —— 孤儿窗会留在那里且不可自愈")
        if writes[-1] != base:
            return False, (f"回滚写回的值不对：{writes[-1]:#x}，应等于进函数时的 "
                           f"{base:#x}")
        now = user32.GetWindowLongW(HWND(ov.hwnd), fw.GWL_STYLE)
        if now & fw.WS_CHILD:
            return False, f"回滚后窗口仍带着 WS_CHILD（{now:#x}）"
        if not (now & fw.WS_POPUP_STYLE):
            return False, f"回滚后窗口丢了 WS_POPUP（{now:#x}）"
        if ov._parented:
            return False, "_parented 被置成了 True（状态被污染）"
        if tuple(ov._origin) != (0, 0):
            return False, f"_origin 被污染：{ov._origin}"
        return True, (f"回读失败 → 回写原样式（{len(writes)} 次写，最后回到 "
                      f"{base:#x}）→ 无 WS_CHILD 残留、状态未污染")
    finally:
        try:
            ov.destroy()
        except Exception:
            pass


def test_real_no_write_when_style_unavailable():
    """取不到样式时一次都不能写：拿 0 回写会把窗口样式全抹掉。"""
    ov, err = _new_overlay()
    if ov is None:
        return None, err
    try:
        if not user32.IsWindow(HWND(ov.hwnd)):
            return None, "SKIP（窗口无效）"
        base = user32.GetWindowLongW(HWND(ov.hwnd), fw.GWL_STYLE)
        writes = []
        oset, oget = user32.SetWindowLongW, user32.GetWindowLongW

        def fake_get(hwnd, idx):
            if idx == fw.GWL_STYLE:
                return 0                # 模拟"窗口已失效，读不到样式"
            return oget(hwnd, idx)

        def fake_set(hwnd, idx, val):
            writes.append((idx, val))
            return oset(hwnd, idx, val)

        user32.GetWindowLongW = fake_get
        user32.SetWindowLongW = fake_set
        try:
            ok = ov._attach_desktop()
        finally:
            user32.GetWindowLongW = oget
            user32.SetWindowLongW = oset

        if ok:
            return False, "读不到样式却报告挂载成功"
        if writes:
            return False, (f"读不到样式时仍然写了 {len(writes)} 次 —— "
                           "拿 0 回写会把 WS_POPUP/WS_VISIBLE 一起抹掉")
        after = oget(HWND(ov.hwnd), fw.GWL_STYLE)
        if after != base:
            return False, f"窗口样式被改动了：{base:#x} -> {after:#x}"
        return True, "取不到样式时直接放弃，样式一字未动"
    finally:
        try:
            ov.destroy()
        except Exception:
            pass


# ------------------------------------------------------------------ 驱动
def main():
    tests = [
        test_source_rolls_back_style,
        test_ast_check_is_sensitive,
        test_source_guards_zero_style,
        test_real_rollback_on_readback_failure,
        test_real_no_write_when_style_unavailable,
    ]
    print("== 挂载失败路径的清理对称性 ==")
    bad = 0
    ran = 0
    for fn in tests:
        try:
            r = fn()
        except Exception as e:
            r = (False, f"exception: {type(e).__name__}: {e}")
        if r is None or (isinstance(r, tuple) and r[0] is None):
            print(f"  [SKIP] {fn.__name__}: {r[1] if r else ''}")
            continue
        ran += 1
        ok, msg = r
        if not ok:
            bad += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {fn.__name__}: {msg}")

    print(f"\n  {ran - bad}/{ran} 项通过（另有 {len(tests) - ran} 项跳过）")
    if bad:
        print("== ATTACH ROLLBACK FAILED ==")
        raise SystemExit(1)
    print("== ATTACH ROLLBACK VERIFIED ==")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
