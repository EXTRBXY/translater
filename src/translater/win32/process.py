from __future__ import annotations

import ctypes
from ctypes import wintypes

_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
_ERROR_ALREADY_EXISTS = 183

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.SetPriorityClass.restype = wintypes.BOOL
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


def set_below_normal_priority() -> None:
    if not _kernel32.SetPriorityClass(_kernel32.GetCurrentProcess(), _BELOW_NORMAL_PRIORITY_CLASS):
        raise ctypes.WinError(ctypes.get_last_error())


class SingleInstance:
    def __init__(self, name: str) -> None:
        ctypes.set_last_error(0)
        self._handle = _kernel32.CreateMutexW(None, False, name)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.acquired = ctypes.get_last_error() != _ERROR_ALREADY_EXISTS

    def close(self) -> None:
        if self._handle:
            _kernel32.CloseHandle(self._handle)
            self._handle = None
