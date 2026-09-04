"""Rewrite the launcher .bat with CRLF endings and verify it is ASCII-only.

Why this helper exists: the Write tool emits LF, and cmd.exe cannot resolve
`goto` labels in an LF-only batch file. Run this after every edit to
启动桌面特效.bat.
"""
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
targets = [p for p in glob.glob(os.path.join(HERE, "*.bat"))
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
