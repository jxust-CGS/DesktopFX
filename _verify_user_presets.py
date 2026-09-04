# -*- coding: utf-8 -*-
"""验证用户自定义预设：保存、按页分组、删除、应用。"""
import json
import os
import tempfile

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
