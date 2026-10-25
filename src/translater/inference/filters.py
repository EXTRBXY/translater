from __future__ import annotations

import re

# Whisper fills silence and noise with phrases memorised from video subtitles.
_HALLUCINATIONS = frozenset(
    {
        "you",
        "bye",
        "thank you",
        "thanks",
        "thanks for watching",
        "thank you for watching",
        "thank you so much for watching",
        "please subscribe",
        "subtitles by the amara org community",
        "продолжение следует",
        "спасибо за просмотр",
        "спасибо за внимание",
        "подписывайтесь на канал",
        "субтитры сделал dimatorzok",
        "субтитры создавал dimatorzok",
        "редактор субтитров а семкин корректор а егорова",
    }
)
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)
_REPEAT = re.compile(r"\b(\w+)(?:\W+\1\b){3,}", re.IGNORECASE | re.UNICODE)


def normalize(text: str) -> str:
    return _NON_WORD.sub(" ", text.lower()).strip()


def is_meaningful(text: str) -> bool:
    key = normalize(text)
    return bool(key) and key not in _HALLUCINATIONS and not _REPEAT.search(text)
