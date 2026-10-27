from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from translater.config import AppConfig
from translater.controller import Mode, ModeState
from translater.ui.theme import (
    COLOR_ACTIVE,
    COLOR_IDLE,
    COLOR_LOADING,
    COLOR_RECORDING,
    MODE_LABELS,
    STATE_LABELS,
    dot_icon,
)
from translater.win32.keys import format_hotkey


class TrayIcon(QObject):
    toggle_requested = Signal(object)
    open_requested = Signal()
    save_audio_requested = Signal()
    chat_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._states = {mode: ModeState.IDLE for mode in Mode}
        self._recording = False

        self._menu = QMenu()
        self._menu.addAction("Открыть Translater").triggered.connect(self.open_requested)
        self._menu.addSeparator()
        self._actions: dict[Mode, QAction] = {}
        for mode in Mode:
            action = self._menu.addAction(MODE_LABELS[mode])
            action.setCheckable(True)
            action.triggered.connect(lambda _checked, m=mode: self._on_toggle(m))
            self._actions[mode] = action
        self._menu.addSeparator()
        self._menu.addAction("Окно чата").triggered.connect(self.chat_requested)
        self._save_audio = self._menu.addAction("Сохранить последние 60 с звука игры")
        self._save_audio.triggered.connect(self.save_audio_requested)
        self._menu.addSeparator()
        self._menu.addAction("Выход").triggered.connect(self.quit_requested)

        self._tray = QSystemTrayIcon(self)
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_activated)
        self._refresh()
        self._tray.show()

    def set_hotkey_hints(self, config: AppConfig) -> None:
        hints = {Mode.INCOMING: config.hotkeys.incoming_toggle, Mode.OUTGOING: config.hotkeys.outgoing_toggle}
        for mode, action in self._actions.items():
            action.setText(f"{MODE_LABELS[mode]}\t{format_hotkey(hints[mode])}")
        self._save_audio.setText(f"Сохранить последние 60 с звука игры\t{format_hotkey(config.hotkeys.save_audio)}")

    def set_state(self, mode: Mode, state: ModeState) -> None:
        self._states[mode] = state
        self._refresh()

    def set_recording(self, recording: bool) -> None:
        self._recording = recording
        self._refresh()

    def show_message(self, text: str, error: bool = False) -> None:
        icon = QSystemTrayIcon.MessageIcon.Warning if error else QSystemTrayIcon.MessageIcon.Information
        self._tray.showMessage("Translater", text, icon)

    def _on_toggle(self, mode: Mode) -> None:
        self.toggle_requested.emit(mode)
        self._refresh()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_requested.emit()

    def _refresh(self) -> None:
        states = set(self._states.values())
        if self._recording:
            color = COLOR_RECORDING
        elif ModeState.LOADING in states:
            color = COLOR_LOADING
        elif ModeState.ACTIVE in states:
            color = COLOR_ACTIVE
        else:
            color = COLOR_IDLE
        self._tray.setIcon(dot_icon(color))
        self._tray.setToolTip(
            "Translater\n" + "\n".join(f"{MODE_LABELS[m]}: {STATE_LABELS[s].lower()}" for m, s in self._states.items())
        )
        for mode, action in self._actions.items():
            action.setChecked(self._states[mode] is not ModeState.IDLE)
