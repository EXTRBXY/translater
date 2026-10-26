from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from multiprocessing.connection import Connection

import numpy as np
import pytest

from translater.audio.segmenter import WINDOW, SpeechSegmenter
from translater.incoming import IncomingRunner, Subtitle
from translater.inference.process import InferenceProcess, WorkerError
from translater.inference.worker import serve


@dataclass(frozen=True)
class Result:
    language: str
    text: str
    translation: str | None


class FakeSource:
    description = "тест"

    def __init__(self, audio: np.ndarray) -> None:
        self.audio = audio
        self.stopped = False

    def start(self, on_audio) -> None:
        on_audio(self.audio)

    def stop(self) -> None:
        self.stopped = True


def _speech() -> np.ndarray:
    return np.concatenate([np.zeros(WINDOW * 5), np.ones(WINDOW * 30), np.zeros(WINDOW * 20)]).astype(np.float32)


def _runner(process_phrase, subtitles: list, failures: list, source: FakeSource) -> IncomingRunner:
    done = threading.Event()

    def on_subtitle(subtitle: Subtitle) -> None:
        subtitles.append(subtitle)
        done.set()

    def on_failure(exc: BaseException) -> None:
        failures.append(str(exc))
        done.set()

    runner = IncomingRunner(
        source_factory=lambda: source,
        segmenter_factory=lambda: SpeechSegmenter(lambda w: float(w[0])),
        executor=ThreadPoolExecutor(max_workers=1),
        process_phrase=process_phrase,
        on_subtitle=on_subtitle,
        on_source=lambda _text: None,
        on_failure=on_failure,
    )
    runner.done = done  # type: ignore[attr-defined]
    return runner


def test_runner_translates_phrase_and_stops_source() -> None:
    subtitles: list[Subtitle] = []
    source = FakeSource(_speech())
    runner = _runner(lambda phrase: Result("en", "hello", "привет"), subtitles, [], source)

    runner.start()
    assert runner.done.wait(5)  # type: ignore[attr-defined]
    runner.stop()

    assert subtitles == [Subtitle("привет", "hello")]
    assert source.stopped


def test_runner_shows_russian_as_is() -> None:
    subtitles: list[Subtitle] = []
    runner = _runner(lambda phrase: Result("ru", "налево", None), subtitles, [], FakeSource(_speech()))
    runner.start()
    assert runner.done.wait(5)  # type: ignore[attr-defined]
    runner.stop()
    assert subtitles == [Subtitle("налево", None)]


def test_runner_reports_failure() -> None:
    failures: list[str] = []

    def fail(_phrase):
        raise WorkerError("crash")

    runner = _runner(fail, [], failures, FakeSource(_speech()))
    runner.start()
    assert runner.done.wait(5)  # type: ignore[attr-defined]
    runner.stop()
    assert failures == ["crash"]


def _echo_worker(conn: Connection, settings: str) -> None:
    serve(conn, {"load": lambda name: None, "unload": lambda name: None, "echo": lambda x: (settings, x), "die": _die})


def _die(_payload) -> None:
    raise SystemExit(3)


def test_inference_process_round_trip_and_lifecycle() -> None:
    process = InferenceProcess(_echo_worker, "cfg")
    assert process.load("a") == "a"
    assert process.call("echo", 5) == ("cfg", 5)
    process.unload("a")
    assert process._process is None  # last model unloaded -> worker stopped, VRAM returned
    assert process.call("echo", 1) == ("cfg", 1)  # restarts on demand
    process.stop()


def test_inference_process_reports_worker_crash() -> None:
    process = InferenceProcess(_echo_worker, "cfg")
    process.load("a")
    with pytest.raises(WorkerError):
        process.call("die", None)
    assert process.call("echo", 2) == ("cfg", 2)
    process.stop()


def _failing_worker(conn: Connection, settings: str) -> None:
    raise SystemExit(1)


def test_inference_process_reports_worker_start_failure() -> None:
    process = InferenceProcess(_failing_worker, "cfg")
    with pytest.raises(WorkerError):
        process.load("a")
    process.stop()


def test_inference_process_relays_handler_errors() -> None:
    process = InferenceProcess(_echo_worker, "cfg")
    with pytest.raises(WorkerError, match="KeyError"):
        process.call("missing", None)
    process.stop()


def test_recent_audio_keeps_last_minute_only() -> None:
    from translater.incoming import RECENT_AUDIO_S

    runner = _runner(lambda phrase: None, [], [], FakeSource(np.zeros(0, dtype=np.float32)))
    runner.start()
    chunk = np.zeros(16000, dtype=np.float32)
    for _ in range(RECENT_AUDIO_S + 15):
        runner._on_audio(chunk)
    runner.stop()
    assert RECENT_AUDIO_S * 16000 <= len(runner.recent_audio()) <= (RECENT_AUDIO_S + 1) * 16000
