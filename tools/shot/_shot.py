# -*- coding: utf-8 -*-
"""纯 ctypes 全屏截图，用于实机验证覆盖层与面板效果。"""
import ctypes
import os
import struct
import zlib

u32dll = ctypes.WinDLL("user32", use_last_error=True)
g32 = ctypes.WinDLL("gdi32", use_last_error=True)

u32dll.GetDC.restype = ctypes.c_void_p
u32dll.GetDC.argtypes = [ctypes.c_void_p]
u32dll.GetSystemMetrics.restype = ctypes.c_int
u32dll.GetSystemMetrics.argtypes = [ctypes.c_int]
g32.CreateCompatibleDC.restype = ctypes.c_void_p
g32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
g32.CreateCompatibleBitmap.restype = ctypes.c_void_p
g32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
g32.SelectObject.restype = ctypes.c_void_p
g32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
g32.BitBlt.restype = ctypes.c_int
g32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                       ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
g32.GetDIBits.restype = ctypes.c_int
g32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                          ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
g32.DeleteObject.restype = ctypes.c_int
g32.DeleteObject.argtypes = [ctypes.c_void_p]
g32.DeleteDC.restype = ctypes.c_int
g32.DeleteDC.argtypes = [ctypes.c_void_p]
u32dll.ReleaseDC.restype = ctypes.c_int
u32dll.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

SRCCOPY = 0x00CC0020
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


def write_png(path, w, h, rgba_rows):
    raw = b"".join(b"\x00" + row for row in rgba_rows)

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def main(out="shot.png"):
    x = u32dll.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = u32dll.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = u32dll.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = u32dll.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    print("virtual screen:", x, y, w, h)

    sdc = u32dll.GetDC(0)
    mdc = g32.CreateCompatibleDC(sdc)
    bmp = g32.CreateCompatibleBitmap(sdc, w, h)
    g32.SelectObject(mdc, bmp)
    g32.BitBlt(mdc, 0, 0, w, h, sdc, x, y, SRCCOPY)

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h          # top-down
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0

    buf = (ctypes.c_ubyte * (w * h * 4))()
    g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)

    mv = memoryview(buf).cast("B")
    rows = []
    stride = w * 4
    for r in range(h):
        row = mv[r * stride:(r + 1) * stride]
        # BGRA -> RGBA
        a = bytearray(row)
        a[0::4], a[2::4] = row[2::4], row[0::4]
        rows.append(bytes(a))

    write_png(out, w, h, rows)
    print("saved", os.path.abspath(out), w, "x", h)

    g32.DeleteObject(bmp)
    g32.DeleteDC(mdc)
    u32dll.ReleaseDC(0, sdc)


if __name__ == "__main__":
    main()
