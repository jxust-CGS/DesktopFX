# -*- coding: utf-8 -*-
"""诊断：播放声音时，`AudioLevel` 到底读到没有？

halo_live 报"帧间差异 0"有两种可能：
  A. 电平表读不到声音（音频链路问题）→ 光环一直是静态的
  B. 电平读到了但画面没变（绘制问题）
这个脚本只管 A：放一段声音，**直接读 AudioLevel 的值**。

用法：python tools/diag/tone_probe.py
"""
import os
import sys
import time
import winsound

ROOT = __file__.rsplit("tools", 1)[0].rstrip("/\\")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fx_halo import AudioLevel  # noqa: E402

FAILS = []


def check(cond, msg):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))
    if not cond:
        FAILS.append(msg)


def sfx_files():
    d = os.path.join(ROOT, "assets", "sfx")
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d))
            if f.lower().endswith(".wav")]


def main():
    al = AudioLevel()
    al.start()
    time.sleep(1.5)
    base = [al.level() for _ in range(6)]
    print("静音时电平: %s" % [round(v, 4) for v in base])
    check(max(base) < 0.05, "静音时电平接近 0（最大 %.4f）" % max(base))

    files = sfx_files()
    if not files:
        print("没有内置音效可播")
        return 1
    path = files[0]
    print("\n播放 %s（异步 + 循环）…" % os.path.basename(path))
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC
                       | winsound.SND_LOOP)
    time.sleep(0.6)
    hot = [al.level() for _ in range(10)]
    winsound.PlaySound(None, winsound.SND_PURGE)
    print("有声时电平: %s" % [round(v, 4) for v in hot])
    check(max(hot) > 0.05, "播放时电平明显大于 0（最大 %.4f）" % max(hot))
    check(max(hot) > max(base) * 2 + 0.02,
          "播放时比静音时高得多（%.4f vs %.4f）" % (max(hot), max(base)))
    al.stop()
    print("\n%s（失败 %d 项）" % ("全部通过" if not FAILS else "有失败",
                                  len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
