"""Pure classification plus a native Windows connectivity probe (no traffic)."""
WM_POWERBROADCAST=0x218
WM_QUERYENDSESSION=0x11
WM_ENDSESSION=0x16


def power_event(message,wparam):
    if message!=WM_POWERBROADCAST:
        return None
    return {4:"suspend",7:"resume",18:"resume"}.get(wparam)


def network_connected():
    import ctypes
    from ctypes import wintypes
    flags=wintypes.DWORD()
    probe=ctypes.WinDLL("wininet",use_last_error=True).InternetGetConnectedState
    probe.argtypes=[ctypes.POINTER(wintypes.DWORD),wintypes.DWORD]
    probe.restype=wintypes.BOOL
    return bool(probe(ctypes.byref(flags),0))
