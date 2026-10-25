from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
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
    UNLOAD_DELAY_RANGE_S,
    AppConfig,
    HotkeyConfig,
    validate,
)
from translater.ui.key_capture import KeyCaptureEdit
from translater.ui.theme import COLOR_ACTIVE, COLOR_RECORDING

_HINT = (
    "Щёлкните по полю и нажмите нужное сочетание. Для включения режимов нужен Ctrl, Alt, Shift или Win. "
    "Клавиша записи не должна совпадать с клавишей рации в игре."
)


class SettingsForm(QWidget):
    saved = Signal(object)
    capturing_changed = Signal(bool)

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        hotkeys = config.hotkeys
        self._incoming = KeyCaptureEdit(hotkeys.incoming_toggle, allow_modifiers=True, allow_mouse=False)
        self._outgoing = KeyCaptureEdit(hotkeys.outgoing_toggle, allow_modifiers=True, allow_mouse=False)
        self._hold = KeyCaptureEdit(hotkeys.outgoing_hold, allow_modifiers=False, allow_mouse=True)
        self._unload_delay = _spin_box(UNLOAD_DELAY_RANGE_S, config.unload_delay_s)
        self._max_record = _spin_box(MAX_RECORD_RANGE_S, config.max_record_s)
        self._status = QLabel()
        self._status.setWordWrap(True)
        save = QPushButton("Сохранить")
        save.clicked.connect(self._save)

        for edit in (self._incoming, self._outgoing, self._hold):
            edit.capturing_changed.connect(self.capturing_changed)
            edit.hotkey_changed.connect(lambda: self._status.clear())

        form = QFormLayout()
        form.addRow(f"{HOTKEY_LABELS['incoming_toggle']}, вкл/выкл:", self._incoming)
        form.addRow(f"{HOTKEY_LABELS['outgoing_toggle']}, вкл/выкл:", self._outgoing)
        form.addRow(f"{HOTKEY_LABELS['outgoing_hold']} (удерживать):", self._hold)
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
            ),
            unload_delay_s=self._unload_delay.value(),
            max_record_s=self._max_record.value(),
        )
        errors = validate(candidate)
        if errors:
            self.show_status("\n".join(errors), error=True)
            return
        self.saved.emit(candidate)

    def show_status(self, text: str, error: bool = False) -> None:
        self._status.setStyleSheet(f"color: {COLOR_RECORDING if error else COLOR_ACTIVE};")
        self._status.setText(text)


def _spin_box(bounds: tuple[int, int], value: int) -> QSpinBox:
    box = QSpinBox()
    box.setRange(*bounds)
    box.setValue(value)
    box.setSuffix(" с")
    return box
