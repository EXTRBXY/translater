from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from translater.controller import Mode, ModeState

COLOR_IDLE = "#8a8a8a"
COLOR_LOADING = "#e0b000"
COLOR_ACTIVE = "#2e9d4f"
COLOR_RECORDING = "#d13438"

STATE_COLORS = {ModeState.IDLE: COLOR_IDLE, ModeState.LOADING: COLOR_LOADING, ModeState.ACTIVE: COLOR_ACTIVE}
STATE_LABELS = {ModeState.IDLE: "Выключен", ModeState.LOADING: "Загрузка…", ModeState.ACTIVE: "Включён"}
MODE_LABELS = {Mode.INCOMING: "Входящий перевод", Mode.OUTGOING: "Исходящий перевод"}
MODE_DESCRIPTIONS = {
    Mode.INCOMING: "Речь игроков → субтитры на русском поверх игры.",
    Mode.OUTGOING: (
        "Держите клавишу записи и говорите по-русски → английский текст и сигнал. "
        "Затем зажмите рацию в игре — прозвучит английская озвучка."
    ),
}

_icon_cache: dict[tuple[str, int], QIcon] = {}


def dot_pixmap(color: str, size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#202020"))
    painter.setBrush(QColor(color))
    margin = max(1, size // 10)
    painter.drawEllipse(margin, margin, size - 2 * margin, size - 2 * margin)
    painter.end()
    return pixmap


def dot_icon(color: str, size: int = 64) -> QIcon:
    key = (color, size)
    icon = _icon_cache.get(key)
    if icon is None:
        icon = _icon_cache[key] = QIcon(dot_pixmap(color, size))
    return icon
