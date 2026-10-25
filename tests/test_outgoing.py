from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from translater.audio import SAMPLE_RATE
from translater.outgoing import OutgoingRunner, Utterance


@dataclass(frozen=True)
class Result:
    text: str
    translation: str


class FakeMic:
    description = "микрофон тест"

    def __init__(self) -> None:
        self.on_audio = None

    def start(self, on_audio) -> None:
        self.on_audio = on_audio

    def stop(self) -> None:
        self.on_audio = None

    def speak(self, seconds: float) -> None:
        for _ in range(int(seconds * 50)):
            self.on_audio(np.full(SAMPLE_RATE // 50, 0.1, dtype=np.float32))


class FakePlayer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.played: list[int] = []
        self.stopped = 0
        self.closed = False
        self.complete = None

    def play(self, samples, rate, on_complete=None) -> None:
        self.played.append(len(samples))
        self.complete = on_complete

    def stop(self) -> None:
        self.stopped += 1

    def close(self) -> None:
        self.closed = True


class FakeSpeech:
    voice_name = "Microsoft Zira"

    def synthesize(self, text: str):
        return np.zeros(len(text) * 100, dtype=np.float32), 22050


HELLO = Result("привет", "hello")


class Harness:
    def __init__(self, voice_available: bool = True, result: Result | None = HELLO) -> None:
        self.mic = FakeMic()
        self.voice = FakePlayer("CABLE Input")
        self.cue = FakePlayer("Наушники")
        self.results: list[Utterance | None] = []
        self.sources: list[str] = []
        self.done = threading.Event()
        self.clips: list[int] = []

        def voice_factory():
            if not voice_available:
                raise LookupError("CABLE")
            return self.voice

        def process(clip):
            self.clips.append(len(clip))
            return result

        def on_result(utterance):
            self.results.append(utterance)

        self.runner = OutgoingRunner(
            mic_factory=lambda: self.mic,
            synthesizer_factory=FakeSpeech,
            voice_player_factory=voice_factory,
            cue_player_factory=lambda: self.cue,
            executor=ThreadPoolExecutor(max_workers=1),
            process_clip=process,
            max_record_s=lambda: 2,
            on_result=on_result,
            on_source=self.sources.append,
            on_failure=lambda exc: None,
        )

    def record(self, seconds: float) -> None:
        self.runner.begin_recording()
        self.mic.speak(seconds)
        self.runner.end_recording()

    def wait_until(self, predicate) -> None:
        for _ in range(200):
            if predicate():
                return
            threading.Event().wait(0.01)
        raise AssertionError("timeout")


def test_records_translates_cues_and_plays_on_ptt() -> None:
    h = Harness()
    h.runner.start()
    h.mic.speak(1)  # before the key: only the pre-roll is kept
    h.record(1)
    h.wait_until(lambda: h.cue.played)

    assert h.results == [Utterance("привет", "hello")]
    assert 1.2 * SAMPLE_RATE <= h.clips[0] <= 1.4 * SAMPLE_RATE  # 1 s speech + 0.3 s pre-roll
    assert h.voice.played == []

    h.runner.ptt_pressed()
    assert h.voice.played == [500]
    h.runner.ptt_released()
    assert h.voice.stopped == 1

    h.runner.ptt_pressed()  # replayed until it finishes once
    h.voice.complete()
    h.runner.ptt_pressed()
    assert len(h.voice.played) == 2
    assert "Microsoft Zira" in h.sources[0]
    h.runner.stop()
    assert h.voice.closed and h.cue.closed


def test_short_press_is_ignored_and_recording_is_capped() -> None:
    h = Harness()
    h.runner.start()
    h.record(0.1)
    h.record(5)
    h.wait_until(lambda: h.results)
    h.runner.stop()
    assert len(h.clips) == 1
    assert h.clips[0] <= 2.4 * SAMPLE_RATE


def test_ready_clip_plays_immediately_when_ptt_already_held() -> None:
    h = Harness()
    h.runner.start()
    h.runner.ptt_pressed()
    h.record(1)
    h.wait_until(lambda: h.voice.played)
    h.runner.stop()
    assert h.cue.played == []


def test_works_without_virtual_cable() -> None:
    h = Harness(voice_available=False)
    h.runner.start()
    h.record(1)
    h.wait_until(lambda: h.results)
    h.runner.ptt_pressed()
    h.runner.stop()
    assert "VB-Cable" in h.sources[0]
    assert h.voice.played == []


def test_unrecognized_speech_reports_none() -> None:
    h = Harness(result=None)
    h.runner.start()
    h.record(1)
    h.wait_until(lambda: h.results)
    h.runner.stop()
    assert h.results == [None]
    assert h.cue.played == []
