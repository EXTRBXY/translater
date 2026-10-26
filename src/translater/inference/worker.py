"""Entry point of the model worker process."""

from __future__ import annotations

import gc
import logging
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

WHISPER = "whisper"
MT_EN_RU = "mt_en_ru"
MT_RU_EN = "mt_ru_en"
_PRELOAD_DLLS = ("cublasLt64_12.dll", "cublas64_12.dll")


@dataclass(frozen=True)
class WorkerSettings:
    cuda_dir: str
    model_dirs: Mapping[str, str]
    cpu_threads: int
    log_file: str


@dataclass(frozen=True)
class SpeechRequest:
    audio: np.ndarray
    beam_size: int


@dataclass(frozen=True)
class IncomingResult:
    language: str
    text: str
    translation: str | None


@dataclass(frozen=True)
class OutgoingResult:
    text: str
    translation: str


def run(conn: Connection, settings: WorkerSettings) -> None:
    _setup_logging(settings.log_file)
    try:
        _prepare_cuda(Path(settings.cuda_dir))
        serve(conn, _Service(settings).handlers())
    except Exception:
        log.exception("Worker failed")
        raise SystemExit(1) from None  # the parent sees the closed pipe and reports the failure


def serve(conn: Connection, handlers: Mapping[str, Callable[[Any], Any]]) -> None:
    while True:
        try:
            kind, payload = conn.recv()
        except EOFError:
            return
        if kind == "exit":
            return
        try:
            conn.send((True, handlers[kind](payload)))
        except Exception as exc:
            log.exception("Request %s failed", kind)
            conn.send((False, f"{type(exc).__name__}: {exc}"))


class _Service:
    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        self._models: dict[str, Any] = {}

    def handlers(self) -> dict[str, Callable[[Any], Any]]:
        return {
            "load": self._load,
            "unload": self._unload,
            "incoming": self._incoming,
            "outgoing": self._outgoing,
            "probe": _probe,
        }

    def _load(self, name: str) -> None:
        if name in self._models:
            return
        from translater.inference.speech import SpeechRecognizer, TextTranslator

        path = Path(self._settings.model_dirs[name])
        factory = SpeechRecognizer if name == WHISPER else TextTranslator
        self._models[name] = factory(path, self._settings.cpu_threads)
        log.info("Loaded %s", name)

    def _unload(self, name: str) -> None:
        self._models.pop(name, None)
        gc.collect()
        log.info("Unloaded %s", name)

    def _incoming(self, request: SpeechRequest) -> IncomingResult | None:
        recognition = self._models[WHISPER].recognize(request.audio, ("en", "ru"), request.beam_size)
        if recognition is None:
            return None
        translation = self._models[MT_EN_RU].translate(recognition.text) if recognition.language == "en" else None
        return IncomingResult(recognition.language, recognition.text, translation)

    def _outgoing(self, request: SpeechRequest) -> OutgoingResult | None:
        recognition = self._models[WHISPER].recognize(request.audio, ("ru",), request.beam_size)
        if recognition is None:
            return None
        return OutgoingResult(recognition.text, self._models[MT_RU_EN].translate(recognition.text))


def _probe(_payload: Any) -> dict[str, Any]:
    import ctranslate2
    import faster_whisper
    import sentencepiece

    return {
        "ctranslate2": ctranslate2.__version__,
        "faster_whisper": faster_whisper.__version__,
        "sentencepiece": sentencepiece.__version__,
        "cuda_devices": ctranslate2.get_cuda_device_count(),
    }


def _prepare_cuda(directory: Path) -> None:
    if sys.platform != "win32" or not directory.is_dir():
        return
    import ctypes

    os.add_dll_directory(str(directory))
    os.environ["PATH"] = f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"  # cuDNN loads its sub-libraries by name
    for name in _PRELOAD_DLLS:
        ctypes.WinDLL(str(directory / name))


def _setup_logging(path: str) -> None:
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s worker %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
