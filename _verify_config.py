# -*- coding: utf-8 -*-
"""验证：(1) 配置保存 / 加载 (2) 全部 8 个预设能正常应用。"""
import json
import os
import sys
import tempfile

import desktop_fx as d


def test_presets():
    """所有预设应该能 apply_preset 而不抛错，且关键字段被正确覆盖。"""
    print("== test_presets ==")
    for name in d.PRESETS:
        app = d.App.__new__(d.App)
        app.cfg = dict(d.DEFAULTS)
        app.snapshot = dict(app.cfg)
        app.apply_preset(name)
        p = d.PRESETS[name]
        for k, v in p.items():
            assert app.cfg[k] == v, f"{name}: {k} -> {app.cfg[k]} != {v}"
        print(f"  [OK] {name:14s}  rain_amount={app.cfg['rain_amount']:.0f}"
              f"  rain_color={app.cfg['rain_color']}  wind={app.cfg['wind']:+.2f}")
    print(f"  total: {len(d.PRESETS)} presets\n")


def test_config_persistence():
    """用临时目录模拟 APPDIR，验证 load / save / reload。"""
    print("== test_config_persistence ==")
    tmp = tempfile.mkdtemp(prefix="desktop_fx_test_")
    saved = d.APP_DIR
    saved_path = d.CONFIG_PATH
    d.APP_DIR = tmp
    d.CONFIG_PATH = os.path.join(tmp, "config.json")
    try:
        # 第一次启动：默认 + 应用一个预设 + 改一个值
        app1 = d.App.__new__(d.App)
        app1.cfg = dict(d.DEFAULTS)
        app1.snapshot = dict(app1.cfg)
        app1.apply_preset("暴雨")
        app1.set("rain_amount", 777.0)
        app1.set("wind", -0.33)
        app1.save()
        assert os.path.exists(d.CONFIG_PATH), "config.json not written"
        with open(d.CONFIG_PATH, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        assert saved_data["rain_amount"] == 777.0
        assert saved_data["wind"] == -0.33
        assert saved_data["rain_color"] == d.PRESETS["暴雨"]["rain_color"]
        print(f"  [OK] wrote config to {d.CONFIG_PATH}  size={os.path.getsize(d.CONFIG_PATH)}B")
        print(f"       rain_amount={saved_data['rain_amount']}  wind={saved_data['wind']}"
              f"  rain_color={saved_data['rain_color']}")

        # 第二次启动：加载回来
        app2 = d.App.__new__(d.App)
        app2.cfg = dict(d.DEFAULTS)
        app2.snapshot = dict(app2.cfg)
        app2.load()
        assert app2.cfg["rain_amount"] == 777.0, f"reload rain_amount = {app2.cfg['rain_amount']}"
        assert app2.cfg["wind"] == -0.33
        assert app2.cfg["rain_color"] == d.PRESETS["暴雨"]["rain_color"]
        # 未指定字段应保持默认
        assert app2.cfg["rain_enabled"] == d.DEFAULTS["rain_enabled"]
        print(f"  [OK] reloaded: rain_amount={app2.cfg['rain_amount']}  wind={app2.cfg['wind']}"
              f"  rain_enabled={app2.cfg['rain_enabled']}")

        # 坏文件应被忽略而不是炸
        with open(d.CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write("{this is not json")
        app3 = d.App.__new__(d.App)
        app3.cfg = dict(d.DEFAULTS)
        app3.snapshot = dict(app3.cfg)
        app3.load()
        assert app3.cfg == d.DEFAULTS, "corrupt config should fall back to DEFAULTS"
        print(f"  [OK] corrupt config ignored, fell back to DEFAULTS")
    finally:
        d.APP_DIR = saved
        d.CONFIG_PATH = saved_path
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print()


def test_snapshot_consistency():
    """apply_preset / set 必须同步更新 snapshot。"""
    print("== test_snapshot_consistency ==")
    app = d.App.__new__(d.App)
    app.cfg = dict(d.DEFAULTS)
    app.snapshot = dict(app.cfg)
    app.apply_preset("夜雨（青）")
    assert app.snapshot == app.cfg, "snapshot not in sync after apply_preset"
    app.set("lightning", True)
    assert app.snapshot["lightning"] is True, "snapshot not in sync after set"
    app.set("ripple_opacity", 0.5)
    assert app.snapshot["ripple_opacity"] == 0.5
    print(f"  [OK] snapshot mirrors cfg after apply_preset + set\n")


if __name__ == "__main__":
    test_presets()
    test_config_persistence()
    test_snapshot_consistency()
    print("== ALL VERIFIED ==")
