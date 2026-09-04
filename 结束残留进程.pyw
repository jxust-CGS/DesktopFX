# -*- coding: utf-8 -*-
"""结束残留的桌面特效进程。

什么时候用：托盘图标消失了，但双击启动时提示"已经在运行中"。
这说明有个进程还占着单实例锁、界面却已经失效（僵尸实例）。

双击本文件即可（.pyw 用 pythonw 运行，不弹黑色控制台窗口）。
"""
import ctypes
import subprocess

# 只杀命令行里带 desktop_fx.py 的 pythonw，绝不误伤其他 Python 程序
_PS = (
    "$p = Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
    "Where-Object { $_.CommandLine -like '*desktop_fx.py*' }; "
    "if ($p) { $p | ForEach-Object { "
    "Stop-Process -Id $_.ProcessId -Force; $_.ProcessId } }"
)


def main():
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", _PS],
                             capture_output=True, text=True, timeout=60)
        pids = [ln.strip() for ln in (out.stdout or "").splitlines()
                if ln.strip().isdigit()]
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(
            0, "清理失败：\n%r" % (exc,), "桌面特效 - 清理残留进程", 0x10)
        return

    if pids:
        msg = ("已结束 %d 个残留进程：\n%s\n\n"
               "现在可以重新双击 启动桌面特效.bat 了。"
               % (len(pids), "、".join("PID " + p for p in pids)))
    else:
        msg = "没有找到残留的桌面特效进程。\n\n可以直接启动。"

    ctypes.windll.user32.MessageBoxW(0, msg, "桌面特效 - 清理残留进程", 0x40)


main()
