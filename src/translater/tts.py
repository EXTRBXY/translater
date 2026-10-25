"""English speech with the voices built into Windows (SAPI 5)."""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

_SAFT_22KHZ_16BIT_MONO = 22
_RATE = 22050
_ENGLISH_LANGID = "409"


class WindowsSpeech:
    """Not thread-safe: create and use it on one thread (it initialises COM there)."""

    def __init__(self) -> None:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        self._client = win32com.client
        self._voice = win32com.client.Dispatch("SAPI.SpVoice")
        voices = self._voice.GetVoices(f"Language={_ENGLISH_LANGID}")
        if voices.Count == 0:
            raise RuntimeError("В Windows нет английского голоса. Добавьте его: Параметры → Время и язык → Речь.")
        self._voice.Voice = voices.Item(0)
        self.voice_name = str(voices.Item(0).GetDescription())
        log.info("TTS voice: %s", self.voice_name)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        stream = self._client.Dispatch("SAPI.SpMemoryStream")
        stream.Format.Type = _SAFT_22KHZ_16BIT_MONO
        self._voice.AudioOutputStream = stream
        self._voice.Speak(text, 0)
        pcm = np.frombuffer(bytes(stream.GetData()), dtype=np.int16).astype(np.float32) / 32768.0
        return pcm, _RATE
