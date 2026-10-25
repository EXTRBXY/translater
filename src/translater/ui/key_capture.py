from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QLineEdit, QWidget

from translater.config import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MODIFIER_VKS, Hotkey
from translater.win32.keys import format_hotkey

_MOUSE_BUTTON_VK = {
    Qt.MouseButton.MiddleButton: 0x04,
    Qt.MouseButton.BackButton: 0x05,
    Qt.MouseButton.ForwardButton: 0x06,
}
_QT_MODIFIERS = (
    (Qt.KeyboardModifier.ControlModifier, MOD_CONTROL),
    (Qt.KeyboardModifier.AltModifier, MOD_ALT),
    (Qt.KeyboardModifier.ShiftModifier, MOD_SHIFT),
    (Qt.KeyboardModifier.MetaModifier, MOD_WIN),
)


class KeyCaptureEdit(QLineEdit):
    """Click, then press a key combination (or a mouse button if allowed). Esc cancels."""

    capturing_changed = Signal(bool)
    hotkey_changed = Signal()

    def __init__(
        self, hotkey: Hotkey, *, allow_modifiers: bool, allow_mouse: bool, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._hotkey = hotkey
        self._allow_modifiers = allow_modifiers
        self._allow_mouse = allow_mouse
        self._capturing = False
        self.setReadOnly(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._render()

    @property
    def hotkey(self) -> Hotkey:
        return self._hotkey

    def mousePressEvent(self, event: QMouseEvent) -> None:
        event.accept()
        if not self._capturing:
            if event.button() == Qt.MouseButton.LeftButton:
                self._set_capturing(True)
            return
        vk = _MOUSE_BUTTON_VK.get(event.button())
        if vk is not None and self._allow_mouse:
            self._finish(Hotkey(vk))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._capturing:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                self._set_capturing(True)
            else:
                event.ignore()
            return
        event.accept()
        if event.key() == Qt.Key.Key_Escape:
            self._set_capturing(False)
            return
        vk = event.nativeVirtualKey()
        if not vk or vk in MODIFIER_VKS:
            return
        modifiers = 0
        if self._allow_modifiers:
            for qt_modifier, bit in _QT_MODIFIERS:
                if event.modifiers() & qt_modifier:
                    modifiers |= bit
        self._finish(Hotkey(vk, modifiers))

    def focusOutEvent(self, event: QFocusEvent) -> None:
        self._set_capturing(False)
        super().focusOutEvent(event)

    def _finish(self, hotkey: Hotkey) -> None:
        changed = hotkey != self._hotkey
        self._hotkey = hotkey
        self._set_capturing(False)
        if changed:
            self.hotkey_changed.emit()

    def _set_capturing(self, capturing: bool) -> None:
        if capturing != self._capturing:
            self._capturing = capturing
            self.capturing_changed.emit(capturing)
        self._render()

    def _render(self) -> None:
        self.setText("Нажмите клавишу… (Esc — отмена)" if self._capturing else format_hotkey(self._hotkey))
