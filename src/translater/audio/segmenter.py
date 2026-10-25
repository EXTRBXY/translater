from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from translater.audio import SAMPLE_RATE

WINDOW = 512  # samples per Silero VAD window at 16 kHz


@dataclass(frozen=True)
class SegmenterSettings:
    threshold: float = 0.5
    release_threshold: float = 0.35
    min_speech_ms: int = 250
    min_silence_ms: int = 500
    padding_ms: int = 200
    max_phrase_s: float = 12.0


def _windows(ms: float) -> int:
    return max(1, round(ms * SAMPLE_RATE / 1000 / WINDOW))


class SpeechSegmenter:
    """Cuts a 16 kHz mono stream into phrases: speech start above threshold, end after a pause."""

    def __init__(self, probability: Callable[[np.ndarray], float], settings: SegmenterSettings | None = None) -> None:
        self._probability = probability
        self._settings = s = settings or SegmenterSettings()
        self._min_speech = _windows(s.min_speech_ms)
        self._min_silence = _windows(s.min_silence_ms)
        self._padding = _windows(s.padding_ms)
        self._max_windows = _windows(s.max_phrase_s * 1000)
        self._pending = np.zeros(0, dtype=np.float32)
        self._before: deque[np.ndarray] = deque(maxlen=self._padding)
        self._phrase: list[np.ndarray] = []
        self._voiced = 0
        self._silence = 0

    def feed(self, samples: np.ndarray) -> list[np.ndarray]:
        self._pending = np.concatenate([self._pending, samples.astype(np.float32, copy=False)])
        phrases: list[np.ndarray] = []
        count = len(self._pending) // WINDOW
        for i in range(count):
            phrase = self._step(self._pending[i * WINDOW : (i + 1) * WINDOW])
            if phrase is not None:
                phrases.append(phrase)
        self._pending = self._pending[count * WINDOW :]
        return phrases

    def flush(self) -> list[np.ndarray]:
        phrase = self._finish(trailing_silence=self._silence)
        return [phrase] if phrase is not None else []

    def _step(self, window: np.ndarray) -> np.ndarray | None:
        probability = self._probability(window)
        s = self._settings
        if not self._phrase:
            if probability >= s.threshold:
                self._phrase = [*self._before, window]
                self._before.clear()
                self._voiced, self._silence = 1, 0
            else:
                self._before.append(window)
            return None

        self._phrase.append(window)
        if probability >= s.threshold:
            self._voiced += 1
            self._silence = 0
        elif probability < s.release_threshold:
            self._silence += 1
        if self._silence >= self._min_silence:
            return self._finish(trailing_silence=self._silence)
        if len(self._phrase) >= self._max_windows:
            return self._finish(trailing_silence=0)
        return None

    def _finish(self, trailing_silence: int) -> np.ndarray | None:
        phrase, voiced = self._phrase, self._voiced
        self._phrase, self._voiced, self._silence = [], 0, 0
        if not phrase or voiced < self._min_speech:
            return None
        keep = len(phrase) - max(0, trailing_silence - self._padding)
        return np.concatenate(phrase[:keep])
