# -*- coding: utf-8 -*-
"""解开 PyInstaller 产物，归档里逐个模块查新特性字串。

直接搜 exe 字节是**无效**的：代码被打进压缩的 PYZ 归档，原始字节里搜不到
任何中文字符串（连旧样式名也搜不到 —— 那不代表缺失）。
这个脚本走 PyInstaller 自己的 `ZlibArchiveReader`，把每个模块的字节码
解出来再查字符串常量。

用法：python tools/diag/exe_features.py [产物路径]
"""
import marshal
import sys
import types

from PyInstaller.archive.readers import CArchiveReader

EXE = sys.argv[1] if len(sys.argv) > 1 else "dist/DesktopFX.exe"

CHECKS = [
    ("新样式 · 赛博风", "赛博风"),
    ("新样式 · 折线圆环", "折线圆环"),
    ("新样式 · 三条线", "三条线"),
    ("新滑块 · 波动幅度", "波动幅度"),
    ("暂停 · pause-state 事件", "pause-state"),
    ("暂停 · on_pause_event 入口", "on_pause_event"),
    ("暂停 · _fix_layers_after_resume", "_fix_layers_after_resume"),
    ("对照 · 旧样式 呼吸圆环", "呼吸圆环"),
]


def collect_strings(obj, out):
    """把模块里的所有字符串常量收集起来（递归进嵌套的 code 对象）。"""
    for name in ("co_consts", "co_names"):
        for v in getattr(obj, name, ()) or ():
            if isinstance(v, str):
                out.append(v)
            elif isinstance(v, bytes):
                try:
                    out.append(v.decode("utf-8"))
                except Exception:
                    pass
            elif isinstance(v, types.CodeType):
                collect_strings(v, out)


def main():
    arch = CArchiveReader(EXE)
    print("产物: %s" % EXE)

    pyz_name = None
    for n in arch.toc.keys():
        if n.endswith(".pyz"):
            pyz_name = n
    if pyz_name is None:
        print("❌ 归档里没有 PYZ，内容:", list(arch.toc)[:20])
        return 1

    # PYZ 归档用 ZlibArchiveReader 读（它不支持 with，直接用）
    pyz = arch.open_embedded_archive(pyz_name)
    toc = pyz.toc
    print("PYZ 归档: %s   模块数: %d" % (pyz_name, len(toc)))
    per_module = {}
    for modname in toc:
        try:
            data = pyz.extract(modname)
        except Exception:
            continue
        if not isinstance(data, types.CodeType):
            continue
        out = []
        collect_strings(data, out)
        per_module[modname] = out

    blob = "\n".join(s for ss in per_module.values() for s in ss)

    # ⚠️ **入口脚本（desktop_fx）不在 PYZ 里** —— 它是 CArchive 的顶层条目
    # （PyInstaller 把 `a.scripts` 直接打进 EXE）。而且顶层条目存的是
    # **编译后的 .pyc 字节码**，不是源码文本 —— 所以不能按文本搜，
    # 得 marshal .loads 之后再取字符串常量。
    top_strings = []
    for entry in arch.toc:
        if entry.endswith(".pyz") or entry == "PYZ.pyz":
            continue
        try:
            data = arch.extract(entry)
        except Exception:
            continue
        if not isinstance(data, bytes):
            continue
        # 试两种形态：直接是 pyc（有 magic 头）或裸 marshal 的 code 对象
        for candidate in (data, data[16:] if len(data) > 16 else b""):
            try:
                code = marshal.loads(candidate)
            except Exception:
                continue
            if isinstance(code, types.CodeType):
                out = []
                collect_strings(code, out)
                top_strings.extend(out)
                break
    top_blob = "\n".join(top_strings)
    blob = blob + "\n" + top_blob

    print()
    ok = True
    for label, needle in CHECKS:
        hit = needle in blob
        where = ""
        if hit:
            for m, ss in per_module.items():
                if needle in ss:
                    where = "  ← %s" % m
                    break
            else:
                if needle in top_blob:
                    where = "  ← 入口脚本(desktop_fx)"
        print("  %-30s %s%s" % (label, "✅ 含" if hit else "❌ 无", where))
        if not hit:
            ok = False

    print()
    print("结论:", "✅ v1.5.0 的新特性都在产物里" if ok else "❌ 有特性缺失")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
