from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from translater.config import AppConfig
from translater.controller import Mode, ModeState
from translater.ui.settings import SettingsForm
from translater.ui.theme import (
    COLOR_ACTIVE,
    COLOR_RECORDING,
    MODE_DESCRIPTIONS,
    MODE_LABELS,
    STATE_COLORS,
    STATE_LABELS,
    dot_icon,
    dot_pixmap,
)
from translater.win32.keys import format_hotkey

_STATUS_DOT_SIZE = 14


class _ModeCard(QFrame):
    toggle_requested = Signal(object)

    def __init__(self, mode: Mode, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = mode
        self.setFrameShape(QFrame.Shape.StyledPanel)

        title = QLabel(MODE_LABELS[mode])
        font = title.font()
        font.setPointSizeF(font.pointSizeF() * 1.3)
        font.setBold(True)
        title.setFont(font)
        description = QLabel(MODE_DESCRIPTIONS[mode])
        description.setWordWrap(True)
        self._hotkey = QLabel()
        self._dot = QLabel()
        self._state = QLabel()
        self._extra = QLabel()
        self._button = QPushButton()
        self._button.setMinimumHeight(36)
        self._button.clicked.connect(lambda: self.toggle_requested.emit(self._mode))

        layout = QGridLayout(self)
        layout.addWidget(title, 0, 0, 1, 3)
        layout.addWidget(description, 1, 0, 1, 3)
        layout.addWidget(self._hotkey, 2, 0, 1, 3)
        layout.addWidget(self._dot, 3, 0)
        layout.addWidget(self._state, 3, 1)
        layout.addWidget(self._button, 3, 2)
        layout.addWidget(self._extra, 4, 0, 1, 3)
        layout.setColumnStretch(1, 1)
        self._extra.hide()
        self.set_state(ModeState.IDLE)

    def set_hotkey_text(self, text: str) -> None:
        self._hotkey.setText(text)

    def set_state(self, state: ModeState) -> None:
        self._dot.setPixmap(dot_pixmap(STATE_COLORS[state], _STATUS_DOT_SIZE))
        self._state.setText(STATE_LABELS[state])
        self._button.setText("Включить" if state is ModeState.IDLE else "Выключить")

    def set_extra(self, text: str, color: str) -> None:
        self._extra.setStyleSheet(f"color: {color}; font-weight: bold;")
        self._extra.setText(text)
        self._extra.setVisible(bool(text))


class MainWindow(QMainWindow):
    toggle_requested = Signal(object)
    settings_saved = Signal(object)
    capturing_changed = Signal(bool)
    hidden_to_tray = Signal()

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.setWindowTitle("Translater")
        self.setWindowIcon(dot_icon(COLOR_ACTIVE))
        self._quitting = False
        self._config = config

        self._cards = {mode: _ModeCard(mode) for mode in Mode}
        for card in self._cards.values():
            card.toggle_requested.connect(self.toggle_requested)

        self._settings = SettingsForm(config)
        self._settings.saved.connect(self.settings_saved)
        self._settings.capturing_changed.connect(self.capturing_changed)
        settings_box = QGroupBox("Клавиши и параметры")
        QVBoxLayout(settings_box).addWidget(self._settings)

        footer = QLabel("При закрытии окна программа продолжает работать в трее. Выход — через меню значка в трее.")
        footer.setWordWrap(True)
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setStyleSheet("color: palette(placeholder-text);")

        central = QWidget()
        layout = QVBoxLayout(central)
        for card in self._cards.values():
            layout.addWidget(card)
        layout.addWidget(settings_box)
        layout.addStretch(1)
        layout.addWidget(footer)
        self.setCentralWidget(central)
        self.setMinimumWidth(520)
        self.apply_config(config)

    def apply_config(self, config: AppConfig) -> None:
        self._config = config
        hotkeys = config.hotkeys
        self._cards[Mode.INCOMING].set_hotkey_text(f"Вкл/выкл: {format_hotkey(hotkeys.incoming_toggle)}")
        self._cards[Mode.OUTGOING].set_hotkey_text(
            f"Вкл/выкл: {format_hotkey(hotkeys.outgoing_toggle)}    "
            f"Запись: удерживать {format_hotkey(hotkeys.outgoing_hold)}"
        )

    def set_state(self, mode: Mode, state: ModeState) -> None:
        self._cards[mode].set_state(state)
        if mode is Mode.OUTGOING and state is not ModeState.ACTIVE:
            self.set_recording(False)

    def set_recording(self, recording: bool) -> None:
        self._cards[Mode.OUTGOING].set_extra("● Идёт запись" if recording else "", COLOR_RECORDING)

    def show_settings_status(self, text: str, error: bool = False) -> None:
        self._settings.show_status(text, error)

    def bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def prepare_quit(self) -> None:
        self._quitting = True

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._quitting:
            event.accept()
            return
        event.ignore()
        self.hide()
        self.hidden_to_tray.emit()
