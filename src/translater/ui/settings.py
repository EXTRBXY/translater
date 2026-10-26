from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from translater.config import (
    HOTKEY_LABELS,
    MAX_RECORD_RANGE_S,
    MONITOR_VOLUME_RANGE,
    SPEECH_THRESHOLD_RANGE,
    UNLOAD_DELAY_RANGE_S,
    AppConfig,
    HotkeyConfig,
    validate,
)
from translater.ui.key_capture import KeyCaptureEdit
from translater.ui.theme import COLOR_ACTIVE, COLOR_RECORDING

_HINT = (
    "Щёлкните по полю и нажмите нужное сочетание. Для включения режимов нужен Ctrl, Alt, Shift или Win. "
    "Клавиша рации — та же, что в настройках игры: программа только видит её нажатие "
    "и в этот момент озвучивает перевод. Клавиша записи должна от неё отличаться. "
    "Для субтитров выберите игру, пока она запущена: "
    "тогда Discord и музыка в субтитры не попадут."
)


class SettingsForm(QWidget):
    saved = Signal(object)
    capturing_changed = Signal(bool)
    voice_preview_requested = Signal(str)

    def __init__(
        self,
        config: AppConfig,
        list_games: Callable[[], list[tuple[str, str]]],
        list_voices: Callable[[], list[str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._list_games = list_games
        hotkeys = config.hotkeys
        self._incoming = KeyCaptureEdit(hotkeys.incoming_toggle, allow_modifiers=True, allow_mouse=False)
        self._outgoing = KeyCaptureEdit(hotkeys.outgoing_toggle, allow_modifiers=True, allow_mouse=False)
        self._hold = KeyCaptureEdit(hotkeys.outgoing_hold, allow_modifiers=False, allow_mouse=True)
        self._ptt = KeyCaptureEdit(hotkeys.game_ptt, allow_modifiers=False, allow_mouse=True)
        self._unload_delay = _spin_box(UNLOAD_DELAY_RANGE_S, config.unload_delay_s)
        self._max_record = _spin_box(MAX_RECORD_RANGE_S, config.max_record_s)
        self._game = QComboBox()
        self._game.setMinimumContentsLength(24)
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(lambda: self._fill_games(self._game.currentData()))
        game_row = QHBoxLayout()
        game_row.addWidget(self._game, 1)
        game_row.addWidget(refresh)
        self._fill_games(config.game_exe)
        self._voice = QComboBox()
        self._voice.addItem("Автоматически (мужской, если установлен)", "")
        for name in list_voices():
            self._voice.addItem(name, name)
        self._voice.setCurrentIndex(max(self._voice.findData(config.tts_voice), 0))
        preview = QPushButton("Прослушать")
        preview.clicked.connect(lambda: self.voice_preview_requested.emit(self._voice.currentData() or ""))
        voice_row = QHBoxLayout()
        voice_row.addWidget(self._voice, 1)
        voice_row.addWidget(preview)
        self._speech_threshold = _spin_box(SPEECH_THRESHOLD_RANGE, config.speech_threshold, " %")
        self._speech_threshold.setToolTip("Ниже — субтитры ловят тихую и зашумлённую речь, но чаще срабатывают на шум.")
        self._monitor = _spin_box(MONITOR_VOLUME_RANGE, config.monitor_volume, " %")
        self._monitor.setSpecialValueText("выключено")
        self._status = QLabel()
        self._status.setWordWrap(True)
        save = QPushButton("Сохранить")
        save.clicked.connect(self._save)

        for edit in (self._incoming, self._outgoing, self._hold, self._ptt):
            edit.capturing_changed.connect(self.capturing_changed)
            edit.hotkey_changed.connect(lambda: self._status.clear())

        form = QFormLayout()
        form.addRow(f"{HOTKEY_LABELS['incoming_toggle']}, вкл/выкл:", self._incoming)
        form.addRow(f"{HOTKEY_LABELS['outgoing_toggle']}, вкл/выкл:", self._outgoing)
        form.addRow(f"{HOTKEY_LABELS['outgoing_hold']} (удерживать):", self._hold)
        form.addRow(f"{HOTKEY_LABELS['game_ptt']}:", self._ptt)
        form.addRow("Звук для субтитров:", game_row)
        form.addRow("Порог речи (ниже — чувствительнее):", self._speech_threshold)
        form.addRow("Голос озвучки:", voice_row)
        form.addRow("Озвучка в наушниках:", self._monitor)
        form.addRow("Выгружать модели через:", self._unload_delay)
        form.addRow("Максимальная длина записи:", self._max_record)

        hint = QLabel(_HINT)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        footer = QHBoxLayout()
        footer.addWidget(self._status, 1)
        footer.addWidget(save)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addLayout(footer)

    def _save(self) -> None:
        candidate = AppConfig(
            hotkeys=HotkeyConfig(
                incoming_toggle=self._incoming.hotkey,
                outgoing_toggle=self._outgoing.hotkey,
                outgoing_hold=self._hold.hotkey,
                game_ptt=self._ptt.hotkey,
            ),
            unload_delay_s=self._unload_delay.value(),
            max_record_s=self._max_record.value(),
            game_exe=self._game.currentData() or "",
            tts_voice=self._voice.currentData() or "",
            monitor_volume=self._monitor.value(),
            speech_threshold=self._speech_threshold.value(),
        )
        errors = validate(candidate)
        if errors:
            self.show_status("\n".join(errors), error=True)
            return
        self.saved.emit(candidate)

    def _fill_games(self, selected: str) -> None:
        self._game.clear()
        self._game.addItem("Весь звук системы", "")
        running = self._list_games()
        for exe, title in running:
            self._game.addItem(f"{title} ({exe})", exe)
        if selected and all(exe.lower() != selected.lower() for exe, _ in running):
            self._game.addItem(f"{selected} (не запущена)", selected)
        index = self._game.findData(selected)
        self._game.setCurrentIndex(max(index, 0))

    def show_status(self, text: str, error: bool = False) -> None:
        self._status.setStyleSheet(f"color: {COLOR_RECORDING if error else COLOR_ACTIVE};")
        self._status.setText(text)


def _spin_box(bounds: tuple[int, int], value: int, suffix: str = " с") -> QSpinBox:
    box = QSpinBox()
    box.setRange(*bounds)
    box.setValue(value)
    box.setSuffix(suffix)
    return box
