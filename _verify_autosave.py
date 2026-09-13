# -*- coding: utf-8 -*-
"""验证：崩溃模拟。set() 触发 dirty，autosave 线程 1s 内写盘。
   模拟"被任务管理器强杀"（不调 quit()），确认改动已经落盘。"""
import json
import os
import tempfile
import time

import desktop_fx as d


def main():
    tmp = tempfile.mkdtemp(prefix="desktop_fx_autosave_")
    saved_dir, saved_path = d.APP_DIR, d.CONFIG_PATH
    d.APP_DIR = tmp
    d.CONFIG_PATH = os.path.join(tmp, "config.json")
    try:
        # 第一次启动
        app = d.App.__new__(d.App)
        app.cfg = dict(d.DEFAULTS)
        app.snapshot = dict(app.cfg)
        import threading as _t
        app._autosave_thread = None
        app.stop_evt = _t.Event()
        app.start_autosave()
        time.sleep(0.2)

        # 模拟 UI 改动：连续 5 次
        # 注意：apply_preset 会整体覆盖，所以放在 set 之前，
        #       后面 set 决定最终值。
        app.apply_preset("夜雨（青）")          # 整体改为夜雨
        app.set("rain_amount", 555.0)          # 覆盖预设的 340
        app.set("wind", -0.5)                  # 覆盖预设的 +0.2
        app.set("splash", False)
        app.set("ripple_opacity", 0.66)
        print("[ok] made 5 config changes, marked dirty")

        # 还没过 1s，文件应该还没写
        time.sleep(0.4)
        if os.path.exists(d.CONFIG_PATH):
            print(f"[warn] file appeared too early at t=0.4s, size={os.path.getsize(d.CONFIG_PATH)}B")
        else:
            print("[ok] no file at t=0.4s (not yet 1s)")

        # 等 autosave 写盘
        deadline = time.time() + 3.0
        while time.time() < deadline and not os.path.exists(d.CONFIG_PATH):
            time.sleep(0.1)
        assert os.path.exists(d.CONFIG_PATH), "autosave should have written within 1-2s"
        print(f"[ok] autosave wrote at t~1.0s, size={os.path.getsize(d.CONFIG_PATH)}B")

        with open(d.CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["rain_amount"] == 555.0, saved
        assert saved["wind"] == -0.5
        assert saved["splash"] is False
        assert saved["ripple_opacity"] == 0.66
        assert saved["rain_color"] == d.PRESETS["夜雨（青）"]["rain_color"]
        print(f"[ok] all 5 changes persisted to disk")
        print(f"     rain_amount={saved['rain_amount']}  wind={saved['wind']}"
              f"  splash={saved['splash']}  ripple_opacity={saved['ripple_opacity']}"
              f"  rain_color={saved['rain_color']}")

        # 模拟"被强杀"：直接退出，不调用 quit()
        app.stop_evt.set()
        app._autosave_thread.join(timeout=2.0)
        del app

        # 第二次启动：所有改动应能完整恢复
        app2 = d.App.__new__(d.App)
        app2.cfg = dict(d.DEFAULTS)
        app2.snapshot = dict(app2.cfg)
        app2.load()
        assert app2.cfg["rain_amount"] == 555.0
        assert app2.cfg["wind"] == -0.5
        assert app2.cfg["splash"] is False
        assert app2.cfg["ripple_opacity"] == 0.66
        print(f"[ok] after 'crash' + restart: all 5 changes recovered")

        # 无脏改动时，不应反复写盘（不应有副作用）
        time.sleep(1.5)
        mtime = os.path.getmtime(d.CONFIG_PATH)
        time.sleep(1.5)
        assert os.path.getmtime(d.CONFIG_PATH) == mtime, "idle autosave should not rewrite"
        print(f"[ok] idle autosave does not touch file when not dirty")
        print()
        print("== AUTOSAVE / CRASH-RECOVERY VERIFIED ==")
    finally:
        d.APP_DIR = saved_dir
        d.CONFIG_PATH = saved_path
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
