# -*- coding: utf-8 -*-
"""验证 开机自启 注册表读写（用临时子键不影响真实 HKCU\\Run）。"""
import sys
import os

# Monkey-patch _RUN_KEY to a test key, then call set/get
# 本脚本住在 tests/ 子目录里：`sys.path[0]` 与 `__file__` 的父目录都不再是项目根。
# 向上找到含 desktop_fx.py 的目录（= 项目根）：产品模块从那里 import；脚本里已有的
# HERE 也一律以它为基准（读 desktop_fx.py / fx_effects.py / .gitattributes /
# 启动桌面特效.bat、写临时配置都按项目根算）。
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = _TESTS_DIR
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
# tests/ 自己也要进 path：有几个脚本互相 import（_verify_click / _verify_pet ->
# _verify_kinds、_verify_tray -> _verify_launcher）。
for _p in (ROOT, _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import desktop_fx as d

ORIG_KEY = d.App._RUN_KEY
TEST_KEY = r"Software\DesktopFXTest"
d.App._RUN_KEY = TEST_KEY
TEST_VALUE = "DesktopFXTest"

try:
    app = d.App.__new__(d.App)
    app.cfg = dict(d.DEFAULTS)
    app.snapshot = dict(app.cfg)

    # 初始：未注册
    assert app.get_autostart() is False, "should start as not-registered"
    print("[OK] initial state: not registered")

    # 启用
    assert app.set_autostart(True) is True
    assert app.get_autostart() is True, "should be registered after set True"
    print(f"[OK] after set_autostart(True): registered")
    print(f"     cmd: {app._autostart_cmd()}")

    # 关闭
    assert app.set_autostart(False) is True
    assert app.get_autostart() is False, "should be unregistered after set False"
    print(f"[OK] after set_autostart(False): unregistered")

    # 幂等关闭
    assert app.set_autostart(False) is True
    assert app.get_autostart() is False
    print(f"[OK] double-off idempotent")

    # 命令行格式检查
    cmd = app._autostart_cmd()
    assert cmd.startswith('"') and '"' in cmd[1:], f"paths must be quoted: {cmd}"
    assert cmd.endswith("desktop_fx.py\""), f"must end with desktop_fx.py: {cmd}"
    assert "pythonw" in cmd, f"must use pythonw.exe (silent): {cmd}"
    print(f"[OK] command format: {cmd}")
    print()
    print("== AUTOSTART VERIFIED ==")
finally:
    d.App._RUN_KEY = ORIG_KEY
    # 清理测试键（包括子键本身）
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_KEY,
                            0, winreg.KEY_SET_VALUE | winreg.KEY_READ) as k:
            i = 0
            while True:
                try:
                    name, _, _ = winreg.EnumValue(k, i)
                except OSError:
                    break
                try:
                    winreg.DeleteValue(k, name)
                except FileNotFoundError:
                    pass
                i += 1
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_KEY)
    except FileNotFoundError:
        pass
    except Exception:
        pass
