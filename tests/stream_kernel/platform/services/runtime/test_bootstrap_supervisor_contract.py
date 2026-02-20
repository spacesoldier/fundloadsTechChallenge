from __future__ import annotations

import json
import time
from threading import Event, Lock, Thread
from importlib import import_module
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from stream_kernel.application_context.service import discover_services
from stream_kernel.kernel.trace import MessageSignature, TraceRecord
from stream_kernel.observability.events import (
    MonitoringMetricsSnapshotEvent,
    MonitoringMetricsSnapshotResult,
    TraceDispatchEvent,
    WorkerQueueTelemetryEvent,
)
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.observability.adapters.logging import StdoutPlainLogSink
from stream_kernel.platform.services.runtime.bootstrap import (
    BootstrapSupervisor,
    LocalBootstrapSupervisor,
    MultiprocessBootstrapSupervisor,
)
from stream_kernel.execution.orchestration.child_bootstrap import ChildBootstrapBundle
from stream_kernel.routing.envelope import Envelope
from stream_kernel.adapters.contracts import (
    BusinessDispatchPort,
    ControlPlaneDispatchPort,
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


def test_supervisor_observability_service_worker_offloads_lifecycle_sink_emit(monkeypatch) -> None:
    # OBS-SVC-WORKER-01: when service worker is enabled lifecycle sink emit must not block supervisor path.
    gate = Event()
    emitted: list[LogMessage] = []

    def _blocking_capture(self, message: LogMessage) -> None:  # noqa: ANN001 - monkeypatch target signature
        emitted.append(message)
        gate.wait(timeout=0.2)

    monkeypatch.setattr(
        "stream_kernel.observability.adapters.logging.StdoutLogSink.emit",
        _blocking_capture,
    )

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_observability_worker(
        {
            "service_worker": {
                "enabled": True,
                "queue_max_items": 256,
                "drop_policy": "block_with_timeout",
                "block_timeout_ms": 100,
            }
        }
    )
    supervisor.configure_lifecycle_logging(
        {
            "exporters": [{"kind": "stdout"}],
            "lifecycle_events": {"enabled": True, "level": "debug"},
        }
    )

    started = time.perf_counter()
    supervisor._emit_event(kind="test_event", group_name="execution.cpu")  # noqa: SLF001 - contract probe
    elapsed = time.perf_counter() - started

    assert elapsed < 0.1
    gate.set()
    supervisor._close_observability_service_worker()  # noqa: SLF001 - contract probe
    assert emitted


def test_p5pre_sup_07a_lifecycle_events_add_trace_summary_fields_for_grep() -> None:
    supervisor = MultiprocessBootstrapSupervisor()

    supervisor._emit_event(  # noqa: SLF001 - contract probe
        kind="boundary_dispatch_item",
        trace_ids=["run:1", "run:2", "", 123],  # type: ignore[list-item]
        targets=["node.a", "node.b", ""],
        payload_types=["LoadAttempt", "TraceDispatchEvent", ""],
    )

    events = _lifecycle_events(supervisor)
    last = events[-1]
    assert last.get("trace_ids") == ["run:1", "run:2", "", 123]
    assert last.get("trace_id_first") == "run:1"
    assert last.get("trace_count") == 2
    assert "trace_id" not in last
    assert last.get("target_first") == "node.a"
    assert last.get("target_count") == 2
    assert last.get("payload_type_first") == "LoadAttempt"
    assert last.get("payload_type_count") == 2


def test_p5pre_sup_07b_lifecycle_events_promote_single_trace_id_for_grep() -> None:
    supervisor = MultiprocessBootstrapSupervisor()

    supervisor._emit_event(  # noqa: SLF001 - contract probe
        kind="boundary_receive_item",
        trace_ids=["run:single"],
    )

    events = _lifecycle_events(supervisor)
    last = events[-1]
    assert last.get("trace_id_first") == "run:single"
    assert last.get("trace_count") == 1
    assert last.get("trace_id") == "run:single"


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


def test_supervisor_trace_dispatch_diagnostics_expose_pending_at_stop() -> None:
    class _LoopStub:
        def metrics(self) -> dict[str, int]:
            return {
                "submitted": 11,
                "processed": 7,
                "failed": 1,
                "dropped": 0,
                "queue_depth": 3,
                "pending": 3,
                "running": 1,
            }

    class _SinkStub:
        def diagnostics(self) -> dict[str, int]:
            return {"exported": 7, "dropped": 0, "buffered": 2}

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._trace_dispatch_loop = _LoopStub()  # noqa: SLF001 - contract probe.
    supervisor._trace_sinks = [_SinkStub()]  # noqa: SLF001 - contract probe.
    supervisor._tracing_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._trace_dispatch_dropped = 4  # noqa: SLF001 - contract probe.

    supervisor._emit_trace_dispatch_diagnostics(stage="stop_requested")  # noqa: SLF001 - contract probe.
    events = _lifecycle_events(supervisor)
    event = events[-1]

    assert event.get("kind") == "trace_dispatch_diagnostics"
    assert event.get("stage") == "stop_requested"
    assert event.get("dispatch_pending") == 3
    assert event.get("sink_pending_total") == 2
    assert event.get("pending_total_estimate") == 5
    assert event.get("dispatch_submit_dropped_total") == 4
    sink_diagnostics = event.get("sink_diagnostics")
    assert isinstance(sink_diagnostics, list)
    assert sink_diagnostics
    first = sink_diagnostics[0]
    assert isinstance(first, dict)
    assert first.get("buffered") == 2
    assert first.get("pending_estimate") == 2


def test_supervisor_trace_dispatch_diagnostics_feed_metrics_service() -> None:
    class _LoopStub:
        def metrics(self) -> dict[str, int]:
            return {
                "submitted": 2,
                "processed": 2,
                "failed": 0,
                "dropped": 0,
                "queue_depth": 0,
                "pending": 0,
                "running": 1,
            }

    class _MetricsStub:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def ingest_trace_dispatch_snapshot(self, *, stage: str, snapshot: dict[str, object]) -> None:
            self.calls.append((stage, dict(snapshot)))

        def snapshot(self) -> dict[str, object]:
            return {}

        def metric_records(self) -> list[dict[str, object]]:
            return []

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._trace_dispatch_loop = _LoopStub()  # noqa: SLF001 - contract probe.
    supervisor._trace_sinks = []  # noqa: SLF001 - contract probe.
    supervisor._tracing_enabled = True  # noqa: SLF001 - contract probe.
    metrics_stub = _MetricsStub()
    supervisor.observability_metrics_service = metrics_stub  # noqa: SLF001 - contract probe.

    supervisor._emit_trace_dispatch_diagnostics(stage="stop_requested")  # noqa: SLF001 - contract probe.

    assert len(metrics_stub.calls) == 1
    stage, snapshot = metrics_stub.calls[0]
    assert stage == "stop_requested"
    assert snapshot.get("dispatch_submitted") == 2
    assert snapshot.get("dispatch_pending") == 0


def test_supervisor_trace_dispatch_queue_policy_is_wired_from_tracing_settings(tmp_path) -> None:
    path = tmp_path / "supervisor_trace_dispatch_policy.jsonl"
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_tracing(
        {
            "dispatch_queue": {
                "max_items": 32,
                "drop_policy": "block_with_timeout",
                "block_timeout_ms": 250,
            },
            "exporters": [
                {
                    "kind": "jsonl",
                    "settings": {"path": str(path), "write_mode": "line", "flush_every_n": 1},
                }
            ],
        },
        strict=True,
    )

    loop = supervisor._trace_dispatch_loop  # noqa: SLF001 - contract probe.
    assert loop is not None
    metrics = loop.metrics()
    assert metrics.get("queue_max_items") == 32
    assert metrics.get("drop_policy") == "block_with_timeout"
    assert supervisor._trace_dispatch_submit_timeout_seconds == 0.25  # noqa: SLF001 - contract probe.
    supervisor._close_trace_sinks()  # noqa: SLF001 - contract probe.


def test_supervisor_monitoring_prometheus_textfile_exports_metrics_snapshot(tmp_path) -> None:
    class _LoopStub:
        def metrics(self) -> dict[str, int]:
            return {
                "submitted": 3,
                "processed": 2,
                "failed": 0,
                "dropped": 0,
                "queue_depth": 1,
                "pending": 1,
                "running": 1,
            }

    path = tmp_path / "metrics.prom"
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._trace_dispatch_loop = _LoopStub()  # noqa: SLF001 - contract probe.
    supervisor._trace_sinks = []  # noqa: SLF001 - contract probe.
    supervisor._tracing_enabled = True  # noqa: SLF001 - contract probe.
    supervisor.configure_monitoring(
        {
            "exporters": [
                {
                    "kind": "prometheus",
                    "settings": {
                        "mode": "textfile",
                        "textfile": {"path": str(path)},
                    },
                }
            ]
        },
        strict=True,
    )

    supervisor._emit_trace_dispatch_diagnostics(stage="stop_requested")  # noqa: SLF001 - contract probe.
    supervisor._close_monitoring_sinks()  # noqa: SLF001 - contract probe.
    payload = path.read_text(encoding="utf-8")
    assert "stream_kernel_observability_dispatch_submitted_total" in payload
    assert "stream_kernel_observability_dispatch_pending" in payload


def test_supervisor_monitoring_exporter_failure_is_isolated() -> None:
    class _LoopStub:
        def metrics(self) -> dict[str, int]:
            return {
                "submitted": 1,
                "processed": 1,
                "failed": 0,
                "dropped": 0,
                "queue_depth": 0,
                "pending": 0,
                "running": 1,
            }

    class _BrokenMonitoringExporter:
        def publish_metrics(self, *, records: list[dict[str, object]], snapshot: dict[str, object], stage: str) -> None:
            _ = (records, snapshot, stage)
            raise RuntimeError("monitoring-exporter-failed")

        def close(self) -> None:
            return None

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._trace_dispatch_loop = _LoopStub()  # noqa: SLF001 - contract probe.
    supervisor._trace_sinks = []  # noqa: SLF001 - contract probe.
    supervisor._tracing_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._monitoring_exporters = [_BrokenMonitoringExporter()]  # noqa: SLF001 - contract probe.

    supervisor._emit_trace_dispatch_diagnostics(stage="close_begin")  # noqa: SLF001 - contract probe.
    events = _lifecycle_events(supervisor)
    assert events[-1].get("kind") == "trace_dispatch_diagnostics"
    assert events[-1].get("stage") == "close_begin"


def test_supervisor_stop_groups_flushes_monitoring_metrics_before_exporter_close() -> None:
    class _LoopStub:
        def metrics(self) -> dict[str, int]:
            return {
                "submitted": 5,
                "processed": 5,
                "failed": 0,
                "dropped": 0,
                "queue_depth": 0,
                "pending": 0,
                "running": 1,
            }

        def stop(self, *, drain: bool, timeout_seconds: float) -> None:
            _ = (drain, timeout_seconds)
            return None

    class _SinkStub:
        def diagnostics(self) -> dict[str, int]:
            return {"exported": 5, "dropped": 0, "buffered": 0}

        def close(self) -> None:
            return None

    class _MonitoringProbe:
        def __init__(self) -> None:
            self.closed = False
            self.calls: list[dict[str, object]] = []

        def publish_metrics(self, *, records: list[dict[str, object]], snapshot: dict[str, object], stage: str) -> None:
            assert self.closed is False
            self.calls.append(
                {
                    "stage": stage,
                    "pending_total_estimate": snapshot.get("pending_total_estimate"),
                    "metric_count": len(records),
                }
            )

        def close(self) -> None:
            self.closed = True

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._trace_dispatch_loop = _LoopStub()  # noqa: SLF001 - contract probe.
    supervisor._trace_sinks = [_SinkStub()]  # noqa: SLF001 - contract probe.
    supervisor._tracing_enabled = True  # noqa: SLF001 - contract probe.
    probe = _MonitoringProbe()
    supervisor._monitoring_exporters = [probe]  # noqa: SLF001 - contract probe.

    supervisor.stop_groups(graceful_timeout_seconds=1, drain_inflight=True)

    assert probe.closed is True
    stages = [str(call["stage"]) for call in probe.calls]
    assert "stop_requested" in stages
    assert "close_begin" in stages
    assert "close_end" in stages
    assert stages.index("stop_requested") < stages.index("close_begin") < stages.index("close_end")
    assert probe.calls[-1]["pending_total_estimate"] == 0


def test_supervisor_overload_metrics_are_alertable_for_monitoring_exporters() -> None:
    class _LoopStub:
        def metrics(self) -> dict[str, int]:
            return {
                "submitted": 50,
                "processed": 20,
                "failed": 1,
                "dropped": 4,
                "queue_depth": 12,
                "pending": 12,
                "submit_block_count": 0,
                "submit_timeout_count": 0,
                "submit_block_wait_ms_total": 0,
                "running": 1,
            }

    class _SinkStub:
        def diagnostics(self) -> dict[str, int]:
            return {"exported": 20, "dropped": 2, "buffered": 8}

    class _MonitoringProbe:
        def __init__(self) -> None:
            self.records_by_stage: dict[str, list[dict[str, object]]] = {}

        def publish_metrics(self, *, records: list[dict[str, object]], snapshot: dict[str, object], stage: str) -> None:
            _ = snapshot
            self.records_by_stage[stage] = [item for item in records if isinstance(item, dict)]

        def close(self) -> None:
            return None

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._trace_dispatch_loop = _LoopStub()  # noqa: SLF001 - contract probe.
    supervisor._trace_sinks = [_SinkStub()]  # noqa: SLF001 - contract probe.
    supervisor._trace_dispatch_dropped = 3  # noqa: SLF001 - contract probe.
    supervisor._tracing_enabled = True  # noqa: SLF001 - contract probe.
    probe = _MonitoringProbe()
    supervisor._monitoring_exporters = [probe]  # noqa: SLF001 - contract probe.

    supervisor._emit_trace_dispatch_diagnostics(stage="stop_requested")  # noqa: SLF001 - contract probe.

    records = probe.records_by_stage.get("stop_requested", [])
    metric_map = {
        str(item.get("name")): int(item.get("value", 0))
        for item in records
        if isinstance(item.get("name"), str) and isinstance(item.get("value"), int)
    }
    assert metric_map.get("stream_kernel_observability_dispatch_dropped_total") == 4
    assert metric_map.get("stream_kernel_observability_dispatch_submit_dropped_total") == 3
    assert metric_map.get("stream_kernel_observability_sink_dropped_total") == 2
    assert metric_map.get("stream_kernel_observability_loss_estimate_total") == 6
    assert metric_map.get("stream_kernel_observability_dispatch_wait_count_total") == 0


def test_supervisor_trace_dispatch_diagnostics_uses_monitoring_metrics_system_node_contract() -> None:
    class _LoopStub:
        def metrics(self) -> dict[str, int]:
            return {
                "submitted": 2,
                "processed": 2,
                "failed": 0,
                "dropped": 0,
                "queue_depth": 0,
                "pending": 0,
                "running": 1,
            }

    class _MonitoringProbe:
        def __init__(self) -> None:
            self.metric_counts: list[int] = []

        def publish_metrics(self, *, records: list[dict[str, object]], snapshot: dict[str, object], stage: str) -> None:
            _ = (snapshot, stage)
            self.metric_counts.append(len(records))

        def close(self) -> None:
            return None

    class _NodeProbe:
        def __init__(self) -> None:
            self.events: list[MonitoringMetricsSnapshotEvent] = []

        def __call__(self, event: object, _ctx: object | None) -> list[object]:
            if isinstance(event, MonitoringMetricsSnapshotEvent):
                self.events.append(event)
                return [
                    MonitoringMetricsSnapshotResult(
                        stage=event.stage,
                        snapshot=dict(event.snapshot),
                        metric_records=[
                            {
                                "name": "stream_kernel_observability_dispatch_submitted_total",
                                "type": "counter",
                                "value": 2,
                                "labels": {"scope": "observability_transport"},
                            }
                        ],
                    )
                ]
            return []

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._trace_dispatch_loop = _LoopStub()  # noqa: SLF001 - contract probe.
    supervisor._trace_sinks = []  # noqa: SLF001 - contract probe.
    supervisor._tracing_enabled = True  # noqa: SLF001 - contract probe.
    node_probe = _NodeProbe()
    supervisor._monitoring_metrics_dispatch_node = node_probe  # noqa: SLF001 - contract probe.
    exporter = _MonitoringProbe()
    supervisor._monitoring_exporters = [exporter]  # noqa: SLF001 - contract probe.

    supervisor._emit_trace_dispatch_diagnostics(stage="stop_requested")  # noqa: SLF001 - contract probe.

    assert len(node_probe.events) == 1
    assert node_probe.events[0].stage == "stop_requested"
    assert exporter.metric_counts == [1]


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


def test_p5pre_sup_10c_worker_loop_skips_lifecycle_traces_for_system_observability_messages(
    monkeypatch,
) -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Pipe:
        def __init__(self) -> None:
            self._messages = [
                {
                    "kind": "execute_boundary",
                    "correlation_id": "obs-c0",
                    "run_id": "run",
                    "scenario_id": "system_observability",
                    "no_reply": True,
                    "inputs": [
                        SimpleNamespace(
                            payload={"trace": "payload"},
                            dispatch_group="system.observability",
                            target="system.obs.trace_dispatch",
                            trace_id="run:obs:1",
                            reply_to=None,
                            source_group="supervisor.transport",
                            route_hop=0,
                            span_id="parent-obs-span",
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
        lambda **_kwargs: [],
    )

    pipe = _Pipe()
    bootstrap_module._worker_loop(  # noqa: SLF001 - contract probe
        None,
        pipe,
        object(),
        "system.observability#1",
        "system.observability",
        "async",
    )

    traces = [msg for msg in pipe.sent if msg.get("kind") == "worker_trace"]
    assert traces == []


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


def test_p5pre_sup_16c_worker_loop_does_not_emit_queue_telemetry() -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Pipe:
        def __init__(self) -> None:
            self._messages = [{"kind": "stop", "correlation_id": "c1"}]
            self.sent: list[dict[str, object]] = []

        def poll(self, _timeout: float) -> bool:
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
        object(),
        "execution.features#1",
        "execution.features",
        "sync",
        0.001,
    )
    queue_samples = [item for item in pipe.sent if item.get("kind") == "worker_queue_telemetry"]
    assert queue_samples == []


def test_p5pre_sup_16d_supervisor_forwards_worker_queue_telemetry_to_observability_owner_group(monkeypatch) -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._observability_service_process_group = "system.observability"  # noqa: SLF001 - contract probe.
    supervisor._worker_queue_telemetry_enabled = True  # noqa: SLF001 - contract probe.

    owner_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(991),  # type: ignore[arg-type]
        control_parent=None,
    )
    owner_handle.bootstrap_received = True
    owner_handle.ready = True
    supervisor._workers = {"system.observability": [owner_handle]}  # noqa: SLF001 - contract probe.
    supervisor._group_rr_cursor = {"system.observability": 0}  # noqa: SLF001 - contract probe.
    captured: dict[str, object] = {}

    def _capture_send(_handle: object, *, command: dict[str, object]) -> None:
        captured["command"] = command

    monkeypatch.setattr(supervisor, "_send_boundary_command_no_wait", _capture_send)

    source_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="execution.features",
        worker_index=1,
        worker_id="execution.features#1",
        process=_Process(992),  # type: ignore[arg-type]
        control_parent=None,
    )

    accepted = supervisor._handle_worker_bootstrap_message(  # noqa: SLF001 - contract probe
        source_handle,
        {
            "kind": "worker_queue_telemetry",
            "group_name": "execution.features",
            "worker_id": "execution.features#1",
            "pid": 992,
            "queue_depth": 4,
            "inflight": 1,
            "runner_profile": "sync",
            "ts_epoch_ms": 1234567890,
        },
    )

    assert accepted is True
    command = captured.get("command")
    assert isinstance(command, dict)
    assert command.get("kind") == "execute_boundary"
    assert command.get("no_reply") is True
    assert command.get("scenario_id") == "system_observability"
    inputs = command.get("inputs")
    assert isinstance(inputs, list) and len(inputs) == 1
    dispatch_input = inputs[0]
    assert getattr(dispatch_input, "dispatch_group", None) == "system.observability"
    assert getattr(dispatch_input, "target", None) == "system.obs.worker_queue_dispatch"
    payload = getattr(dispatch_input, "payload", None)
    assert isinstance(payload, WorkerQueueTelemetryEvent)
    assert payload.group_name == "execution.features"
    assert payload.queue_depth == 4
    assert payload.inflight == 1


def test_p5pre_sup_16dd_supervisor_batches_worker_queue_telemetry_samples(monkeypatch) -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._observability_service_process_group = "system.observability"  # noqa: SLF001 - contract probe.
    supervisor._worker_queue_telemetry_enabled = True  # noqa: SLF001 - contract probe.

    owner_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(991),  # type: ignore[arg-type]
        control_parent=None,
    )
    owner_handle.bootstrap_received = True
    owner_handle.ready = True
    supervisor._workers = {"system.observability": [owner_handle]}  # noqa: SLF001 - contract probe.
    supervisor._group_rr_cursor = {"system.observability": 0}  # noqa: SLF001 - contract probe.
    captured: list[dict[str, object]] = []

    def _capture_send(_handle: object, *, command: dict[str, object]) -> None:
        captured.append(command)

    monkeypatch.setattr(supervisor, "_send_boundary_command_no_wait", _capture_send)

    samples = [
        WorkerQueueTelemetryEvent(
            group_name="execution.features",
            worker_id="execution.features#1",
            pid=992,
            queue_depth=4,
            inflight=1,
            runner_profile="sync",
            ts_epoch_ms=1234567890,
        ),
        WorkerQueueTelemetryEvent(
            group_name="execution.policy",
            worker_id="execution.policy#1",
            pid=993,
            queue_depth=2,
            inflight=2,
            runner_profile="sync",
            ts_epoch_ms=1234567891,
        ),
    ]

    supervisor._dispatch_worker_queue_telemetry_batch(samples=samples)  # noqa: SLF001 - contract probe.

    assert len(captured) == 1
    command = captured[0]
    assert command.get("kind") == "execute_boundary"
    inputs = command.get("inputs")
    assert isinstance(inputs, list) and len(inputs) == 2
    payloads = [getattr(item, "payload", None) for item in inputs]
    assert all(isinstance(payload, WorkerQueueTelemetryEvent) for payload in payloads)


def test_p5pre_sup_16f_supervisor_forwards_trace_to_observability_owner_group(monkeypatch) -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._observability_service_process_group = "system.observability"  # noqa: SLF001 - contract probe.
    supervisor.configure_tracing({"exporters": [{"kind": "otel_otlp_logical", "settings": {}}]}, strict=True)

    owner_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(1991),  # type: ignore[arg-type]
        control_parent=None,
    )
    owner_handle.bootstrap_received = True
    owner_handle.ready = True
    supervisor._workers = {"system.observability": [owner_handle]}  # noqa: SLF001 - contract probe.
    supervisor._group_rr_cursor = {"system.observability": 0}  # noqa: SLF001 - contract probe.

    captured: dict[str, object] = {}

    def _capture_send(_handle: object, *, command: dict[str, object]) -> None:
        captured["command"] = command

    monkeypatch.setattr(supervisor, "_send_boundary_command_no_wait", _capture_send)

    now = datetime(2026, 2, 20, 0, 0, 0, tzinfo=UTC)
    record = TraceRecord(
        trace_id="trace-forward-1",
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
        span_id="abc123abc123abce",
    )

    supervisor._emit_trace_record(record)  # noqa: SLF001 - contract probe.
    supervisor._flush_trace_sinks()  # noqa: SLF001 - contract probe.

    command = captured.get("command")
    assert isinstance(command, dict)
    assert command.get("kind") == "execute_boundary"
    assert command.get("no_reply") is True
    assert command.get("scenario_id") == "system_observability"
    inputs = command.get("inputs")
    assert isinstance(inputs, list) and len(inputs) == 1
    dispatch_input = inputs[0]
    assert getattr(dispatch_input, "dispatch_group", None) == "system.observability"
    assert getattr(dispatch_input, "target", None) == "system.obs.trace_dispatch"
    payload = getattr(dispatch_input, "payload", None)
    assert isinstance(payload, TraceDispatchEvent)
    assert payload.payload == record
    assert payload.trace_id == "trace-forward-1"
    assert supervisor._trace_forward_submitted == 1  # noqa: SLF001


def test_p5pre_sup_16fb_supervisor_dispatch_trace_skips_system_observability_messages(monkeypatch) -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._observability_service_process_group = "system.observability"  # noqa: SLF001 - contract probe.
    supervisor.configure_tracing({"exporters": [{"kind": "otel_otlp_logical", "settings": {}}]}, strict=True)

    owner_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(2999),  # type: ignore[arg-type]
        control_parent=None,
    )
    owner_handle.bootstrap_received = True
    owner_handle.ready = True
    supervisor._workers = {"system.observability": [owner_handle]}  # noqa: SLF001 - contract probe.
    supervisor._group_rr_cursor = {"system.observability": 0}  # noqa: SLF001 - contract probe.

    captured: list[dict[str, object]] = []

    def _capture_send(_handle: object, *, command: dict[str, object]) -> None:
        captured.append(command)

    monkeypatch.setattr(supervisor, "_send_boundary_command_no_wait", _capture_send)

    dispatched_item = SimpleNamespace(
        payload={"control": "trace"},
        trace_id="trace-service-1",
        source_group="supervisor.transport",
        target="system.obs.trace_dispatch",
        route_hop=0,
        span_id="zz11zz11zz11zz11",
    )
    supervisor._emit_supervisor_dispatch_trace(  # noqa: SLF001 - contract probe
        dispatched_item=dispatched_item,
        dispatch_group="system.observability",
        scenario_id="system_observability",
    )
    supervisor._flush_trace_sinks()  # noqa: SLF001 - contract probe.

    assert captured == []
    assert supervisor._trace_forward_submitted == 0  # noqa: SLF001


def test_p5pre_sup_17a_execute_boundary_uses_business_dispatch_port(monkeypatch) -> None:
    class _BusinessPort(BusinessDispatchPort):
        def __init__(self) -> None:
            self.send_calls = 0
            self.send_no_wait_calls = 0

        def send(
            self,
            *,
            handle: object,
            command: dict[str, object],
            timeout_seconds: float,
            raise_on_timeout: bool = True,
        ) -> dict[str, object]:
            _ = (handle, command, timeout_seconds, raise_on_timeout)
            self.send_calls += 1
            return {"kind": "execute_boundary_result", "terminal_outputs": []}

        def send_no_wait(
            self,
            *,
            handle: object,
            command: dict[str, object],
        ) -> None:
            _ = (handle, command)
            self.send_no_wait_calls += 1

    class _ControlPort(ControlPlaneDispatchPort):
        def __init__(self) -> None:
            self.calls = 0

        def send_no_wait(self, *, handle: object, command: dict[str, object]) -> None:
            _ = (handle, command)
            self.calls += 1
            raise AssertionError("control port must not be used by business boundary dispatch")

    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

    supervisor = MultiprocessBootstrapSupervisor()
    business_port = _BusinessPort()
    control_port = _ControlPort()
    supervisor.set_dispatch_ports(business=business_port, control=control_port)
    handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="execution.features",
        worker_index=1,
        worker_id="execution.features#1",
        process=_Process(3391),  # type: ignore[arg-type]
        control_parent=None,
        ready=True,
        bootstrap_received=True,
    )
    supervisor._workers = {"execution.features": [handle]}  # noqa: SLF001
    supervisor._group_rr_cursor = {"execution.features": 0}  # noqa: SLF001

    result = supervisor.execute_boundary(
        run=lambda: None,
        run_id="run",
        scenario_id="scenario",
        inputs=[
            SimpleNamespace(
                payload={"id": "x"},
                dispatch_group="execution.features",
                target="compute_features",
                trace_id="run:0",
                reply_to=None,
                source_group="supervisor.entry",
                route_hop=0,
                span_id=None,
            )
        ],
    )

    assert result.terminal_outputs == []
    assert business_port.send_calls == 1
    assert business_port.send_no_wait_calls == 0
    assert control_port.calls == 0


def test_p5pre_sup_17b_observability_trace_forward_uses_control_dispatch_port() -> None:
    class _BusinessPort(BusinessDispatchPort):
        def send(
            self,
            *,
            handle: object,
            command: dict[str, object],
            timeout_seconds: float,
            raise_on_timeout: bool = True,
        ) -> dict[str, object]:
            _ = (handle, command, timeout_seconds, raise_on_timeout)
            raise AssertionError("business port must not be used by observability control-plane forward")

        def send_no_wait(
            self,
            *,
            handle: object,
            command: dict[str, object],
        ) -> None:
            _ = (handle, command)
            raise AssertionError("business port must not be used by observability control-plane forward")

    class _ControlPort(ControlPlaneDispatchPort):
        def __init__(self) -> None:
            self.calls = 0

        def send_no_wait(self, *, handle: object, command: dict[str, object]) -> None:
            _ = (handle, command)
            self.calls += 1

    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001
    supervisor._observability_service_process_group = "system.observability"  # noqa: SLF001
    supervisor._tracing_enabled = True  # noqa: SLF001
    business_port = _BusinessPort()
    control_port = _ControlPort()
    supervisor.set_dispatch_ports(business=business_port, control=control_port)

    owner_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(3392),  # type: ignore[arg-type]
        control_parent=None,
        ready=True,
        bootstrap_received=True,
    )
    supervisor._workers = {"system.observability": [owner_handle]}  # noqa: SLF001
    supervisor._group_rr_cursor = {"system.observability": 0}  # noqa: SLF001

    now = datetime(2026, 2, 20, 0, 0, 0, tzinfo=UTC)
    record = TraceRecord(
        trace_id="trace-control-forward",
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
        span_id="c1c2c3c4c5c6c7c8",
    )

    accepted = supervisor._dispatch_trace_record_to_observability_owner(record=record)  # noqa: SLF001
    assert accepted is True
    assert control_port.calls == 1


def test_p5pre_sup_17c_control_plane_congestion_does_not_block_business_dispatch() -> None:
    class _BusinessPort(BusinessDispatchPort):
        def send(
            self,
            *,
            handle: object,
            command: dict[str, object],
            timeout_seconds: float,
            raise_on_timeout: bool = True,
        ) -> dict[str, object]:
            _ = (handle, command, timeout_seconds, raise_on_timeout)
            return {"kind": "execute_boundary_result", "terminal_outputs": []}

        def send_no_wait(
            self,
            *,
            handle: object,
            command: dict[str, object],
        ) -> None:
            _ = (handle, command)
            return None

    class _ControlPort(ControlPlaneDispatchPort):
        def __init__(self) -> None:
            self.entered = Event()
            self.release = Event()

        def send_no_wait(self, *, handle: object, command: dict[str, object]) -> None:
            _ = (handle, command)
            self.entered.set()
            self.release.wait(timeout=1.0)

    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001
    supervisor._observability_service_process_group = "system.observability"  # noqa: SLF001
    supervisor._tracing_enabled = True  # noqa: SLF001
    business_port = _BusinessPort()
    control_port = _ControlPort()
    supervisor.set_dispatch_ports(business=business_port, control=control_port)

    owner_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(4391),  # type: ignore[arg-type]
        control_parent=None,
        ready=True,
        bootstrap_received=True,
    )
    business_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="execution.features",
        worker_index=1,
        worker_id="execution.features#1",
        process=_Process(4392),  # type: ignore[arg-type]
        control_parent=None,
        ready=True,
        bootstrap_received=True,
    )
    supervisor._workers = {  # noqa: SLF001
        "system.observability": [owner_handle],
        "execution.features": [business_handle],
    }
    supervisor._group_rr_cursor = {  # noqa: SLF001
        "system.observability": 0,
        "execution.features": 0,
    }

    now = datetime(2026, 2, 20, 0, 0, 0, tzinfo=UTC)
    record = TraceRecord(
        trace_id="trace-control-blocked",
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
        span_id="d1d2d3d4d5d6d7d8",
    )

    forward_thread = Thread(
        target=lambda: supervisor._dispatch_trace_record_to_observability_owner(record=record),  # noqa: SLF001
        daemon=True,
    )
    forward_thread.start()
    assert control_port.entered.wait(timeout=0.2), "control forward must enter blocked send path"

    # Avoid extra control-plane activity from lifecycle tracing while measuring business dispatch.
    supervisor._tracing_enabled = False  # noqa: SLF001
    started = time.perf_counter()
    result = supervisor.execute_boundary(
        run=lambda: None,
        run_id="run",
        scenario_id="scenario",
        inputs=[
            SimpleNamespace(
                payload={"id": "x"},
                dispatch_group="execution.features",
                target="compute_features",
                trace_id="run:0",
                reply_to=None,
                source_group="supervisor.entry",
                route_hop=0,
                span_id=None,
            )
        ],
    )
    elapsed = time.perf_counter() - started
    control_port.release.set()
    forward_thread.join(timeout=0.5)

    assert result.terminal_outputs == []
    assert elapsed < 0.1


def test_p5pre_sup_16fa_dedicated_mode_forwards_full_message_lifecycle_spans(monkeypatch) -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._observability_service_process_group = "system.observability"  # noqa: SLF001 - contract probe.
    supervisor.configure_tracing({"exporters": [{"kind": "otel_otlp_logical", "settings": {}}]}, strict=True)

    owner_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(1994),  # type: ignore[arg-type]
        control_parent=None,
    )
    owner_handle.bootstrap_received = True
    owner_handle.ready = True
    supervisor._workers = {"system.observability": [owner_handle]}  # noqa: SLF001 - contract probe.
    supervisor._group_rr_cursor = {"system.observability": 0}  # noqa: SLF001 - contract probe.

    captured_step_names: list[str] = []

    def _capture_send(_handle: object, *, command: dict[str, object]) -> None:
        inputs = command.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            return
        for item in inputs:
            payload = getattr(item, "payload", None)
            if isinstance(payload, TraceDispatchEvent) and isinstance(payload.payload, TraceRecord):
                captured_step_names.append(payload.payload.step_name)

    monkeypatch.setattr(supervisor, "_send_boundary_command_no_wait", _capture_send)

    now = datetime(2026, 2, 20, 0, 0, 0, tzinfo=UTC)
    worker_receive = TraceRecord(
        trace_id="trace-lifecycle-1",
        scenario="exp",
        step_index=-1,
        step_name="system.obs.worker_boundary_receive",
        work_index=0,
        t_enter=now,
        t_exit=now,
        duration_ms=0.0,
        msg_in=MessageSignature(type_name="dict", identity=None, hash=None),
        msg_out=(MessageSignature(type_name="boundary_dispatch", identity=None, hash=None),),
        msg_out_count=1,
        ctx_before=None,
        ctx_after=None,
        ctx_diff=None,
        status="ok",
        error=None,
        span_id="ab1111ab1111ab11",
    )
    worker_emit = replace(
        worker_receive,
        step_name="system.obs.worker_boundary_emit",
        span_id="ab2222ab2222ab22",
    )

    worker_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="execution.features",
        worker_index=1,
        worker_id="execution.features#1",
        process=_Process(2991),  # type: ignore[arg-type]
        control_parent=None,
    )
    assert supervisor._handle_worker_bootstrap_message(  # noqa: SLF001 - contract probe
        worker_handle,
        {"kind": "worker_trace", "record": worker_receive},
    )
    assert supervisor._handle_worker_bootstrap_message(  # noqa: SLF001 - contract probe
        worker_handle,
        {"kind": "worker_trace", "record": worker_emit},
    )

    dispatched_item = SimpleNamespace(
        payload={"id": "A-1"},
        trace_id="trace-lifecycle-1",
        source_group="execution.ingress",
        target="compute_features",
        route_hop=0,
        span_id=worker_emit.span_id,
    )
    supervisor._emit_supervisor_dispatch_trace(  # noqa: SLF001 - contract probe
        dispatched_item=dispatched_item,
        dispatch_group="execution.features",
        scenario_id="exp",
    )

    pending: list[object] = []
    terminal_outputs: list[object] = []
    response = {
        "kind": "execute_boundary_result",
        "terminal_outputs": [
            Envelope(
                payload={"result": "ok"},
                trace_id="trace-lifecycle-1",
                target=None,
                span_id="ab3333ab3333ab33",
            )
        ],
    }
    output_count, requeued_count, terminal_count = supervisor._consume_boundary_response(  # noqa: SLF001
        response=response,
        group_name="execution.features",
        scenario_id="exp",
        dispatched_item=dispatched_item,
        pending=pending,
        terminal_outputs=terminal_outputs,
    )
    assert output_count == 1
    assert requeued_count == 0
    assert terminal_count == 1

    supervisor._flush_trace_sinks()  # noqa: SLF001 - contract probe.
    steps = set(captured_step_names)
    assert "system.obs.worker_boundary_receive" in steps
    assert "system.obs.worker_boundary_emit" in steps
    assert "system.obs.supervisor_boundary_dispatch" in steps
    assert "system.obs.supervisor_boundary_receive" in steps


def test_p5pre_sup_16g_supervisor_drops_trace_until_observability_owner_ready(monkeypatch) -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._observability_service_process_group = "system.observability"  # noqa: SLF001 - contract probe.
    supervisor.configure_tracing({"exporters": [{"kind": "otel_otlp_logical", "settings": {}}]}, strict=True)

    owner_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(1992),  # type: ignore[arg-type]
        control_parent=None,
    )
    owner_handle.bootstrap_received = False
    owner_handle.ready = False
    supervisor._workers = {"system.observability": [owner_handle]}  # noqa: SLF001 - contract probe.
    supervisor._group_rr_cursor = {"system.observability": 0}  # noqa: SLF001 - contract probe.

    send_calls = 0

    def _capture_send(_handle: object, *, command: dict[str, object]) -> None:
        _ = command
        nonlocal send_calls
        send_calls += 1

    monkeypatch.setattr(supervisor, "_send_boundary_command_no_wait", _capture_send)

    now = datetime(2026, 2, 20, 0, 0, 0, tzinfo=UTC)
    record = TraceRecord(
        trace_id="trace-forward-drop",
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
        span_id="def456def456def4",
    )

    supervisor._emit_trace_record(record)  # noqa: SLF001 - contract probe.
    supervisor._flush_trace_sinks()  # noqa: SLF001 - contract probe.

    assert send_calls == 0
    assert supervisor._trace_forward_submitted == 0  # noqa: SLF001
    assert supervisor._trace_forward_failed == 1  # noqa: SLF001
    assert supervisor._trace_forward_dropped_before_owner_ready == 1  # noqa: SLF001
    assert supervisor._trace_dispatch_dropped == 1  # noqa: SLF001


def test_p5pre_sup_16h_configure_tracing_dedicated_owner_does_not_build_supervisor_sinks(tmp_path) -> None:
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001 - contract probe.

    path = tmp_path / "must_not_be_used_by_supervisor.jsonl"
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

    assert supervisor._tracing_enabled is True  # noqa: SLF001 - contract probe.
    assert supervisor._trace_sinks == []  # noqa: SLF001 - contract probe.
    assert supervisor._trace_dispatch_loop is not None  # noqa: SLF001 - contract probe.


def test_p5pre_sup_16hc_emit_trace_record_dedicated_owner_does_not_block_on_transport_send(monkeypatch) -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._observability_service_process_group = "system.observability"  # noqa: SLF001 - contract probe.
    supervisor.configure_tracing({"exporters": [{"kind": "otel_otlp_logical", "settings": {}}]}, strict=True)

    owner_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(1993),  # type: ignore[arg-type]
        control_parent=None,
    )
    owner_handle.bootstrap_received = True
    owner_handle.ready = True
    supervisor._workers = {"system.observability": [owner_handle]}  # noqa: SLF001 - contract probe.
    supervisor._group_rr_cursor = {"system.observability": 0}  # noqa: SLF001 - contract probe.

    captured: list[dict[str, object]] = []

    def _capture_send(_handle: object, *, command: dict[str, object]) -> None:
        time.sleep(0.2)
        captured.append(command)

    monkeypatch.setattr(supervisor, "_send_boundary_command_no_wait", _capture_send)

    now = datetime(2026, 2, 20, 0, 0, 0, tzinfo=UTC)
    record = TraceRecord(
        trace_id="trace-forward-nonblocking",
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
        span_id="abc123abc123abcf",
    )

    started = time.perf_counter()
    supervisor._emit_trace_record(record)  # noqa: SLF001 - contract probe.
    elapsed = time.perf_counter() - started

    # Contract: emit path must enqueue and return quickly even when transport send is slow.
    assert elapsed < 0.1
    supervisor._flush_trace_sinks()  # noqa: SLF001 - contract probe.
    assert len(captured) == 1


def test_p5pre_sup_16hd_emit_trace_record_dedicated_owner_without_loop_drops_without_inline_forward(
    monkeypatch,
) -> None:
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._observability_service_process_group = "system.observability"  # noqa: SLF001 - contract probe.
    supervisor._tracing_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._trace_dispatch_loop = None  # noqa: SLF001 - contract probe.

    called = {"forward": 0}

    def _capture_forward(*, record: TraceRecord) -> bool:
        _ = record
        called["forward"] += 1
        return True

    monkeypatch.setattr(supervisor, "_dispatch_trace_record_to_observability_owner", _capture_forward)

    now = datetime(2026, 2, 20, 0, 0, 0, tzinfo=UTC)
    record = TraceRecord(
        trace_id="trace-forward-loop-missing",
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
        span_id="f0f1f2f3f4f5f6f7",
    )

    supervisor._emit_trace_record(record)  # noqa: SLF001 - contract probe.

    assert called["forward"] == 0
    assert supervisor._trace_dispatch_dropped == 1  # noqa: SLF001 - contract probe.


def test_p5pre_sup_16he_trace_forward_uses_configured_batching(monkeypatch) -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._observability_service_process_group = "system.observability"  # noqa: SLF001 - contract probe.
    supervisor.configure_tracing(
        {
            "dispatch_queue": {
                "forward_batch_max_items": 3,
                "forward_flush_interval_ms": 60000,
            },
            "exporters": [{"kind": "otel_otlp_logical", "settings": {}}],
        },
        strict=True,
    )

    owner_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(2001),  # type: ignore[arg-type]
        control_parent=None,
    )
    owner_handle.bootstrap_received = True
    owner_handle.ready = True
    supervisor._workers = {"system.observability": [owner_handle]}  # noqa: SLF001 - contract probe.
    supervisor._group_rr_cursor = {"system.observability": 0}  # noqa: SLF001 - contract probe.

    captured: list[dict[str, object]] = []

    def _capture_send(_handle: object, *, command: dict[str, object]) -> None:
        captured.append(command)

    monkeypatch.setattr(supervisor, "_send_boundary_command_no_wait", _capture_send)

    now = datetime(2026, 2, 20, 0, 0, 0, tzinfo=UTC)
    for idx in range(7):
        record = TraceRecord(
            trace_id=f"trace-forward-batch-{idx}",
            scenario="exp",
            step_index=1,
            step_name="compute_features",
            work_index=idx,
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
            span_id=f"{idx:016x}",
        )
        supervisor._emit_trace_record(record)  # noqa: SLF001 - contract probe.

    supervisor._flush_trace_sinks()  # noqa: SLF001 - contract probe.

    assert len(captured) == 3
    batch_sizes = [len(command.get("inputs", [])) for command in captured]
    assert batch_sizes == [3, 3, 1]
    assert supervisor._trace_forward_submitted == 7  # noqa: SLF001 - contract probe.
    assert supervisor._trace_dispatch_dropped == 0  # noqa: SLF001 - contract probe.


def test_p5pre_sup_16ha_configure_lifecycle_logging_dedicated_owner_keeps_stdout_only(tmp_path) -> None:
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001 - contract probe.

    lifecycle_path = tmp_path / "must_not_be_used_by_supervisor.log"
    all_path = tmp_path / "must_not_be_used_by_supervisor.jsonl"
    supervisor.configure_lifecycle_logging(
        {
            "exporters": [
                {"kind": "file_plain", "mode": "lifecycle", "settings": {"path": str(lifecycle_path)}},
                {"kind": "jsonl", "mode": "all", "settings": {"path": str(all_path)}},
            ],
            "lifecycle_events": {"enabled": True, "level": "debug"},
        }
    )

    assert supervisor._lifecycle_logging_enabled is True  # noqa: SLF001 - contract probe.
    assert supervisor._all_logging_enabled is False  # noqa: SLF001 - contract probe.
    assert isinstance(supervisor._lifecycle_log_sink, StdoutPlainLogSink)  # noqa: SLF001 - contract probe.
    assert supervisor._all_log_sink is None  # noqa: SLF001 - contract probe.


def test_p5pre_sup_16hb_configure_monitoring_dedicated_owner_skips_supervisor_sinks(tmp_path) -> None:
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001 - contract probe.

    monitoring_path = tmp_path / "must_not_be_used_by_supervisor.prom"
    supervisor.configure_monitoring(
        {
            "exporters": [
                {
                    "kind": "prometheus",
                    "settings": {
                        "mode": "textfile",
                        "textfile": {"path": str(monitoring_path)},
                    },
                }
            ]
        },
        strict=True,
    )

    assert supervisor._monitoring_exporters == []  # noqa: SLF001 - contract probe.


def test_p5pre_sup_16da_supervisor_skips_worker_queue_telemetry_until_observability_owner_bootstrapped(
    monkeypatch,
) -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._observability_service_process_group = "system.observability"  # noqa: SLF001 - contract probe.
    supervisor._worker_queue_telemetry_enabled = True  # noqa: SLF001 - contract probe.

    owner_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(991),  # type: ignore[arg-type]
        control_parent=None,
    )
    owner_handle.bootstrap_received = False
    supervisor._workers = {"system.observability": [owner_handle]}  # noqa: SLF001 - contract probe.
    supervisor._group_rr_cursor = {"system.observability": 0}  # noqa: SLF001 - contract probe.

    send_calls = 0

    def _capture_send(_handle: object, *, command: dict[str, object]) -> None:
        _ = command
        nonlocal send_calls
        send_calls += 1

    monkeypatch.setattr(supervisor, "_send_boundary_command_no_wait", _capture_send)

    source_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="execution.features",
        worker_index=1,
        worker_id="execution.features#1",
        process=_Process(992),  # type: ignore[arg-type]
        control_parent=None,
    )

    accepted = supervisor._handle_worker_bootstrap_message(  # noqa: SLF001 - contract probe
        source_handle,
        {
            "kind": "worker_queue_telemetry",
            "group_name": "execution.features",
            "worker_id": "execution.features#1",
            "pid": 992,
            "queue_depth": 4,
            "inflight": 1,
            "runner_profile": "sync",
            "ts_epoch_ms": 1234567890,
        },
    )

    assert accepted is True
    assert send_calls == 0
    assert supervisor._worker_queue_telemetry_dropped_before_owner_ready == 1  # noqa: SLF001


def test_p5pre_sup_16db_supervisor_skips_worker_queue_telemetry_self_loop_for_owner_group(
    monkeypatch,
) -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._observability_service_process_group = "system.observability"  # noqa: SLF001 - contract probe.
    supervisor._worker_queue_telemetry_enabled = True  # noqa: SLF001 - contract probe.

    owner_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(991),  # type: ignore[arg-type]
        control_parent=None,
    )
    owner_handle.bootstrap_received = True
    owner_handle.ready = True
    supervisor._workers = {"system.observability": [owner_handle]}  # noqa: SLF001 - contract probe.
    supervisor._group_rr_cursor = {"system.observability": 0}  # noqa: SLF001 - contract probe.

    send_calls = 0

    def _capture_send(_handle: object, *, command: dict[str, object]) -> None:
        _ = command
        nonlocal send_calls
        send_calls += 1

    monkeypatch.setattr(supervisor, "_send_boundary_command_no_wait", _capture_send)

    source_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(991),  # type: ignore[arg-type]
        control_parent=None,
    )

    accepted = supervisor._handle_worker_bootstrap_message(  # noqa: SLF001 - contract probe
        source_handle,
        {
            "kind": "worker_queue_telemetry",
            "group_name": "system.observability",
            "worker_id": "system.observability#1",
            "pid": 991,
            "queue_depth": 7,
            "inflight": 1,
            "runner_profile": "async",
            "ts_epoch_ms": 1234567890,
        },
    )

    assert accepted is True
    assert send_calls == 0
    assert supervisor._worker_queue_telemetry_dropped_before_owner_ready == 1  # noqa: SLF001


def test_p5pre_sup_16dc_supervisor_emits_worker_queue_telemetry_snapshots_from_inflight_counters(
    monkeypatch,
) -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._observability_service_process_group = "system.observability"  # noqa: SLF001 - contract probe.
    supervisor._worker_queue_telemetry_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._worker_queue_telemetry_interval_seconds = 0.1  # noqa: SLF001 - contract probe.
    supervisor._next_worker_queue_telemetry_ts = 0.0  # noqa: SLF001 - contract probe.
    supervisor._group_runner_profiles = {  # noqa: SLF001 - contract probe.
        "execution.ingress": "auto",
        "execution.features": "sync",
        "system.observability": "async",
    }

    owner_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(9001),  # type: ignore[arg-type]
        control_parent=None,
        ready=True,
        bootstrap_received=True,
    )
    ingress_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="execution.ingress",
        worker_index=1,
        worker_id="execution.ingress#1",
        process=_Process(9002),  # type: ignore[arg-type]
        control_parent=None,
        ready=True,
        bootstrap_received=True,
        inflight_commands=3,
    )
    features_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="execution.features",
        worker_index=1,
        worker_id="execution.features#1",
        process=_Process(9003),  # type: ignore[arg-type]
        control_parent=None,
        ready=True,
        bootstrap_received=True,
        inflight_commands=1,
    )

    supervisor._workers = {  # noqa: SLF001 - contract probe.
        "system.observability": [owner_handle],
        "execution.ingress": [ingress_handle],
        "execution.features": [features_handle],
    }
    supervisor._group_rr_cursor = {"system.observability": 0}  # noqa: SLF001 - contract probe.
    captured_commands: list[dict[str, object]] = []

    def _capture_send(_handle: object, *, command: dict[str, object]) -> None:
        captured_commands.append(command)

    monkeypatch.setattr(supervisor, "_send_boundary_command_no_wait", _capture_send)

    supervisor._emit_supervisor_worker_queue_telemetry_if_due(now_monotonic=10.0)  # noqa: SLF001 - contract probe.
    supervisor._emit_supervisor_worker_queue_telemetry_if_due(now_monotonic=10.01)  # noqa: SLF001 - contract probe.

    assert len(captured_commands) == 2
    payloads = []
    for command in captured_commands:
        inputs = command.get("inputs")
        assert isinstance(inputs, list) and len(inputs) == 1
        payload = getattr(inputs[0], "payload", None)
        assert isinstance(payload, WorkerQueueTelemetryEvent)
        payloads.append(payload)

    by_worker = {payload.worker_id: payload for payload in payloads}
    assert set(by_worker.keys()) == {"execution.ingress#1", "execution.features#1"}
    assert by_worker["execution.ingress#1"].queue_depth == 3
    assert by_worker["execution.ingress#1"].inflight == 3
    assert by_worker["execution.features#1"].queue_depth == 1
    assert by_worker["execution.features#1"].inflight == 1


def test_p5pre_sup_16e_worker_queue_telemetry_handling_is_lock_reentrant(monkeypatch) -> None:
    # BOOT-OBS-16E: telemetry handling must not deadlock when called during locked ready-drain path.
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._observability_service_process_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._observability_service_process_group = "system.observability"  # noqa: SLF001 - contract probe.

    owner_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(991),  # type: ignore[arg-type]
        control_parent=None,
    )
    monkeypatch.setattr(
        supervisor,
        "_select_worker_for_group",
        lambda group_name: owner_handle if group_name == "system.observability" else None,
    )
    monkeypatch.setattr(supervisor, "_send_boundary_command_no_wait", lambda *_args, **_kwargs: None)

    source_handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="execution.features",
        worker_index=1,
        worker_id="execution.features#1",
        process=_Process(992),  # type: ignore[arg-type]
        control_parent=None,
    )

    done = Event()
    result: dict[str, object] = {}

    def _target() -> None:
        with supervisor._lock:  # noqa: SLF001 - emulate wait_ready lock scope.
            accepted = supervisor._handle_worker_bootstrap_message(  # noqa: SLF001 - contract probe
                source_handle,
                {
                    "kind": "worker_queue_telemetry",
                    "group_name": "execution.features",
                    "worker_id": "execution.features#1",
                    "pid": 992,
                    "queue_depth": 4,
                    "inflight": 1,
                    "runner_profile": "sync",
                    "ts_epoch_ms": 1234567890,
                },
            )
            result["accepted"] = accepted
            done.set()

    thread = Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=0.5)

    assert done.is_set(), "worker_queue_telemetry handling must not deadlock under supervisor lock"
    assert result.get("accepted") is True


def test_p5pre_sup_16f_drain_worker_bootstrap_messages_is_bounded_per_cycle() -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

    class _Pipe:
        def __init__(self) -> None:
            self.recv_calls = 0

        def poll(self, _timeout: float) -> bool:
            return True

        def recv(self) -> dict[str, object]:
            self.recv_calls += 1
            return {"kind": "worker_lifecycle", "message": "bootstrap.worker_ping", "fields": {}}

    supervisor = MultiprocessBootstrapSupervisor()
    pipe = _Pipe()
    handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe
        group_name="execution.features",
        worker_index=1,
        worker_id="execution.features#1",
        process=_Process(993),  # type: ignore[arg-type]
        control_parent=pipe,  # type: ignore[arg-type]
    )

    supervisor._drain_worker_bootstrap_messages_locked(handle, max_messages=5)  # noqa: SLF001

    assert pipe.recv_calls == 5


def test_p5pre_sup_16fa_control_channel_sends_are_serialized_per_worker() -> None:
    # P5PRE-SUP-16FA: concurrent control-plane no-reply sends to one worker must be serialized.
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return True

    class _Pipe:
        def __init__(self) -> None:
            self.max_parallel = 0
            self._active = 0
            self._lock = Lock()

        def send(self, _payload: dict[str, object]) -> None:
            with self._lock:
                self._active += 1
                self.max_parallel = max(self.max_parallel, self._active)
            time.sleep(0.01)
            with self._lock:
                self._active = max(0, self._active - 1)

    supervisor = MultiprocessBootstrapSupervisor()
    pipe = _Pipe()
    errors: list[Exception] = []
    handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe.
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(995),  # type: ignore[arg-type]
        control_parent=pipe,  # type: ignore[arg-type]
    )

    def _target(n: int) -> None:
        try:
            supervisor._send_boundary_command_no_wait(  # noqa: SLF001 - contract probe.
                handle,
                command={"kind": "execute_boundary", "correlation_id": f"t{n}", "inputs": []},
            )
        except Exception as exc:  # noqa: BLE001 - test probe.
            errors.append(exc)

    threads: list[Thread] = []
    for index in range(8):
        thread = Thread(
            target=_target,
            args=(index,),
            daemon=True,
        )
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join(timeout=1.0)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert pipe.max_parallel == 1


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


def test_p5pre_sup_18_routes_system_observability_targets_to_dedicated_group() -> None:
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_process_groups(
        [
            {"name": "execution.cpu", "workers": 1, "nodes": ["compute_features"]},
            {"name": "system.observability", "workers": 1, "nodes": ["system.obs.trace_dispatch"]},
        ]
    )

    mapped = supervisor._build_boundary_input_from_envelope(  # noqa: SLF001 - contract probe.
        Envelope(payload={"kind": "trace"}, target="system.obs.trace_dispatch", trace_id="t1"),
        source_group="execution.cpu",
        route_hop=1,
    )

    assert getattr(mapped, "dispatch_group", None) == "system.observability"
    assert getattr(mapped, "source_group", None) == "execution.cpu"


def test_p5pre_sup_19_wait_ready_includes_observability_process_group() -> None:
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_process_groups(
        [
            {"name": "execution.cpu", "workers": 1},
            {"name": "system.observability", "workers": 1},
        ]
    )

    try:
        supervisor.start_groups(["execution.cpu", "system.observability"])
        assert supervisor.wait_ready(2) is True

        events = _lifecycle_events(supervisor)
        assert any(
            event.get("kind") == "worker_ready" and event.get("group_name") == "execution.cpu"
            for event in events
        )
        assert any(
            event.get("kind") == "worker_ready" and event.get("group_name") == "system.observability"
            for event in events
        )
    finally:
        try:
            supervisor.stop_groups(graceful_timeout_seconds=1, drain_inflight=True)
        except Exception:  # noqa: BLE001 - cleanup fallback in constrained CI environments.
            supervisor.force_terminate_groups(["execution.cpu", "system.observability"])


def test_p5pre_sup_19a_wait_ready_requires_worker_bootstrap_for_child_bundle() -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self) -> None:
            self.pid = 881

        def is_alive(self) -> bool:
            return True

    class _Pipe:
        def poll(self, _timeout: float) -> bool:
            return False

        def recv(self) -> dict[str, object]:
            return {}

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._child_bundle = ChildBootstrapBundle(  # noqa: SLF001 - contract probe.
        scenario_id="scenario",
        run_id="run",
        process_group=None,
        discovery_modules=[],
        runtime={},
        adapters={},
        config={},
        key_bundle=object(),  # type: ignore[arg-type]
    )
    handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe.
        group_name="execution.cpu",
        worker_index=1,
        worker_id="execution.cpu#1",
        process=_Process(),  # type: ignore[arg-type]
        control_parent=_Pipe(),  # type: ignore[arg-type]
    )
    supervisor._workers = {"execution.cpu": [handle]}  # noqa: SLF001 - contract probe.
    supervisor._start_ts = time.monotonic() - 1.0  # noqa: SLF001 - contract probe.

    assert supervisor.wait_ready(1) is False


def test_p5pre_sup_19b_wait_ready_fails_fast_when_worker_reports_bootstrap_failed() -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self) -> None:
            self.pid = 882

        def is_alive(self) -> bool:
            return True

    class _Pipe:
        def __init__(self) -> None:
            self._messages = [
                {
                    "kind": "worker_lifecycle",
                    "message": "bootstrap.worker_bootstrap_failed",
                    "fields": {
                        "error_type": "ValueError",
                        "error_message": "monitoring_prometheus failed to bind http endpoint 0.0.0.0:9465",
                    },
                }
            ]

        def poll(self, _timeout: float) -> bool:
            return bool(self._messages)

        def recv(self) -> dict[str, object]:
            return self._messages.pop(0)

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._child_bundle = ChildBootstrapBundle(  # noqa: SLF001 - contract probe.
        scenario_id="scenario",
        run_id="run",
        process_group=None,
        discovery_modules=[],
        runtime={},
        adapters={},
        config={},
        key_bundle=object(),  # type: ignore[arg-type]
    )
    handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe.
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(),  # type: ignore[arg-type]
        control_parent=_Pipe(),  # type: ignore[arg-type]
    )
    supervisor._workers = {"system.observability": [handle]}  # noqa: SLF001 - contract probe.
    supervisor._start_ts = time.monotonic() - 1.0  # noqa: SLF001 - contract probe.

    started = time.monotonic()
    assert supervisor.wait_ready(5) is False
    assert (time.monotonic() - started) < 1.0
    events = _lifecycle_events(supervisor)
    failure_events = [
        event
        for event in events
        if event.get("kind") == "worker_failed" and event.get("group_name") == "system.observability"
    ]
    assert failure_events
    assert failure_events[-1].get("reason") == "bootstrap_failed"
    assert failure_events[-1].get("error_type") == "ValueError"


def test_p5pre_sup_20_child_bundle_marks_observability_group_as_observability_worker() -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    bundle = ChildBootstrapBundle(
        scenario_id="scenario",
        run_id="run",
        process_group=None,
        discovery_modules=[],
        runtime={
            "observability": {
                "service_process": {
                    "enabled": True,
                    "group_name": "system.observability",
                }
            }
        },
        adapters={},
        config={},
        key_bundle=object(),  # type: ignore[arg-type]
    )

    business = bootstrap_module._build_child_bundle_for_group(bundle, "execution.cpu")  # noqa: SLF001
    assert isinstance(business, ChildBootstrapBundle)
    assert business.runtime.get("__process_role") == "worker"

    obs = bootstrap_module._build_child_bundle_for_group(bundle, "system.observability")  # noqa: SLF001
    assert isinstance(obs, ChildBootstrapBundle)
    assert obs.runtime.get("__process_role") == "observability_worker"


def test_p5pre_sup_20a_system_observability_dispatch_uses_fire_and_forget_no_reply() -> None:
    from stream_kernel.platform.services.runtime import bootstrap as bootstrap_module

    class _Process:
        def __init__(self) -> None:
            self.pid = 882

        def is_alive(self) -> bool:
            return True

    class _Pipe:
        def __init__(self) -> None:
            self.sent: list[dict[str, object]] = []

        def send(self, payload: dict[str, object]) -> None:
            self.sent.append(dict(payload))

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_observability_worker(
        {
            "service_process": {
                "enabled": True,
                "group_name": "system.observability",
            }
        }
    )
    handle = bootstrap_module._WorkerHandle(  # noqa: SLF001 - contract probe.
        group_name="system.observability",
        worker_index=1,
        worker_id="system.observability#1",
        process=_Process(),  # type: ignore[arg-type]
        control_parent=_Pipe(),  # type: ignore[arg-type]
    )
    supervisor._workers = {"system.observability": [handle]}  # noqa: SLF001 - contract probe.
    supervisor._group_rr_cursor = {"system.observability": 0}  # noqa: SLF001 - contract probe.

    result = supervisor.execute_boundary(
        run=lambda: None,
        run_id="run",
        scenario_id="scenario",
        inputs=[
            SimpleNamespace(
                payload={"kind": "trace"},
                dispatch_group="system.observability",
                target="system.obs.trace_dispatch",
                trace_id="run:0",
                reply_to=None,
                source_group="execution.cpu",
                route_hop=1,
                span_id=None,
            )
        ],
    )

    assert result.terminal_outputs == []
    sent = getattr(handle.control_parent, "sent", [])
    assert sent
    assert sent[0].get("kind") == "execute_boundary"
    assert sent[0].get("no_reply") is True


def test_p5pre_sup_21_shutdown_order_stops_observability_group_last(monkeypatch) -> None:
    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid
            self._alive = True

        def is_alive(self) -> bool:
            return self._alive

        def join(self, timeout: float | None = None) -> None:
            _ = timeout
            self._alive = False

        def terminate(self) -> None:
            self._alive = False

    def _handle(group_name: str, pid: int) -> SimpleNamespace:
        return SimpleNamespace(
            group_name=group_name,
            worker_id=f"{group_name}#1",
            worker_index=1,
            process=_Process(pid),
            stop_event=None,
            control_parent=object(),
            output_closed=False,
        )

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._group_nodes = {  # noqa: SLF001 - contract probe.
        "execution.ingress": ["source:source", "ingress_line_bridge"],
        "execution.features": ["compute_features"],
        "system.observability": ["system.obs.trace_dispatch", "system.obs.log_dispatch"],
    }
    supervisor._workers = {  # noqa: SLF001 - contract probe.
        "execution.ingress": [_handle("execution.ingress", 101)],
        "execution.features": [_handle("execution.features", 102)],
        "system.observability": [_handle("system.observability", 103)],
    }

    stop_order: list[str] = []

    def _fake_try_send_stop_command(handle: object, *, timeout_seconds: float) -> bool:
        _ = timeout_seconds
        stop_order.append(str(getattr(handle, "group_name", "")))
        setattr(handle, "output_closed", True)
        process = getattr(handle, "process", None)
        if process is not None and callable(getattr(process, "terminate", None)):
            process.terminate()
        return True

    monkeypatch.setattr(supervisor, "_try_send_stop_command", _fake_try_send_stop_command)

    supervisor.stop_groups(graceful_timeout_seconds=2, drain_inflight=True)

    assert stop_order == [
        "execution.ingress",
        "execution.features",
        "system.observability",
    ]
    events = _lifecycle_events(supervisor)
    phases = [
        str(event.get("phase"))
        for event in events
        if event.get("kind") == "shutdown_phase"
    ]
    assert phases == [
        "stop_business_ingress",
        "stop_business_remaining",
        "stop_observability_owner",
    ]


def test_p5pre_sup_22_force_terminate_diagnostics_include_pending_dropped_timed_out() -> None:
    class _LoopStub:
        def metrics(self) -> dict[str, int]:
            return {
                "submitted": 20,
                "processed": 12,
                "failed": 1,
                "dropped": 2,
                "queue_depth": 4,
                "pending": 7,
                "submit_block_count": 3,
                "submit_timeout_count": 5,
                "submit_block_wait_ms_total": 17,
                "running": 1,
            }

    class _SinkStub:
        def diagnostics(self) -> dict[str, int]:
            return {"buffered": 3, "dropped": 6}

        def close(self) -> None:
            return None

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._trace_dispatch_loop = _LoopStub()  # noqa: SLF001 - contract probe.
    supervisor._trace_sinks = [_SinkStub()]  # noqa: SLF001 - contract probe.
    supervisor._trace_dispatch_dropped = 4  # noqa: SLF001 - contract probe.
    supervisor._tracing_enabled = True  # noqa: SLF001 - contract probe.

    supervisor.force_terminate_groups([])

    events = _lifecycle_events(supervisor)
    force_diag = next(
        event
        for event in events
        if event.get("kind") == "trace_dispatch_diagnostics"
        and event.get("stage") == "force_terminate_requested"
    )
    assert force_diag.get("pending") == 10
    assert force_diag.get("dropped") == 8
    assert force_diag.get("timed_out") == 5
    assert force_diag.get("business_pending") == 0
    assert force_diag.get("business_dropped") == 0
    assert force_diag.get("business_timed_out") == 0
    assert force_diag.get("control_pending") == 10
    assert force_diag.get("control_dropped") == 8
    assert force_diag.get("control_timed_out") == 5


def test_p5pre_sup_22a_force_terminate_diagnostics_include_channel_breakdown() -> None:
    class _TraceLoopStub:
        def metrics(self) -> dict[str, int]:
            return {
                "submitted": 20,
                "processed": 12,
                "failed": 1,
                "dropped": 2,
                "queue_depth": 4,
                "pending": 7,
                "submit_block_count": 3,
                "submit_timeout_count": 5,
                "submit_block_wait_ms_total": 17,
                "running": 1,
            }

    class _ServiceLoopStub:
        def metrics(self) -> dict[str, int]:
            return {
                "submitted": 8,
                "processed": 2,
                "failed": 0,
                "dropped": 7,
                "queue_depth": 6,
                "pending": 6,
                "submit_timeout_count": 9,
                "running": 1,
            }

    class _SinkStub:
        def diagnostics(self) -> dict[str, int]:
            return {"buffered": 3, "dropped": 6}

        def close(self) -> None:
            return None

    supervisor = MultiprocessBootstrapSupervisor()
    supervisor._trace_dispatch_loop = _TraceLoopStub()  # noqa: SLF001 - contract probe.
    supervisor._observability_service_worker_loop = _ServiceLoopStub()  # noqa: SLF001 - contract probe.
    supervisor._trace_sinks = [_SinkStub()]  # noqa: SLF001 - contract probe.
    supervisor._trace_dispatch_dropped = 4  # noqa: SLF001 - contract probe.
    supervisor._tracing_enabled = True  # noqa: SLF001 - contract probe.
    supervisor._boundary_inflight_commands = 3  # noqa: SLF001 - contract probe.
    supervisor._business_dispatch_timeout_total = 2  # noqa: SLF001 - contract probe.
    supervisor._business_dispatch_transport_error_total = 1  # noqa: SLF001 - contract probe.
    supervisor._business_dispatch_failure_total = 3  # noqa: SLF001 - contract probe.
    supervisor._business_dispatch_no_wait_failure_total = 1  # noqa: SLF001 - contract probe.
    supervisor._control_dispatch_no_wait_failure_total = 2  # noqa: SLF001 - contract probe.
    supervisor._observability_service_worker_dropped = 4  # noqa: SLF001 - contract probe.

    supervisor.force_terminate_groups([])

    events = _lifecycle_events(supervisor)
    force_diag = next(
        event
        for event in events
        if event.get("kind") == "trace_dispatch_diagnostics"
        and event.get("stage") == "force_terminate_requested"
    )
    assert force_diag.get("business_pending") == 3
    assert force_diag.get("business_dropped") == 1
    assert force_diag.get("business_timed_out") == 2
    assert force_diag.get("business_failed") == 5
    assert force_diag.get("control_pending") == 16
    assert force_diag.get("control_dropped") == 21
    assert force_diag.get("control_timed_out") == 14
    assert force_diag.get("control_failed") == 3


def test_p5pre_sup_23_stop_groups_closes_observability_worker_without_deadlock() -> None:
    class _LoopStub:
        def __init__(self, supervisor: MultiprocessBootstrapSupervisor) -> None:
            self._supervisor = supervisor
            self.stop_calls = 0

        def stop(self, *, drain: bool, timeout_seconds: float) -> None:
            _ = (drain, timeout_seconds)
            self.stop_calls += 1
            acquired = self._supervisor._lock.acquire(timeout=0.1)  # noqa: SLF001 - contract probe.
            if not acquired:
                raise RuntimeError("deadlock while closing observability service worker")
            self._supervisor._lock.release()  # noqa: SLF001 - contract probe.

    supervisor = MultiprocessBootstrapSupervisor()
    loop = _LoopStub(supervisor)
    supervisor._observability_service_worker_loop = loop  # noqa: SLF001 - contract probe.

    supervisor.stop_groups(graceful_timeout_seconds=1, drain_inflight=True)

    assert loop.stop_calls == 1


def test_p5pre_sup_23a_stop_groups_drains_saturated_observability_queue_without_deadlock() -> None:
    class _LoopStub:
        def __init__(self, supervisor: MultiprocessBootstrapSupervisor) -> None:
            self._supervisor = supervisor
            self.drain_calls = 0
            self.stop_calls = 0

        def drain(self, timeout_seconds: float) -> bool:
            _ = timeout_seconds
            self.drain_calls += 1
            acquired = self._supervisor._lock.acquire(timeout=0.1)  # noqa: SLF001 - contract probe.
            if not acquired:
                raise RuntimeError("deadlock while draining observability service worker")
            self._supervisor._lock.release()  # noqa: SLF001 - contract probe.
            return False

        def stop(self, *, drain: bool, timeout_seconds: float) -> None:
            _ = (drain, timeout_seconds)
            self.stop_calls += 1
            acquired = self._supervisor._lock.acquire(timeout=0.1)  # noqa: SLF001 - contract probe.
            if not acquired:
                raise RuntimeError("deadlock while stopping observability service worker")
            self._supervisor._lock.release()  # noqa: SLF001 - contract probe.

        def metrics(self) -> dict[str, int]:
            return {
                "submitted": 100,
                "processed": 1,
                "failed": 0,
                "dropped": 0,
                "queue_depth": 99,
                "pending": 99,
                "running": 1,
            }

    supervisor = MultiprocessBootstrapSupervisor()
    loop = _LoopStub(supervisor)
    supervisor._observability_service_worker_loop = loop  # noqa: SLF001 - contract probe.

    supervisor.stop_groups(graceful_timeout_seconds=1, drain_inflight=True)

    assert loop.drain_calls == 1
    assert loop.stop_calls == 1
