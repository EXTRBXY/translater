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

from translater.audio import AudioSource
from translater.audio.segmenter import SpeechSegmenter

log = logging.getLogger(__name__)

_MAX_QUEUE = 3
_MAX_AGE_S = 8.0


@dataclass(frozen=True)
class Subtitle:
    text: str
    original: str | None


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
    ) -> None:
        self._source_factory = source_factory
        self._segmenter_factory = segmenter_factory
        self._executor = executor
        self._process_phrase = process_phrase
        self._on_subtitle = on_subtitle
        self._on_source = on_source
        self._on_failure = on_failure
        self._queue: deque[tuple[float, np.ndarray]] = deque(maxlen=_MAX_QUEUE)
        self._condition = threading.Condition()
        self._running = False
        self._source: AudioSource | None = None
        self._segmenter: SpeechSegmenter | None = None
        self._worker: threading.Thread | None = None

    def start(self) -> None:
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
        phrases = segmenter.feed(samples)
        if phrases:
            with self._condition:
                self._queue.extend((time.monotonic(), phrase) for phrase in phrases)  # deque drops the oldest
                self._condition.notify()

    def _process_loop(self) -> None:
        while True:
            with self._condition:
                while self._running and not self._queue:
                    self._condition.wait()
                if not self._running:
                    return
                created, phrase = self._queue.popleft()
            if time.monotonic() - created > _MAX_AGE_S:
                continue
            try:
                result = self._executor.submit(self._process_phrase, phrase).result()
            except Exception as exc:
                log.exception("Phrase processing failed")
                self._on_failure(exc)
                return
            if result is not None:
                self._on_subtitle(
                    Subtitle(result.translation or result.text, result.text if result.translation else None)
                )
