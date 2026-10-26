from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Any

import numpy as np

from translater.audio import SAMPLE_RATE, AudioSource
from translater.audio.segmenter import SpeechSegmenter

log = logging.getLogger(__name__)

_MAX_QUEUE = 3
_MAX_AGE_S = 8.0
_LEVEL_LOG_SAMPLES = 10 * SAMPLE_RATE
RECENT_AUDIO_S = 60


@dataclass(frozen=True)
class Subtitle:
    text: str
    original: str | None


@dataclass
class IncomingStats:
    phrases: int = 0
    shown: int = 0
    skipped: int = 0


class IncomingRunner:
    """Game audio -> phrases (VAD) -> recognition and translation on the model executor -> subtitles."""

    def __init__(
        self,
        source_factory: Callable[[], AudioSource],
        segmenter_factory: Callable[[], SpeechSegmenter],
        executor: Executor,
        process_phrase: Callable[[np.ndarray], Any],
        on_subtitle: Callable[[Subtitle], None],
        on_source: Callable[[str], None],
        on_failure: Callable[[BaseException], None],
        on_stats: Callable[[IncomingStats], None] = lambda _stats: None,
    ) -> None:
        self._source_factory = source_factory
        self._segmenter_factory = segmenter_factory
        self._executor = executor
        self._process_phrase = process_phrase
        self._on_subtitle = on_subtitle
        self._on_source = on_source
        self._on_failure = on_failure
        self._on_stats = on_stats
        self._stats = IncomingStats()
        self._queue: deque[tuple[float, np.ndarray]] = deque(maxlen=_MAX_QUEUE)
        self._recent: deque[np.ndarray] = deque()
        self._recent_samples = 0
        self._recent_lock = threading.Lock()
        self._condition = threading.Condition()
        self._running = False
        self._source: AudioSource | None = None
        self._segmenter: SpeechSegmenter | None = None
        self._worker: threading.Thread | None = None

    def start(self) -> None:
        with self._recent_lock:
            self._recent.clear()
            self._recent_samples = 0
        self._stats = IncomingStats()
        self._level_samples = 0
        self._level_energy = 0.0
        self._segmenter = self._segmenter_factory()
        self._running = True
        self._worker = threading.Thread(target=self._process_loop, name="incoming", daemon=True)
        self._worker.start()
        try:
            source = self._source_factory()
            source.start(self._on_audio)
        except BaseException:
            self.stop()
            raise
        self._source = source
        self._on_source(source.description)

    def stop(self) -> None:
        source, self._source = self._source, None
        if source is not None:
            source.stop()
        with self._condition:
            self._running = False
            self._queue.clear()
            self._condition.notify_all()
        if self._worker is not None:
            self._worker.join()
            self._worker = None

    def _on_audio(self, samples: np.ndarray) -> None:
        segmenter = self._segmenter
        if segmenter is None or not len(samples):
            return
        self._remember(samples)
        phrases = segmenter.feed(samples)
        self._log_level(segmenter, samples)
        if phrases:
            with self._condition:
                self._queue.extend((time.monotonic(), phrase) for phrase in phrases)  # deque drops the oldest
                self._condition.notify()

    def recent_audio(self) -> np.ndarray:
        """Last RECENT_AUDIO_S seconds of the captured stream (16 kHz mono); kept only in memory."""
        with self._recent_lock:
            return np.concatenate(self._recent) if self._recent else np.zeros(0, dtype=np.float32)

    def _remember(self, samples: np.ndarray) -> None:
        with self._recent_lock:
            self._recent.append(samples)
            self._recent_samples += len(samples)
            while self._recent_samples - len(self._recent[0]) >= RECENT_AUDIO_S * SAMPLE_RATE:
                self._recent_samples -= len(self._recent.popleft())

    def _log_level(self, segmenter: SpeechSegmenter, samples: np.ndarray) -> None:
        self._level_samples += len(samples)
        self._level_energy += float(np.dot(samples, samples))
        if self._level_samples < _LEVEL_LOG_SAMPLES:
            return
        rms = (self._level_energy / self._level_samples) ** 0.5
        log.info(
            "Audio %.0f s: rms %.4f, peak speech probability %.2f",
            self._level_samples / SAMPLE_RATE,
            rms,
            segmenter.peak_probability,
        )
        self._level_samples, self._level_energy, segmenter.peak_probability = 0, 0.0, 0.0

    def _process_loop(self) -> None:
        while True:
            with self._condition:
                while self._running and not self._queue:
                    self._condition.wait()
                if not self._running:
                    return
                created, phrase = self._queue.popleft()
            self._stats.phrases += 1
            if time.monotonic() - created > _MAX_AGE_S:
                self._stats.skipped += 1
                log.info("Phrase dropped: waited too long")
                self._on_stats(self._stats)
                continue
            started = time.monotonic()
            try:
                result = self._executor.submit(self._process_phrase, phrase).result()
            except Exception as exc:
                log.exception("Phrase processing failed")
                self._on_failure(exc)
                return
            log.info(
                "Phrase %.1f s processed in %d ms: %s",
                len(phrase) / SAMPLE_RATE,
                (time.monotonic() - started) * 1000,
                "shown" if result is not None else "nothing recognized",
            )
            if result is not None:
                self._stats.shown += 1
                self._on_subtitle(
                    Subtitle(result.translation or result.text, result.text if result.translation else None)
                )
            else:
                self._stats.skipped += 1
            self._on_stats(self._stats)
