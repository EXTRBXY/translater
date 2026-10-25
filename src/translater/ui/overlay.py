from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

_MAX_LINES = 3
_LINE_SECONDS = 7
_WIDTH_RATIO = 0.6
_BOTTOM_MARGIN_RATIO = 0.12
_STYLE = (
    "QLabel { color: white; background-color: rgba(0, 0, 0, 170); border-radius: 6px;"
    " padding: 6px 12px; font-size: 20px; }"
)
_ORIGINAL_STYLE = "color: rgba(255, 255, 255, 170); font-size: 14px;"


class SubtitleOverlay(QWidget):
    """Click-through, never focused, always on top. Visible over games in borderless windowed mode only."""

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
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._layout.addStretch(1)
        self._labels: list[QLabel] = []

    def add_line(self, text: str, original: str | None = None) -> None:
        label = QLabel()
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setText(
            _escape(text) + (f"<br><span style='{_ORIGINAL_STYLE}'>{_escape(original)}</span>" if original else "")
        )
        self._layout.addWidget(label)
        label.show()  # children added to an already visible window stay hidden otherwise
        self._labels.append(label)
        while len(self._labels) > _MAX_LINES:
            self._remove(self._labels[0])
        QTimer.singleShot(_LINE_SECONDS * 1000, label, lambda: self._remove(label))
        self._place()
        self.show()

    def clear(self) -> None:
        for label in list(self._labels):
            self._remove(label)

    def _remove(self, label: QLabel) -> None:
        if label not in self._labels:
            return
        self._labels.remove(label)
        self._layout.removeWidget(label)
        label.deleteLater()
        if self._labels:
            self._place()
        else:
            self.hide()

    def _place(self) -> None:
        screen = QGuiApplication.primaryScreen().geometry()
        width = int(screen.width() * _WIDTH_RATIO)
        self.setFixedWidth(width)
        self._layout.activate()
        self.setFixedHeight(self.sizeHint().height())
        x = screen.x() + (screen.width() - width) // 2
        y = screen.y() + screen.height() - int(screen.height() * _BOTTOM_MARGIN_RATIO) - self.height()
        self.move(x, y)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
