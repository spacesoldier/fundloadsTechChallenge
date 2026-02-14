from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from typing import Any

from stream_kernel.application_context.service import discover_services
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.platform.services.bootstrap import (
    BootstrapSupervisor,
    LocalBootstrapSupervisor,
    MultiprocessBootstrapSupervisor,
)


def _resolve_discovered_bootstrap_supervisor() -> BootstrapSupervisor:
    # Resolve discovered BootstrapSupervisor class without instantiating unrelated platform services.
    module = import_module("stream_kernel.platform.services")
    candidates = [
        cls
        for cls in discover_services([module])
        if isinstance(cls, type) and issubclass(cls, BootstrapSupervisor)
    ]
    assert candidates, "No BootstrapSupervisor implementation discovered in platform.services"
    candidate = candidates[0]()
    assert isinstance(candidate, BootstrapSupervisor)
    return candidate


def _lifecycle_events(supervisor: object) -> list[dict[str, Any]]:
    events_fn = getattr(supervisor, "lifecycle_events", None)
    assert callable(events_fn), "multiprocess supervisor must expose lifecycle_events()"
    events = events_fn()
    assert isinstance(events, list), "lifecycle_events() must return list"
    return events


def test_p5pre_sup_01_process_supervisor_contract_forbids_local_fallback() -> None:
    # P5PRE-SUP-01 (RED): process_supervisor runtime must not resolve LocalBootstrapSupervisor fallback.
    supervisor = _resolve_discovered_bootstrap_supervisor()
    assert not isinstance(
        supervisor, LocalBootstrapSupervisor
    ), "process_supervisor must resolve multiprocess service, not local fallback"


def test_p5pre_sup_02_multiprocess_supervisor_class_is_declared_and_discoverable() -> None:
    # P5PRE-SUP-02 (RED): dedicated multiprocess supervisor class should exist in platform services.
    module = import_module("stream_kernel.platform.services.bootstrap")
    cls = getattr(module, "MultiprocessBootstrapSupervisor", None)
    assert cls is not None, "MultiprocessBootstrapSupervisor class is required for process-supervisor mode"
    assert issubclass(cls, BootstrapSupervisor)


def test_p5pre_sup_03_startup_timeout_is_not_silent() -> None:
    # P5PRE-SUP-03 (RED): zero-timeout readiness after spawn must not silently report ready.
    supervisor = _resolve_discovered_bootstrap_supervisor()
    supervisor.start_groups(["execution.cpu"])
    assert supervisor.wait_ready(0) is False, "wait_ready(0) must fail deterministically when readiness not reached"


def test_p5pre_sup_04_graceful_stop_emits_lifecycle_event() -> None:
    # P5PRE-SUP-04 (RED): graceful stop path must emit structured lifecycle event.
    supervisor = _resolve_discovered_bootstrap_supervisor()
    supervisor.start_groups(["execution.cpu"])
    _ = supervisor.wait_ready(1)
    supervisor.stop_groups(graceful_timeout_seconds=1, drain_inflight=True)
    events = _lifecycle_events(supervisor)
    assert any(
        event.get("kind") == "worker_stopped" and event.get("mode") == "graceful"
        for event in events
    ), "graceful stop lifecycle event is required"


def test_p5pre_sup_05_forced_stop_emits_lifecycle_event() -> None:
    # P5PRE-SUP-05 (RED): forced terminate path must emit structured lifecycle event.
    supervisor = _resolve_discovered_bootstrap_supervisor()
    supervisor.start_groups(["execution.cpu"])
    supervisor.force_terminate_groups(["execution.cpu"])
    events = _lifecycle_events(supervisor)
    assert any(
        event.get("kind") == "worker_stopped" and event.get("mode") == "forced"
        for event in events
    ), "forced stop lifecycle event is required"


def test_p5pre_sup_06_workers_per_group_cardinality_contract() -> None:
    # P5PRE-SUP-06 (RED): supervisor must expose deterministic workers-per-group orchestration contract.
    supervisor = _resolve_discovered_bootstrap_supervisor()
    configure_groups = getattr(supervisor, "configure_process_groups", None)
    assert callable(configure_groups), "multiprocess supervisor must expose configure_process_groups(...)"
    configure_groups([{"name": "execution.cpu", "workers": 2}])
    supervisor.start_groups(["execution.cpu"])
    snapshot_fn = getattr(supervisor, "snapshot", None)
    assert callable(snapshot_fn), "multiprocess supervisor must expose snapshot()"
    snapshot = snapshot_fn()
    assert isinstance(snapshot, dict)
    workers = snapshot.get("execution.cpu", [])
    assert isinstance(workers, list)
    assert len(workers) == 2, "workers count from runtime.process_groups[].workers must be honored"


def test_p5pre_sup_07_lifecycle_events_can_emit_structured_logs(monkeypatch) -> None:
    # BOOT-LOG-02: lifecycle event emission should produce platform log messages when enabled.
    emitted: list[LogMessage] = []

    def _capture(self, message: LogMessage) -> None:  # noqa: ANN001 - monkeypatch target signature
        emitted.append(message)

    monkeypatch.setattr(
        "stream_kernel.observability.adapters.logging.StdoutLogSink.emit",
        _capture,
    )

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_lifecycle_logging(
        {
            "exporters": [{"kind": "stdout"}],
            "lifecycle_events": {"enabled": True, "level": "debug"},
        }
    )
    supervisor._emit_event(kind="test_event", group_name="execution.cpu")  # noqa: SLF001 - contract probe

    assert emitted
    message = emitted[-1]
    assert message.level == "debug"
    assert message.message == "bootstrap.test_event"
    assert message.fields.get("group_name") == "execution.cpu"


def test_p5pre_sup_08_worker_stop_closes_child_runtime_scope(monkeypatch) -> None:
    # BOOT-LIFE-03: child runtime scope should be closed on worker stop command.
    from stream_kernel.platform.services import bootstrap as bootstrap_module

    closed: list[str] = []

    class _Scope:
        def close(self) -> None:
            closed.append("closed")

    class _Pipe:
        def __init__(self) -> None:
            self._messages = [{"kind": "stop", "correlation_id": "c1"}]
            self.sent: list[dict[str, object]] = []
            self.closed = False

        def poll(self, _timeout: float) -> bool:
            return bool(self._messages)

        def recv(self) -> dict[str, object]:
            return self._messages.pop(0)

        def send(self, payload: dict[str, object]) -> None:
            self.sent.append(payload)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(bootstrap_module, "_is_child_bootstrap_bundle", lambda _b: True)
    monkeypatch.setattr(
        bootstrap_module,
        "_bootstrap_child_runtime",
        lambda _b: SimpleNamespace(scenario_scope=_Scope()),
    )

    pipe = _Pipe()
    bootstrap_module._worker_loop(None, pipe, object())  # noqa: SLF001 - contract probe
    assert closed == ["closed"]
    assert pipe.closed is True
    assert pipe.sent and pipe.sent[-1].get("kind") == "stop_ack"
