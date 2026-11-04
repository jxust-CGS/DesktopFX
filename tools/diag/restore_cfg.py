# -*- coding: utf-8 -*-
"""把被诊断脚本改动的配置键恢复成默认值。

诊断脚本（如 halo_live）为了隔离效果，会把 rain/snow/... 一大批特效关掉、
把光环打开 —— 那是为了测量，不是用户的选择。测完必须还原。

⚠️ 只动**被诊断脚本改过的那几个键**，其余（用户预设、颜色、素材路径等）
一律不碰 —— 配置里还有用户自己调过的东西。

用法：python tools/diag/restore_cfg.py
"""
import json
import os
import sys

ROOT = __file__.rsplit("tools", 1)[0].rstrip("/\\")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from _paths import atomic_write_json  # noqa: E402
import desktop_fx as d  # noqa: E402

# 诊断脚本动过的键（列出是为了"只还原这些"，避免误伤用户的其它设置）
TOUCHED = [
    "halo_enabled", "halo_style", "halo_color", "halo_size",
    "halo_sensitivity",
    "rain_enabled", "snow_enabled", "petal_enabled", "meteor_enabled",
    "spotlight_enabled", "paint_enabled", "wave_enabled",
    "win_ripple_enabled", "ripple_enabled", "pet_enabled",
]


def main():
    p = os.path.join(ROOT, "config.json")
    with open(p, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    changed = []
    for k in TOUCHED:
        if k in d.DEFAULTS and k in cfg and cfg[k] != d.DEFAULTS[k]:
            changed.append("%s: %r -> %r" % (k, cfg[k], d.DEFAULTS[k]))
            cfg[k] = d.DEFAULTS[k]
    if changed:
        atomic_write_json(p, cfg)
        print("已还原 %d 个键：" % len(changed))
        for line in changed:
            print("   ", line)
    else:
        print("无需还原（诊断脚本动过的键都已是默认值）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
