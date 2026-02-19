from __future__ import annotations

import json
from importlib import import_module
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from stream_kernel.application_context.service import discover_services
from stream_kernel.kernel.trace import MessageSignature, TraceRecord
from stream_kernel.observability.events import TraceDispatchEvent
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.platform.services.runtime.bootstrap import (
    BootstrapSupervisor,
    LocalBootstrapSupervisor,
    MultiprocessBootstrapSupervisor,
)
from stream_kernel.routing.envelope import Envelope


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
    module = import_module("stream_kernel.platform.services.runtime.bootstrap")
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
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

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
    bootstrapped = [msg for msg in pipe.sent if msg.get("kind") == "worker_bootstrapped"]
    assert bootstrapped
    assert bootstrapped[0].get("runner_profile_effective") in {"sync", "async"}
    assert "observability_exporters" in bootstrapped[0]
    assert pipe.sent and pipe.sent[-1].get("kind") == "stop_ack"
    assert pipe.sent[-1].get("output_closed") is True


def test_p5pre_sup_08b_worker_sends_stop_ack_only_after_runtime_close(monkeypatch) -> None:
    # BOOT-LIFE-03B: stop_ack must be emitted only after child runtime close completes.
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    order: list[str] = []

    class _Pipe:
        def __init__(self) -> None:
            self._messages = [{"kind": "stop", "correlation_id": "c1"}]
            self.closed = False

        def poll(self, _timeout: float) -> bool:
            return bool(self._messages)

        def recv(self) -> dict[str, object]:
            return self._messages.pop(0)

        def send(self, payload: dict[str, object]) -> None:
            if payload.get("kind") == "stop_ack":
                order.append("ack")

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(bootstrap_module, "_is_child_bootstrap_bundle", lambda _b: True)
    monkeypatch.setattr(
        bootstrap_module,
        "_bootstrap_child_runtime",
        lambda _b: SimpleNamespace(scenario_scope=SimpleNamespace(close=lambda: None)),
    )

    def _close_child_runtime(_child_runtime: object | None) -> None:
        order.append("close")

    monkeypatch.setattr(bootstrap_module, "_close_child_runtime", _close_child_runtime)

    bootstrap_module._worker_loop(None, _Pipe(), object())  # noqa: SLF001 - contract probe
    assert order == ["close", "ack"]


def test_p5pre_sup_09_lifecycle_jsonl_exporter_writes_supervisor_events(tmp_path) -> None:
    # BOOT-LOG-03: supervisor lifecycle events should be writable to jsonl sink.
    path = tmp_path / "supervisor_lifecycle.jsonl"
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_lifecycle_logging(
        {
            "exporters": [{"kind": "jsonl", "settings": {"path": str(path)}}],
            "lifecycle_events": {"enabled": True, "level": "debug"},
        }
    )
    supervisor._emit_event(kind="test_event", group_name="execution.cpu")  # noqa: SLF001 - contract probe
    assert path.exists()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
    assert rows[-1].get("message") == "bootstrap.test_event"


def test_supervisor_tracing_consumes_worker_trace_event_and_emits_hop_record(tmp_path) -> None:
    path = tmp_path / "supervisor_trace.jsonl"
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_tracing(
        {
            "exporters": [
                {
                    "kind": "jsonl",
                    "settings": {"path": str(path), "write_mode": "line", "flush_every_n": 1},
                }
            ]
        },
        strict=True,
    )
    now = datetime(2026, 2, 18, 0, 0, 0, tzinfo=UTC)
    record = TraceRecord(
        trace_id="run:source:1",
        scenario="exp",
        step_index=1,
        step_name="compute_features",
        work_index=0,
        t_enter=now,
        t_exit=now,
        duration_ms=0.0,
        msg_in=MessageSignature(type_name="LoadAttempt", identity=None, hash=None),
        msg_out=(MessageSignature(type_name="Decision", identity=None, hash=None),),
        msg_out_count=1,
        ctx_before=None,
        ctx_after=None,
        ctx_diff=None,
        status="ok",
        error=None,
        span_id="abc123abc123abcd",
    )
    consumed = supervisor._consume_supervisor_trace_output(  # noqa: SLF001 - contract probe.
        output=Envelope(
            payload=TraceDispatchEvent(payload=record, trace_id=record.trace_id),
            trace_id=record.trace_id,
        ),
        source_group="execution.features",
        route_hop=3,
    )
    assert consumed is True
    supervisor._flush_trace_sinks()  # noqa: SLF001 - contract probe.

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 2
    assert rows[0]["step_name"] == "compute_features"
    assert rows[1]["step_name"] == "system.obs.supervisor_handoff"
    assert rows[1]["process_group"] == "supervisor.transport"
    assert rows[1]["handoff_from"] == "execution.features"
    assert rows[1]["route_hop"] == 3


def test_supervisor_tracing_dispatch_does_not_call_asyncio_run_per_record(tmp_path, monkeypatch) -> None:
    path = tmp_path / "supervisor_trace_dispatch.jsonl"
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_tracing(
        {
            "exporters": [
                {
                    "kind": "jsonl",
                    "settings": {"path": str(path), "write_mode": "line", "flush_every_n": 1},
                }
            ]
        },
        strict=True,
    )

    def _fail_run(*_args, **_kwargs):  # noqa: ANN001 - monkeypatch target signature.
        raise AssertionError("asyncio.run must not be called on per-record supervisor trace dispatch")

    monkeypatch.setattr(
        "stream_kernel.platform.services.runtime.bootstrap.asyncio.run",
        _fail_run,
    )

    now = datetime(2026, 2, 18, 0, 0, 0, tzinfo=UTC)
    record = TraceRecord(
        trace_id="run:source:2",
        scenario="exp",
        step_index=2,
        step_name="compute_time_keys",
        work_index=0,
        t_enter=now,
        t_exit=now,
        duration_ms=0.0,
        msg_in=MessageSignature(type_name="LoadAttempt", identity=None, hash=None),
        msg_out=(MessageSignature(type_name="Decision", identity=None, hash=None),),
        msg_out_count=1,
        ctx_before=None,
        ctx_after=None,
        ctx_diff=None,
        status="ok",
        error=None,
        span_id="abc123abc123abce",
    )
    supervisor._emit_trace_record(record)  # noqa: SLF001 - contract probe.
    supervisor._flush_trace_sinks()  # noqa: SLF001 - contract probe.
    supervisor._close_trace_sinks()  # noqa: SLF001 - contract probe.

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["step_name"] == "compute_time_keys"


def test_supervisor_tracing_view_aliases_apply_slice_defaults() -> None:
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_tracing(
        {
            "exporters": [
                {
                    "kind": "otel_otlp_logical",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "_export_fn": lambda _span: None,
                    },
                },
                {
                    "kind": "otel_otlp_topology",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "_export_fn": lambda _span: None,
                    },
                },
            ]
        },
        strict=True,
    )
    sinks = supervisor._trace_sinks  # noqa: SLF001 - contract probe.
    assert len(sinks) == 2
    assert getattr(sinks[0], "_trace_view", None) == "logical"
    assert getattr(sinks[0], "_logical_include_platform_spans", None) is False
    assert getattr(sinks[1], "_trace_view", None) == "topology"
    assert getattr(sinks[1], "_topology_include_business_spans", None) is False
    supervisor._close_trace_sinks()  # noqa: SLF001 - contract probe.


def test_p5pre_sup_10_worker_loop_emits_lifecycle_over_control_channel() -> None:
    # BOOT-LOG-04: worker lifecycle events should be forwarded to supervisor via control channel.
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

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

    pipe = _Pipe()
    bootstrap_module._worker_loop(  # noqa: SLF001 - contract probe
        None,
        pipe,
        None,
        "execution.cpu#1",
        "execution.cpu",
    )

    lifecycle_messages = [msg for msg in pipe.sent if msg.get("kind") == "worker_lifecycle"]
    messages = [msg.get("message") for msg in lifecycle_messages]
    assert "bootstrap.worker_loop_started" in messages
    assert "bootstrap.worker_stop_command" in messages


def test_p5pre_sup_10b_worker_loop_emits_message_lifecycle_traces_over_control_channel(monkeypatch) -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Pipe:
        def __init__(self) -> None:
            self._messages = [
                {
                    "kind": "execute_boundary",
                    "correlation_id": "c0",
                    "run_id": "run",
                    "scenario_id": "scenario",
                    "inputs": [
                        SimpleNamespace(
                            payload={"id": "1"},
                            dispatch_group="execution.ingress",
                            target="parse_load_attempt",
                            trace_id="run:1",
                            reply_to=None,
                            source_group="supervisor.entry",
                            route_hop=0,
                            span_id="parent-span",
                        )
                    ],
                },
                {"kind": "stop", "correlation_id": "c1"},
            ]
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
        lambda _b: SimpleNamespace(scenario_scope=SimpleNamespace(close=lambda: None)),
    )
    monkeypatch.setattr(
        bootstrap_module,
        "_execute_child_boundary_from_runtime",
        lambda **_kwargs: [Envelope(payload={"ok": True}, trace_id="run:1", target=None, span_id="worker-span")],
    )

    pipe = _Pipe()
    bootstrap_module._worker_loop(  # noqa: SLF001 - contract probe
        None,
        pipe,
        object(),
        "execution.ingress#1",
        "execution.ingress",
    )

    traces = [
        msg.get("record")
        for msg in pipe.sent
        if msg.get("kind") == "worker_trace"
    ]
    step_names = [getattr(record, "step_name", None) for record in traces]
    assert "system.obs.worker_boundary_receive" in step_names
    assert "system.obs.worker_boundary_emit" in step_names


def test_p5pre_sup_10a_worker_loop_uses_configured_control_poll_timeout() -> None:
    # BOOT-DISP-03: worker control loop should poll using configured low-latency timeout.
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Pipe:
        def __init__(self) -> None:
            self._messages = [{"kind": "stop", "correlation_id": "c1"}]
            self.sent: list[dict[str, object]] = []
            self.poll_timeouts: list[float] = []

        def poll(self, timeout: float) -> bool:
            self.poll_timeouts.append(timeout)
            return bool(self._messages)

        def recv(self) -> dict[str, object]:
            return self._messages.pop(0)

        def send(self, payload: dict[str, object]) -> None:
            self.sent.append(payload)

        def close(self) -> None:
            return None

    pipe = _Pipe()
    bootstrap_module._worker_loop(  # noqa: SLF001 - contract probe
        None,
        pipe,
        None,
        control_poll_seconds=0.007,
    )

    assert pipe.poll_timeouts
    assert pipe.poll_timeouts[0] == pytest.approx(0.007, rel=0.0, abs=1e-9)


def test_p5pre_sup_11_supervisor_lifecycle_can_fanout_stdout_and_jsonl(tmp_path, monkeypatch) -> None:
    # BOOT-LOG-05: supervisor lifecycle logging should support multi-exporter fan-out.
    emitted: list[LogMessage] = []

    def _capture(self, message: LogMessage) -> None:  # noqa: ANN001 - monkeypatch target signature
        emitted.append(message)

    monkeypatch.setattr(
        "stream_kernel.observability.adapters.logging.StdoutLogSink.emit",
        _capture,
    )

    path = tmp_path / "supervisor_fanout.jsonl"
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_lifecycle_logging(
        {
            "exporters": [
                {"kind": "stdout"},
                {"kind": "jsonl", "settings": {"path": str(path)}},
            ],
            "lifecycle_events": {"enabled": True, "level": "debug"},
        }
    )
    supervisor._emit_event(kind="test_fanout", group_name="execution.cpu")  # noqa: SLF001 - contract probe

    assert emitted
    assert emitted[-1].message == "bootstrap.test_fanout"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
    assert rows[-1].get("message") == "bootstrap.test_fanout"


def test_p5pre_sup_12_drain_inflight_prefers_stop_command_over_stop_event(monkeypatch) -> None:
    # BOOT-LIFE-06: when drain_inflight=True, supervisor should request stop via control command first.
    class _Process:
        def __init__(self) -> None:
            self.pid = 123
            self._alive = True

        def is_alive(self) -> bool:
            return self._alive

        def join(self, timeout: float | None = None) -> None:
            _ = timeout
            self._alive = False

        def terminate(self) -> None:
            self._alive = False

    class _StopEvent:
        def __init__(self) -> None:
            self.calls = 0

        def set(self) -> None:
            self.calls += 1

    supervisor = MultiprocessBootstrapSupervisor()
    stop_event = _StopEvent()
    handle = SimpleNamespace(
        group_name="execution.cpu",
        worker_id="execution.cpu#1",
        process=_Process(),
        stop_event=stop_event,
        control_parent=object(),
    )
    supervisor._workers = {"execution.cpu": [handle]}  # noqa: SLF001 - contract probe

    sent: list[str] = []

    def _fake_send_boundary_command(*_args, **_kwargs) -> dict[str, object]:
        sent.append("stop")
        return {"kind": "stop_ack", "output_closed": True}

    monkeypatch.setattr(
        supervisor,
        "_send_boundary_command",
        _fake_send_boundary_command,
    )

    supervisor.stop_groups(graceful_timeout_seconds=1, drain_inflight=True)
    assert sent == ["stop"]
    assert stop_event.calls == 0


def test_p5pre_sup_12b_drain_inflight_stop_command_uses_graceful_budget(monkeypatch) -> None:
    # BOOT-LIFE-06B: drain stop-command timeout should use graceful budget, not fixed tiny timeout.
    class _Process:
        def __init__(self) -> None:
            self.pid = 123
            self._alive = True

        def is_alive(self) -> bool:
            return self._alive

        def join(self, timeout: float | None = None) -> None:
            _ = timeout
            self._alive = False

        def terminate(self) -> None:
            self._alive = False

    supervisor = MultiprocessBootstrapSupervisor()
    handle = SimpleNamespace(
        group_name="execution.cpu",
        worker_id="execution.cpu#1",
        process=_Process(),
        stop_event=None,
        control_parent=object(),
    )
    supervisor._workers = {"execution.cpu": [handle]}  # noqa: SLF001 - contract probe

    observed_timeouts: list[float] = []

    def _fake_try_send_stop_command(_handle: object, *, timeout_seconds: float) -> bool:
        observed_timeouts.append(timeout_seconds)
        return True

    monkeypatch.setattr(supervisor, "_try_send_stop_command", _fake_try_send_stop_command)

    supervisor.stop_groups(graceful_timeout_seconds=7, drain_inflight=True)
    assert observed_timeouts
    assert observed_timeouts[0] > 1.0


def test_p5pre_sup_12c_stop_timeout_is_not_treated_as_stop_ack(monkeypatch) -> None:
    # BOOT-LIFE-06C: timeout response must not be treated as successful stop ack.
    supervisor = MultiprocessBootstrapSupervisor()
    handle = SimpleNamespace(group_name="execution.cpu", worker_id="execution.cpu#1")

    monkeypatch.setattr(
        supervisor,
        "_send_boundary_command",
        lambda *_args, **_kwargs: {"kind": "timeout"},
    )
    assert supervisor._try_send_stop_command(handle, timeout_seconds=0.5) is False  # noqa: SLF001

    monkeypatch.setattr(
        supervisor,
        "_send_boundary_command",
        lambda *_args, **_kwargs: {"kind": "stop_ack", "output_closed": True},
    )
    assert supervisor._try_send_stop_command(handle, timeout_seconds=0.5) is True  # noqa: SLF001


def test_p5pre_sup_12d_stop_ack_requires_output_closed_handshake(monkeypatch) -> None:
    # BOOT-LIFE-06D: stop_ack without output_closed confirmation is not enough.
    supervisor = MultiprocessBootstrapSupervisor()
    handle = SimpleNamespace(group_name="execution.cpu", worker_id="execution.cpu#1")

    monkeypatch.setattr(
        supervisor,
        "_send_boundary_command",
        lambda *_args, **_kwargs: {"kind": "stop_ack", "output_closed": False},
    )
    assert supervisor._try_send_stop_command(handle, timeout_seconds=0.5) is False  # noqa: SLF001


def test_p5pre_sup_12e_wait_output_closed_reflects_last_stop_handshake() -> None:
    # BOOT-LIFE-06E: wait_output_closed should expose last graceful stop output-close state.
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._workers = {}  # noqa: SLF001 - contract probe
    supervisor._last_output_closed = False  # noqa: SLF001 - contract probe
    assert supervisor.wait_output_closed(timeout_seconds=1) is False

    supervisor._last_output_closed = True  # noqa: SLF001 - contract probe
    assert supervisor.wait_output_closed(timeout_seconds=1) is True


def test_p5pre_sup_12f_output_closed_emits_lifecycle_event(monkeypatch) -> None:
    # BOOT-LIFE-06F: successful output-closed handshake should be visible in supervisor lifecycle logs/events.
    class _Process:
        def __init__(self) -> None:
            self.pid = 123
            self._alive = True

        def is_alive(self) -> bool:
            return self._alive

        def join(self, timeout: float | None = None) -> None:
            _ = timeout
            self._alive = False

        def terminate(self) -> None:
            self._alive = False

    supervisor = MultiprocessBootstrapSupervisor()
    handle = SimpleNamespace(
        group_name="execution.cpu",
        worker_id="execution.cpu#1",
        process=_Process(),
        stop_event=None,
        control_parent=object(),
    )
    supervisor._workers = {"execution.cpu": [handle]}  # noqa: SLF001 - contract probe

    monkeypatch.setattr(
        supervisor,
        "_send_boundary_command",
        lambda *_args, **_kwargs: {"kind": "stop_ack", "output_closed": True},
    )

    supervisor.stop_groups(graceful_timeout_seconds=1, drain_inflight=True)
    events = _lifecycle_events(supervisor)
    assert any(
        event.get("kind") == "worker_output_closed"
        and event.get("group_name") == "execution.cpu"
        and event.get("worker_id") == "execution.cpu#1"
        for event in events
    )


def test_p5pre_sup_13_lifecycle_level_off_disables_log_emission(monkeypatch) -> None:
    # BOOT-LOG-06: lifecycle level=off disables lifecycle logging even when exporters are configured.
    emitted: list[LogMessage] = []

    def _capture(self, message: LogMessage) -> None:  # noqa: ANN001
        emitted.append(message)

    monkeypatch.setattr(
        "stream_kernel.observability.adapters.logging.StdoutLogSink.emit",
        _capture,
    )

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_lifecycle_logging(
        {
            "exporters": [{"kind": "stdout"}],
            "lifecycle_events": {"enabled": True, "level": "off"},
        }
    )
    supervisor._emit_event(kind="worker_spawned", group_name="execution.cpu")  # noqa: SLF001
    assert emitted == []


def test_p5pre_sup_13a_all_mode_exporter_emits_events_when_lifecycle_level_off(tmp_path) -> None:
    path = tmp_path / "all_events.jsonl"
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_lifecycle_logging(
        {
            "exporters": [{"kind": "jsonl", "mode": "all", "settings": {"path": str(path)}}],
            "lifecycle_events": {"enabled": True, "level": "off"},
        }
    )
    supervisor._emit_event(kind="worker_spawned", group_name="execution.cpu", worker_id="execution.cpu#1")  # noqa: SLF001
    assert path.exists()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
    assert rows[-1].get("message") == "bootstrap.worker_spawned"


def test_p5pre_sup_14_stdout_plain_exporter_uses_human_readable_format(monkeypatch) -> None:
    # BOOT-LOG-07: stdout_plain should print `[process-name-id]: [LEVEL]: message` format.
    lines: list[str] = []

    def _capture_print(*args: object, **kwargs: object) -> None:
        _ = kwargs
        lines.append(" ".join(str(arg) for arg in args))

    monkeypatch.setattr("builtins.print", _capture_print)

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_lifecycle_logging(
        {
            "exporters": [{"kind": "stdout_plain"}],
            "lifecycle_events": {"enabled": True, "level": "info"},
        }
    )
    supervisor._emit_event(kind="worker_spawned", group_name="execution.cpu", worker_id="execution.cpu#1")  # noqa: SLF001
    assert lines
    last = lines[-1]
    assert "[INFO]" in last
    assert "bootstrap.worker_spawned" in last
    assert "[" in last and "]:" in last


def test_p5pre_sup_14a_file_plain_exporter_writes_human_readable_file_line(tmp_path) -> None:
    path = tmp_path / "lifecycle.log"
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_lifecycle_logging(
        {
            "exporters": [{"kind": "file_plain", "settings": {"path": str(path)}}],
            "lifecycle_events": {"enabled": True, "level": "info"},
        }
    )
    supervisor._emit_event(kind="worker_spawned", group_name="execution.cpu", worker_id="execution.cpu#1")  # noqa: SLF001
    assert path.exists()
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
    assert "[INFO]" in rows[-1]
    assert "bootstrap.worker_spawned" in rows[-1]


def test_p5pre_sup_15_info_level_filters_full_boundary_events(monkeypatch) -> None:
    # BOOT-LOG-08: `info` should keep startup/stop events and suppress full boundary processing chatter.
    emitted: list[LogMessage] = []

    def _capture(self, message: LogMessage) -> None:  # noqa: ANN001
        emitted.append(message)

    monkeypatch.setattr(
        "stream_kernel.observability.adapters.logging.StdoutLogSink.emit",
        _capture,
    )

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_lifecycle_logging(
        {
            "exporters": [{"kind": "stdout"}],
            "lifecycle_events": {"enabled": True, "level": "info"},
        }
    )
    supervisor._emit_event(kind="worker_spawned", group_name="execution.cpu", worker_id="execution.cpu#1")  # noqa: SLF001
    supervisor._emit_event(kind="boundary_dispatch_completed", outputs=10, terminal=1)  # noqa: SLF001

    assert any(item.message == "bootstrap.worker_spawned" for item in emitted)
    assert not any(item.message == "bootstrap.boundary_dispatch_completed" for item in emitted)


def test_p5pre_sup_16_supervisor_records_worker_bootstrap_runner_diagnostics() -> None:
    # BOOT-LOG-09: supervisor should persist worker bootstrap runner diagnostics from control-plane message.
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self) -> None:
            self.pid = 777

        def is_alive(self) -> bool:
            return True

    supervisor = MultiprocessBootstrapSupervisor()
    handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="execution.cpu",
        worker_index=1,
        worker_id="execution.cpu#1",
        process=_Process(),  # type: ignore[arg-type]
        control_parent=None,
    )
    accepted = supervisor._handle_worker_bootstrap_message(  # noqa: SLF001 - contract probe
        handle,
        {
            "kind": "worker_bootstrapped",
            "runner_profile_requested": "auto",
            "runner_profile_effective": "async",
            "async_nodes": ["node.async"],
            "async_services": ["ObservabilityService"],
            "async_adapters": ["stream<LogMessage>"],
            "node_runner_plan": {"node.async": "async"},
        },
    )
    assert accepted is True
    events = _lifecycle_events(supervisor)
    last = events[-1]
    assert last.get("kind") == "worker_bootstrapped"
    assert last.get("runner_profile_effective") == "async"
    assert handle.runner_profile_effective == "async"


def test_p5pre_sup_16a_supervisor_logs_worker_lifecycle_events_from_control_plane(tmp_path) -> None:
    # BOOT-LOG-10: worker lifecycle control messages are emitted by supervisor-owned logging exporters.
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self) -> None:
            self.pid = 778

        def is_alive(self) -> bool:
            return True

    log_path = tmp_path / "supervisor_lifecycle.jsonl"
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_lifecycle_logging(
        {
            "exporters": [{"kind": "jsonl", "settings": {"path": str(log_path)}}],
            "lifecycle_events": {"enabled": True, "level": "debug"},
        }
    )
    handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="execution.cpu",
        worker_index=1,
        worker_id="execution.cpu#1",
        process=_Process(),  # type: ignore[arg-type]
        control_parent=None,
    )
    accepted = supervisor._handle_worker_bootstrap_message(  # noqa: SLF001 - contract probe
        handle,
        {
            "kind": "worker_lifecycle",
            "message": "bootstrap.worker_stop_command",
            "fields": {"correlation_id": "stop:123"},
        },
    )
    assert accepted is True

    events = _lifecycle_events(supervisor)
    assert any(
        event.get("kind") == "worker_stop_command"
        and event.get("worker_id") == "execution.cpu#1"
        and event.get("correlation_id") == "stop:123"
        for event in events
    )
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
    assert rows[-1].get("message") == "bootstrap.worker_stop_command"


def test_p5pre_sup_16b_supervisor_exports_worker_trace_records_from_control_plane(tmp_path) -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self) -> None:
            self.pid = 779

        def is_alive(self) -> bool:
            return True

    trace_path = tmp_path / "supervisor_trace.jsonl"
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_tracing(
        {
            "exporters": [
                {
                    "kind": "jsonl",
                    "settings": {"path": str(trace_path), "write_mode": "line", "flush_every_n": 1},
                }
            ]
        },
        strict=True,
    )
    handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="execution.cpu",
        worker_index=1,
        worker_id="execution.cpu#1",
        process=_Process(),  # type: ignore[arg-type]
        control_parent=None,
    )
    record = TraceRecord(
        trace_id="run:1",
        scenario="scenario",
        step_index=-1,
        step_name="system.obs.worker_boundary_receive",
        work_index=0,
        t_enter=datetime.now(tz=UTC),
        t_exit=datetime.now(tz=UTC),
        duration_ms=0.0,
        msg_in=MessageSignature(type_name="dict", identity=None, hash=None),
        msg_out=(MessageSignature(type_name="boundary_dispatch", identity=None, hash=None),),
        msg_out_count=1,
        ctx_before=None,
        ctx_after=None,
        ctx_diff=None,
        status="ok",
        error=None,
        span_id="abc123abc123abcd",
    )
    accepted = supervisor._handle_worker_bootstrap_message(  # noqa: SLF001 - contract probe
        handle,
        {"kind": "worker_trace", "record": record},
    )
    assert accepted is True
    supervisor._flush_trace_sinks()  # noqa: SLF001 - contract probe.

    rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
    assert rows[-1]["step_name"] == "system.obs.worker_boundary_receive"


def test_p5pre_sup_17_execute_boundary_drains_pending_bootstrap_messages() -> None:
    # BOOT-DISP-04: execute_boundary should drain pending worker_bootstrapped control messages
    # before first dispatch, so bootstrap diagnostics are not delayed until first worker command.
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self) -> None:
            self.pid = 778

        def is_alive(self) -> bool:
            return True

    class _Pipe:
        def __init__(self) -> None:
            self._messages = [
                {
                    "kind": "worker_bootstrapped",
                    "runner_profile_requested": "auto",
                    "runner_profile_effective": "async",
                    "async_nodes": ["system.obs.trace_dispatch"],
                    "async_services": ["ObservabilityPipelineService"],
                    "async_adapters": ["stream<TraceSinkPort>"],
                    "node_runner_plan": {"system.obs.trace_dispatch": "async"},
                }
            ]
            self.sent: list[dict[str, object]] = []

        def poll(self, _timeout: float) -> bool:
            return bool(self._messages)

        def recv(self) -> dict[str, object]:
            return self._messages.pop(0)

        def send(self, payload: dict[str, object]) -> None:
            self.sent.append(payload)
            if payload.get("kind") == "execute_boundary":
                self._messages.append(
                    {
                        "kind": "execute_boundary_result",
                        "correlation_id": payload.get("correlation_id"),
                        "terminal_outputs": [],
                    }
                )

    supervisor = MultiprocessBootstrapSupervisor()
    handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="execution.cpu",
        worker_index=1,
        worker_id="execution.cpu#1",
        process=_Process(),  # type: ignore[arg-type]
        control_parent=_Pipe(),  # type: ignore[arg-type]
    )
    supervisor._workers = {"execution.cpu": [handle]}  # noqa: SLF001 - contract probe
    supervisor._group_rr_cursor = {"execution.cpu": 0}  # noqa: SLF001 - contract probe

    result = supervisor.execute_boundary(
        run=lambda: None,
        run_id="run",
        scenario_id="scenario",
        inputs=[
            SimpleNamespace(
                payload={"id": "x"},
                dispatch_group="execution.cpu",
                target="child.nop",
                trace_id="run:0",
                reply_to=None,
                source_group="supervisor.entry",
                route_hop=0,
                span_id=None,
            )
        ],
    )
    assert result.terminal_outputs == []
    events = _lifecycle_events(supervisor)
    assert any(event.get("kind") == "worker_bootstrapped" for event in events)
    assert handle.runner_profile_effective == "async"
