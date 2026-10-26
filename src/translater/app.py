from __future__ import annotations

import getpass
import logging
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import partial
from logging.handlers import RotatingFileHandler
from types import TracebackType
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from translater.audio import SAMPLE_RATE
from translater.audio.capture import GameAudioSource, MicrophoneSource
from translater.audio.playback import DevicePlayer
from translater.audio.segmenter import SegmenterSettings, SpeechSegmenter
from translater.audio.vad import SileroVad
from translater.audio.wav import save_wav
from translater.components import (
    CUDA,
    MT_EN_RU,
    MT_RU_EN,
    WHISPER,
    Component,
    ComponentError,
    ComponentStore,
    DownloadCancelled,
)
from translater.config import HOTKEY_LABELS, AppConfig, config_path, data_dir, load_config, log_dir, save_config
from translater.controller import Mode, ModeController, ModeState
from translater.incoming import IncomingRunner, IncomingStats, Subtitle
from translater.inference import worker
from translater.inference.process import InferenceProcess
from translater.models import ModelManager, ModelSpec
from translater.outgoing import OutgoingRunner, Utterance
from translater.tts import PREVIEW_TEXT, WindowsSpeech, english_voices
from translater.ui.hold_key import HoldKeyWatcher
from translater.ui.main_window import MainWindow
from translater.ui.overlay import SubtitleOverlay
from translater.ui.theme import COLOR_ACTIVE, COLOR_RECORDING, MODE_LABELS, STATE_COLORS, STATE_LABELS
from translater.ui.toast import StatusToast
from translater.ui.tray import TrayIcon
from translater.win32.hotkeys import GlobalHotkeys
from translater.win32.process import SingleInstance, set_below_normal_priority
from translater.win32.processes import find_pid, windowed_processes

log = logging.getLogger(__name__)

_MUTEX_NAME = "Local\\Translater.App"
_ACTIVATION_PIPE = f"Translater.App.{getpass.getuser()}"
_ACTIVATION_TIMEOUT_MS = 1000
_HOTKEY_MODES = {"incoming_toggle": Mode.INCOMING, "outgoing_toggle": Mode.OUTGOING}
_ERROR_HOTKEY_ALREADY_REGISTERED = 1409
_DISK_RESERVE_FACTOR = 2.2  # archive plus extracted files
_VIRTUAL_CABLE = "CABLE Input"

MODE_COMPONENTS: dict[Mode, tuple[Component, ...]] = {
    Mode.INCOMING: (CUDA, WHISPER, MT_EN_RU),
    Mode.OUTGOING: (CUDA, WHISPER, MT_RU_EN),
}
MODE_MODELS: dict[Mode, tuple[str, ...]] = {
    Mode.INCOMING: (worker.WHISPER, worker.MT_EN_RU),
    Mode.OUTGOING: (worker.WHISPER, worker.MT_RU_EN),
}
_MODEL_COMPONENTS = {worker.WHISPER: WHISPER, worker.MT_EN_RU: MT_EN_RU, worker.MT_RU_EN: MT_RU_EN}


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


def _list_games() -> list[tuple[str, str]]:
    own = os.path.basename(sys.executable).lower()
    return [(p.exe, p.title) for p in windowed_processes() if p.exe.lower() != own]


def _list_voices() -> list[str]:
    try:
        return english_voices()
    except Exception:
        log.exception("Cannot list Windows voices")
        return []


class _Bridge(QObject):
    """Moves callbacks from worker threads to the UI thread (queued signal delivery)."""

    hotkey = Signal(str)
    mode_state = Signal(object, object)
    mode_error = Signal(object, str)
    mode_failure = Signal(object, str)
    subtitle = Signal(object)
    source = Signal(object, str)
    stats = Signal(object)
    utterance = Signal(object)
    preview = Signal(object)
    download_progress = Signal(str, object, object)
    download_finished = Signal(object, str)


class _Application:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._tray_hint_shown = False
        self._download_cancel: threading.Event | None = None
        self._overlay: SubtitleOverlay | None = None
        self._source_text = ""
        self._bridge = _Bridge()
        self._store = ComponentStore(data_dir() / "components")
        self._inference = InferenceProcess(
            worker.run,
            worker.WorkerSettings(
                cuda_dir=str(self._store.path(CUDA)),
                model_dirs={name: str(self._store.path(c)) for name, c in _MODEL_COMPONENTS.items()},
                cpu_threads=max(1, min(4, (os.cpu_count() or 2) // 2)),
                log_file=str(log_dir() / "worker.log"),
            ),
        )
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inference")
        specs = {
            name: ModelSpec(
                load=partial(self._inference.load, name), unload=lambda _m, n=name: self._inference.unload(n)
            )
            for name in _MODEL_COMPONENTS
        }
        self._models = ModelManager(specs, self._executor, config.unload_delay_s)
        self._incoming = IncomingRunner(
            source_factory=lambda: GameAudioSource(self._config.game_exe, self._game_pid()),
            segmenter_factory=lambda: SpeechSegmenter(
                SileroVad(), SegmenterSettings.from_threshold(self._config.speech_threshold / 100)
            ),
            executor=self._executor,
            process_phrase=partial(self._recognize, "incoming"),
            on_subtitle=self._bridge.subtitle.emit,
            on_source=lambda text: self._bridge.source.emit(Mode.INCOMING, f"Источник: {text}"),
            on_failure=lambda exc: self._bridge.mode_failure.emit(Mode.INCOMING, str(exc)),
            on_stats=lambda stats: self._bridge.stats.emit(IncomingStats(stats.phrases, stats.shown, stats.skipped)),
        )
        self._outgoing = OutgoingRunner(
            mic_factory=MicrophoneSource,
            synthesizer_factory=lambda: WindowsSpeech(self._config.tts_voice),
            voice_player_factory=lambda: DevicePlayer(_VIRTUAL_CABLE),
            cue_player_factory=DevicePlayer,
            executor=self._executor,
            process_clip=partial(self._recognize, "outgoing"),
            max_record_s=lambda: self._config.max_record_s,
            monitor_volume=lambda: self._config.monitor_volume,
            on_result=self._bridge.utterance.emit,
            on_source=lambda text: self._bridge.source.emit(Mode.OUTGOING, text),
            on_failure=lambda exc: self._bridge.mode_failure.emit(Mode.OUTGOING, str(exc)),
        )
        self._controller = ModeController(
            self._models,
            MODE_MODELS,
            runners={Mode.INCOMING: self._incoming, Mode.OUTGOING: self._outgoing},
            on_state=self._bridge.mode_state.emit,
            on_error=lambda mode, exc: self._bridge.mode_error.emit(mode, str(exc)),
        )
        self._window = MainWindow(config, _list_games, _list_voices)
        self._preview_player: DevicePlayer | None = None
        self._tray = TrayIcon()
        self._toast = StatusToast()
        self._hold = HoldKeyWatcher()
        self._ptt = HoldKeyWatcher()
        self._hotkeys = GlobalHotkeys(self._bridge.hotkey.emit)
        self._activation = QLocalServer()
        self._activation.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self._activation.listen(_ACTIVATION_PIPE):
            log.warning("Activation server not started: %s", self._activation.errorString())
        self._activation.newConnection.connect(self._on_activation)

        self._bridge.hotkey.connect(lambda name: self._toggle(_HOTKEY_MODES[name]))
        self._bridge.mode_state.connect(self._on_mode_state)
        self._bridge.mode_error.connect(self._on_mode_error)
        self._bridge.mode_failure.connect(self._on_mode_failure)
        self._bridge.subtitle.connect(self._on_subtitle)
        self._bridge.source.connect(self._on_source)
        self._bridge.stats.connect(self._on_stats)
        self._bridge.utterance.connect(self._on_utterance)
        self._bridge.preview.connect(self._play_preview)
        self._bridge.download_progress.connect(self._window.set_download_progress)
        self._bridge.download_finished.connect(self._on_download_finished)
        self._window.toggle_requested.connect(self._toggle)
        self._window.settings_saved.connect(self._save_settings)
        self._window.capturing_changed.connect(self._on_capturing)
        self._window.hidden_to_tray.connect(self._on_hidden_to_tray)
        self._window.download_cancel_requested.connect(self._cancel_download)
        self._window.voice_preview_requested.connect(self._preview_voice)
        self._tray.toggle_requested.connect(self._toggle)
        self._tray.open_requested.connect(self._window.bring_to_front)
        self._tray.save_audio_requested.connect(self._save_recent_audio)
        self._tray.quit_requested.connect(self._quit)
        self._hold.pressed.connect(lambda: self._set_recording(True))
        self._hold.released.connect(lambda: self._set_recording(False))
        self._ptt.pressed.connect(self._outgoing.ptt_pressed)
        self._ptt.released.connect(self._outgoing.ptt_released)

        self._apply_hotkeys()
        self._window.show()

    def shutdown(self) -> None:
        self._cancel_download()
        self._activation.close()
        self._hold.stop()
        self._ptt.stop()
        if self._preview_player is not None:
            self._preview_player.close()
        self._hotkeys.close()
        self._controller.shutdown()
        self._models.close()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._inference.stop()

    def _quit(self) -> None:
        self._window.prepare_quit()
        QApplication.quit()

    def _game_pid(self) -> int | None:
        return find_pid(self._config.game_exe) if self._config.game_exe else None

    def _recognize(self, kind: str, audio: np.ndarray) -> Any:
        return self._inference.call(kind, worker.SpeechRequest(audio, self._config.beam_size))

    def _toggle(self, mode: Mode) -> None:
        if self._controller.state(mode) is not ModeState.IDLE:
            self._controller.deactivate(mode)
            return
        if self._download_cancel is not None:
            self._window.bring_to_front()
            return
        missing = self._store.missing(MODE_COMPONENTS.get(mode, ()))
        if not missing:
            self._controller.activate(mode)
            return
        self._offer_download(mode, missing)

    def _offer_download(self, mode: Mode, missing: tuple[Component, ...]) -> None:
        size = sum(c.size for c in missing)
        root = data_dir()
        root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(root).free
        self._window.bring_to_front()
        if free < size * _DISK_RESERVE_FACTOR:
            QMessageBox.warning(
                self._window,
                "Translater",
                f"Недостаточно места на диске {root.drive}: нужно {size * _DISK_RESERVE_FACTOR / 1e9:.1f} ГБ, "
                f"свободно {free / 1e9:.1f} ГБ.",
            )
            return
        names = "\n".join(f"• {c.title} — {c.size / 1e9:.2f} ГБ" for c in missing)
        answer = QMessageBox.question(
            self._window,
            "Translater",
            f"Для режима «{MODE_LABELS[mode]}» нужно один раз скачать компоненты ({size / 1e9:.1f} ГБ):\n\n{names}\n\n"
            "Источники: официальные пакеты NVIDIA на PyPI и Hugging Face. Файлы проверяются по контрольным суммам. "
            "Прерванная загрузка продолжится с места остановки.\n\nСкачать сейчас?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        cancel = threading.Event()
        self._download_cancel = cancel
        threading.Thread(target=self._download, args=(mode, missing, cancel), name="download", daemon=True).start()

    def _download(self, mode: Mode, components: tuple[Component, ...], cancel: threading.Event) -> None:
        try:
            for component in components:
                self._store.install(
                    component,
                    lambda done, total, c=component: self._bridge.download_progress.emit(c.title, done, total),
                    cancel,
                )
        except DownloadCancelled:
            self._bridge.download_finished.emit(mode, "cancelled")
        except (ComponentError, OSError) as exc:
            log.exception("Component download failed")
            self._bridge.download_finished.emit(mode, str(exc))
        else:
            self._bridge.download_finished.emit(mode, "")

    def _cancel_download(self) -> None:
        if self._download_cancel is not None:
            self._download_cancel.set()

    def _on_download_finished(self, mode: Mode, error: str) -> None:
        self._download_cancel = None
        self._window.hide_download()
        if error == "cancelled":
            return
        if error:
            QMessageBox.warning(self._window, "Translater", f"Загрузка не завершена.\n\n{error}")
            return
        self._tray.show_message("Компоненты загружены. Режим включается.")
        self._controller.activate(mode)

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
        source_changed = (new_config.game_exe, new_config.tts_voice) != (self._config.game_exe, self._config.tts_voice)
        self._config = new_config
        self._models.unload_delay_s = new_config.unload_delay_s
        if self._controller.state(Mode.OUTGOING) is ModeState.ACTIVE:
            self._start_outgoing_keys()
        restart_hint = source_changed and any(self._controller.state(m) is not ModeState.IDLE for m in Mode)
        self._window.show_settings_status(
            "Сохранено. Источник звука и голос применятся при следующем включении режима."
            if restart_hint
            else "Сохранено."
        )
        self._apply_hotkeys()

    def _on_mode_state(self, mode: Mode, state: ModeState) -> None:
        self._toast.show_message(f"{MODE_LABELS[mode]}: {STATE_LABELS[state].lower()}", STATE_COLORS[state])
        self._tray.set_state(mode, state)
        self._window.set_state(mode, state)
        if mode is Mode.OUTGOING:
            if state is ModeState.ACTIVE:
                self._start_outgoing_keys()
            else:
                self._hold.stop()
                self._ptt.stop()
        active = any(self._controller.state(m) is ModeState.ACTIVE for m in Mode)
        if active and self._overlay is None:
            self._overlay = SubtitleOverlay()
        elif not active and self._overlay is not None:
            self._overlay.close()
            self._overlay.deleteLater()
            self._overlay = None

    def _start_outgoing_keys(self) -> None:
        self._hold.start(self._config.hotkeys.outgoing_hold.vk)
        self._ptt.start(self._config.hotkeys.game_ptt.vk)

    def _on_mode_error(self, mode: Mode, message: str) -> None:
        self._toast.show_message(f"{MODE_LABELS[mode]}: не включён", COLOR_RECORDING)
        self._tray.show_message(f"Режим «{MODE_LABELS[mode]}» не включён: {message}", error=True)

    def _on_mode_failure(self, mode: Mode, message: str) -> None:
        self._controller.deactivate(mode)
        self._toast.show_message(f"{MODE_LABELS[mode]}: остановлен из-за ошибки", COLOR_RECORDING)
        self._tray.show_message(f"Режим «{MODE_LABELS[mode]}» остановлен из-за ошибки: {message}", error=True)

    def _on_source(self, mode: Mode, text: str) -> None:
        if mode is Mode.INCOMING:
            self._source_text = text
        self._window.set_source(mode, text)

    def _on_stats(self, stats: IncomingStats) -> None:
        if self._controller.state(Mode.INCOMING) is ModeState.ACTIVE:
            self._window.set_source(
                Mode.INCOMING,
                f"{self._source_text}\nФраз услышано: {stats.phrases}, показано: {stats.shown}, "
                f"отброшено: {stats.skipped}",
            )

    def _on_subtitle(self, subtitle: Subtitle) -> None:
        if self._overlay is not None:
            self._overlay.add_line(subtitle.text, subtitle.original)

    def _preview_voice(self, voice_name: str) -> None:
        def synthesize() -> None:
            try:
                speech = WindowsSpeech(voice_name)
                self._bridge.preview.emit((speech.voice_name, *speech.synthesize(PREVIEW_TEXT)))
            except Exception as exc:
                log.exception("Voice preview failed")
                self._bridge.preview.emit(exc)

        threading.Thread(target=synthesize, name="voice-preview", daemon=True).start()

    def _play_preview(self, result: tuple | Exception) -> None:
        if isinstance(result, Exception):
            self._window.show_settings_status(f"Не удалось воспроизвести голос: {result}", error=True)
            return
        voice_name, samples, rate = result
        try:
            if self._preview_player is None:
                self._preview_player = DevicePlayer()
            self._preview_player.play(samples, rate)
            self._window.show_settings_status(f"Звучит голос: {voice_name}")
        except (OSError, LookupError) as exc:
            log.exception("Preview playback failed")
            self._window.show_settings_status(f"Не удалось воспроизвести голос: {exc}", error=True)

    def _on_utterance(self, utterance: Utterance | None) -> None:
        if self._overlay is None:
            return
        if utterance is None:
            self._overlay.add_line("Речь не распознана, повторите")
            return
        QApplication.clipboard().setText(utterance.translation)
        self._overlay.add_line(f"Вы: {utterance.translation}", utterance.text)

    def _set_recording(self, recording: bool) -> None:
        if recording:
            self._outgoing.begin_recording()
        else:
            self._outgoing.end_recording()
        self._tray.set_recording(recording)
        self._window.set_recording(recording)

    def _save_recent_audio(self) -> None:
        samples = self._incoming.recent_audio()
        if not len(samples):
            self._tray.show_message("Нет записанного звука: сначала включите входящий перевод.", error=True)
            return
        path = log_dir() / f"game-audio-{datetime.now():%Y%m%d-%H%M%S}.wav"
        try:
            save_wav(path, samples)
        except OSError as exc:
            log.exception("Cannot save diagnostic audio")
            self._tray.show_message(f"Не удалось сохранить звук: {exc}", error=True)
            return
        log.info("Diagnostic audio saved: %s, %.0f s", path.name, len(samples) / SAMPLE_RATE)
        self._toast.show_message(f"Сохранено {len(samples) / SAMPLE_RATE:.0f} с звука: {path.name}", COLOR_ACTIVE)

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
