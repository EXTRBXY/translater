from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from translater.audio import SAMPLE_RATE, AudioSource
from translater.audio.playback import beep

log = logging.getLogger(__name__)

_PREROLL_S = 0.3
_MIN_CLIP_S = 0.4


class Synthesizer(Protocol):
    voice_name: str

    def synthesize(self, text: str) -> tuple[np.ndarray, int]: ...


class Player(Protocol):
    name: str

    def play(self, samples: np.ndarray, rate: int, on_complete: Callable[[], None] | None = None) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class Utterance:
    text: str
    translation: str


class OutgoingRunner:
    """Record while the hold key is down -> Russian speech to English text -> Windows voice,
    played into the virtual cable while the game's push-to-talk key is held."""

    def __init__(
        self,
        mic_factory: Callable[[], AudioSource],
        synthesizer_factory: Callable[[], Synthesizer],
        voice_player_factory: Callable[[], Player],
        cue_player_factory: Callable[[], Player],
        executor: Executor,
        process_clip: Callable[[np.ndarray], Any],
        max_record_s: Callable[[], int],
        monitor_volume: Callable[[], int],
        on_result: Callable[[Utterance | None], None],
        on_source: Callable[[str], None],
        on_failure: Callable[[BaseException], None],
    ) -> None:
        self._mic_factory = mic_factory
        self._synthesizer_factory = synthesizer_factory
        self._voice_player_factory = voice_player_factory
        self._cue_player_factory = cue_player_factory
        self._executor = executor
        self._process_clip = process_clip
        self._max_record_s = max_record_s
        self._monitor_volume = monitor_volume
        self._on_result = on_result
        self._on_source = on_source
        self._on_failure = on_failure
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._preroll: deque[np.ndarray] = deque()
        self._preroll_samples = 0
        self._recording: list[np.ndarray] | None = None
        self._recorded_samples = 0
        self._clips: deque[np.ndarray] = deque(maxlen=2)
        self._pending: tuple[np.ndarray, int] | None = None
        self._ptt_down = False
        self._running = False
        self._mic: AudioSource | None = None
        self._voice: Player | None = None
        self._cue: Player | None = None
        self._worker: threading.Thread | None = None
        self._ready = threading.Event()
        self._synthesizer: Synthesizer | None = None
        self._voice_status = ""

    def start(self) -> None:
        self._running = True
        self._pending = None
        self._clips.clear()
        self._ready.clear()
        self._worker = threading.Thread(target=self._process_loop, name="outgoing", daemon=True)
        self._worker.start()
        self._ready.wait()
        try:
            self._cue = self._cue_player_factory()
            try:
                self._voice = self._voice_player_factory()
            except LookupError:
                self._voice = None
                self._voice_status = "озвучка выключена: VB-Cable (CABLE Input) не найден"
            mic = self._mic_factory()
            mic.start(self._on_audio)
        except BaseException:
            self.stop()
            raise
        self._mic = mic
        self._on_source(f"{mic.description}; {self._voice_status or self._voice_description()}")

    def stop(self) -> None:
        mic, self._mic = self._mic, None
        if mic is not None:
            mic.stop()
        with self._condition:
            self._running = False
            self._recording = None
            self._clips.clear()
            self._condition.notify_all()
        if self._worker is not None:
            self._worker.join()
            self._worker = None
        for player in (self._voice, self._cue):
            if player is not None:
                player.close()
        self._voice = self._cue = None
        self._pending = None

    def begin_recording(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._recording = list(self._preroll)
            self._recorded_samples = self._preroll_samples

    def end_recording(self) -> None:
        with self._condition:
            chunks, self._recording = self._recording, None
            if not chunks:
                return
            clip = np.concatenate(chunks)
            if len(clip) < _MIN_CLIP_S * SAMPLE_RATE:
                log.info("Recording too short: %.2f s", len(clip) / SAMPLE_RATE)
                return
            self._clips.append(clip)
            self._condition.notify()

    def ptt_pressed(self) -> None:
        with self._lock:
            self._ptt_down = True
            pending = self._pending
        if pending is not None:
            self._play(pending)

    def ptt_released(self) -> None:
        with self._lock:
            self._ptt_down = False
            players = (self._voice, self._cue)
        for player in players:
            if player is not None:
                player.stop()

    def _play(self, clip: tuple[np.ndarray, int]) -> None:
        """Into the cable for the game and, quieter, into the headphones so the speaker hears when it ends."""
        voice, cue = self._voice, self._cue
        if voice is None:
            return
        voice.play(*clip, on_complete=lambda: self._played(clip))
        volume = self._monitor_volume()
        if cue is not None and volume > 0:
            samples, rate = clip
            cue.play(samples * (volume / 100), rate)

    def _played(self, clip: tuple[np.ndarray, int]) -> None:
        with self._lock:
            if self._pending is clip:
                self._pending = None

    def _on_audio(self, samples: np.ndarray) -> None:
        with self._lock:
            self._preroll.append(samples)
            self._preroll_samples += len(samples)
            while self._preroll and self._preroll_samples - len(self._preroll[0]) >= _PREROLL_S * SAMPLE_RATE:
                self._preroll_samples -= len(self._preroll.popleft())
            if self._recording is not None and self._recorded_samples < self._max_record_s() * SAMPLE_RATE:
                self._recording.append(samples)
                self._recorded_samples += len(samples)

    def _process_loop(self) -> None:
        synthesizer: Synthesizer | None = None
        try:
            synthesizer = self._synthesizer_factory()
        except Exception as exc:
            log.exception("Speech synthesizer unavailable")
            self._voice_status = f"озвучка выключена: {exc}"
        finally:
            self._synthesizer = synthesizer
            self._ready.set()
        while True:
            with self._condition:
                while self._running and not self._clips:
                    self._condition.wait()
                if not self._running:
                    return
                clip = self._clips.popleft()
            started = time.monotonic()
            try:
                result = self._executor.submit(self._process_clip, clip).result()
            except Exception as exc:
                log.exception("Clip processing failed")
                self._on_failure(exc)
                return
            log.info(
                "Recording %.1f s processed in %d ms: %s",
                len(clip) / SAMPLE_RATE,
                (time.monotonic() - started) * 1000,
                "translated" if result is not None else "nothing recognized",
            )
            utterance = Utterance(result.text, result.translation) if result is not None else None
            self._on_result(utterance)
            if utterance is not None:
                self._prepare_voice(synthesizer, utterance.translation)

    def _prepare_voice(self, synthesizer: Synthesizer | None, text: str) -> None:
        voice, cue = self._voice, self._cue
        if synthesizer is None or voice is None:
            return
        try:
            audio = synthesizer.synthesize(text)
        except Exception:
            log.exception("Speech synthesis failed")
            return
        with self._lock:
            self._pending = audio
            play_now = self._ptt_down
        if play_now:
            self._play(audio)
        elif cue is not None:
            cue.play(beep(), 48000)

    def _voice_description(self) -> str:
        voice_name = self._synthesizer.voice_name if self._synthesizer is not None else "—"
        return f"озвучка: {voice_name} → {self._voice.name if self._voice else '—'}"
