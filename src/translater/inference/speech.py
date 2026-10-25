"""Model wrappers. Imported only inside the worker process."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from translater.inference.filters import is_meaningful

log = logging.getLogger(__name__)

_MIN_LANGUAGE_PROBABILITY = 0.3


@dataclass(frozen=True)
class Recognition:
    language: str
    text: str


class SpeechRecognizer:
    def __init__(self, model_dir: Path, cpu_threads: int) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(str(model_dir), device="cuda", compute_type="int8_float16", cpu_threads=cpu_threads)

    def recognize(self, audio: np.ndarray, languages: tuple[str, ...]) -> Recognition | None:
        """With one language detection is skipped; with several the most probable of them is used."""
        language = languages[0] if len(languages) == 1 else self._detect(audio, languages)
        if language is None:
            return None
        segments, _ = self._model.transcribe(
            audio,
            language=language,
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=False,
        )
        segments = list(segments)
        text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.6).strip()
        if not is_meaningful(text):
            log.info("Skipped: %d segments, empty or known hallucination after filtering (%s)", len(segments), language)
            return None
        log.info("Recognized %s, %d chars", language, len(text))
        return Recognition(language, text)

    def _detect(self, audio: np.ndarray, languages: tuple[str, ...]) -> str | None:
        _, _, all_probabilities = self._model.detect_language(audio)
        probabilities = dict(all_probabilities)
        language = max(languages, key=lambda code: probabilities.get(code, 0.0))
        probability = probabilities.get(language, 0.0)
        if probability < _MIN_LANGUAGE_PROBABILITY:
            top = max(probabilities, key=probabilities.get)
            log.info("Skipped: language %s %.2f below threshold (most likely %s)", language, probability, top)
            return None
        return language


class TextTranslator:
    def __init__(self, model_dir: Path, cpu_threads: int) -> None:
        import ctranslate2
        import sentencepiece

        self._translator = ctranslate2.Translator(
            str(model_dir), device="cpu", compute_type="int8", inter_threads=1, intra_threads=cpu_threads
        )
        self._source = sentencepiece.SentencePieceProcessor(model_file=str(model_dir / "src.spm.model"))
        self._target = sentencepiece.SentencePieceProcessor(model_file=str(model_dir / "tgt.spm.model"))

    def translate(self, text: str) -> str:
        tokens = self._source.encode(text, out_type=str)
        result = self._translator.translate_batch([tokens], beam_size=4, max_decoding_length=256)
        return self._target.decode(result[0].hypotheses[0])
