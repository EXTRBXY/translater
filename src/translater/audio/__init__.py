from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import numpy as np

SAMPLE_RATE = 16_000

AudioCallback = Callable[[np.ndarray], None]


class AudioSource(Protocol):
    """Delivers float32 mono 16 kHz chunks to the callback from its own thread."""

    description: str

    def start(self, on_audio: AudioCallback) -> None: ...

    def stop(self) -> None: ...
