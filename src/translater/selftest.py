"""Packaging check for CI: Translater.exe --self-test <report> imports every runtime dependency."""

from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path


def run(report: Path) -> int:
    results: dict[str, object] = {}
    try:
        import numpy as np

        from translater.audio.resample import MonoResampler
        from translater.audio.segmenter import SpeechSegmenter
        from translater.audio.vad import SileroVad
        from translater.inference import worker
        from translater.inference.process import InferenceProcess

        segmenter = SpeechSegmenter(SileroVad())
        results["vad_phrases"] = len(segmenter.feed(np.zeros(16000, dtype=np.float32)))
        results["resampled"] = len(MonoResampler(48000, 2)(np.zeros(9600, dtype=np.float32)))
        if sys.platform == "win32":
            import comtypes  # noqa: F401
            import pyaudiowpatch  # noqa: F401

            from translater.audio import capture  # noqa: F401
            from translater.win32 import processes

            results["windowed_processes"] = len(processes.windowed_processes())

        with tempfile.TemporaryDirectory() as tmp:
            settings = worker.WorkerSettings(tmp, {}, 1, str(Path(tmp) / "worker.log"))
            process = InferenceProcess(worker.run, settings)
            try:
                results["worker"] = process.call("probe", None)
            finally:
                process.stop()
        results["ok"] = True
    except BaseException:
        results["ok"] = False
        results["error"] = traceback.format_exc()
    report.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if results["ok"] else 1
