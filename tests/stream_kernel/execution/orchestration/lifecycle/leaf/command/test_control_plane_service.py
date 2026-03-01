from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service import (
    DefaultLeafWorkerControlPlaneService,
    leaf_worker_process_entry,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafHelloEvent,
)


@dataclass
class _StopEvent:
    set_calls: int = 0
    wait_calls: list[float] = None  # type: ignore[assignment]
    _is_set: bool = True

    def __post_init__(self) -> None:
        if self.wait_calls is None:
            self.wait_calls = []

    def is_set(self) -> bool:
        return self._is_set

    def wait(self, timeout: float) -> bool:
        self.wait_calls.append(timeout)
        return self._is_set


@dataclass
class _ControlPipe:
    sent: list[object]

    def send(self, item: object) -> None:
        self.sent.append(item)


@dataclass
class _ExecutionIpc:
    sent: list[tuple[str, object, bool]]

    def send(self, target_id: str, payload: object, *, no_reply: bool = False):
        self.sent.append((target_id, payload, bool(no_reply)))
        return None

    def recv(self, target_id: str, *, timeout: float | None = None):
        _ = (target_id, timeout)
        return None


@dataclass
class _FlakyExecutionIpc:
    sent: list[tuple[str, object, bool]]
    failures_before_success: int

    def __post_init__(self) -> None:
        self._remaining_failures = max(0, int(self.failures_before_success))

    def send(self, target_id: str, payload: object, *, no_reply: bool = False):
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise ConnectionError("transient send failure")
        self.sent.append((target_id, payload, bool(no_reply)))
        return None

    def recv(self, target_id: str, *, timeout: float | None = None):
        _ = (target_id, timeout)
        return None


def test_default_leaf_worker_control_plane_service_returns_spawn_target() -> None:
    service = DefaultLeafWorkerControlPlaneService()

    target = service.worker_target()

    assert callable(target)
    assert target is leaf_worker_process_entry


def test_leaf_worker_process_entry_bootstraps_runtime_and_sends_leaf_hello(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service as mod

    fake_session = SimpleNamespace(
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="auto",
        runner_profile_effective="async",
        child=SimpleNamespace(runtime={"platform": {"bootstrap": {"mode": "process_supervisor"}}}),
    )
    seen: list[tuple[str, str, str]] = []

    def _bootstrap(*, bundle: object, worker_id: str, group_name: str, runner_profile_requested: str):
        _ = bundle
        seen.append((worker_id, group_name, runner_profile_requested))
        return fake_session

    monkeypatch.setattr(mod, "bootstrap_leaf_worker_runtime_from_bundle", _bootstrap)
    startup_events: list[object] = [
        ControlPlaneLeafHelloEvent(
            target_group=fake_session.group_name,
            worker_id=fake_session.worker_id,
            pid=111,
            runner_profile=fake_session.runner_profile_effective,
        )
    ]
    captured_runtime: list[dict[str, object]] = []

    def _build_startup_events(*, runtime: dict[str, object]):
        captured_runtime.append(dict(runtime))
        return startup_events

    monkeypatch.setattr(mod, "build_leaf_startup_events", _build_startup_events)
    ipc = _ExecutionIpc(sent=[])
    monkeypatch.setattr(mod, "_resolve_execution_ipc_service", lambda _session, explicit=None: ipc)
    pipe = _ControlPipe(sent=[])
    stop_event = _StopEvent(_is_set=True)

    leaf_worker_process_entry(
        stop_event,
        pipe,
        {"bundle": "raw"},
        "execution.alpha#1",
        "execution.alpha",
        "auto",
        0.01,
        "pickle",
    )

    assert seen == [("execution.alpha#1", "execution.alpha", "auto")]
    assert len(ipc.sent) == 1
    assert ipc.sent[0][0] == "execution.alpha#1"
    assert isinstance(ipc.sent[0][1], ControlPlaneLeafHelloEvent)
    assert captured_runtime
    runtime = captured_runtime[0]
    assert runtime["__process_role"] == "worker"
    assert runtime["__process_group"] == "execution.alpha"
    assert runtime["__worker_id"] == "execution.alpha#1"
    assert runtime["__runner_profile_requested"] == "auto"


def test_leaf_worker_process_entry_tolerates_missing_control_pipe(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service as mod

    monkeypatch.setattr(
        mod,
        "bootstrap_leaf_worker_runtime_from_bundle",
        lambda **_kwargs: SimpleNamespace(
            worker_id="w#1",
            group_name="g",
            runner_profile_requested="auto",
            runner_profile_effective="sync",
            child=SimpleNamespace(runtime={"platform": {"bootstrap": {"mode": "process_supervisor"}}}),
        ),
    )
    monkeypatch.setattr(
        mod,
        "build_leaf_startup_events",
        lambda **_kwargs: [ControlPlaneLeafHelloEvent(
            target_group="g",
            worker_id="w#1",
            pid=111,
            runner_profile="sync",
        )],
    )

    leaf_worker_process_entry(
        _StopEvent(_is_set=True),
        None,
        {"bundle": "raw"},
        "w#1",
        "g",
        "auto",
        0.01,
        "pickle",
    )


def test_leaf_worker_process_entry_does_not_run_inline_command_loop(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service as mod

    fake_session = SimpleNamespace(
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="auto",
        runner_profile_effective="async",
        child=SimpleNamespace(runtime={"platform": {"bootstrap": {"mode": "process_supervisor"}}}),
    )
    monkeypatch.setattr(mod, "bootstrap_leaf_worker_runtime_from_bundle", lambda **_kwargs: fake_session)
    monkeypatch.setattr(
        mod,
        "build_leaf_startup_events",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("process entry must delegate orchestration and not build startup events inline")
        ),
    )
    calls: list[tuple[object, object, object, str]] = []

    class _OrchestrationService:
        def run(
            self,
            *,
            session,
            control_pipe,
            stop_event,
            worker_id,
            group_name,
            runner_profile_requested,
            boundary_control_poll_seconds,
        ):
            _ = (group_name, boundary_control_poll_seconds)
            calls.append((session, control_pipe, stop_event, runner_profile_requested))

    monkeypatch.setattr(
        mod,
        "resolve_leaf_process_entry_orchestration_service",
        lambda _session: _OrchestrationService(),
    )

    pipe = _ControlPipe(sent=[])
    stop_event = _StopEvent(_is_set=True)
    leaf_worker_process_entry(
        stop_event,
        pipe,
        {"bundle": "raw"},
        "execution.alpha#1",
        "execution.alpha",
        "auto",
        0.25,
        "pickle",
    )

    assert pipe.sent == []
    assert len(calls) == 1
    assert calls[0][0] is fake_session
    assert calls[0][1] is pipe
    assert calls[0][2] is stop_event
    assert calls[0][3] == "auto"


def test_build_leaf_startup_events_emits_leaf_hello_from_leaf_pulse() -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service as mod

    runtime = {
        "__process_group": "execution.alpha",
        "__worker_id": "execution.alpha#1",
        "__runner_profile_requested": "async",
    }

    produced = mod.build_leaf_startup_events(runtime=runtime)

    assert len(produced) == 1
    hello = produced[0]
    assert isinstance(hello, ControlPlaneLeafHelloEvent)
    assert hello.target_group == "execution.alpha"
    assert hello.worker_id == "execution.alpha#1"
    assert hello.runner_profile == "async"


def test_leaf_startup_events_returns_empty_for_non_pulse_payload() -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service as mod

    produced = mod.build_leaf_startup_events(runtime={"x": 1}, pulse_payload={"not": "pulse"})

    assert produced == []


def test_leaf_worker_process_entry_sends_startup_events_in_order(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service as mod

    fake_session = SimpleNamespace(
        child=SimpleNamespace(runtime={}),
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="async",
        runner_profile_effective="async",
    )
    monkeypatch.setattr(mod, "bootstrap_leaf_worker_runtime_from_bundle", lambda **_kwargs: fake_session)
    sequence = [
        ControlPlaneLeafHelloEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            pid=111,
            runner_profile="async",
        ),
        {"kind": "phaseA.synthetic"},
    ]
    monkeypatch.setattr(mod, "build_leaf_startup_events", lambda **_kwargs: list(sequence))
    ipc = _ExecutionIpc(sent=[])
    monkeypatch.setattr(mod, "_resolve_execution_ipc_service", lambda _session, explicit=None: ipc)
    pipe = _ControlPipe(sent=[])

    leaf_worker_process_entry(
        _StopEvent(_is_set=True),
        pipe,
        {"bundle": "raw"},
        "execution.alpha#1",
        "execution.alpha",
        "async",
        0.01,
        "pickle",
    )

    assert [payload for _target, payload, _no_reply in ipc.sent] == sequence


def test_leaf_worker_process_entry_delegates_to_control_ingress_service(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service as mod

    fake_session = SimpleNamespace(
        child=SimpleNamespace(runtime={}),
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="async",
        runner_profile_effective="async",
    )
    monkeypatch.setattr(mod, "bootstrap_leaf_worker_runtime_from_bundle", lambda **_kwargs: fake_session)
    monkeypatch.setattr(
        mod,
        "build_leaf_startup_events",
        lambda **_kwargs: [
            ControlPlaneLeafHelloEvent(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                pid=111,
                runner_profile="async",
            )
        ],
    )
    calls: list[tuple[object, object, object, float]] = []

    class _IngressService:
        def run_until_stopped(self, *, session, control_pipe, stop_event, poll_interval_seconds):
            calls.append((session, control_pipe, stop_event, poll_interval_seconds))
            return "stop_requested"

    monkeypatch.setattr(mod, "resolve_leaf_control_ingress_service", lambda _session: _IngressService())
    ipc = _ExecutionIpc(sent=[])
    monkeypatch.setattr(mod, "_resolve_execution_ipc_service", lambda _session, explicit=None: ipc)
    pipe = _ControlPipe(sent=[])
    stop_event = _StopEvent(_is_set=False)

    leaf_worker_process_entry(
        stop_event,
        pipe,
        {"bundle": "raw"},
        "execution.alpha#1",
        "execution.alpha",
        "async",
        0.25,
        "pickle",
    )

    assert len(ipc.sent) == 1
    assert isinstance(ipc.sent[0][1], ControlPlaneLeafHelloEvent)
    assert len(calls) == 1
    assert calls[0][0] is fake_session
    assert calls[0][1] is pipe
    assert calls[0][2] is stop_event
    assert calls[0][3] == 0.25


def test_leaf_worker_process_entry_retries_startup_event_send_before_ingress(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service as mod

    fake_session = SimpleNamespace(
        child=SimpleNamespace(runtime={"platform": {"readiness": {"readiness_timeout_seconds": 2}}}),
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="async",
        runner_profile_effective="async",
    )
    monkeypatch.setattr(mod, "bootstrap_leaf_worker_runtime_from_bundle", lambda **_kwargs: fake_session)
    monkeypatch.setattr(
        mod,
        "build_leaf_startup_events",
        lambda **_kwargs: [
            ControlPlaneLeafHelloEvent(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                pid=111,
                runner_profile="async",
            )
        ],
    )
    ingress_calls: list[tuple[object, object, object, float]] = []

    class _IngressService:
        def run_until_stopped(self, *, session, control_pipe, stop_event, poll_interval_seconds):
            ingress_calls.append((session, control_pipe, stop_event, poll_interval_seconds))
            return "stop_requested"

    monkeypatch.setattr(mod, "resolve_leaf_control_ingress_service", lambda _session: _IngressService())
    ipc = _FlakyExecutionIpc(sent=[], failures_before_success=1)
    monkeypatch.setattr(mod, "_resolve_execution_ipc_service", lambda _session, explicit=None: ipc)
    pipe = _ControlPipe(sent=[])
    stop_event = _StopEvent(_is_set=False)

    leaf_worker_process_entry(
        stop_event,
        pipe,
        {"bundle": "raw"},
        "execution.alpha#1",
        "execution.alpha",
        "async",
        0.25,
        "pickle",
    )

    assert len(ipc.sent) == 1
    assert isinstance(ipc.sent[0][1], ControlPlaneLeafHelloEvent)
    assert len(ingress_calls) == 1
