from __future__ import annotations

import gc
import logging
import threading
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Executor, Future
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelSpec:
    load: Callable[[], Any]
    unload: Callable[[Any], None] | None = None


class Cancellable(Protocol):
    daemon: bool

    def start(self) -> None: ...

    def cancel(self) -> None: ...


TimerFactory = Callable[..., Cancellable]


class ModelManager:
    """Reference-counted lazy models. Load and unload run on the given executor,
    which must be single-threaded: it also serializes all GPU work."""

    def __init__(
        self,
        specs: Mapping[str, ModelSpec],
        executor: Executor,
        unload_delay_s: float,
        timer_factory: TimerFactory = threading.Timer,
    ) -> None:
        self._specs = dict(specs)
        self._executor = executor
        self._timer_factory = timer_factory
        self._lock = threading.Lock()
        self._refs: dict[str, int] = {}
        self._models: dict[str, Any] = {}
        self._timers: dict[str, Cancellable] = {}
        self.unload_delay_s = unload_delay_s

    def get(self, name: str) -> Any:
        with self._lock:
            return self._models[name]

    def loaded(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._models)

    def acquire(self, names: Iterable[str]) -> Future[None]:
        names = tuple(names)
        unknown = [n for n in names if n not in self._specs]
        if unknown:
            raise KeyError(f"unknown models: {unknown}")
        with self._lock:
            for name in names:
                self._refs[name] = self._refs.get(name, 0) + 1
                timer = self._timers.pop(name, None)
                if timer is not None:
                    timer.cancel()
        return self._executor.submit(self._ensure_loaded, names)

    def release(self, names: Iterable[str]) -> None:
        with self._lock:
            for name in names:
                refs = self._refs.get(name, 0) - 1
                if refs < 0:
                    raise RuntimeError(f"model {name!r} released more times than acquired")
                self._refs[name] = refs
                if refs == 0:
                    self._schedule_unload(name)

    def close(self) -> None:
        with self._lock:
            timers = list(self._timers.values())
            self._timers.clear()
        for timer in timers:
            timer.cancel()

    def _ensure_loaded(self, names: tuple[str, ...]) -> None:
        for name in names:
            with self._lock:
                if name in self._models:
                    continue
            log.info("Loading model %s", name)
            model = self._specs[name].load()
            with self._lock:
                self._models[name] = model

    def _schedule_unload(self, name: str) -> None:
        if self.unload_delay_s <= 0:
            self._submit_unload(name)
            return
        timer = self._timer_factory(self.unload_delay_s, self._submit_unload, args=(name,))
        timer.daemon = True
        self._timers[name] = timer
        timer.start()

    def _submit_unload(self, name: str) -> None:
        try:
            self._executor.submit(self._unload_if_unused, name)
        except RuntimeError:
            pass  # executor already shut down: the process is exiting

    def _unload_if_unused(self, name: str) -> None:
        with self._lock:
            if self._refs.get(name, 0) or name not in self._models:
                return
            model = self._models.pop(name)
        log.info("Unloading model %s", name)
        unload = self._specs[name].unload
        try:
            if unload is not None:
                unload(model)
        except Exception:
            log.exception("Model %s unload failed", name)
        del model
        gc.collect()
