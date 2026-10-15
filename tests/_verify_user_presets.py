# -*- coding: utf-8 -*-
"""验证用户自定义预设：保存、按页分组、删除、应用。"""
import json
import os
import tempfile

import sys

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


def test_user_preset_lifecycle():
    print("== test_user_preset_lifecycle ==")
    saved_path = d.USER_PRESETS_PATH
    tmp = tempfile.mkdtemp(prefix="desktop_fx_presets_")
    d.USER_PRESETS_PATH = os.path.join(tmp, "presets_user.json")
    try:
        app = d.App.__new__(d.App)
        app.cfg = dict(d.DEFAULTS)
        app.snapshot = dict(app.cfg)
        app.user_presets = {}
        app._autosave_dirty = False

        # 修改几个值再保存
        app.set("rain_amount", 333.0)
        app.set("rain_color", "#FF0000")
        ok, msg = app.save_preset("我的暴雨", "下雨")
        assert ok, msg
        assert "我的暴雨" in app.user_presets
        assert app.user_presets["我的暴雨"]["_page"] == "下雨"
        assert app.user_presets["我的暴雨"]["rain_amount"] == 333.0

        # 持久化文件存在且可解析
        assert os.path.exists(d.USER_PRESETS_PATH)
        with open(d.USER_PRESETS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "我的暴雨" in data
        print(f"  [OK] saved user preset '我的暴雨' rain_amount={data['我的暴雨']['rain_amount']}")

        # 按页分组
        assert "我的暴雨" in app.presets_for_page("下雨")
        assert "我的暴雨" not in app.presets_for_page("下雪")
        assert app.is_user_preset("我的暴雨")
        print("  [OK] preset page grouping correct")

        # 应用用户预设
        app.cfg["rain_amount"] = 0.0
        app.apply_preset("我的暴雨")
        assert app.cfg["rain_amount"] == 333.0
        assert app.cfg["rain_color"] == "#FF0000"
        print("  [OK] apply user preset restores values")

        # 删除用户预设
        ok, msg = app.delete_preset("我的暴雨")
        assert ok, msg
        assert "我的暴雨" not in app.user_presets
        print("  [OK] delete user preset")

        # 不能删除内置预设
        ok, msg = app.delete_preset("暴雨")
        assert not ok
        print("  [OK] built-in preset not deletable")

        # 不能覆盖内置预设名
        app.cfg["rain_amount"] = 123.0
        ok, msg = app.save_preset("暴雨", "下雨")
        assert not ok
        print("  [OK] cannot shadow built-in preset name")

        # 重名自定义预设必须报错，不可静默覆盖第一个
        # （用全新名称，避免与上面已删除的预设混淆）
        app.cfg["rain_amount"] = 777.0
        ok, msg = app.save_preset("重复测试", "下雨")
        assert ok, msg
        app.cfg["rain_amount"] = 999.0
        ok, msg = app.save_preset("重复测试", "下雨")
        assert not ok, msg
        assert app.user_presets["重复测试"]["rain_amount"] == 777.0, "被覆盖!"
        print("  [OK] duplicate custom name rejected (no silent overwrite)")
    finally:
        d.USER_PRESETS_PATH = saved_path
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print()


if __name__ == "__main__":
    test_user_preset_lifecycle()
    print("== ALL VERIFIED ==")
