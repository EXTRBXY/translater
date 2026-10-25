from __future__ import annotations

import ctypes
import logging
import threading
from collections.abc import Callable, Mapping
from ctypes import wintypes

from translater.config import Hotkey

log = logging.getLogger(__name__)

_WM_QUIT = 0x0012
_WM_HOTKEY = 0x0312
_WM_APP_APPLY = 0x8000
_MOD_NOREPEAT = 0x4000
_PM_NOREMOVE = 0x0000
_APPLY_TIMEOUT_S = 5.0

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.UnregisterHotKey.restype = wintypes.BOOL
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.GetMessageW.restype = ctypes.c_int
_user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
    wintypes.UINT,
]
_user32.PeekMessageW.restype = wintypes.BOOL
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostThreadMessageW.restype = wintypes.BOOL
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class GlobalHotkeys:
    """RegisterHotKey on a dedicated message-loop thread. on_hotkey runs on that thread."""

    def __init__(self, on_hotkey: Callable[[str], None]) -> None:
        self._on_hotkey = on_hotkey
        self._call_lock = threading.Lock()
        self._ready = threading.Event()
        self._applied = threading.Event()
        self._pending: dict[str, Hotkey] = {}
        self._failed: dict[str, int] = {}
        self._thread_id = 0
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait()

    def set_bindings(self, bindings: Mapping[str, Hotkey]) -> dict[str, int]:
        """Replaces all bindings. Returns {name: win32_error} for bindings that failed."""
        with self._call_lock:
            self._pending = dict(bindings)
            self._applied.clear()
            if not _user32.PostThreadMessageW(self._thread_id, _WM_APP_APPLY, 0, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self._applied.wait(_APPLY_TIMEOUT_S):
                raise TimeoutError("hotkey thread did not respond")
            return dict(self._failed)

    def close(self) -> None:
        _user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
        self._thread.join(timeout=_APPLY_TIMEOUT_S)

    def _run(self) -> None:
        self._thread_id = _kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, _PM_NOREMOVE)  # creates the thread message queue
        self._ready.set()
        registered: dict[int, str] = {}
        try:
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == _WM_HOTKEY:
                    name = registered.get(msg.wParam)
                    if name is not None:
                        self._dispatch(name)
                elif msg.message == _WM_APP_APPLY:
                    registered = self._apply(registered)
        finally:
            for hotkey_id in registered:
                _user32.UnregisterHotKey(None, hotkey_id)

    def _apply(self, registered: dict[int, str]) -> dict[int, str]:
        for hotkey_id in registered:
            _user32.UnregisterHotKey(None, hotkey_id)
        result: dict[int, str] = {}
        failed: dict[str, int] = {}
        for hotkey_id, (name, hotkey) in enumerate(self._pending.items(), start=1):
            if _user32.RegisterHotKey(None, hotkey_id, hotkey.modifiers | _MOD_NOREPEAT, hotkey.vk):
                result[hotkey_id] = name
            else:
                failed[name] = ctypes.get_last_error()
        self._failed = failed
        self._applied.set()
        return result

    def _dispatch(self, name: str) -> None:
        try:
            self._on_hotkey(name)
        except Exception:
            log.exception("Hotkey handler failed")
