"""Debug harness: run desktop_fx.main() under pythonw.exe and capture all
output / tracebacks to a file, because pythonw has no console.

Usage:  pythonw.exe _pwdbg.py
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "_pw.log")

_f = open(LOG, "w", encoding="utf-8", buffering=1)
sys.stdout = _f
sys.stderr = _f

print("=== pythonw debug run ===")
print("argv          :", sys.argv)
print("executable    :", sys.executable)
print("cwd           :", os.getcwd())
print("sys.path[0]   :", sys.path[0] if sys.path else None)
print("frozen        :", getattr(sys, "frozen", False))
print("stdout was    :", "None (pythonw)")
print("env AUTOKILL  :", os.environ.get("DESKTOP_FX_AUTOKILL"))
print("-" * 60)

try:
    import desktop_fx

    rc = desktop_fx.main()
    print("main() returned:", rc)
except BaseException:
    print("!!! EXCEPTION !!!")
    traceback.print_exc()
finally:
    print("-" * 60)
    print("end of run")
    _f.flush()
