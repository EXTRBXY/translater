"""English speech with the voices built into Windows (SAPI 5)."""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

_SAFT_22KHZ_16BIT_MONO = 22
_RATE = 22050
_ENGLISH = "Language=409"
_PREFERRED = "Gender=Male"
PREVIEW_TEXT = "Enemy on the left, behind the truck. Cover me."


def english_voices() -> list[str]:
    """Descriptions of installed English SAPI voices; callable from any thread."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    tokens = win32com.client.Dispatch("SAPI.SpVoice").GetVoices(_ENGLISH)
    return [str(tokens.Item(i).GetDescription()) for i in range(tokens.Count)]


class WindowsSpeech:
    """Not thread-safe: create and use it on one thread (it initialises COM there).
    voice_name empty or not installed -> a male English voice if there is one, else the first English voice."""

    def __init__(self, voice_name: str = "") -> None:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        self._client = win32com.client
        self._voice = win32com.client.Dispatch("SAPI.SpVoice")
        tokens = self._voice.GetVoices(_ENGLISH, _PREFERRED)  # second argument only orders the result
        if tokens.Count == 0:
            raise RuntimeError("В Windows нет английского голоса. Добавьте его: Параметры → Время и язык → Речь.")
        chosen = next(
            (tokens.Item(i) for i in range(tokens.Count) if str(tokens.Item(i).GetDescription()) == voice_name),
            tokens.Item(0),
        )
        self._voice.Voice = chosen
        self.voice_name = str(chosen.GetDescription())
        log.info("TTS voice: %s", self.voice_name)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        stream = self._client.Dispatch("SAPI.SpMemoryStream")
        stream.Format.Type = _SAFT_22KHZ_16BIT_MONO
        self._voice.AudioOutputStream = stream
        self._voice.Speak(text, 0)
        pcm = np.frombuffer(bytes(stream.GetData()), dtype=np.int16).astype(np.float32) / 32768.0
        return pcm, _RATE
