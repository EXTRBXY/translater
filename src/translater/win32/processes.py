from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

import psutil

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_EnumWindowsProc, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD


@dataclass(frozen=True)
class WindowedProcess:
    exe: str
    title: str


def windowed_processes() -> list[WindowedProcess]:
    """Processes with a visible titled window: the candidates for a game."""
    titles: dict[int, str] = {}

    @_EnumWindowsProc
    def visit(hwnd: int, _lparam: int) -> bool:
        length = _user32.GetWindowTextLengthW(hwnd)
        if length and _user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in titles:
                buffer = ctypes.create_unicode_buffer(length + 1)
                _user32.GetWindowTextW(hwnd, buffer, len(buffer))
                titles[pid.value] = buffer.value
        return True

    _user32.EnumWindows(visit, 0)
    result: dict[str, WindowedProcess] = {}
    for pid, title in titles.items():
        try:
            exe = psutil.Process(pid).name()
        except psutil.Error:
            continue
        result.setdefault(exe.lower(), WindowedProcess(exe, title))
    return sorted(result.values(), key=lambda p: p.exe.lower())


def find_pid(exe: str) -> int | None:
    target = exe.lower()
    for process in psutil.process_iter(["name"]):
        name = process.info.get("name")
        if name and name.lower() == target:
            return process.pid
    return None
