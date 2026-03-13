from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service import (
    DefaultLeafProcessEntryOrchestrationService,
    DefaultLeafWorkerControlPlaneService,
    LeafProcessEntryOrchestrationService,
    build_leaf_startup_init_input,
    build_leaf_startup_runtime,
    leaf_worker_process_entry,
    resolve_leaf_debug_logging_service,
    resolve_leaf_process_entry_orchestration_service,
    resolve_leaf_runtime_debug_buffer,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneInitEvent,
)
from stream_kernel.observability.domain.debug import DebugMessage
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class _Orchestrator(LeafProcessEntryOrchestrationService):
    calls: list[dict[str, object]]

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
    ) -> None:
        self.calls.append(
            {
                "session": session,
                "control_pipe": control_pipe,
                "stop_event": stop_event,
                "worker_id": worker_id,
                "group_name": group_name,
                "runner_profile_requested": runner_profile_requested,
                "boundary_control_poll_seconds": boundary_control_poll_seconds,
            }
        )


@dataclass(slots=True)
class _Runner:
    run_until_stopped_calls: list[dict[str, object]]
    on_run_end_calls: int = 0
    request_stop_calls: int = 0
    run_id: str = "run"

    def request_stop(self) -> None:
        self.request_stop_calls += 1

    def run_until_stopped(self, *, poll_timeout_seconds: float, idle_timeout_seconds: float | None) -> None:
        self.run_until_stopped_calls.append(
            {
                "poll_timeout_seconds": poll_timeout_seconds,
                "idle_timeout_seconds": idle_timeout_seconds,
            }
        )

    def on_run_end(self) -> None:
        self.on_run_end_calls += 1


@dataclass(slots=True)
class _ExecutionIpc:
    binds: list[tuple[str, object]]

    def bind_local_endpoint(self, target_id: str, endpoint: object) -> None:
        self.binds.append((target_id, endpoint))


@dataclass(slots=True)
class _Ingress:
    configured: list[float]

    def configure_poll_timeout_seconds(self, timeout_seconds: float) -> None:
        self.configured.append(timeout_seconds)


@dataclass(slots=True)
class _RunnerControl:
    bound: list[object]
    cleared: int = 0

    def bind_runner_stop(self, callback: object) -> None:
        self.bound.append(callback)

    def clear_runner_stop(self) -> None:
        self.cleared += 1

    def request_stop(self) -> None:
        callback = self.bound[-1] if self.bound else None
        if callable(callback):
            callback()

    def stop_requested(self) -> bool:
        return False


@dataclass(slots=True)
class _SessionState:
    bound_sessions: list[object]
    cleared: int = 0

    def bind_session(self, session: object) -> None:
        self.bound_sessions.append(session)

    def clear_session(self) -> None:
        self.cleared += 1

    def current_session(self) -> object | None:
        return self.bound_sessions[-1] if self.bound_sessions else None


@dataclass(slots=True)
class _Finalization:
    sessions: list[object]

    def finalize(self, *, session: object) -> None:
        self.sessions.append(session)


@dataclass(slots=True)
class _DebugBuffer:
    messages: list[DebugMessage]

    def publish(self, message: DebugMessage) -> None:
        self.messages.append(message)

    def drain(self, *, max_items: int = 256) -> list[DebugMessage]:
        _ = max_items
        return []


@dataclass(slots=True)
class _DebugLoggingService:
    events: list[str]

    def configure(self, *, runtime: dict[str, object] | None, group_name: str, worker_id: str):
        _ = (runtime, group_name, worker_id)
        return None

    def bind_sink(self, sink: object | None) -> None:
        _ = sink

    def log(self, *, event: str, **fields: object) -> None:
        _ = fields
        self.events.append(event)

    def flush(self):
        return None


def test_default_leaf_worker_control_plane_service_returns_spawn_target() -> None:
    service = DefaultLeafWorkerControlPlaneService()

    target = service.worker_target()

    assert callable(target)
    assert target is leaf_worker_process_entry


def test_leaf_worker_process_entry_delegates_to_resolved_orchestrator(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service as mod

    fake_session = SimpleNamespace(
        child=SimpleNamespace(runtime={}),
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="auto",
        runner_profile_effective="async",
    )
    seen: list[tuple[str, str, str]] = []

    def _bootstrap(*, bundle: object, worker_id: str, group_name: str, runner_profile_requested: str):
        _ = bundle
        seen.append((worker_id, group_name, runner_profile_requested))
        return fake_session

    calls: list[dict[str, object]] = []
    orchestrator = _Orchestrator(calls=calls)

    monkeypatch.setattr(mod, "bootstrap_leaf_worker_runtime_from_bundle", _bootstrap)
    monkeypatch.setattr(mod, "resolve_leaf_process_entry_orchestration_service", lambda _session: orchestrator)

    control_pipe = object()
    stop_event = object()

    leaf_worker_process_entry(
        stop_event,
        control_pipe,
        {"bundle": "raw"},
        "execution.alpha#1",
        "execution.alpha",
        "auto",
        0.25,
        "pickle",
    )

    assert seen == [("execution.alpha#1", "execution.alpha", "auto")]
    assert len(calls) == 1
    assert calls[0]["session"] is fake_session
    assert calls[0]["control_pipe"] is control_pipe
    assert calls[0]["stop_event"] is stop_event
    assert calls[0]["worker_id"] == "execution.alpha#1"
    assert calls[0]["group_name"] == "execution.alpha"
    assert calls[0]["runner_profile_requested"] == "auto"
    assert calls[0]["boundary_control_poll_seconds"] == 0.25


def test_leaf_worker_process_entry_raises_when_orchestrator_missing(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service as mod

    fake_session = SimpleNamespace(
        child=SimpleNamespace(runtime={}),
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="auto",
        runner_profile_effective="async",
    )

    monkeypatch.setattr(mod, "bootstrap_leaf_worker_runtime_from_bundle", lambda **_kwargs: fake_session)
    monkeypatch.setattr(mod, "resolve_leaf_process_entry_orchestration_service", lambda _session: None)

    with pytest.raises(RuntimeError, match="LeafProcessEntryOrchestrationService binding is required"):
        leaf_worker_process_entry(
            object(),
            object(),
            {"bundle": "raw"},
            "execution.alpha#1",
            "execution.alpha",
            "auto",
            0.01,
            "pickle",
        )


def test_resolve_leaf_process_entry_orchestration_service_accepts_only_protocol() -> None:
    calls: list[dict[str, object]] = []
    orchestrator = _Orchestrator(calls=calls)

    session = SimpleNamespace(
        child=SimpleNamespace(
            scenario_scope=SimpleNamespace(
                resolve=lambda port_type, data_type: orchestrator,
            )
        )
    )

    resolved = resolve_leaf_process_entry_orchestration_service(session)

    assert resolved is orchestrator


def test_resolve_leaf_process_entry_orchestration_service_accepts_protocol_compatible_instance() -> None:
    class _Duck:
        def run(self, **_kwargs) -> None:  # pragma: no cover
            return

    session = SimpleNamespace(
        child=SimpleNamespace(
            scenario_scope=SimpleNamespace(
                resolve=lambda port_type, data_type: _Duck(),
            )
        )
    )

    resolved = resolve_leaf_process_entry_orchestration_service(session)

    assert resolved is not None


def test_resolve_leaf_runtime_debug_buffer_accepts_protocol_compatible_instance() -> None:
    buffer = _DebugBuffer(messages=[])
    session = SimpleNamespace(
        child=SimpleNamespace(
            scenario_scope=SimpleNamespace(
                resolve=lambda port_type, data_type: buffer,
            )
        )
    )

    resolved = resolve_leaf_runtime_debug_buffer(session)

    assert resolved is buffer


def test_resolve_leaf_debug_logging_service_accepts_protocol_compatible_instance() -> None:
    service = _DebugLoggingService(events=[])
    session = SimpleNamespace(
        child=SimpleNamespace(
            scenario_scope=SimpleNamespace(
                resolve=lambda port_type, data_type: service,
            )
        )
    )

    resolved = resolve_leaf_debug_logging_service(session)

    assert resolved is service


def test_build_leaf_startup_runtime_sets_worker_role_by_default() -> None:
    session = SimpleNamespace(
        child=SimpleNamespace(runtime={}),
        runner_profile_requested="auto",
    )

    runtime = build_leaf_startup_runtime(
        session=session,
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="auto",
    )

    assert runtime["__process_role"] == "worker"
    assert runtime["__process_group"] == "execution.alpha"
    assert runtime["__worker_id"] == "execution.alpha#1"
    assert runtime["__runner_profile_requested"] == "auto"


def test_default_leaf_process_entry_orchestration_service_runs_runner_and_finalizes(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service as mod

    ipc = _ExecutionIpc(binds=[])
    ingress = _Ingress(configured=[])
    runner_control = _RunnerControl(bound=[])
    session_state = _SessionState(bound_sessions=[])
    finalization = _Finalization(sessions=[])
    runner = _Runner(run_until_stopped_calls=[])

    enqueue_calls: list[str] = []
    sink_binds: list[object] = []

    monkeypatch.setattr(mod, "_build_leaf_runner", lambda **_kwargs: runner)
    monkeypatch.setattr(mod, "apply_injection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        mod,
        "_bind_leaf_debug_runtime_sink",
        lambda session, **_kwargs: sink_binds.append(session),
    )
    monkeypatch.setattr(
        mod,
        "_enqueue_leaf_startup_init",
        lambda **_kwargs: enqueue_calls.append("init"),
    )

    service = DefaultLeafProcessEntryOrchestrationService(
        execution_ipc=ipc,
        command_channel_ingress=ingress,
        runner_control=runner_control,
        session_state=session_state,
        finalization_service=finalization,
    )

    session = SimpleNamespace(
        child=SimpleNamespace(
            runtime={},
            scenario_scope=SimpleNamespace(),
        ),
        runner_profile_requested="auto",
        runner_profile_effective="async",
    )

    service.run(
        session=session,
        control_pipe=None,
        stop_event=None,
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="auto",
        boundary_control_poll_seconds=0.05,
    )

    assert ingress.configured == [0.05]
    assert sink_binds == [session]
    assert enqueue_calls == ["init"]
    assert len(runner_control.bound) == 1
    assert runner_control.cleared == 1
    assert session_state.bound_sessions == [session]
    assert session_state.cleared == 1
    assert len(runner.run_until_stopped_calls) == 1
    assert runner.run_until_stopped_calls[0]["poll_timeout_seconds"] == 0.05
    assert runner.run_until_stopped_calls[0]["idle_timeout_seconds"] is None
    assert runner.on_run_end_calls == 1
    assert finalization.sessions == [session]


def test_build_leaf_startup_init_input_targets_consumer_bindings_bootstrap() -> None:
    payload = build_leaf_startup_init_input(runtime={"platform": {"bootstrap": {"mode": "process_supervisor"}}})

    assert isinstance(payload, Envelope)
    assert payload.target == "system.cp.consumer_registry_bindings_bootstrap"
    assert isinstance(payload.payload, ControlPlaneInitEvent)


def test_leaf_worker_process_entry_binds_debug_runtime_sink(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service as mod

    fake_session = SimpleNamespace(
        child=SimpleNamespace(runtime={}),
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="auto",
        runner_profile_effective="async",
    )
    orchestrator = _Orchestrator(calls=[])
    sink_binds: list[object] = []

    monkeypatch.setattr(mod, "bootstrap_leaf_worker_runtime_from_bundle", lambda **_kwargs: fake_session)
    monkeypatch.setattr(mod, "resolve_leaf_process_entry_orchestration_service", lambda _session: orchestrator)
    monkeypatch.setattr(
        mod,
        "_bind_leaf_debug_runtime_sink",
        lambda session, **_kwargs: sink_binds.append(session),
    )

    leaf_worker_process_entry(
        object(),
        object(),
        {"bundle": "raw"},
        "execution.alpha#1",
        "execution.alpha",
        "auto",
        0.01,
        "pickle",
    )

    assert sink_binds == [fake_session]
