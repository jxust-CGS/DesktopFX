# -*- coding: utf-8 -*-
"""验证故障可见性：错误必须落盘、去重、可恢复、并在彻底坏掉时熔断退出。

背景：渲染循环原来捕获异常后只写 self.last_error，而这个字段没人读。
pythonw 又没有控制台，print 全部消失 —— 于是程序可以永久静默失败：
桌面一片空白，用户毫无线索，进程还以 5 FPS 一直空转烧 CPU。
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import desktop_fx as D


def fresh_app(log_path):
    """构造一个不碰真实配置的 App，错误日志指向临时文件。"""
    app = D.App.__new__(D.App)
    app.cfg = dict(D.DEFAULTS)
    app.snapshot = dict(app.cfg)
    app.stop_evt = D.threading.Event()
    app.paused = False
    app._autosave_thread = None
    app.fps = 0.0
    app.cost_ms = 0.0
    app.overlay = None
    app.tray = None
    app._thread = None
    app._last_color = None
    app._last_ripple_color = None
    app.last_error = None
    app._err_streak = 0
    app._err_logged = None
    app._autosave_dirty = False
    # 让落盘落到临时文件，别污染项目目录
    app._log_path = log_path
    return app


def patch_log(app, tmpdir):
    """把 report_error 的落盘目标换成临时文件（保留原逻辑其余部分）。"""
    log_file = os.path.join(tmpdir, "error.log")
    orig = D.App.report_error

    def patched(self, where, exc, fatal=False):
        msg = f"{where}: {type(exc).__name__}: {exc}"
        self.last_error = msg
        if fatal:
            self._err_streak += 1
        if msg != self._err_logged:
            self._err_logged = msg
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{stamp}] {msg}\n")
        return self._err_streak >= D.MAX_CONSECUTIVE_ERRORS

    D.App.report_error = patched
    return log_file, orig


def read_log(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln for ln in f.read().splitlines() if ln.strip()]


def test_logged_and_deduped(tmpdir):
    print("== test_logged_and_deduped ==")
    log_file, orig = patch_log(None, tmpdir)
    try:
        app = fresh_app(log_file)
        # 同一个错误连报 50 次，只应落盘 1 条
        for _ in range(50):
            app.report_error("render", ValueError("boom"), fatal=True)
        lines = read_log(log_file)
        assert len(lines) == 1, f"同一条错误应只记 1 次，实际 {len(lines)} 条"
        assert "boom" in lines[0], lines[0]
        print(f"  [OK] 同一错误 50 次只落盘 1 条: {lines[0]}")
        print(f"  [OK] last_error 可供 UI 读取: {app.last_error}")
    finally:
        D.App.report_error = orig


def test_new_message_logged_again(tmpdir):
    print("== test_new_message_logged_again ==")
    log_file, orig = patch_log(None, tmpdir)
    try:
        app = fresh_app(log_file)
        app.report_error("render", ValueError("first"), fatal=True)
        app.report_error("render", ValueError("second"), fatal=True)
        lines = read_log(log_file)
        assert len(lines) == 2, f"消息变化应再记一条，实际 {len(lines)}"
        print("  [OK] 错误消息变化后会重新记录")
    finally:
        D.App.report_error = orig


def test_breaker_trips(tmpdir):
    print("== test_breaker_trips ==")
    log_file, orig = patch_log(None, tmpdir)
    try:
        app = fresh_app(log_file)
        tripped_at = None
        for i in range(1, D.MAX_CONSECUTIVE_ERRORS + 5):
            if app.report_error("render", RuntimeError("dead"), fatal=True):
                tripped_at = i
                break
        assert tripped_at == D.MAX_CONSECUTIVE_ERRORS, \
            f"应在第 {D.MAX_CONSECUTIVE_ERRORS} 次熔断，实际 {tripped_at}"
        print(f"  [OK] 连续 {tripped_at} 次失败后熔断（不再无限空转）")
    finally:
        D.App.report_error = orig


def test_transient_error_recovers(tmpdir):
    print("== test_transient_error_recovers ==")
    log_file, orig = patch_log(None, tmpdir)
    try:
        app = fresh_app(log_file)
        # 偶发单帧失败：远未到阈值，中间被成功帧清零
        for _ in range(D.MAX_CONSECUTIVE_ERRORS - 1):
            app.report_error("render", RuntimeError("hiccup"), fatal=True)
        app.clear_error()
        assert app._err_streak == 0, "成功帧应清零连续失败计数"
        assert app.last_error is None, "清零后 UI 不该继续显示错误"
        # 再失败一次，不应触发熔断
        assert not app.report_error("render", RuntimeError("hiccup"), fatal=True), \
            "偶发错误不应熔断"
        print("  [OK] 偶发故障被容忍，成功帧会清零计数")
    finally:
        D.App.report_error = orig


def test_save_failure_not_fatal(tmpdir):
    print("== test_save_failure_not_fatal ==")
    log_file, orig = patch_log(None, tmpdir)
    saved_cfg = os.path.join(tmpdir, "config.json")
    orig_path = D.CONFIG_PATH
    D.CONFIG_PATH = os.path.join(tmpdir, "no", "such", "dir", "config.json")
    try:
        app = fresh_app(log_file)
        ok = app.save()
        assert ok is False, "不可写路径应返回 False"
        assert app.last_error is not None and "config-save" in app.last_error, \
            f"存盘失败必须上报，实际 last_error={app.last_error}"
        assert app._err_streak == 0, "存盘失败不该计入渲染熔断"
        print(f"  [OK] 存盘失败已上报: {app.last_error[:60]}...")
        print("  [OK] 存盘失败不触发渲染熔断")
    finally:
        D.CONFIG_PATH = orig_path
        D.App.report_error = orig


def test_corrupt_config_reports_and_recovers(tmpdir):
    print("== test_corrupt_config_reports_and_recovers ==")
    log_file, orig = patch_log(None, tmpdir)
    cfg = os.path.join(tmpdir, "config.json")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write("{ this is not json")
    orig_path = D.CONFIG_PATH
    D.CONFIG_PATH = cfg
    try:
        app = fresh_app(log_file)
        ok = app.load()
        assert ok is False, "损坏配置应返回 False"
        assert app.cfg == D.DEFAULTS, "损坏配置必须回退到 DEFAULTS"
        assert app.last_error is not None and "config-load" in app.last_error, \
            "损坏配置必须留下记录，否则用户不知道为何设置被重置"
        print(f"  [OK] 损坏配置回退 DEFAULTS 且已记录: {app.last_error[:50]}...")
    finally:
        D.CONFIG_PATH = orig_path
        D.App.report_error = orig


def test_non_dict_config(tmpdir):
    print("== test_non_dict_config ==")
    log_file, orig = patch_log(None, tmpdir)
    cfg = os.path.join(tmpdir, "config.json")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write("[1, 2, 3]")          # 合法 JSON，但不是对象
    orig_path = D.CONFIG_PATH
    D.CONFIG_PATH = cfg
    try:
        app = fresh_app(log_file)
        ok = app.load()
        assert ok is False, "JSON 数组不是合法配置"
        assert app.cfg == D.DEFAULTS, "应回退到 DEFAULTS"
        print("  [OK] 非对象配置被拒绝并回退（不会 .items() 崩溃）")
    finally:
        D.CONFIG_PATH = orig_path
        D.App.report_error = orig


def test_clean_exit_has_no_log(tmpdir):
    print("== test_clean_exit_has_no_log ==")
    log_file, orig = patch_log(None, tmpdir)
    try:
        app = fresh_app(log_file)
        # 全程无错误 -> 不应产生 error.log
        assert not os.path.exists(log_file), "正常运行不该产生 error.log"
        print("  [OK] 正常运行不产生 error.log（不制造噪音）")
    finally:
        D.App.report_error = orig


def test_reporter_never_raises(tmpdir=None):
    """错误上报路径本身绝不能抛异常。

    曾经：测试用 App.__new__ 构造实例（未跑 __init__），一旦配置损坏触发
    load() -> report_error()，就在访问 _err_logged 时 AttributeError，
    原始错误被这个新异常彻底盖掉，故障变得无法诊断。
    """
    print("== test_reporter_never_raises ==")
    bare = D.App.__new__(D.App)      # 故意不跑 __init__：字段全部缺失
    try:
        bare.report_error("render", ValueError("before init"), fatal=True)
        bare.report_error("render", ValueError("before init"), fatal=True)
        bare.clear_error()
        print("  [OK] 字段缺失时 report_error / clear_error 均不抛异常")
    except Exception as exc:
        raise AssertionError(f"错误上报路径抛异常了: {type(exc).__name__}: {exc}")

    # 落盘目标不可写时也必须吞掉，不能把原错误弄丢
    orig = D.ERROR_LOG_PATH
    D.ERROR_LOG_PATH = os.path.join(tmpdir or ".", "no", "such", "dir", "e.log")
    try:
        app = fresh_app(None)
        app.report_error("render", ValueError("unwritable log"), fatal=True)
        assert app.last_error is not None, "日志写不了也应保住 last_error"
        print("  [OK] 日志文件不可写时静默降级，last_error 仍可用")
    finally:
        D.ERROR_LOG_PATH = orig


def test_ui_banner_shows_and_retracts(tmpdir=None):
    """UI 测试不需要临时目录，但为保持统一签名仍接收该参数。"""
    print("== test_ui_banner_shows_and_retracts ==")
    try:
        from fx_ui import ControlPanel
    except Exception as e:
        print(f"  [SKIP] 无法构建面板: {e}")
        return

    app = D.App()
    ui = ControlPanel(app)
    ui.root.withdraw()   # 不闪窗；withdraw 后子控件 winfo_ismapped 恒假，改查 pack

    def sync():
        err = getattr(app, "last_error", None)
        if err != ui._err_shown:
            ui._err_shown = err
            if err:
                ui.err_bar.config(text=f"注意  {err}\n详情见 error.log")
                ui.err_bar.pack(fill="x", before=ui._main)
            else:
                ui.err_bar.pack_forget()
        ui.root.update_idletasks()

    slaves0 = len(ui.root.pack_slaves())
    assert ui.err_bar not in ui.root.pack_slaves(), "无错误时横幅不该占位"
    print(f"  [OK] 无错误: 横幅未插入 (面板子控件 {slaves0} 个)")

    app.last_error = "render: ValueError: simulated failure"
    sync()
    slaves = ui.root.pack_slaves()
    assert ui.err_bar in slaves, "错误横幅没有插入"
    assert slaves.index(ui.err_bar) < slaves.index(ui._main), \
        "横幅必须插在内容区之前，否则会被挤到面板底部看不见"
    print("  [OK] 有错误: 横幅插入且位于内容区之前")
    h_with = ui.root.winfo_reqheight()

    app.last_error = None
    sync()
    assert ui.err_bar not in ui.root.pack_slaves(), "错误恢复后横幅应收回"
    h_without = ui.root.winfo_reqheight()
    assert h_with > h_without, "横幅应当真的占位"
    print(f"  [OK] 恢复后: 横幅收回 (面板高度 {h_with} -> {h_without} px)")

    ui.root.destroy()


def main():
    print("== error-reporting / failure-visibility verification ==")
    print(f"   熔断阈值 MAX_CONSECUTIVE_ERRORS = {D.MAX_CONSECUTIVE_ERRORS}")
    print()
    for fn in (test_logged_and_deduped,
               test_new_message_logged_again,
               test_breaker_trips,
               test_transient_error_recovers,
               test_save_failure_not_fatal,
               test_corrupt_config_reports_and_recovers,
               test_non_dict_config,
               test_clean_exit_has_no_log,
               test_reporter_never_raises,
               test_ui_banner_shows_and_retracts):
        with tempfile.TemporaryDirectory() as tmpdir:
            fn(tmpdir)
        print()
    print("== ERROR-REPORTING VERIFIED ==")


if __name__ == "__main__":
    main()
