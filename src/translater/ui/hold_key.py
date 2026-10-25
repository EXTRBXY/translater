from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from translater.win32.keys import is_key_down

_POLL_INTERVAL_MS = 15


class HoldKeyWatcher(QObject):
    """Polls GetAsyncKeyState only while started: no input hook, the key still reaches the game."""

    pressed = Signal()
    released = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._vk = 0
        self._down = False
        self._wait_release = False
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    def start(self, vk: int) -> None:
        self.stop()
        self._vk = vk
        self._wait_release = is_key_down(vk)  # a key already held at start is not a new press
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        if self._down:
            self._down = False
            self.released.emit()

    def _poll(self) -> None:
        down = is_key_down(self._vk)
        if self._wait_release:
            self._wait_release = down
            return
        if down == self._down:
            return
        self._down = down
        (self.pressed if down else self.released).emit()
