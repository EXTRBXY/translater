from __future__ import annotations

import getpass
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from types import TracebackType

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from translater.config import HOTKEY_LABELS, AppConfig, config_path, load_config, log_dir, save_config
from translater.controller import Mode, ModeController, ModeState
from translater.models import ModelManager, ModelSpec
from translater.ui.hold_key import HoldKeyWatcher
from translater.ui.main_window import MainWindow
from translater.ui.tray import TrayIcon
from translater.win32.hotkeys import GlobalHotkeys
from translater.win32.process import SingleInstance, set_below_normal_priority

log = logging.getLogger(__name__)

_MUTEX_NAME = "Local\\Translater.App"
_ACTIVATION_PIPE = f"Translater.App.{getpass.getuser()}"
_ACTIVATION_TIMEOUT_MS = 1000
_HOTKEY_MODES = {"incoming_toggle": Mode.INCOMING, "outgoing_toggle": Mode.OUTGOING}
_ERROR_HOTKEY_ALREADY_REGISTERED = 1409

MODEL_SPECS: dict[str, ModelSpec] = {}
MODE_REQUIREMENTS: dict[Mode, tuple[str, ...]] = {}


def main() -> int:
    _setup_logging()
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("Translater")
    qt_app.setQuitOnLastWindowClosed(False)
    instance = SingleInstance(_MUTEX_NAME)
    try:
        if not instance.acquired:
            if not _activate_running_instance():
                QMessageBox.information(None, "Translater", "Программа уже запущена. Значок — в области уведомлений.")
            return 0
        if not QSystemTrayIcon.isSystemTrayAvailable():
            QMessageBox.critical(None, "Translater", "Область уведомлений Windows недоступна.")
            return 1
        try:
            set_below_normal_priority()
        except OSError:
            log.warning("Cannot lower process priority", exc_info=True)
        app = _Application(load_config(config_path()))
        qt_app.aboutToQuit.connect(app.shutdown)
        return qt_app.exec()
    finally:
        instance.close()


def _activate_running_instance() -> bool:
    socket = QLocalSocket()
    socket.connectToServer(_ACTIVATION_PIPE)
    if not socket.waitForConnected(_ACTIVATION_TIMEOUT_MS):
        return False
    socket.write(b"show")
    socket.waitForBytesWritten(_ACTIVATION_TIMEOUT_MS)
    socket.disconnectFromServer()
    return True


class _Bridge(QObject):
    """Moves callbacks from worker threads to the UI thread (queued signal delivery)."""

    hotkey = Signal(str)
    mode_state = Signal(object, object)
    mode_error = Signal(object, str)


class _Application:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._tray_hint_shown = False
        self._bridge = _Bridge()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inference")
        self._models = ModelManager(MODEL_SPECS, self._executor, config.unload_delay_s)
        self._controller = ModeController(
            self._models,
            MODE_REQUIREMENTS,
            runners={},
            on_state=self._bridge.mode_state.emit,
            on_error=lambda mode, exc: self._bridge.mode_error.emit(mode, str(exc)),
        )
        self._window = MainWindow(config)
        self._tray = TrayIcon()
        self._hold = HoldKeyWatcher()
        self._hotkeys = GlobalHotkeys(self._bridge.hotkey.emit)
        self._activation = QLocalServer()
        self._activation.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self._activation.listen(_ACTIVATION_PIPE):
            log.warning("Activation server not started: %s", self._activation.errorString())
        self._activation.newConnection.connect(self._on_activation)

        self._bridge.hotkey.connect(lambda name: self._controller.toggle(_HOTKEY_MODES[name]))
        self._bridge.mode_state.connect(self._on_mode_state)
        self._bridge.mode_error.connect(self._on_mode_error)
        self._window.toggle_requested.connect(self._controller.toggle)
        self._window.settings_saved.connect(self._save_settings)
        self._window.capturing_changed.connect(self._on_capturing)
        self._window.hidden_to_tray.connect(self._on_hidden_to_tray)
        self._tray.toggle_requested.connect(self._controller.toggle)
        self._tray.open_requested.connect(self._window.bring_to_front)
        self._tray.quit_requested.connect(self._quit)
        self._hold.pressed.connect(lambda: self._set_recording(True))
        self._hold.released.connect(lambda: self._set_recording(False))

        self._apply_hotkeys()
        self._window.show()

    def shutdown(self) -> None:
        self._activation.close()
        self._hold.stop()
        self._hotkeys.close()
        self._controller.shutdown()
        self._models.close()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _quit(self) -> None:
        self._window.prepare_quit()
        QApplication.quit()

    def _apply_hotkeys(self) -> None:
        hotkeys = self._config.hotkeys
        failed = self._hotkeys.set_bindings({name: getattr(hotkeys, name) for name in _HOTKEY_MODES})
        self._tray.set_hotkey_hints(self._config)
        self._window.apply_config(self._config)
        if not failed:
            return
        log.warning("Hotkeys not registered: %s", failed)
        busy = any(code == _ERROR_HOTKEY_ALREADY_REGISTERED for code in failed.values())
        labels = ", ".join(HOTKEY_LABELS[name] for name in failed)
        reason = "занято другой программой" if busy else "не удалось зарегистрировать"
        message = f"Сочетание ({labels}) {reason}. Выберите другое."
        self._window.show_settings_status(message, error=True)
        if not self._window.isVisible():
            self._tray.show_message(message, error=True)

    def _on_capturing(self, capturing: bool) -> None:
        if capturing:
            self._hotkeys.set_bindings({})  # otherwise capturing a bound combination would toggle a mode
        else:
            self._apply_hotkeys()

    def _save_settings(self, new_config: AppConfig) -> None:
        try:
            save_config(config_path(), new_config)
        except OSError:
            log.exception("Cannot save config")
            self._window.show_settings_status("Не удалось сохранить настройки.", error=True)
            return
        self._config = new_config
        self._models.unload_delay_s = new_config.unload_delay_s
        if self._controller.state(Mode.OUTGOING) is ModeState.ACTIVE:
            self._hold.start(new_config.hotkeys.outgoing_hold.vk)
        self._window.show_settings_status("Сохранено.")
        self._apply_hotkeys()

    def _on_mode_state(self, mode: Mode, state: ModeState) -> None:
        self._tray.set_state(mode, state)
        self._window.set_state(mode, state)
        if mode is Mode.OUTGOING:
            if state is ModeState.ACTIVE:
                self._hold.start(self._config.hotkeys.outgoing_hold.vk)
            else:
                self._hold.stop()

    def _on_mode_error(self, mode: Mode, message: str) -> None:
        self._tray.show_message(f"Режим не включён: {message}", error=True)

    def _set_recording(self, recording: bool) -> None:
        self._tray.set_recording(recording)
        self._window.set_recording(recording)

    def _on_hidden_to_tray(self) -> None:
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self._tray.show_message("Translater работает в трее. Горячие клавиши активны.")

    def _on_activation(self) -> None:
        while (socket := self._activation.nextPendingConnection()) is not None:
            socket.disconnected.connect(socket.deleteLater)
        self._window.bring_to_front()


def _setup_logging() -> None:
    directory = log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / "translater.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    sys.excepthook = _log_unhandled
    threading.excepthook = lambda args: _log_unhandled(args.exc_type, args.exc_value, args.exc_traceback)


def _log_unhandled(exc_type: type[BaseException], exc: BaseException | None, tb: TracebackType | None) -> None:
    logging.getLogger("unhandled").critical("Unhandled exception", exc_info=(exc_type, exc, tb))
