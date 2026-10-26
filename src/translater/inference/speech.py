"""Model wrappers. Imported only inside the worker process."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from translater.inference.filters import collapse_repeats, decoding_limit, is_meaningful

log = logging.getLogger(__name__)

_MIN_LANGUAGE_PROBABILITY = 0.3
_SAMPLE_RATE = 16000


@dataclass(frozen=True)
class Recognition:
    language: str
    text: str


class SpeechRecognizer:
    def __init__(
        self, model_dir: Path, cpu_threads: int, device: str = "cuda", compute_type: str = "int8_float16"
    ) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(str(model_dir), device=device, compute_type=compute_type, cpu_threads=cpu_threads)
        # Library defaults for our decoding settings; the lazy generator is never run, so nothing is decoded here.
        _, info = self._model.transcribe(
            np.zeros(_SAMPLE_RATE, dtype=np.float32),
            language="en",
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=False,
        )
        self._options = info.transcription_options

    def recognize(self, audio: np.ndarray, languages: tuple[str, ...], beam_size: int) -> Recognition | None:
        """Encodes the first 30 s once and uses it for both language choice and decoding.

        With one language detection is skipped; with several the most probable of them is used.
        """
        from faster_whisper.audio import pad_or_trim
        from faster_whisper.tokenizer import Tokenizer

        extractor = self._model.feature_extractor
        features = extractor(audio)
        first_window = features[:, : min(features.shape[-1] - 1, extractor.nb_max_frames)]
        encoder_output = self._model.encode(pad_or_trim(first_window))
        language = languages[0] if len(languages) == 1 else self._detect(encoder_output, languages)
        if language is None:
            return None
        tokenizer = Tokenizer(
            self._model.hf_tokenizer, self._model.model.is_multilingual, task="transcribe", language=language
        )
        options = replace(self._options, beam_size=beam_size)
        segments = list(
            self._model.generate_segments(
                features, tokenizer, options, log_progress=False, encoder_output=encoder_output
            )
        )
        text = " ".join(s.text.strip() for s in segments).strip()  # faster-whisper already drops silent segments
        if not is_meaningful(text):
            log.info("Skipped: %d segments, empty or known hallucination after filtering (%s)", len(segments), language)
            return None
        log.info("Recognized %s, %d chars", language, len(text))
        return Recognition(language, text)

    def _detect(self, encoder_output: Any, languages: tuple[str, ...]) -> str | None:
        probabilities = {token[2:-2]: p for token, p in self._model.model.detect_language(encoder_output)[0]}
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
        result = self._translator.translate_batch(
            [tokens],
            beam_size=4,
            max_decoding_length=decoding_limit(len(tokens)),
            no_repeat_ngram_size=3,
            repetition_penalty=1.1,
        )
        return collapse_repeats(self._target.decode(result[0].hypotheses[0]))
