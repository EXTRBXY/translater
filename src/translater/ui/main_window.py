from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from translater.config import AppConfig
from translater.controller import Mode, ModeState
from translater.ui.settings import SettingsForm
from translater.ui.theme import (
    COLOR_ACTIVE,
    COLOR_IDLE,
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


class _DownloadPanel(QGroupBox):
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Загрузка компонентов", parent)
        self._label = QLabel()
        self._label.setWordWrap(True)
        self._bar = QProgressBar()
        self._bar.setRange(0, 1000)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.cancel_requested)
        row = QHBoxLayout()
        row.addWidget(self._bar, 1)
        row.addWidget(cancel)
        layout = QVBoxLayout(self)
        layout.addWidget(self._label)
        layout.addLayout(row)
        self.hide()

    def set_progress(self, title: str, done: int, total: int) -> None:
        self._label.setText(f"{title}: {done / 1e9:.2f} из {total / 1e9:.2f} ГБ")
        self._bar.setValue(int(done * 1000 / total) if total else 0)
        self.show()


class MainWindow(QMainWindow):
    toggle_requested = Signal(object)
    settings_saved = Signal(object)
    capturing_changed = Signal(bool)
    hidden_to_tray = Signal()
    download_cancel_requested = Signal()
    voice_preview_requested = Signal(str)

    def __init__(
        self,
        config: AppConfig,
        list_games: Callable[[], list[tuple[str, str]]],
        list_voices: Callable[[], list[str]],
    ) -> None:
        super().__init__()
        self.setWindowTitle("Translater")
        self.setWindowIcon(dot_icon(COLOR_ACTIVE))
        self._quitting = False
        self._config = config

        self._cards = {mode: _ModeCard(mode) for mode in Mode}
        self._sources = dict.fromkeys(Mode, "")
        for card in self._cards.values():
            card.toggle_requested.connect(self.toggle_requested)

        self._download = _DownloadPanel()
        self._download.cancel_requested.connect(self.download_cancel_requested)
        self._settings = SettingsForm(config, list_games, list_voices)
        self._settings.voice_preview_requested.connect(self.voice_preview_requested)
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
        layout.addWidget(self._download)
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
            f"Запись: {format_hotkey(hotkeys.outgoing_hold)}    Рация: {format_hotkey(hotkeys.game_ptt)}"
        )

    def set_state(self, mode: Mode, state: ModeState) -> None:
        self._cards[mode].set_state(state)
        if state is not ModeState.ACTIVE:
            self.set_source(mode, "")

    def set_source(self, mode: Mode, text: str) -> None:
        self._sources[mode] = text
        self._cards[mode].set_extra(text, COLOR_IDLE)

    def set_download_progress(self, title: str, done: int, total: int) -> None:
        self._download.set_progress(title, done, total)

    def hide_download(self) -> None:
        self._download.hide()

    def set_recording(self, recording: bool) -> None:
        if recording:
            self._cards[Mode.OUTGOING].set_extra("● Идёт запись", COLOR_RECORDING)
        else:
            self._cards[Mode.OUTGOING].set_extra(self._sources[Mode.OUTGOING], COLOR_IDLE)

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
