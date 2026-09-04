# -*- coding: utf-8 -*-
"""路径助手：兼容源码运行与 PyInstaller 冻结运行。

- app_root()：持久化数据目录。
  源码运行 = 项目目录（原 __file__ 行为不变）；
  冻结运行 = exe 所在目录（写 config.json / presets_user.json / ui_state.json /
  error.log 都在这里，随 exe 走，重启不丢）。
- res_path(name)：只读资源。
  冻结单文件模式下资源打包进 sys._MEIPASS 临时目录，从那里取；
  否则退回 app_root()（开发时 tray_icon.* 就在项目目录）。
"""
import os
import sys


def app_root():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def res_path(name):
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return os.path.join(app_root(), name)
