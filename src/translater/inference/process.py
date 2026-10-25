"""Models live in a child process: stopping it is the only reliable way to return all VRAM held by the CUDA context."""

from __future__ import annotations

import logging
import multiprocessing
import threading
from collections.abc import Callable
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any

log = logging.getLogger(__name__)

_POLL_S = 0.5
_STOP_TIMEOUT_S = 5.0

WorkerEntry = Callable[[Connection, Any], None]


class WorkerError(Exception):
    pass


class InferenceProcess:
    """Blocking RPC to a lazily started worker. Calls are serialized; the worker exits when nothing is loaded."""

    def __init__(self, entry: WorkerEntry, settings: Any) -> None:
        self._entry = entry
        self._settings = settings
        self._lock = threading.Lock()
        self._process: BaseProcess | None = None
        self._conn: Connection | None = None
        self._loaded: set[str] = set()

    def load(self, name: str) -> str:
        with self._lock:
            self._call_locked("load", name)
            self._loaded.add(name)
        return name

    def unload(self, name: str) -> None:
        with self._lock:
            if name not in self._loaded:
                return
            self._loaded.discard(name)
            if self._loaded:
                self._call_locked("unload", name)
            else:
                self._stop_locked()

    def call(self, kind: str, payload: Any) -> Any:
        with self._lock:
            return self._call_locked(kind, payload)

    def stop(self) -> None:
        with self._lock:
            self._loaded.clear()
            self._stop_locked()

    def _call_locked(self, kind: str, payload: Any) -> Any:
        conn = self._ensure_started()
        try:
            conn.send((kind, payload))
            while not conn.poll(_POLL_S):
                if self._process is None or not self._process.is_alive():
                    raise EOFError
            ok, result = conn.recv()
        except (EOFError, OSError) as exc:
            code = self._process.exitcode if self._process is not None else None
            self._loaded.clear()
            self._stop_locked()
            raise WorkerError(f"Процесс моделей завершился аварийно (код {code}).") from exc
        if not ok:
            raise WorkerError(result)
        return result

    def _ensure_started(self) -> Connection:
        if self._conn is not None and self._process is not None and self._process.is_alive():
            return self._conn
        self._stop_locked()
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(
            target=self._entry, args=(child, self._settings), name="translater-models", daemon=True
        )
        process.start()
        child.close()
        self._process, self._conn = process, parent
        log.info("Model worker started, pid %s", process.pid)
        return parent

    def _stop_locked(self) -> None:
        process, conn = self._process, self._conn
        self._process = self._conn = None
        if conn is not None:
            try:
                conn.send(("exit", None))
            except (OSError, ValueError):
                pass
            conn.close()
        if process is not None:
            process.join(_STOP_TIMEOUT_S)
            if process.is_alive():
                process.kill()
                process.join(_STOP_TIMEOUT_S)
            log.info("Model worker stopped, exit code %s", process.exitcode)
