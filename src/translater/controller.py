from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from translater.models import ModelManager

log = logging.getLogger(__name__)


class Mode(StrEnum):
    INCOMING = "incoming"
    OUTGOING = "outgoing"


class ModeState(StrEnum):
    IDLE = "idle"
    LOADING = "loading"
    ACTIVE = "active"


class ModeRunner(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


@dataclass
class _Slot:
    state: ModeState = ModeState.IDLE
    generation: int = 0


class ModeController:
    """Mode lifecycle IDLE -> LOADING -> ACTIVE -> IDLE.
    Callbacks may run on the model executor thread and must be thread-safe."""

    def __init__(
        self,
        models: ModelManager,
        requirements: Mapping[Mode, tuple[str, ...]],
        runners: Mapping[Mode, ModeRunner],
        on_state: Callable[[Mode, ModeState], None],
        on_error: Callable[[Mode, BaseException], None],
    ) -> None:
        self._models = models
        self._requirements = dict(requirements)
        self._runners = dict(runners)
        self._on_state = on_state
        self._on_error = on_error
        self._lock = threading.RLock()
        self._slots = {mode: _Slot() for mode in Mode}

    def state(self, mode: Mode) -> ModeState:
        with self._lock:
            return self._slots[mode].state

    def toggle(self, mode: Mode) -> None:
        with self._lock:
            idle = self._slots[mode].state is ModeState.IDLE
        if idle:
            self.activate(mode)
        else:
            self.deactivate(mode)

    def activate(self, mode: Mode) -> None:
        with self._lock:
            slot = self._slots[mode]
            if slot.state is not ModeState.IDLE:
                return
            slot.generation += 1
            generation = slot.generation
            self._set_state(mode, ModeState.LOADING)
        future = self._models.acquire(self._requirements.get(mode, ()))
        future.add_done_callback(lambda f: self._on_loaded(mode, generation, f))

    def deactivate(self, mode: Mode) -> None:
        with self._lock:
            slot = self._slots[mode]
            if slot.state is ModeState.IDLE:
                return
            was_active = slot.state is ModeState.ACTIVE
            slot.generation += 1
            self._set_state(mode, ModeState.IDLE)
        if not was_active:
            return  # _on_loaded of the cancelled generation releases the models
        # Outside the lock: a runner may wait for the model executor, whose callbacks take the lock.
        runner = self._runners.get(mode)
        try:
            if runner is not None:
                runner.stop()
        except Exception:
            log.exception("Runner %s stop failed", mode)
        finally:
            self._models.release(self._requirements.get(mode, ()))

    def shutdown(self) -> None:
        for mode in Mode:
            self.deactivate(mode)

    def _on_loaded(self, mode: Mode, generation: int, future: Future[None]) -> None:
        error = future.exception()
        with self._lock:
            slot = self._slots[mode]
            current = slot.generation == generation and slot.state is ModeState.LOADING
            if error is None and current:
                error = self._start_runner(mode)
                if error is None:
                    self._set_state(mode, ModeState.ACTIVE)
                    return
            self._models.release(self._requirements.get(mode, ()))
            if not current:
                return
            self._set_state(mode, ModeState.IDLE)
        log.error("Mode %s failed to start", mode, exc_info=error)
        self._on_error(mode, error)

    def _start_runner(self, mode: Mode) -> BaseException | None:
        runner = self._runners.get(mode)
        if runner is None:
            return None
        try:
            runner.start()
        except Exception as exc:
            return exc
        return None

    def _set_state(self, mode: Mode, state: ModeState) -> None:
        self._slots[mode].state = state
        log.info("Mode %s -> %s", mode, state)
        self._on_state(mode, state)
