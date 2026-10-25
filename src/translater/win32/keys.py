from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from translater.config import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, Hotkey

_MAPVK_VK_TO_VSC_EX = 4
_MOUSE_NAMES = {
    0x01: "ЛКМ",
    0x02: "ПКМ",
    0x04: "Средняя кнопка мыши",
    0x05: "Боковая кнопка мыши 1",
    0x06: "Боковая кнопка мыши 2",
}
_MODIFIER_ORDER = ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift"), (MOD_WIN, "Win"))

if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    _user32.MapVirtualKeyW.restype = wintypes.UINT
    _user32.GetKeyNameTextW.argtypes = [wintypes.LONG, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetKeyNameTextW.restype = ctypes.c_int


def is_key_down(vk: int) -> bool:
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


def key_name(vk: int) -> str:
    if vk in _MOUSE_NAMES:
        return _MOUSE_NAMES[vk]
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if sys.platform == "win32":
        name = _layout_key_name(vk)
        if name:
            return name
    return f"VK 0x{vk:02X}"


def format_hotkey(hotkey: Hotkey) -> str:
    parts = [label for bit, label in _MODIFIER_ORDER if hotkey.modifiers & bit]
    parts.append(key_name(hotkey.vk))
    return "+".join(parts)


def _layout_key_name(vk: int) -> str:
    scan = _user32.MapVirtualKeyW(vk, _MAPVK_VK_TO_VSC_EX)
    if not scan:
        return ""
    lparam = (scan & 0xFF) << 16
    if scan & 0xFF00:
        lparam |= 1 << 24
    buffer = ctypes.create_unicode_buffer(64)
    length = _user32.GetKeyNameTextW(lparam, buffer, len(buffer))
    return buffer.value[:length]
