from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from translater.audio import SAMPLE_RATE


def save_wav(path: Path, samples: np.ndarray, rate: int = SAMPLE_RATE) -> None:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(pcm.tobytes())
