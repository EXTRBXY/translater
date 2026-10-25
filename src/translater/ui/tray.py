from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from translater.config import AppConfig
from translater.controller import Mode, ModeState
from translater.win32.keys import format_hotkey

_COLORS = {
    "idle": "#8a8a8a",
    "loading": "#e0b000",
    "active": "#2e9d4f",
    "recording": "#d13438",
}
_MODE_LABELS = {Mode.INCOMING: "Входящий перевод", Mode.OUTGOING: "Исходящий перевод"}
_STATE_LABELS = {ModeState.IDLE: "выкл", ModeState.LOADING: "загрузка", ModeState.ACTIVE: "вкл"}


class TrayIcon(QObject):
    toggle_requested = Signal(object)
    settings_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._states = {mode: ModeState.IDLE for mode in Mode}
        self._recording = False
        self._icons: dict[str, QIcon] = {}

        self._menu = QMenu()
        self._actions: dict[Mode, QAction] = {}
        for mode in Mode:
            action = self._menu.addAction(_MODE_LABELS[mode])
            action.setCheckable(True)
            action.triggered.connect(lambda _checked, m=mode: self._on_toggle(m))
            self._actions[mode] = action
        self._menu.addSeparator()
        self._menu.addAction("Настройки…").triggered.connect(self.settings_requested)
        self._menu.addAction("Выход").triggered.connect(self.quit_requested)

        self._tray = QSystemTrayIcon(self)
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_activated)
        self._refresh()
        self._tray.show()

    def set_hotkey_hints(self, config: AppConfig) -> None:
        hints = {Mode.INCOMING: config.hotkeys.incoming_toggle, Mode.OUTGOING: config.hotkeys.outgoing_toggle}
        for mode, action in self._actions.items():
            action.setText(f"{_MODE_LABELS[mode]}\t{format_hotkey(hints[mode])}")

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
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.settings_requested.emit()

    def _refresh(self) -> None:
        states = set(self._states.values())
        if self._recording:
            key = "recording"
        elif ModeState.LOADING in states:
            key = "loading"
        elif ModeState.ACTIVE in states:
            key = "active"
        else:
            key = "idle"
        self._tray.setIcon(self._icon(key))
        self._tray.setToolTip(
            "Translater\n" + "\n".join(f"{_MODE_LABELS[m]}: {_STATE_LABELS[s]}" for m, s in self._states.items())
        )
        for mode, action in self._actions.items():
            action.setChecked(self._states[mode] is not ModeState.IDLE)

    def _icon(self, key: str) -> QIcon:
        icon = self._icons.get(key)
        if icon is None:
            pixmap = QPixmap(64, 64)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QColor("#202020"))
            painter.setBrush(QColor(_COLORS[key]))
            painter.drawEllipse(6, 6, 52, 52)
            painter.end()
            icon = self._icons[key] = QIcon(pixmap)
        return icon
