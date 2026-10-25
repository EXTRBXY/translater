from __future__ import annotations

import os

import numpy as np

_CONTEXT = 64
_STATE_SHAPE = (1, 1, 128)


class SileroVad:
    """Streaming wrapper over the Silero VAD model shipped with faster-whisper; state persists between windows."""

    def __init__(self) -> None:
        import onnxruntime
        from faster_whisper.utils import get_assets_path

        options = onnxruntime.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        options.log_severity_level = 4
        self._session = onnxruntime.InferenceSession(
            os.path.join(get_assets_path(), "silero_vad_v6.onnx"),
            providers=["CPUExecutionProvider"],
            sess_options=options,
        )
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros(_STATE_SHAPE, dtype=np.float32)
        self._c = np.zeros(_STATE_SHAPE, dtype=np.float32)
        self._context = np.zeros(_CONTEXT, dtype=np.float32)

    def __call__(self, window: np.ndarray) -> float:
        batch = np.concatenate([self._context, window])[np.newaxis, :].astype(np.float32, copy=False)
        output, self._h, self._c = self._session.run(None, {"input": batch, "h": self._h, "c": self._c})
        self._context = window[-_CONTEXT:]
        return float(np.asarray(output).reshape(-1)[0])
