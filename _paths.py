# -*- coding: utf-8 -*-
"""路径助手：兼容源码运行与 PyInstaller 冻结运行。

- app_root()：持久化数据目录。
  源码运行 = 项目目录（原 __file__ 行为不变）；
  冻结运行 = exe 所在目录（写 config.json / presets_user.json / ui_state.json /
  error.log 都在这里，随 exe 走，重启不丢）。
- res_path(name)：只读资源。
  冻结单文件模式下资源打包进 sys._MEIPASS 临时目录，从那里取；
  否则退回 app_root()。开发时图标在 `assets/` 下，所以调用方一律写
  `res_path(os.path.join("assets", ...))`（与 `fx_effects.builtin_sfx_dir` 同）
  —— 别拼字符串，`_MEIPASS` 那条分支才走得通。
"""
import json
import os
import sys
import tempfile


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


def atomic_write_json(path, obj, indent=2, sync=True):
    """原子写 JSON：先写同目录临时文件，fsync 落盘，再 os.replace 覆盖目标。

    为什么非它不可：`open(path, "w")` 是**先 truncate 再逐块写**，中途断电或
    被强杀就留下半截 JSON。而本项目三个数据文件（config.json /
    presets_user.json / ui_state.json）的读取端对损坏的处理是"整体回退出厂值"
    —— 用户调了半天的参数一次归零，界面上还看不出发生过什么（error.log 里有
    一行 config-load，但没人会去看）。`json.dump` 一个 88 键的对象在机械硬盘或
    网盘同步目录（OneDrive / Dropbox）下写入窗口并不窄。

    同一文件系统内 `os.replace` 是原子的：读者要么看到完整的新文件，要么看到
    完整的旧文件，不存在"半截"这个中间态。

    `sync=False` 跳过 fsync：`os.replace` 仍保证原子性（读者要么看到完整的
    新文件、要么看到完整的旧文件，不会看到半截），丢的只是"最后一点数据还
    没落盘"—— 断电时可能回退到上一版本。适合**每次交互都会写**的偏好文件
    （ui_state.json：窗口尺寸 / 上次停留页），fsync 在机械盘上一次 6ms 起，
    高频写会把交互卡住。config.json / presets_user.json 涉及用户调了半天的
    参数，照旧 sync=True。

    临时文件必须建在**同一目录**：跨卷的 `os.replace` 会退化成非原子的
    拷贝+删除，那样就白做了。
    """
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
            f.flush()
            if sync:
                os.fsync(f.fileno())   # 数据真正落到盘上，replace 才是有意义的最后一步
        os.replace(tmp, path)
    except BaseException:
        # 失败时别把临时文件留在用户目录里（它长得就像一个 json，
        # 还会被"配置目录里怎么多了个文件"这类问题误伤）
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
