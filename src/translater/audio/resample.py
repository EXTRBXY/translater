from __future__ import annotations

import av
import numpy as np

from translater.audio import SAMPLE_RATE


class MonoResampler:
    """Interleaved PCM (frames x channels) at any rate -> float32 mono at target_rate (16 kHz by default), streaming."""

    def __init__(self, rate: int, channels: int, target_rate: int = SAMPLE_RATE) -> None:
        self._rate = rate
        self._channels = channels
        self._resampler = av.AudioResampler(format="flt", layout="mono", rate=target_rate)

    def __call__(self, interleaved: np.ndarray) -> np.ndarray:
        samples = interleaved.reshape(-1, self._channels)
        if samples.dtype == np.int16:
            samples = samples.astype(np.float32) / 32768.0
        mono = np.ascontiguousarray(samples.mean(axis=1, dtype=np.float32)).reshape(1, -1)
        frame = av.AudioFrame.from_ndarray(mono, format="flt", layout="mono")
        frame.sample_rate = self._rate
        out = [f.to_ndarray().reshape(-1) for f in self._resampler.resample(frame)]
        return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


def resample_clip(samples: np.ndarray, rate: int, target_rate: int) -> np.ndarray:
    """Whole mono clip at once, including the tail the streaming resampler keeps buffered."""
    resampler = av.AudioResampler(format="flt", layout="mono", rate=target_rate)
    frame = av.AudioFrame.from_ndarray(
        np.ascontiguousarray(samples, dtype=np.float32).reshape(1, -1), format="flt", layout="mono"
    )
    frame.sample_rate = rate
    out = [f.to_ndarray().reshape(-1) for f in [*resampler.resample(frame), *resampler.resample(None)]]
    return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)
