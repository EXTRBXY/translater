"""Runs against a real CTranslate2 Whisper model when TRANSLATER_WHISPER_TEST_MODEL points to one (CI does this)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

MODEL_DIR = os.environ.get("TRANSLATER_WHISPER_TEST_MODEL")
pytestmark = pytest.mark.skipif(not MODEL_DIR, reason="TRANSLATER_WHISPER_TEST_MODEL is not set")

_PHRASE = "Enemy on the left, behind the red truck. Cover me, I am reloading."


def _english_speech() -> np.ndarray:
    if sys.platform != "win32":
        pytest.skip("English speech is synthesized with Windows voices")
    from translater.audio.resample import resample_clip
    from translater.tts import WindowsSpeech

    pcm, rate = WindowsSpeech().synthesize(_PHRASE)
    return resample_clip(pcm, rate, 16000)


@pytest.fixture(scope="module")
def recognizer():
    from translater.inference.speech import SpeechRecognizer

    return SpeechRecognizer(Path(MODEL_DIR), cpu_threads=2, device="cpu", compute_type="int8")


def _library_text(model, audio: np.ndarray, language: str, beam_size: int) -> str:
    segments, _ = model.transcribe(
        audio,
        language=language,
        beam_size=beam_size,
        temperature=0.0,
        condition_on_previous_text=False,
        without_timestamps=True,
        vad_filter=False,
    )
    return " ".join(s.text.strip() for s in segments).strip()


@pytest.mark.parametrize("beam_size", [2, 5])
def test_single_encoder_pass_matches_library_transcribe(recognizer, monkeypatch, beam_size) -> None:
    audio = _english_speech()
    model = recognizer._model
    encode = model.encode
    calls = []
    monkeypatch.setattr(model, "encode", lambda features: calls.append(1) or encode(features))

    result = recognizer.recognize(audio, ("en", "ru"), beam_size)

    assert len(calls) == 1
    assert result is not None and result.language == "en"
    assert result.text == _library_text(model, audio, "en", beam_size)


def test_clip_longer_than_one_window_matches_library_transcribe(recognizer) -> None:
    speech = _english_speech()
    audio = np.concatenate([speech, np.zeros(16000 * 31 - len(speech), dtype=np.float32), speech])
    result = recognizer.recognize(audio, ("en",), 2)
    assert result is not None
    assert result.text == _library_text(recognizer._model, audio, "en", 2)
