"""Rewrite the launcher .bat with CRLF endings and verify it is ASCII-only.

Why this helper exists: the Write tool emits LF, and cmd.exe cannot resolve
`goto` labels in an LF-only batch file. Run this after every edit to
启动桌面特效.bat.
"""
import glob
import os
import sys

# 本脚本住在 tools/ 子目录里：`sys.path[0]` 与 `__file__` 的父目录都不再是项目根。
# 向上找到含 desktop_fx.py 的目录（= 项目根）当基准，import 与输出路径才成立。
ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(ROOT, "desktop_fx.py")) and os.path.dirname(ROOT) != ROOT:
    ROOT = os.path.dirname(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
targets = [p for p in glob.glob(os.path.join(ROOT, "*.bat"))
           if not os.path.basename(p).startswith("_")]

if not targets:
    print("FAIL: no launcher .bat found")
    sys.exit(1)

ok = True
for path in targets:
    raw = open(path, "rb").read()
    raw = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    open(path, "wb").write(raw)

    check = open(path, "rb").read()
    crlf = check.count(b"\r\n")
    bare_lf = check.count(b"\n") - crlf
    non_ascii = sum(1 for b in check if b > 127)
    print(f"{os.path.basename(path)}: {len(check)} bytes  "
          f"CRLF={crlf} bare-LF={bare_lf} non-ASCII={non_ascii}")
    if bare_lf or non_ascii:
        ok = False

print("OK: launcher(s) normalised to CRLF + ASCII"
      if ok else "FAIL: launcher still has LF or non-ASCII bytes")
sys.exit(0 if ok else 1)
