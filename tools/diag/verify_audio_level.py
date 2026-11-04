# -*- coding: utf-8 -*-
"""验证 `AudioLevel`：能不能真读到系统音频电平，包络行为对不对。

用真实系统跑（要开着音乐才有意义），所以判据分两类：
  A. **不依赖外部状态**的判据（任何时候都成立）：
     - 线程能起、能停
     - level() 恒在 0~1
     - 包络单调性：喂一串上升/下降的假峰值，验证上升快、下降慢
  B. **依赖系统在发声**的判据：读到过非零电平（没放音乐时会跳过并说明）

用法：python tools/diag/verify_audio_level.py [秒数]
"""
import os
import sys
import time

ROOT = __file__.rsplit("tools", 1)[0].rstrip("/\\")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fx_halo import AudioLevel  # noqa: E402

FAILS = []


def check(cond, msg):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))
    if not cond:
        FAILS.append(msg)


def test_envelope():
    """包络：上升快、下降慢（不依赖外部音频）。"""
    print("包络行为：")
    al = AudioLevel()
    al._floor = 0.0
    al._ceil = 1.0
    al._level = 0.0
    # 连续喂 0.9 -> 应该很快上去
    for _ in range(6):
        al._push(0.9)
    up = al._level
    check(up > 0.5, "上升快：喂 6 次 0.9 后电平=%.3f（>0.5）" % up)
    # 突然喂 0 -> 应该缓慢下降
    al._push(0.0)
    one_down = al._level
    check(one_down < up, "下降慢：单次 0 之后电平=%.3f（< 上升后的 %.3f）"
          % (one_down, up))
    for _ in range(40):
        al._push(0.0)
    check(al._level < one_down, "持续 0 会继续下降：%.3f" % al._level)

    print("归一化：")
    al2 = AudioLevel()
    al2._floor = 0.0
    al2._ceil = 1.0
    al2._push(0.5)
    mid = al2._level
    check(0.05 < mid < 0.95, "0.5 归一化后落在中段：%.3f" % mid)
    al2._push(5.0)
    check(al2._level <= 1.0, "超范围输入被钳到 1：%.3f" % al2._level)
    al2._push(-3.0)
    check(al2._level >= 0.0, "负值被钳到 0：%.3f" % al2._level)

    print("灵敏度：")
    al3 = AudioLevel()
    al3._floor = 0.0
    al3._ceil = 1.0
    al3.set_sensitivity(2.0)
    al3._push(0.4)
    s2 = al3._level
    al4 = AudioLevel()
    al4._floor = 0.0
    al4._ceil = 1.0
    al4.set_sensitivity(1.0)
    al4._push(0.4)
    check(s2 > al4._level, "灵敏度 2.0 比 1.0 更敏感：%.3f > %.3f"
          % (s2, al4._level))


def test_live(dur):
    """真机：起线程读系统电平。"""
    print("\n真机采集（%.1f 秒）：" % dur)
    al = AudioLevel()
    al.start()
    time.sleep(1.0)
    samples = []
    t0 = time.time()
    while time.time() - t0 < dur:
        samples.append(al.level())
        time.sleep(0.1)
    al.stop()
    ok = all(0.0 <= s <= 1.0 for s in samples)
    check(ok, "电平恒在 0~1（max=%.3f）" % max(samples))
    nz = [s for s in samples if s > 0.005]
    check(len(nz) > 0, "读到过非零电平：%d/%d 个采样（最大 %.4f）"
          % (len(nz), len(samples), max(samples)))
    if not nz:
        print("       （此刻系统没有声音 —— 放首歌再跑即可确认）")
    check(not al._started or al._thread is None, "stop() 后线程已收")


def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
    test_envelope()
    test_live(dur)
    print("\n%s（失败 %d 项）" % ("全部通过" if not FAILS else "有失败",
                                  len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
