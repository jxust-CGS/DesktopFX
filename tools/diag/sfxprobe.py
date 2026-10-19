# -*- coding: utf-8 -*-
"""验证「点击音效」真的会响 —— 用 waveOut 的播放计数做客观判据。

音效没有视觉，屏幕像素判不了。这里换一个客观信号：项目里 wav 走
`_WavOutPool`（waveOut 直接混音）、mp3/wma 走 MCI，两条路都能从"有没有
真的起播"看出差别。所以直接问播放器：起播返回 True、池里计数增长。

用法：python tools/diag/sfxprobe.py
"""
import os
import sys
import time

ROOT = __file__.rsplit("tools", 1)[0].rstrip("/\\")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import fx_click  # noqa: E402
import desktop_fx as d  # noqa: E402


def main():
    cfg = dict(d.DEFAULTS)
    cfg.update({
        "click_sfx_enabled": True,
        "click_sfx_volume": 1.0,
        "click_sfx_source": "内置音效",
        "click_sfx_builtin": "",
    })
    items, rnd = fx_click.sfx_target(cfg)
    print("音效来源解析: %d 个文件，随机=%s" % (len(items), rnd))
    for p in items[:6]:
        print("   ", os.path.basename(p), os.path.getsize(p), "bytes")
    if not items:
        print("⚠️ 一个音效都没扫到（assets/sfx 没打包？）")
        return 1

    player = fx_click.VoicePlayer()
    ok = 0
    fail = []
    for p in items:
        v = 0.7
        try:
            started = player.play_path(p, overlap=True, volume=v)
        except Exception as exc:
            started = False
            fail.append("%s 抛异常 %s" % (os.path.basename(p), exc))
            continue
        if started:
            ok += 1
        else:
            fail.append("%s 起播失败" % os.path.basename(p))
        time.sleep(0.12)
    busy = player.voices_busy()
    time.sleep(2.5)
    idle = player.voices_busy()
    print("\n起播成功 %d / %d" % (ok, len(items)))
    print("播放中占用 %d 条；2.5 秒后 %d 条（0 = 正常回收）" % (busy, idle))
    # VoicePlayer 没有 stop()（句柄随进程退出回收）；这里只等池排空
    t0 = time.time()
    while time.time() - t0 < 3.0 and player.voices_busy():
        time.sleep(0.2)
    drained = player.voices_busy()
    if fail:
        print("失败明细:")
        for f in fail:
            print("   ", f)
    if ok == 0:
        print("结论: 失败 —— 所有音效都起不了")
        return 1
    if drained != 0:
        print("结论: 部分异常 —— 等待后仍有 %d 条句柄没回收" % drained)
        return 1
    print("结论: 通过 —— %d 个音效都能起播且句柄全部回收（音量通道有效）"
          % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())