# -*- coding: utf-8 -*-
"""
桌面特效 —— 主程序
    鼠标移动/点击会荡开水波，整块桌面飘落雨丝，雨滴触底溅起水花。

架构：
    主线程     tkinter 设置面板 + 事件分发
    渲染线程   全屏分层透明窗口（鼠标穿透），numpy 逐帧绘制
    托盘线程   系统托盘图标 + 全局热键 Ctrl+Alt+R
"""
import ctypes
import json
import os
import queue
import sys
import threading
import time
import winreg

from _win32 import user32, kernel32
from _paths import app_root

# 源码运行 = 项目目录；PyInstaller 冻结 = exe 所在目录（用户数据随 exe 走）
APP_DIR = app_root()
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

CONFIG_PATH = os.path.join(APP_DIR, "config.json")
USER_PRESETS_PATH = os.path.join(APP_DIR, "presets_user.json")
# pythonw 下没有控制台，print 全部消失。渲染/保存出错时必须落到文件，
# 否则故障对用户是完全不可见的（桌面一片空白，没有任何线索）。
ERROR_LOG_PATH = os.path.join(APP_DIR, "error.log")
# 连续失败达到这个次数就停止渲染并退出，而不是永远以 5 FPS 空转烧 CPU。
# 每帧失败会 sleep 0.2s，30 次 ≈ 6 秒，足够区分"偶发单帧异常"和"彻底坏了"。
MAX_CONSECUTIVE_ERRORS = 30

# 单实例互斥体：Local\ 前缀只在当前登录会话内唯一，
# 因此多用户/远程桌面各自能跑自己的一份，互不打架。
SINGLETON_MUTEX = r"Local\DesktopFX_Singleton_v1"
ERROR_ALREADY_EXISTS = 183

_singleton_handle = None

DEFAULTS = {
    "rain_enabled": True,
    "rain_amount": 280.0,
    "rain_speed": 900.0,
    "rain_length": 1.0,
    "wind": 0.12,
    "rain_color": "#5FA8D8",          # 深蓝高对比：白底 / 深底都清晰
    "rain_opacity": 0.85,
    "splash": True,
    "ripple_move": True,
    "ripple_click": True,
    "ripple_step": 24.0,
    "ripple_size": 145.0,
    "ripple_life": 0.95,
    "ripple_color": "#7FE7FF",
    "ripple_opacity": 0.85,
    "lightning": False,
    "fps": 60.0,
    # ---- 雪（与雨正交的独立特效层）----
    "snow_enabled": False,
    "snow_amount": 240.0,
    "snow_speed": 90.0,
    "snow_size": 1.0,
    "snow_drift": 0.06,
    "snow_color": "#EAF2FF",
    "snow_opacity": 0.9,
    # ---- 聚光灯（全屏压暗 + 鼠标处透明圆洞）----
    "spotlight_enabled": False,
    "spotlight_radius": 220.0,
    "spotlight_dim": 0.6,
    "spotlight_color": "#05070A",
    # ---- 窗口水波（窗口移动/最小化/恢复时触发涟漪，复用涟漪池）----
    "win_ripple_enabled": False,
    "win_ripple_size": 1.0,
    "win_ripple_life": 1.0,
    "win_ripple_strength": 0.7,
    "win_ripple_color": "#7FE7FF",
    "win_ripple_edge_density": 12,
}

PRESETS = {
    # 毛毛雨保留"细雨"身份（雨量最少 / 雨丝最短 / 速度最慢），但颜色与不透明度
    # 必须过 _verify_visibility 的 50 分门槛 —— 原值 #8FBADC/0.5 在白底只有 36 分，
    # 白桌面的用户点了会以为程序坏了。
    "毛毛雨": dict(rain_enabled=True, rain_amount=130, rain_speed=430, rain_length=0.6, wind=0.05,
                rain_opacity=0.72, splash=False, rain_color="#6FA5CF"),
    "中雨": dict(rain_enabled=True, rain_amount=280, rain_speed=900, rain_length=1.0, wind=0.12,
               rain_opacity=0.85, splash=True, rain_color="#5FA8D8"),
    "暴雨": dict(rain_enabled=True, rain_amount=620, rain_speed=1520, rain_length=1.6, wind=0.5,
               rain_opacity=0.95, splash=True, rain_color="#3F88C0"),
    "斜风细雨": dict(rain_enabled=True, rain_amount=210, rain_speed=720, rain_length=0.9, wind=0.72,
                 rain_opacity=0.8, splash=True, rain_color="#7DAFD0"),
    "夜雨（青）": dict(rain_enabled=True, rain_amount=340, rain_speed=1050, rain_length=1.2, wind=0.2,
                  rain_opacity=0.85, splash=True, rain_color="#7FE7C4"),
    "暖雨（金）": dict(rain_enabled=True, rain_amount=240, rain_speed=820, rain_length=1.0, wind=0.1,
                  rain_opacity=0.78, splash=True, rain_color="#E8B26A"),
    "深蓝高对比": dict(rain_enabled=True, rain_amount=320, rain_speed=1100, rain_length=1.3, wind=0.15,
                  rain_opacity=0.9, splash=True, rain_color="#5FA8D8"),
    "石墨冷灰": dict(rain_enabled=True, rain_amount=300, rain_speed=1000, rain_length=1.2, wind=0.1,
                 rain_opacity=0.88, splash=True, rain_color="#6A8090"),
    # ---- 雪：独立特效层，可与闪电等任意组合 ----
    "初雪": dict(rain_enabled=False, snow_enabled=True, snow_amount=180,
              snow_speed=70, snow_size=1.0, snow_drift=0.10,
              snow_color="#EAF2FF", snow_opacity=0.9, lightning=False),
    "暴雪": dict(rain_enabled=False, snow_enabled=True, snow_amount=560,
              snow_speed=130, snow_size=1.2, snow_drift=0.35,
              snow_color="#FFFFFF", snow_opacity=0.95, lightning=False),
    "雪夜": dict(rain_enabled=False, snow_enabled=True, snow_amount=260,
              snow_speed=85, snow_size=1.0, snow_drift=0.12,
              snow_color="#DCEBFF", snow_opacity=0.9, lightning=True),
    # ---- 聚光灯：与雨/雪/涟漪自由组合 ----
    "聚光灯": dict(rain_enabled=False, snow_enabled=False,
                spotlight_enabled=True, spotlight_radius=240,
                spotlight_dim=0.62, spotlight_color="#04060A"),
    "舞台光": dict(rain_enabled=False, snow_enabled=False,
                spotlight_enabled=True, spotlight_radius=200,
                spotlight_dim=0.70, spotlight_color="#04060A"),
    # ---- 窗口水波：桌面像水面，拖动/收起窗口荡开涟漪 ----
    "水面": dict(rain_enabled=False, snow_enabled=False,
              win_ripple_enabled=True, win_ripple_size=1.2,
              win_ripple_life=1.1, win_ripple_strength=0.8,
              win_ripple_color="#7FE7FF", win_ripple_edge_density=16,
              ripple_move=True, ripple_click=True,
              ripple_color="#7FE7FF", ripple_opacity=0.7),
}

# 预设方案下沉到各特效页内部：每个特效页显示自己的预设方案
PRESET_GROUPS = {
    "下雨": ["毛毛雨", "中雨", "暴雨", "斜风细雨", "夜雨（青）", "暖雨（金）",
           "深蓝高对比", "石墨冷灰"],
    "下雪": ["初雪", "暴雪", "雪夜"],
    "聚光灯": ["聚光灯", "舞台光"],
    "窗口水波": ["水面"],
}


class App:
    def __init__(self):
        # 错误上报字段必须先于 load() 初始化 —— 配置损坏时 load 会立刻上报
        self.last_error = None
        self._err_streak = 0
        self._err_logged = None

        self.cfg = dict(DEFAULTS)
        self.load()
        self.user_presets = self._load_user_presets()
        self.snapshot = dict(self.cfg)      # 渲染线程只读副本，整体替换保证原子

        # 托盘产出的事件先进 _raw_events，由泵线程写进 error.log 之后再转发到
        # self.events 供控制面板消费。分开的原因是：控制面板可能被隐藏、也可能
        # 压根还没启动，而"托盘窗口建不起来"恰恰是最需要留下痕迹的致命错误 ——
        # 否则用户只会看到"图标没了但雨还在下"，error.log 却干干净净。
        self._raw_events = queue.Queue()
        self.events = queue.Queue()
        self._evt_thread = threading.Thread(target=self._pump_events,
                                            name="fx-events", daemon=True)
        self._evt_thread.start()

        self.stop_evt = threading.Event()
        self.paused = False
        self._autosave_thread = None
        self.fps = 0.0
        self.cost_ms = 0.0
        self.overlay = None
        self.tray = None
        self._thread = None
        self._last_color = None
        self._last_ripple_color = None

    # ---------------------------------------------------------------- 事件泵
    def _pump_events(self):
        """托盘事件 -> 落盘 error.log -> 转发给控制面板。

        致命错误（托盘窗口建不起来、热键被占用）在这里就地记录，不依赖控制面板
        是否在运行。否则面板一旦没起来，这些错误就凭空蒸发了。
        """
        while True:
            item = self._raw_events.get()
            if item is None:
                break
            kind, payload = item
            if kind == "tray-failed":
                self.report_error("tray", RuntimeError(
                    "托盘窗口创建失败，托盘图标与右键菜单不可用"))
            elif kind == "hotkey-failed":
                self.report_error("hotkey", RuntimeError(
                    "Ctrl+Alt+R 已被其他程序占用，暂停快捷键不可用"))
            self.events.put((kind, payload))

    # ---------------------------------------------------------------- 错误上报
    def report_error(self, where, exc, fatal=False):
        """记录一次错误。fatal=True 时计入连续失败，返回 True 表示应当放弃。

        要点：
        - 写 error.log（pythonw 无控制台，这是唯一可靠出口）
        - 同一条消息只记首次，避免每帧刷盘
        - last_error 供控制面板显示，UI 轮询即可
        - 存盘失败用 fatal=False：不该因为它就把渲染熔断
        """
        # 错误上报路径本身绝不能抛异常：否则既丢了原始错误、又多一次崩溃，
        # 故障会变得完全不可诊断。所以这里用 getattr 兜底而不是直接取属性。
        self.last_error = msg = f"{where}: {type(exc).__name__}: {exc}"
        if fatal:
            self._err_streak = getattr(self, "_err_streak", 0) + 1

        if msg != getattr(self, "_err_logged", None):
            self._err_logged = msg
            try:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(f"[{stamp}] {msg}\n")
            except Exception:
                pass          # 连日志都写不了，至少别把原错误也弄丢
            try:
                print(f"[error] {msg}", flush=True)
            except Exception:
                pass          # pythonw 下 stdout 可能是 None

        return getattr(self, "_err_streak", 0) >= MAX_CONSECUTIVE_ERRORS

    def clear_error(self):
        """一帧成功即清零连续失败计数。"""
        if getattr(self, "_err_streak", 0):
            self._err_streak = 0
            self.last_error = None

    # ---------------------------------------------------------------- 配置
    def load(self):
        """读入已存在的键，未知键忽略（配置 schema 演进时旧文件仍可用）。
        文件损坏时回退到 DEFAULTS —— 这是预期行为，但要留下记录，
        否则用户会发现"设置莫名其妙变回出厂值"且查不到原因。"""
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("config root is not an object")
            for k, v in data.items():
                if k in DEFAULTS:
                    self.cfg[k] = v
            return True
        except FileNotFoundError:
            return True          # 首次运行，用 DEFAULTS 即可
        except Exception as exc:
            self.report_error("config-load", exc)
            return False

    def save(self):
        """存盘失败不能静默：否则用户调了半天设置，重启后全部复原且毫无提示。
        常见诱因是 config.json 变成只读（网盘同步、权限问题）。"""
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            self.report_error("config-save", exc)
            return False

    def set(self, key, value):
        self.cfg[key] = value
        self.snapshot = dict(self.cfg)
        self._autosave_dirty = True

    def update(self, mapping):
        """批量更新：单次触发自动保存（不按字段数计）。"""
        self.cfg.update(mapping)
        self.snapshot = dict(self.cfg)
        self._autosave_dirty = True

    # ---------------------------------------------------------------- 自动保存
    def _autosave_loop(self):
        """每 1 秒轮询一次：若有 dirty 则写盘，崩溃 / 强杀也只丢最近 1 秒的改动。"""
        while not self.stop_evt.wait(1.0):
            if getattr(self, "_autosave_dirty", False):
                try:
                    self.save()
                except Exception as e:
                    print(f"[autosave] {e}", flush=True)
                self._autosave_dirty = False

    def start_autosave(self):
        if self._autosave_thread is not None:
            return
        self._autosave_thread = threading.Thread(
            target=self._autosave_loop, name="fx-autosave", daemon=True)
        self._autosave_thread.start()

    # ---------------------------------------------------------------- 开机自启
    _RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    _RUN_VALUE = "DesktopFX"

    def _autostart_cmd(self):
        """生成用于 HKCU\\Run 的命令行。

        冻结运行：直接注册 exe（窗口化、无控制台）；
        源码运行：指向 venv 旁的 pythonw.exe + desktop_fx.py（零控制台闪烁）。"""
        if getattr(sys, "frozen", False):
            return f'"{os.path.abspath(sys.executable)}"'
        here = APP_DIR
        venv_pyw = os.path.join(
            os.path.dirname(sys.executable), "pythonw.exe"
        )  # 当前运行解释器旁的 pythonw.exe（与 .bat 一致）
        script = os.path.join(here, "desktop_fx.py")
        return f'"{venv_pyw}" "{script}"'

    def get_autostart(self):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._RUN_KEY,
                                0, winreg.KEY_READ) as k:
                v, _ = winreg.QueryValueEx(k, self._RUN_VALUE)
                return bool(v)
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def set_autostart(self, enabled):
        try:
            # 子键可能不存在（极少见，但配置文件坏了时可能），用 CreateKeyEx 自建
            k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self._RUN_KEY,
                                   0, winreg.KEY_SET_VALUE)
            try:
                if enabled:
                    winreg.SetValueEx(k, self._RUN_VALUE, 0,
                                      winreg.REG_SZ, self._autostart_cmd())
                else:
                    try:
                        winreg.DeleteValue(k, self._RUN_VALUE)
                    except FileNotFoundError:
                        pass
            finally:
                k.Close()
            return True
        except OSError as e:
            print(f"[autostart] failed: {e}", flush=True)
            return False

    # ---------------------------------------------------------------- 预设
    def _load_user_presets(self):
        """读取用户自定义预设。文件损坏返回空字典（不阻断启动）。"""
        try:
            with open(USER_PRESETS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # 简单校验：值必须是 dict
                return {k: v for k, v in data.items()
                        if isinstance(v, dict) and v}
        except FileNotFoundError:
            pass
        except Exception as exc:
            self.report_error("user-presets-load", exc)
        return {}

    def _save_user_presets(self):
        """写入用户预设文件。失败时上报但不抛异常。"""
        try:
            with open(USER_PRESETS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.user_presets, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            self.report_error("user-presets-save", exc)
            return False

    def preset_names(self):
        """返回所有预设名称（内置在前，用户在后）。"""
        names = list(PRESETS.keys())
        names.extend(k for k in self.user_presets if k not in names)
        return names

    def is_user_preset(self, name):
        return name in self.user_presets

    def preset_page(self, name):
        """判断预设应归属哪个特效页。用户预设有 _page 标签；
        内置预设通过 PRESET_GROUPS 反查。"""
        if name in self.user_presets:
            return self.user_presets[name].get("_page", "其它")
        for page, names in PRESET_GROUPS.items():
            if name in names:
                return page
        return "其它"

    def presets_for_page(self, page):
        """返回属于指定特效页的所有预设名称。"""
        built = [n for n in PRESET_GROUPS.get(page, []) if n in PRESETS]
        user = [n for n, v in self.user_presets.items()
                if v.get("_page", "其它") == page]
        return built + user

    def apply_preset(self, name):
        p = PRESETS.get(name)
        if p is None:
            p = getattr(self, "user_presets", {}).get(name)
        if not p:
            return
        # 下划线开头的键是内部元数据，不要写进 cfg
        self.cfg.update({k: v for k, v in p.items() if not k.startswith("_")})
        self.snapshot = dict(self.cfg)
        self._autosave_dirty = True

    def save_preset(self, name, page):
        """把当前配置保存为用户预设。name 去空；不能与内置预设或已有自定义预设重名
        （重名会静默覆盖第一个，表现为"新增却覆盖了已有预设"，这里改为明确报错）。"""
        name = (name or "").strip()
        if not name:
            return False, "名称不能为空"
        if name in PRESETS:
            return False, f"“{name}”与内置预设重名"
        if name in self.user_presets:
            return False, f"“{name}”已存在，请换个名称"
        snap = {k: self.cfg[k] for k in DEFAULTS if k in self.cfg}
        snap["_page"] = page
        self.user_presets[name] = snap
        ok = self._save_user_presets()
        return ok, "保存成功" if ok else "写入预设文件失败"

    def delete_preset(self, name):
        """删除用户预设；内置预设不可删除。"""
        if name not in self.user_presets:
            return False, "只能删除用户自定义预设"
        self.user_presets.pop(name, None)
        ok = self._save_user_presets()
        return ok, "删除成功" if ok else "写入预设文件失败"

    # ---------------------------------------------------------------- 生命周期
    def start(self):
        self._thread = threading.Thread(target=self._render_loop,
                                        name="fx-render", daemon=True)
        self._thread.start()
        self.start_autosave()

        from fx_tray import TrayHost
        self.tray = TrayHost(self._raw_events)

        def _tray_main():
            try:
                self.tray.run()
            except BaseException:
                # 托盘线程是 daemon：一旦抛异常就静默死亡，窗口随之销毁、图标消失，
                # 而渲染线程照常在跑。pythonw 没有 stderr，异常会彻底不可见 ——
                # 用户只会看到"图标没了但雨还在下"。必须在这里落盘。
                self.report_error("tray", sys.exc_info()[1])
                raise

        threading.Thread(target=_tray_main, name="fx-tray", daemon=True).start()

    def toggle_pause(self):
        self.paused = not self.paused
        return self.paused

    def quit(self):
        self.stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.save()

    # ---------------------------------------------------------------- 渲染
    def _render_loop(self):
        # 延迟导入，保证 numpy 缺失时能在主线程友好提示
        import numpy as np
        from fx_winlayer import Overlay
        from fx_scene import Scene

        ov = Overlay()
        ov.create()
        self.overlay = ov
        w, h = ov.w, ov.h

        # 场景合成器：雨 / 雪 / 涟漪 / 闪电 / 鼠标都收进一个可组合的场景
        scene = Scene(w, h)
        scene.set_origin(ov.x, ov.y)

        last = time.perf_counter()
        frame = 0
        fps_ema = 60.0

        while not self.stop_evt.is_set():
            t0 = time.perf_counter()
            dt = min(max(t0 - last, 0.0005), 0.05)
            last = t0
            cfg = self.snapshot

            if self.paused:
                if self.overlay is not None:
                    try:
                        ov.u32[:] = 0
                        ov.present()
                    except Exception:
                        pass
                ov.pump()
                self.paused_idle = True
                time.sleep(0.05)
                continue

            try:
                if frame % 30 == 0 and ov.resize_if_needed():
                    w, h = ov.w, ov.h
                    scene.resize(w, h)
                    scene.set_origin(ov.x, ov.y)
                frame += 1

                u32 = ov.u32
                u32[:] = 0

                # 整帧合成交给 Scene —— 哪些特效层参与由 cfg 开关决定
                scene.cfg = cfg
                scene.step(dt, u32, w, h)

                ov.present()
                self.clear_error()
            except Exception as exc:      # 单帧异常不终止整个特效
                # 偶发单帧异常（如改分辨率瞬间 DIB 正在重建）应当容忍，
                # 但连续失败说明彻底坏了 —— 记日志并在熔断后干净退出，
                # 不能默默以 5 FPS 永远空转。
                if self.report_error("render", exc, fatal=True):
                    self.report_error(
                        "fatal", RuntimeError(
                            f"连续 {MAX_CONSECUTIVE_ERRORS} 帧渲染失败，已停止。"
                            f"详情见 {ERROR_LOG_PATH}"))
                    self.stop_evt.set()
                    break
                time.sleep(0.2)

            ov.pump()

            # ---------------- 帧率控制与统计
            cost = time.perf_counter() - t0
            self.cost_ms = self.cost_ms * 0.9 + (cost * 1000.0) * 0.1
            fps_ema = fps_ema * 0.92 + (1.0 / max(dt, 1e-4)) * 0.08
            self.fps = fps_ema

            target = 1.0 / max(20.0, min(144.0, float(cfg["fps"])))
            rest = target - cost
            if rest > 0.001:
                time.sleep(rest)

        try:
            ov.u32[:] = 0
            ov.present()
        except Exception:
            pass
        ov.destroy()

    def _ripple_color(self, hex_to_bgr):
        c = self.snapshot["ripple_color"]
        if self._last_ripple_color != c:
            self._ripple_color_cache = hex_to_bgr(c)
            self._last_ripple_color = c
        return self._ripple_color_cache


def _set_dpi_awareness():
    """声明进程 DPI 感知。Win10 125%/150% 缩放下，未声明的进程会被 DPI 虚拟化，
    导致 GetSystemMetrics/SM_CXVIRTUALSCREEN 返回的尺寸按 1.0 缩放错位，
    覆盖层只盖住屏幕一部分。按"新→旧"顺序回退。"""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(-4))   # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        return "per-monitor-v2"
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return "per-monitor"
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "unaware"


def _acquire_singleton():
    """抢占单实例锁。True = 本进程是第一个实例，可以继续启动。"""
    global _singleton_handle
    h = kernel32.CreateMutexW(None, True, SINGLETON_MUTEX)
    if not h:
        # 创建失败就放行：保护机制绝不能把用户挡在门外
        print("[boot] warning: CreateMutexW failed, skipping singleton guard",
              flush=True)
        return True
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(h)
        return False
    _singleton_handle = h   # 持有句柄直到进程退出，互斥体才不会被释放
    return True


def _notify_existing_instance():
    """把已经在跑的那个实例的控制面板叫出来。成功返回 True。"""
    try:
        from fx_winlayer import _CLASS_NAME, show_panel_message
    except Exception as e:
        print(f"[boot] cannot import fx_winlayer: {e}", flush=True)
        return False
    msg = show_panel_message()
    if not msg:
        return False
    hwnd = user32.FindWindowW(_CLASS_NAME, None)
    if not hwnd:
        return False
    return bool(user32.PostMessageW(hwnd, msg, 0, 0))


def _existing_tray_alive():
    """旧实例的托盘窗口还在吗。

    False = 僵尸：进程还占着单实例互斥体（渲染线程照跑），但托盘图标和控制面板
    都已经失效。用户既看不到图标也退不掉，此时提示"已经在运行中"纯属误导。
    """
    try:
        from fx_tray import TrayHost
        return bool(user32.FindWindowW(TrayHost.CLS_NAME, None))
    except Exception:
        # 查不到就按"活着"处理：宁可不提示，也不要误报吓到用户
        return True


def main():
    print("[boot] desktop-fx starting...", flush=True)

    if not _acquire_singleton():
        print("[boot] another instance is already running", flush=True)
        if not _notify_existing_instance():
            # 互斥体被占着，却既唤不出面板、也找不到托盘窗口 —— 僵尸实例。
            # 这时候再说"看看右下角的托盘图标"是纯粹的误导：那个图标根本不存在。
            if _existing_tray_alive():
                ctypes.windll.user32.MessageBoxW(
                    0, "桌面特效 已经在运行中。\n\n"
                       "（看看屏幕右下角的托盘图标）", "桌面特效", 0x40)
            else:
                print("[boot] stale instance: mutex held, tray window gone",
                      flush=True)
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "检测到一个残留的桌面特效进程：\n"
                    "它仍然占着运行锁，但托盘图标和控制面板都已经失效。\n\n"
                    "请双击本目录下的 结束残留进程.pyw 自动清理，\n"
                    "或在任务管理器中结束所有 pythonw.exe 后重试。",
                    "桌面特效 - 无法启动", 0x10)
        return 0

    dpi_mode = _set_dpi_awareness()
    print(f"[boot] dpi awareness: {dpi_mode}", flush=True)
    try:
        import numpy  # noqa: F401
    except ImportError:
        ctypes.windll.user32.MessageBoxW(
            0,
            "缺少依赖 numpy。\n\n请在程序目录执行：\n"
            "pip install numpy\n\n（如网络不佳可使用国内镜像：\n"
            "pip install -i https://pypi.tuna.tsinghua.edu.cn/simple numpy）",
            "桌面特效 - 无法启动", 0x10)
        return 1
    print("[boot] numpy ok", flush=True)

    app = App()
    print("[boot] app created, starting threads...", flush=True)
    app.start()
    print("[boot] render + tray threads launched", flush=True)

    if os.environ.get("DESKTOP_FX_AUTOKILL"):
        # 调试用：跑 N 秒后自动退出（毫秒）
        import threading as _th
        def _killer():
            import time
            time.sleep(int(os.environ["DESKTOP_FX_AUTOKILL"]) / 1000.0)
            print(f"[auto-kill] fps={app.fps:.1f} cost={app.cost_ms:.1f}ms", flush=True)
            os._exit(0)
        _th.Thread(target=_killer, daemon=True).start()
        from fx_ui import ControlPanel
        ui = ControlPanel(app)
        ui.run()
    else:
        from fx_ui import ControlPanel
        ui = ControlPanel(app)
        ui.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
