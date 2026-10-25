"""Packaging check for CI: Translater.exe --self-test <report> imports every runtime dependency."""

from __future__ import annotations

import json
import sys
import tempfile
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any


def run(report: Path) -> int:
    results: dict[str, Any] = {}

    def step(name: str, action: Callable[[], Any]) -> None:
        results[name] = "started"
        report.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        results[name] = action()
        report.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    try:
        step("audio", _audio)
        if sys.platform == "win32":
            step("windows", _windows)
        step("worker", lambda: _worker(report.with_name(report.name + ".worker.log")))
        results["ok"] = True
    except BaseException:
        results["ok"] = False
        results["error"] = traceback.format_exc()
    report.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if results["ok"] else 1


def _audio() -> dict[str, int]:
    import numpy as np

    from translater.audio.resample import MonoResampler
    from translater.audio.segmenter import SpeechSegmenter
    from translater.audio.vad import SileroVad

    segmenter = SpeechSegmenter(SileroVad())
    return {
        "vad_phrases": len(segmenter.feed(np.zeros(16000, dtype=np.float32))),
        "resampled": len(MonoResampler(48000, 2)(np.zeros(9600, dtype=np.float32))),
    }


def _windows() -> dict[str, int]:
    import comtypes  # noqa: F401
    import pyaudiowpatch  # noqa: F401

    from translater.audio import capture  # noqa: F401
    from translater.win32 import processes

    return {"windowed_processes": len(processes.windowed_processes())}


def _worker(log_file: Path) -> dict[str, Any]:
    from translater.inference import worker
    from translater.inference.process import InferenceProcess

    with tempfile.TemporaryDirectory() as tmp:
        settings = worker.WorkerSettings(tmp, {}, 1, str(log_file))
        process = InferenceProcess(worker.run, settings)
        try:
            return process.call("probe", None)
        finally:
            process.stop()
