from __future__ import annotations

import html
from datetime import datetime

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import QTextBrowser, QVBoxLayout, QWidget

from translater.ui.theme import COLOR_ACTIVE, COLOR_LOADING

_MAX_MESSAGES = 500
_DEFAULT_SIZE = (420, 520)
_GEOMETRY_KEY = "chat/geometry"
_STYLE = "QTextBrowser { background-color: #18181b; color: #efeff1; border: none; font-size: 15px; }"
_TIME_STYLE = "color: #8a8a8a; font-size: 12px;"
_ORIGINAL_STYLE = "color: #a8a8b0; font-size: 13px;"


class ChatWindow(QWidget):
    """Phrase history in memory only; never takes focus from the game. Over games in borderless mode only."""

    def __init__(self, state: QSettings) -> None:
        super().__init__(None)
        self._state = state
        self.setWindowTitle("Translater — чат")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._view = QTextBrowser()
        self._view.setStyleSheet(_STYLE)
        self._view.document().setMaximumBlockCount(_MAX_MESSAGES)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)
        self._on_top = True
        self._apply_flags()
        if not self.restoreGeometry(state.value(_GEOMETRY_KEY, b"")):
            screen = QGuiApplication.primaryScreen().availableGeometry()
            self.resize(*_DEFAULT_SIZE)
            self.move(screen.right() - _DEFAULT_SIZE[0] - 24, screen.top() + (screen.height() - _DEFAULT_SIZE[1]) // 2)

    def apply_settings(self, on_top: bool, opacity_percent: int) -> None:
        self.setWindowOpacity(opacity_percent / 100)
        if on_top != self._on_top:
            self._on_top = on_top
            visible = self.isVisible()
            self._apply_flags()
            self.setVisible(visible)

    def add_message(self, text: str, original: str | None = None, author: str | None = None, own: bool = False) -> None:
        parts = [f"<span style='{_TIME_STYLE}'>{datetime.now():%H:%M:%S}</span>"]
        if author:
            color = COLOR_LOADING if own else COLOR_ACTIVE
            parts.append(f"<b style='color: {color};'>{html.escape(author)}:</b>")
        parts.append(html.escape(text))
        line = " ".join(parts)
        if original:
            line += f"<br><span style='{_ORIGINAL_STYLE}'>{html.escape(original)}</span>"
        self._view.append(line)

    def save_geometry(self) -> None:
        self._state.setValue(_GEOMETRY_KEY, self.saveGeometry())
        self._state.sync()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.save_geometry()
        super().closeEvent(event)

    def _apply_flags(self) -> None:
        flags = Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus
        if self._on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
