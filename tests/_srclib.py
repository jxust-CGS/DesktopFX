# -*- coding: utf-8 -*-
"""负向对照助手：按源码打补丁再 exec（fx_effects 拆成三层之后需要）。

**为什么需要它**：`_over_blend` 已搬到 `fx_image.py`，而用到它的
`PetField`（fx_effects）与 `_BurstEngine` / `ClickTextField`（fx_click）
是在**各自 import 时**把名字绑进自己命名空间的。所以只替换 `fx_image`
模块里的函数对象**不会生效** —— 下游绑的还是旧对象，负向对照会"打不动"，
表现为一条本该变红的断言保持绿色（假阴性，比测试失败更危险）。

正解是把打过补丁的 `fx_image` 源码重新 exec 一遍，并让下游的
`from fx_image import *` 拿到这一份。做法：先把假的 `fx_image` 塞进
`sys.modules`，再 exec 下游模块源码，最后还原。

`count_marker()` 配套使用：拆分后同一个东西可能被抄到别的文件里，
唯一性检查必须**跨三个产品文件一起数**，否则"有人又抄了一份混合实现"
这件事就查不出来了。
"""
import contextlib
import io
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) \
        and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)

# 拆分后可能承载目标代码的产品文件（唯一性检查要一起数）
PRODUCT_MODULES = ("fx_image.py", "fx_click.py", "fx_effects.py")


def read_src(name):
    """读产品模块源码。"""
    return io.open(os.path.join(ROOT, name), encoding="utf-8").read()


def count_marker(marker, names=PRODUCT_MODULES):
    """标记行在全部产品文件里出现的**总**次数。

    只看单个文件会漏掉"实现被抄到另一个模块"这种情况 —— 那正是这些负向
    对照要拦的事（`_over_blend` 的注释里写明了：出现两次就说明有人又抄了一份）。
    """
    return sum(read_src(n).count(marker) for n in names)


@contextlib.contextmanager
def patched(dep, patched_src):
    """把打过补丁的 dep 模块（如 "fx_image"）临时塞进 sys.modules。

    下游模块在 `from <dep> import *` 时会拿到这一份，从而用到被改坏的实现。
    """
    saved = sys.modules.get(dep)
    fake = types.ModuleType(dep)
    fake.__file__ = os.path.join(ROOT, dep + ".py")
    sys.modules[dep] = fake
    try:
        exec(compile(patched_src, dep + ".py", "exec"), fake.__dict__)
        yield fake
    finally:
        if saved is None:
            sys.modules.pop(dep, None)
        else:
            sys.modules[dep] = saved


def exec_module(name, alias, src=None):
    """把某产品模块的源码 exec 成一个独立模块对象（不进 sys.modules）。

    src 传了就用它（负向对照改过的那份），否则读文件。
    """
    if src is None:
        src = read_src(name)
    mod = types.ModuleType(alias)
    mod.__dict__["__name__"] = alias
    mod.__dict__["__file__"] = os.path.join(ROOT, name)
    exec(compile(src, name, "exec"), mod.__dict__)
    return mod
