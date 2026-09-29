"""X oturumunun boşta kalma süresi. Kilitliyken biriken süre açılışta sayılmaz."""

from __future__ import annotations

import ctypes
from ctypes import POINTER, c_int, c_ulong, c_void_p


class _XScreenSaverInfo(ctypes.Structure):
    _fields_ = [
        ("window", c_ulong),
        ("state", c_int),
        ("kind", c_int),
        ("since", c_ulong),
        ("idle", c_ulong),
        ("event_mask", c_ulong),
    ]


_x11 = None
_xss = None


def idle_seconds() -> float | None:
    global _x11, _xss
    try:
        if _x11 is None or _xss is None:
            _x11 = ctypes.CDLL("libX11.so.6")
            _xss = ctypes.CDLL("libXss.so.1")
            _x11.XOpenDisplay.argtypes = [c_void_p]
            _x11.XOpenDisplay.restype = c_void_p
            _x11.XDefaultRootWindow.argtypes = [c_void_p]
            _x11.XDefaultRootWindow.restype = c_ulong
            _x11.XCloseDisplay.argtypes = [c_void_p]
            _x11.XFree.argtypes = [c_void_p]
            _xss.XScreenSaverAllocInfo.restype = POINTER(_XScreenSaverInfo)
            _xss.XScreenSaverQueryInfo.argtypes = [c_void_p, c_ulong, POINTER(_XScreenSaverInfo)]
            _xss.XScreenSaverQueryInfo.restype = c_int
    except OSError:
        return None

    display = _x11.XOpenDisplay(None)
    if not display:
        return None
    info = None
    try:
        root = _x11.XDefaultRootWindow(display)
        info = _xss.XScreenSaverAllocInfo()
        if not info or not _xss.XScreenSaverQueryInfo(display, root, info):
            return None
        return float(info.contents.idle) / 1000.0
    finally:
        if info:
            _x11.XFree(ctypes.cast(info, c_void_p))
        _x11.XCloseDisplay(display)
