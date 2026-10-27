from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from translater.ui.chat import ChatWindow  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _window(tmp_path) -> ChatWindow:
    return ChatWindow(QtCore.QSettings(str(tmp_path / "window.ini"), QtCore.QSettings.Format.IniFormat))


def test_messages_are_escaped_and_marked(qt_app, tmp_path) -> None:
    chat = _window(tmp_path)
    chat.add_message("<b>враг</b> слева", "<b>enemy</b> left")
    chat.add_message("Cover me", "Прикрой", author="Вы", own=True)
    text = chat._view.toPlainText()
    assert "<b>враг</b> слева" in text and "<b>enemy</b> left" in text
    assert "Вы: Cover me" in text


def test_history_is_bounded(qt_app, tmp_path) -> None:
    from translater.ui.chat import _MAX_MESSAGES

    chat = _window(tmp_path)
    for i in range(_MAX_MESSAGES + 50):
        chat.add_message(f"фраза {i}")
    assert chat._view.document().blockCount() <= _MAX_MESSAGES


def test_geometry_survives_restart(qt_app, tmp_path) -> None:
    chat = _window(tmp_path)
    chat.resize(333, 444)
    chat.save_geometry()
    assert _window(tmp_path).size() == QtCore.QSize(333, 444)
