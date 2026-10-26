from __future__ import annotations

import numpy as np

from translater.audio.resample import MonoResampler
from translater.audio.segmenter import WINDOW, SegmenterSettings, SpeechSegmenter
from translater.inference.filters import collapse_repeats, decoding_limit, is_meaningful


def _stream(pattern: list[tuple[float, int]]) -> tuple[np.ndarray, list[float]]:
    """pattern: (probability, windows). Samples encode the probability so the fake VAD can read it back."""
    windows = [np.full(WINDOW, p, dtype=np.float32) for p, count in pattern for _ in range(count)]
    return np.concatenate(windows), [float(w[0]) for w in windows]


def _segmenter(**overrides: float) -> SpeechSegmenter:
    return SpeechSegmenter(lambda window: float(window[0]), SegmenterSettings(**overrides))


def test_phrase_ends_after_pause_and_keeps_padding() -> None:
    audio, _ = _stream([(0.0, 20), (0.9, 30), (0.0, 20)])
    phrases = _segmenter().feed(audio)
    assert len(phrases) == 1
    windows = len(phrases[0]) // WINDOW
    assert 30 < windows < 30 + 6 + 16  # speech + leading padding + trailing padding


def test_short_blip_is_ignored() -> None:
    audio, _ = _stream([(0.9, 2), (0.0, 30)])
    assert _segmenter().feed(audio) == []


def test_long_speech_is_split() -> None:
    audio, _ = _stream([(0.9, 200)])
    phrases = _segmenter(max_phrase_s=2.0).feed(audio)
    assert len(phrases) == 3
    assert all(len(p) <= 2.0 * 16000 + WINDOW for p in phrases)


def test_feed_accepts_arbitrary_chunks_and_flush_returns_tail() -> None:
    audio, _ = _stream([(0.0, 5), (0.9, 40)])
    segmenter = _segmenter()
    assert sum(len(segmenter.feed(chunk)) for chunk in np.array_split(audio, 37)) == 0
    assert len(segmenter.flush()) == 1


def test_resampler_outputs_16k_mono_float() -> None:
    resample = MonoResampler(44100, 2)
    out = np.concatenate([resample(np.zeros((441, 2), dtype=np.int16)) for _ in range(100)])
    assert out.dtype == np.float32
    assert abs(len(out) - 16000) < 200


def test_hallucination_filter() -> None:
    assert not is_meaningful("Thank you.")
    assert not is_meaningful("  Продолжение следует...  ")
    assert not is_meaningful("...")
    assert not is_meaningful("go go go go go go")
    assert is_meaningful("Enemy on the left, behind the truck")
    assert is_meaningful("Спасибо, прикрой меня")


def test_collapse_repeats() -> None:
    assert collapse_repeats("1 2 3 3 4 4 5 5 5 5 5 5 5 1 1 1 1 1") == "1 2 3 3 4 4 5 1"
    assert collapse_repeats("go go go go go now") == "go now"
    assert collapse_repeats("Enemy on the left, behind the truck") == "Enemy on the left, behind the truck"


def test_decoding_limit_follows_source_length() -> None:
    assert decoding_limit(5) == 20
    assert decoding_limit(500) == 256


def test_segmenter_settings_from_threshold() -> None:
    settings = SegmenterSettings.from_threshold(0.35)
    assert settings.threshold == 0.35
    assert abs(settings.release_threshold - 0.2) < 1e-9
    assert SegmenterSettings.from_threshold(0.1).release_threshold == 0.05


def test_lower_threshold_catches_weak_speech() -> None:
    audio, _ = _stream([(0.0, 10), (0.4, 30), (0.0, 20)])
    assert _segmenter().feed(audio) == []
    segmenter = SpeechSegmenter(lambda w: float(w[0]), SegmenterSettings.from_threshold(0.35))
    assert len(segmenter.feed(audio)) == 1


def test_save_wav_roundtrip(tmp_path) -> None:
    import wave

    from translater.audio.wav import save_wav

    path = tmp_path / "a.wav"
    save_wav(path, np.linspace(-2, 2, 16000, dtype=np.float32))
    with wave.open(str(path)) as file:
        assert (file.getframerate(), file.getnchannels(), file.getnframes()) == (16000, 1, 16000)
