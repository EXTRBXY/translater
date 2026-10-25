"""English speech with the voices built into Windows (SAPI 5).

Russian Windows exposes only female desktop voices to SAPI; the male ones (David, Mark) are installed as OneCore
voices, so their token category is enumerated as well."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

_SAFT_22KHZ_16BIT_MONO = 22
_RATE = 22050
_ENGLISH = "Language=409"
_ONECORE_VOICES = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"
PREVIEW_TEXT = "Enemy on the left, behind the truck. Cover me."


def english_voices() -> list[str]:
    """Descriptions of installed English voices, male first; callable from any thread."""
    return [description for description, _ in _voice_tokens()]


def _voice_tokens() -> list[tuple[str, Any]]:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    found: dict[str, tuple[Any, bool]] = {}
    sources = (
        lambda: win32com.client.Dispatch("SAPI.SpVoice").GetVoices(_ENGLISH),
        lambda: _category(win32com.client, _ONECORE_VOICES).EnumerateTokens(_ENGLISH),
    )
    for source in sources:
        try:
            tokens = source()
        except Exception:
            log.info("Voice category unavailable", exc_info=True)
            continue
        for i in range(tokens.Count):
            token = tokens.Item(i)
            description = str(token.GetDescription())
            if description not in found:
                found[description] = (token, _attribute(token, "Gender") == "Male")
    return [(d, token) for d, (token, _) in sorted(found.items(), key=lambda item: not item[1][1])]


def _category(client: Any, category_id: str) -> Any:
    category = client.Dispatch("SAPI.SpObjectTokenCategory")
    category.SetId(category_id, False)
    return category


def _attribute(token: Any, name: str) -> str:
    try:
        return str(token.GetAttribute(name))
    except Exception:
        return ""


class WindowsSpeech:
    """Not thread-safe: create and use it on one thread (it initialises COM there).
    voice_name empty or not working -> the first working voice, male voices first."""

    def __init__(self, voice_name: str = "") -> None:
        import win32com.client

        self._client = win32com.client
        tokens = _voice_tokens()
        if not tokens:
            raise RuntimeError("В Windows нет английского голоса. Добавьте язык English (United States) с речью.")
        self._voice = win32com.client.Dispatch("SAPI.SpVoice")
        ordered = sorted(tokens, key=lambda item: item[0] != voice_name)
        for description, token in ordered:
            try:
                self._voice.Voice = token
                self.synthesize("ok")
            except Exception:
                log.warning("Voice %s does not work through SAPI", description, exc_info=True)
                continue
            self.voice_name = description
            log.info("TTS voice: %s", description)
            return
        raise RuntimeError("Ни один английский голос Windows не работает.")

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        stream = self._client.Dispatch("SAPI.SpMemoryStream")
        stream.Format.Type = _SAFT_22KHZ_16BIT_MONO
        self._voice.AudioOutputStream = stream
        self._voice.Speak(text, 0)
        pcm = np.frombuffer(bytes(stream.GetData()), dtype=np.int16).astype(np.float32) / 32768.0
        return pcm, _RATE
