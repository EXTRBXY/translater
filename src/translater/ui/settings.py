from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
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

_HINT = (
    "Нажмите на поле, затем нужное сочетание. Для переключателей нужен Ctrl, Alt, Shift или Win.\n"
    "Клавишу записи удерживайте, пока говорите. Она не должна совпадать с клавишей рации в игре: "
    "озвученный перевод передаётся в игру по рации уже после записи."
)


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Translater — настройки")
        self._result: AppConfig | None = None

        hotkeys = config.hotkeys
        self._incoming = KeyCaptureEdit(hotkeys.incoming_toggle, allow_modifiers=True, allow_mouse=False)
        self._outgoing = KeyCaptureEdit(hotkeys.outgoing_toggle, allow_modifiers=True, allow_mouse=False)
        self._hold = KeyCaptureEdit(hotkeys.outgoing_hold, allow_modifiers=False, allow_mouse=True)
        self._unload_delay = _spin_box(UNLOAD_DELAY_RANGE_S, config.unload_delay_s)
        self._max_record = _spin_box(MAX_RECORD_RANGE_S, config.max_record_s)

        form = QFormLayout()
        form.addRow(f"{HOTKEY_LABELS['incoming_toggle']} (вкл/выкл):", self._incoming)
        form.addRow(f"{HOTKEY_LABELS['outgoing_toggle']} (вкл/выкл):", self._outgoing)
        form.addRow(f"{HOTKEY_LABELS['outgoing_hold']} (удерживать):", self._hold)
        form.addRow("Выгружать модели через:", self._unload_delay)
        form.addRow("Максимальная длина записи:", self._max_record)

        hint = QLabel(_HINT)
        hint.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(buttons)
        self.setMinimumWidth(480)

    def result_config(self) -> AppConfig | None:
        return self._result

    def _try_accept(self) -> None:
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
            QMessageBox.warning(self, "Настройки", "\n".join(errors))
            return
        self._result = candidate
        self.accept()


def _spin_box(bounds: tuple[int, int], value: int) -> QSpinBox:
    box = QSpinBox()
    box.setRange(*bounds)
    box.setValue(value)
    box.setSuffix(" с")
    return box
