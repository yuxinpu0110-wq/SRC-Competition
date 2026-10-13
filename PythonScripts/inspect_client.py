"""Capture only our Client window, including when on a different virtual desktop."""
import argparse
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int, required=True)
    args = parser.parse_args()
    u, g = c.windll.user32, c.windll.gdi32
    u.SetProcessDPIAware()
    handles = []
    callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def visit(hwnd, unused):
        pid = w.DWORD()
        u.GetWindowThreadProcessId(hwnd, c.byref(pid))
        title = c.create_unicode_buffer(256)
        u.GetWindowTextW(hwnd, title, 256)
        if pid.value == args.pid and title.value == 'Client':
            handles.append(hwnd)
        return True
    u.EnumWindows(callback_type(visit), 0)
    if len(handles) != 1:
        raise RuntimeError('Expected exactly one Client window for the selected PID')
    hwnd = handles[0]
    rect = w.RECT()
    u.GetClientRect(hwnd, c.byref(rect))
    width, height = rect.right, rect.bottom
    cloaked = w.DWORD()
    c.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, c.byref(cloaked), c.sizeof(cloaked))
    print(f'Client HWND={hwnd}, size={width}x{height}, DWM cloaked={cloaked.value}', flush=True)
    class Header(c.Structure):
        _fields_ = [('size', w.DWORD), ('width', w.LONG), ('height', w.LONG),
                    ('planes', w.WORD), ('bits', w.WORD), ('compression', w.DWORD),
                    ('image_size', w.DWORD), ('xppm', w.LONG), ('yppm', w.LONG),
                    ('used', w.DWORD), ('important', w.DWORD)]
    class Info(c.Structure):
        _fields_ = [('header', Header), ('colors', w.DWORD*3)]
    u.GetDC.restype = g.CreateCompatibleDC.restype = g.CreateDIBSection.restype = c.c_void_p
    g.SelectObject.restype = c.c_void_p
    u.GetDC.argtypes = [w.HWND]
    g.CreateCompatibleDC.argtypes = [c.c_void_p]
    g.CreateDIBSection.argtypes = [c.c_void_p, c.POINTER(Info), w.UINT,
                                  c.POINTER(c.c_void_p), c.c_void_p, w.DWORD]
    g.SelectObject.argtypes = [c.c_void_p, c.c_void_p]
    u.PrintWindow.argtypes = [w.HWND, c.c_void_p, w.UINT]
    g.DeleteObject.argtypes = g.DeleteDC.argtypes = [c.c_void_p]
    u.ReleaseDC.argtypes = [w.HWND, c.c_void_p]
    info = Info(Header(c.sizeof(Header), width, -height, 1, 32, 0, width*height*4, 0, 0, 0, 0))
    pixels = c.c_void_p()
    dc = u.GetDC(hwnd)
    memory = g.CreateCompatibleDC(dc)
    bitmap = g.CreateDIBSection(dc, c.byref(info), 0, c.byref(pixels), None, 0)
    old = g.SelectObject(memory, bitmap)
    try:
        if not u.PrintWindow(hwnd, memory, 3):
            raise RuntimeError('Client does not support background capture; do not click blindly')
        from PySide6.QtGui import QImage
        picture = QImage(c.string_at(pixels, width*height*4), width, height,
                         width*4, QImage.Format.Format_RGB32)
        from runtime_paths import ROOT
        target = ROOT/'client-inspect.png'
        if not picture.save(str(target)):
            raise RuntimeError('Capture save failed')
        print(target)
    finally:
        g.SelectObject(memory, old)
        g.DeleteObject(bitmap)
        g.DeleteDC(memory)
        u.ReleaseDC(hwnd, dc)


if __name__ == '__main__':
    main()
