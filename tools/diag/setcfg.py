# -*- coding: utf-8 -*-
"""打开/关闭配置里的三个点击特效（走 _paths.atomic_write_json，不直接覆写）。

用法：python tools/diag/setcfg.py on      # 文字+表情+音效 全开
      python tools/diag/setcfg.py off     # 全关
      python tools/diag/setcfg.py show
"""
import json
import os
import sys

ROOT = __file__.rsplit("tools", 1)[0].rstrip("/\\")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from _paths import atomic_write_json  # noqa: E402

CFG = os.path.join(ROOT, "config.json")
KEYS = ("click_text_enabled", "click_emoji_enabled", "click_sfx_enabled")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    with open(CFG, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    before = {k: cfg.get(k) for k in KEYS}
    if cmd == "on":
        for k in KEYS:
            cfg[k] = True
    elif cmd == "off":
        for k in KEYS:
            cfg[k] = False
    elif cmd != "show":
        print("用法: setcfg.py on|off|show")
        return 2
    after = {k: cfg.get(k) for k in KEYS}
    if after != before:
        atomic_write_json(CFG, cfg)
        print("已写入:", after)
    else:
        print("未改动:", after)
    # 顺带报一下同类互斥相关的键，免得"开了却只有一个生效"时看不出原因
    for k in ("click_text_life", "click_emoji_life", "click_sfx_volume",
              "click_sfx_source", "click_sfx_builtin"):
        print("  %-22s %r" % (k, cfg.get(k)))
    return 0


if __name__ == "__main__":
    sys.exit(main())