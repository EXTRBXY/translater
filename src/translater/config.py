from __future__ import annotations

import itertools
import json
import logging
import os
import sys
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

APP_DIR_NAME = "Translater"

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MODIFIER_NAMES: dict[str, int] = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
MOUSE_VKS = frozenset({0x01, 0x02, 0x04, 0x05, 0x06})
MODIFIER_VKS = frozenset({0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5})

UNLOAD_DELAY_RANGE_S = (0, 600)
MAX_EXE_NAME = 260
MAX_VOICE_NAME = 200
MAX_RECORD_RANGE_S = (1, 60)
MONITOR_VOLUME_RANGE = (0, 100)
SPEECH_THRESHOLD_RANGE = (10, 90)
CHAT_OPACITY_RANGE = (30, 100)


@dataclass(frozen=True)
class RecognitionMode:
    label: str
    beam_size: int


RECOGNITION_MODES: dict[str, RecognitionMode] = {
    "speed": RecognitionMode("Скорость", 2),
    "quality": RecognitionMode("Качество", 5),
}


@dataclass(frozen=True)
class Hotkey:
    vk: int
    modifiers: int = 0

    def to_json(self) -> dict[str, Any]:
        return {"vk": self.vk, "modifiers": [name for name, bit in MODIFIER_NAMES.items() if self.modifiers & bit]}

    @classmethod
    def from_json(cls, data: Any) -> Hotkey:
        if not isinstance(data, dict):
            raise ValueError("hotkey must be an object")
        vk = data.get("vk")
        names = data.get("modifiers", [])
        if not _is_int(vk) or not 0 < vk < 0xFF or not isinstance(names, list):
            raise ValueError("invalid hotkey")
        modifiers = 0
        for name in names:
            if name not in MODIFIER_NAMES:
                raise ValueError(f"unknown modifier: {name!r}")
            modifiers |= MODIFIER_NAMES[name]
        return cls(vk, modifiers)


@dataclass(frozen=True)
class HotkeyConfig:
    incoming_toggle: Hotkey = Hotkey(0x78, MOD_CONTROL | MOD_ALT)
    outgoing_toggle: Hotkey = Hotkey(0x79, MOD_CONTROL | MOD_ALT)
    outgoing_hold: Hotkey = Hotkey(0x77)
    game_ptt: Hotkey = Hotkey(0x05)
    save_audio: Hotkey = Hotkey(0x7A, MOD_CONTROL | MOD_ALT)


GLOBAL_HOTKEYS = ("incoming_toggle", "outgoing_toggle", "save_audio")
_HELD_KEYS = ("outgoing_hold", "game_ptt")
# Hotkeys added in later versions: tried in order when the user's file conflicts with the new default.
_NEW_HOTKEY_CANDIDATES: dict[str, tuple[Hotkey, ...]] = {
    "game_ptt": (Hotkey(0x05), Hotkey(0x06), Hotkey(0x04), Hotkey(0x76)),
    "save_audio": tuple(
        Hotkey(vk, mods) for mods in (MOD_CONTROL | MOD_ALT, MOD_CONTROL | MOD_SHIFT) for vk in (0x7A, 0x7B)
    ),
}


@dataclass(frozen=True)
class AppConfig:
    hotkeys: HotkeyConfig = HotkeyConfig()
    unload_delay_s: int = 30
    max_record_s: int = 20
    game_exe: str = ""
    tts_voice: str = ""
    monitor_volume: int = 40
    speech_threshold: int = 35
    recognition: str = "speed"
    subtitles_overlay: bool = True
    chat_window: bool = False
    chat_on_top: bool = True
    chat_opacity: int = 90

    @property
    def beam_size(self) -> int:
        return RECOGNITION_MODES[self.recognition].beam_size

    def to_json(self) -> dict[str, Any]:
        return {
            "hotkeys": {f.name: getattr(self.hotkeys, f.name).to_json() for f in fields(HotkeyConfig)},
            "unload_delay_s": self.unload_delay_s,
            "max_record_s": self.max_record_s,
            "game_exe": self.game_exe,
            "tts_voice": self.tts_voice,
            "monitor_volume": self.monitor_volume,
            "speech_threshold": self.speech_threshold,
            "recognition": self.recognition,
            "subtitles_overlay": self.subtitles_overlay,
            "chat_window": self.chat_window,
            "chat_on_top": self.chat_on_top,
            "chat_opacity": self.chat_opacity,
        }


HOTKEY_LABELS: dict[str, str] = {
    "incoming_toggle": "Входящий перевод",
    "outgoing_toggle": "Исходящий перевод",
    "outgoing_hold": "Клавиша записи",
    "game_ptt": "Клавиша рации в игре",
    "save_audio": "Сохранить звук игры",
}


def validate(config: AppConfig) -> list[str]:
    errors: list[str] = []
    hotkeys = config.hotkeys
    combos = {name: getattr(hotkeys, name) for name in GLOBAL_HOTKEYS}

    for name, hotkey in combos.items():
        label = HOTKEY_LABELS[name]
        if hotkey.vk in MOUSE_VKS or hotkey.vk in MODIFIER_VKS:
            errors.append(f"{label}: нужна клавиша клавиатуры, не кнопка мыши и не один модификатор.")
        if not hotkey.modifiers:
            errors.append(f"{label}: сочетание должно содержать Ctrl, Alt, Shift или Win.")
    for (first, a), (second, b) in itertools.combinations(combos.items(), 2):
        if a == b:
            errors.append(f"Сочетания «{HOTKEY_LABELS[first]}» и «{HOTKEY_LABELS[second]}» совпадают.")

    for name in _HELD_KEYS:
        key = getattr(hotkeys, name)
        label = HOTKEY_LABELS[name]
        if key.vk in (VK_LBUTTON, VK_RBUTTON) or key.vk in MODIFIER_VKS:
            errors.append(f"{label}: левая и правая кнопки мыши и модификаторы не допускаются.")
        if key.modifiers:
            errors.append(f"{label}: должна быть одной клавишей без модификаторов.")
        if key.vk in {c.vk for c in combos.values()}:
            errors.append(f"{label}: совпадает с клавишей одного из сочетаний.")
    if hotkeys.outgoing_hold.vk == hotkeys.game_ptt.vk:
        errors.append(
            "Клавиша записи совпадает с рацией в игре: игра передаст русскую речь во время записи. "
            "Назначьте для записи другую клавишу."
        )

    if not _in_range(config.unload_delay_s, UNLOAD_DELAY_RANGE_S):
        errors.append("Задержка выгрузки моделей вне допустимого диапазона.")
    if not _in_range(config.max_record_s, MAX_RECORD_RANGE_S):
        errors.append("Максимальная длина записи вне допустимого диапазона.")
    if not _is_exe_name(config.game_exe):
        errors.append("Имя процесса игры должно быть именем .exe-файла.")
    if not _in_range(config.speech_threshold, SPEECH_THRESHOLD_RANGE):
        errors.append("Порог распознавания речи вне допустимого диапазона.")
    if not _in_range(config.monitor_volume, MONITOR_VOLUME_RANGE):
        errors.append("Громкость озвучки в наушниках вне допустимого диапазона.")
    if not _is_voice_name(config.tts_voice):
        errors.append("Недопустимое имя голоса озвучки.")
    if config.recognition not in RECOGNITION_MODES:
        errors.append("Неизвестный режим распознавания.")
    if not _in_range(config.chat_opacity, CHAT_OPACITY_RANGE):
        errors.append("Непрозрачность окна чата вне допустимого диапазона.")
    return errors


def load_config(path: Path) -> AppConfig:
    defaults = AppConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return defaults
    except (OSError, ValueError) as exc:
        log.warning("Config unreadable, defaults used: %s", exc)
        return defaults
    if not isinstance(raw, dict):
        log.warning("Config root is not an object, defaults used")
        return defaults

    raw_hotkeys = raw.get("hotkeys")
    raw_hotkeys = raw_hotkeys if isinstance(raw_hotkeys, dict) else {}
    hotkey_values: dict[str, Hotkey] = {}
    for f in fields(HotkeyConfig):
        try:
            hotkey_values[f.name] = Hotkey.from_json(raw_hotkeys[f.name])
        except (KeyError, ValueError):
            hotkey_values[f.name] = getattr(defaults.hotkeys, f.name)

    config = AppConfig(
        hotkeys=HotkeyConfig(**hotkey_values),
        unload_delay_s=_int_or(raw.get("unload_delay_s"), UNLOAD_DELAY_RANGE_S, defaults.unload_delay_s),
        max_record_s=_int_or(raw.get("max_record_s"), MAX_RECORD_RANGE_S, defaults.max_record_s),
        game_exe=raw["game_exe"] if _is_exe_name(raw.get("game_exe")) else defaults.game_exe,
        tts_voice=raw["tts_voice"] if _is_voice_name(raw.get("tts_voice")) else defaults.tts_voice,
        monitor_volume=_int_or(raw.get("monitor_volume"), MONITOR_VOLUME_RANGE, defaults.monitor_volume),
        speech_threshold=_int_or(raw.get("speech_threshold"), SPEECH_THRESHOLD_RANGE, defaults.speech_threshold),
        recognition=raw["recognition"] if raw.get("recognition") in RECOGNITION_MODES else defaults.recognition,
        subtitles_overlay=_bool_or(raw.get("subtitles_overlay"), defaults.subtitles_overlay),
        chat_window=_bool_or(raw.get("chat_window"), defaults.chat_window),
        chat_on_top=_bool_or(raw.get("chat_on_top"), defaults.chat_on_top),
        chat_opacity=_int_or(raw.get("chat_opacity"), CHAT_OPACITY_RANGE, defaults.chat_opacity),
    )
    if validate(config):
        config = _resolve_conflicts(config, defaults)
    return config


def _resolve_conflicts(config: AppConfig, defaults: AppConfig) -> AppConfig:
    """A newly added key must not wipe the user's hotkeys: try free alternatives for it first."""
    names = tuple(_NEW_HOTKEY_CANDIDATES)
    options = [(getattr(config.hotkeys, name), *_NEW_HOTKEY_CANDIDATES[name]) for name in names]
    for values in itertools.product(*options):
        candidate = replace(config, hotkeys=replace(config.hotkeys, **dict(zip(names, values, strict=True))))
        if not validate(candidate):
            return candidate
    log.warning("Config hotkeys conflict, default hotkeys used")
    return replace(config, hotkeys=defaults.hotkeys)


def save_config(path: Path, config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(config.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def config_path() -> Path:
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / APP_DIR_NAME / "config.json"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_DIR_NAME.lower() / "config.json"


def data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / APP_DIR_NAME
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / APP_DIR_NAME.lower()


def log_dir() -> Path:
    return data_dir() / "logs"


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _in_range(value: int, bounds: tuple[int, int]) -> bool:
    return _is_int(value) and bounds[0] <= value <= bounds[1]


def _is_exe_name(value: Any) -> bool:
    if value == "":
        return True
    return (
        isinstance(value, str)
        and len(value) <= MAX_EXE_NAME
        and value.lower().endswith(".exe")
        and not any(c in value for c in '\\/:*?"<>|')
    )


def _is_voice_name(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= MAX_VOICE_NAME and value.isprintable()


def _int_or(value: Any, bounds: tuple[int, int], default: int) -> int:
    return value if _in_range(value, bounds) else default


def _bool_or(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default
