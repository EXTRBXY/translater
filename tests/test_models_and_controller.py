from __future__ import annotations

from concurrent.futures import Executor, Future
from typing import Any

import pytest

from translater.controller import Mode, ModeController, ModeState
from translater.models import ModelManager, ModelSpec


class ManualExecutor(Executor):
    def __init__(self) -> None:
        self.queue: list[tuple[Future[Any], Any, tuple[Any, ...]]] = []

    def submit(self, fn, /, *args, **kwargs):  # type: ignore[override]
        future: Future[Any] = Future()
        self.queue.append((future, fn, args))
        return future

    def run_all(self) -> None:
        while self.queue:
            future, fn, args = self.queue.pop(0)
            try:
                future.set_result(fn(*args))
            except Exception as exc:
                future.set_exception(exc)


class FakeTimer:
    created: list[FakeTimer] = []

    def __init__(self, interval: float, function, args=()) -> None:
        self.function, self.args = function, args
        self.cancelled = self.started = False
        self.daemon = False
        FakeTimer.created.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        if not self.cancelled:
            self.function(*self.args)


class Recorder:
    def __init__(self) -> None:
        self.loads: list[str] = []
        self.unloads: list[str] = []

    def spec(self, name: str, fail: bool = False) -> ModelSpec:
        def load() -> str:
            if fail:
                raise RuntimeError("no GPU")
            self.loads.append(name)
            return name

        return ModelSpec(load=load, unload=self.unloads.append)


class Runner:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.started = self.stopped = 0

    def start(self) -> None:
        if self.fail:
            raise RuntimeError("no audio device")
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


@pytest.fixture(autouse=True)
def _reset_timers() -> None:
    FakeTimer.created.clear()


def _manager(recorder: Recorder, executor: ManualExecutor, delay: float = 30, **fails: bool) -> ModelManager:
    specs = {name: recorder.spec(name, fails.get(name, False)) for name in ("stt", "mt")}
    return ModelManager(specs, executor, delay, timer_factory=FakeTimer)


def test_shared_model_loads_once_and_unloads_after_last_release() -> None:
    recorder, executor = Recorder(), ManualExecutor()
    models = _manager(recorder, executor)

    models.acquire(["stt"])
    models.acquire(["stt", "mt"])
    executor.run_all()
    assert recorder.loads == ["stt", "mt"]

    models.release(["stt", "mt"])
    assert [t.args for t in FakeTimer.created] == [("mt",)]
    models.release(["stt"])
    for timer in FakeTimer.created:
        timer.fire()
    executor.run_all()
    assert sorted(recorder.unloads) == ["mt", "stt"]
    assert models.loaded() == frozenset()


def test_reacquire_cancels_pending_unload() -> None:
    recorder, executor = Recorder(), ManualExecutor()
    models = _manager(recorder, executor)
    models.acquire(["stt"])
    executor.run_all()
    models.release(["stt"])
    models.acquire(["stt"])
    FakeTimer.created[0].fire()
    executor.run_all()
    assert recorder.unloads == []
    assert recorder.loads == ["stt"]


def test_zero_delay_unloads_immediately() -> None:
    recorder, executor = Recorder(), ManualExecutor()
    models = _manager(recorder, executor, delay=0)
    models.acquire(["stt"])
    executor.run_all()
    models.release(["stt"])
    executor.run_all()
    assert recorder.unloads == ["stt"]
    assert FakeTimer.created == []


def test_over_release_raises() -> None:
    models = _manager(Recorder(), ManualExecutor())
    with pytest.raises(RuntimeError):
        models.release(["stt"])


def _controller(models: ModelManager, runner: Runner, events: list, errors: list) -> ModeController:
    return ModeController(
        models,
        {Mode.INCOMING: ("stt", "mt"), Mode.OUTGOING: ("stt",)},
        {Mode.INCOMING: runner},
        on_state=lambda mode, state: events.append((mode, state)),
        on_error=lambda mode, exc: errors.append((mode, str(exc))),
    )


def test_toggle_on_and_off() -> None:
    recorder, executor, runner, events, errors = Recorder(), ManualExecutor(), Runner(), [], []
    controller = _controller(_manager(recorder, executor), runner, events, errors)

    controller.toggle(Mode.INCOMING)
    assert controller.state(Mode.INCOMING) is ModeState.LOADING
    executor.run_all()
    assert controller.state(Mode.INCOMING) is ModeState.ACTIVE
    assert runner.started == 1

    controller.toggle(Mode.INCOMING)
    assert controller.state(Mode.INCOMING) is ModeState.IDLE
    assert runner.stopped == 1
    assert events == [
        (Mode.INCOMING, ModeState.LOADING),
        (Mode.INCOMING, ModeState.ACTIVE),
        (Mode.INCOMING, ModeState.IDLE),
    ]
    assert errors == []


def test_toggle_off_while_loading_releases_models_without_starting() -> None:
    recorder, executor, runner = Recorder(), ManualExecutor(), Runner()
    models = _manager(recorder, executor, delay=0)
    controller = _controller(models, runner, [], [])

    controller.toggle(Mode.INCOMING)
    controller.toggle(Mode.INCOMING)
    executor.run_all()

    assert controller.state(Mode.INCOMING) is ModeState.IDLE
    assert runner.started == 0
    assert models.loaded() == frozenset()


def test_load_failure_returns_to_idle_and_reports() -> None:
    recorder, executor, errors = Recorder(), ManualExecutor(), []
    models = _manager(recorder, executor, delay=0, mt=True)
    controller = _controller(models, Runner(), [], errors)

    controller.activate(Mode.INCOMING)
    executor.run_all()

    assert controller.state(Mode.INCOMING) is ModeState.IDLE
    assert errors == [(Mode.INCOMING, "no GPU")]
    executor.run_all()
    assert models.loaded() == frozenset()


def test_runner_failure_returns_to_idle_and_releases() -> None:
    recorder, executor, errors = Recorder(), ManualExecutor(), []
    models = _manager(recorder, executor, delay=0)
    controller = _controller(models, Runner(fail=True), [], errors)

    controller.activate(Mode.INCOMING)
    executor.run_all()
    executor.run_all()

    assert controller.state(Mode.INCOMING) is ModeState.IDLE
    assert errors == [(Mode.INCOMING, "no audio device")]
    assert models.loaded() == frozenset()


def test_modes_share_models() -> None:
    recorder, executor = Recorder(), ManualExecutor()
    models = _manager(recorder, executor, delay=0)
    controller = _controller(models, Runner(), [], [])

    controller.activate(Mode.INCOMING)
    controller.activate(Mode.OUTGOING)
    executor.run_all()
    controller.deactivate(Mode.INCOMING)
    executor.run_all()

    assert recorder.loads == ["stt", "mt"]
    assert models.loaded() == frozenset({"stt"})
