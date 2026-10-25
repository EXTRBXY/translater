from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from translater.ui.theme import dot_pixmap

_SHOW_MS = 3000
_TOP_MARGIN_RATIO = 0.04
_DOT_SIZE = 14
_STYLE = (
    "QFrame#toast { background-color: rgba(20, 20, 20, 200); border-radius: 8px; }"
    " QLabel { color: white; font-size: 16px; background: transparent; }"
)


class StatusToast(QWidget):
    """Short status message at the top of the screen over the game; click-through, never focused."""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(_STYLE)
        frame = QFrame()
        frame.setObjectName("toast")
        QVBoxLayout(self).setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(frame)
        self._dot = QLabel()
        self._text = QLabel()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(14, 8, 16, 8)
        layout.setSpacing(8)
        layout.addWidget(self._dot)
        layout.addWidget(self._text)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, color: str) -> None:
        self._dot.setPixmap(dot_pixmap(color, _DOT_SIZE))
        self._text.setText(text)
        self.adjustSize()
        screen = QGuiApplication.primaryScreen().geometry()
        self.move(
            screen.x() + (screen.width() - self.width()) // 2,
            screen.y() + int(screen.height() * _TOP_MARGIN_RATIO),
        )
        self.show()
        self.raise_()
        self._timer.start(_SHOW_MS)
