from __future__ import annotations

from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.observability.domain.monitoring import MonitoringMessage
from stream_kernel.observability.events import (
    LogDispatchEvent,
    MetricDispatchEvent,
    MonitorDispatchEvent,
    TraceDispatchEvent,
)
from stream_kernel.platform.services.observability_dispatch import DispatchingObservabilityService


class _Sink:
    def __init__(self) -> None:
        self.payloads: list[object] = []

    def emit(self, payload: object) -> None:
        self.payloads.append(payload)


class _ReplyCoordinator:
    def __init__(self) -> None:
        self.registered: list[tuple[str | None, str | None]] = []
        self.completed: list[tuple[str | None, object | None]] = []

    def register_if_requested(self, *, trace_id: str | None, reply_to: str | None) -> None:
        self.registered.append((trace_id, reply_to))

    def complete_if_waiting(self, *, trace_id: str | None, terminal_event: object | None) -> None:
        self.completed.append((trace_id, terminal_event))


def test_dispatching_observability_service_emits_dispatch_events_in_supervisor_mode() -> None:
    service = DispatchingObservabilityService(
        runtime={
            "platform": {"bootstrap": {"mode": "process_supervisor"}},
            "observability": {
                "service_worker": {"enabled": True},
                "tracing": {"exporters": [{"kind": "jsonl", "enabled": True}]},
                "logging": {"exporters": [{"kind": "jsonl", "enabled": True}], "runner_node_events": True},
                "monitoring": {"exporters": [{"kind": "jsonl", "enabled": True}]},
                "telemetry": {"exporters": [{"kind": "stdout", "enabled": True}]},
            },
        }
    )
    state = service.before_node(
        node_name="n1",
        payload={"v": 1},
        ctx={"__run_id": "run", "__scenario_id": "s1"},
        trace_id="t1",
    )
    produced = service.after_node(
        node_name="n1",
        payload={"v": 1},
        ctx={},
        trace_id="t1",
        outputs=[{"ok": True}],
        state=state,
    )
    assert isinstance(produced, list)
    assert any(isinstance(item, TraceDispatchEvent) for item in produced)
    assert any(isinstance(item, LogDispatchEvent) for item in produced)
    assert any(isinstance(item, MetricDispatchEvent) for item in produced)
    assert any(isinstance(item, MonitorDispatchEvent) for item in produced)


def test_dispatching_observability_service_emits_directly_to_sinks_in_observability_worker() -> None:
    trace_sink = _Sink()
    log_sink = _Sink()
    telemetry_sink = _Sink()
    monitoring_sink = _Sink()
    service = DispatchingObservabilityService(
        runtime={
            "__process_role": "observability_worker",
            "platform": {"bootstrap": {"mode": "process_supervisor"}},
            "observability": {
                "service_worker": {"enabled": True},
                "tracing": {"exporters": [{"kind": "jsonl", "enabled": True}]},
                "logging": {"exporters": [{"kind": "jsonl", "enabled": True}], "runner_node_events": True},
                "monitoring": {"exporters": [{"kind": "jsonl", "enabled": True}]},
                "telemetry": {"exporters": [{"kind": "stdout", "enabled": True}]},
            },
        },
        trace_sinks=[trace_sink],
        log_sinks=[log_sink],
        telemetry_sinks=[telemetry_sink],
        monitoring_sinks=[monitoring_sink],
    )

    # No dispatch wrapping in observability worker mode.
    state = service.before_node(
        node_name="n2",
        payload={"v": 2},
        ctx={"__run_id": "run", "__scenario_id": "s1"},
        trace_id="t2",
    )
    produced = service.after_node(
        node_name="n2",
        payload={"v": 2},
        ctx={},
        trace_id="t2",
        outputs=[],
        state=state,
    )
    assert produced is None
    assert trace_sink.payloads
    assert log_sink.payloads
    assert monitoring_sink.payloads


def test_dispatching_observability_service_ingress_uses_reply_coordinator_and_emits_monitor_event() -> None:
    reply = _ReplyCoordinator()
    service = DispatchingObservabilityService(
        runtime={
            "platform": {"bootstrap": {"mode": "process_supervisor"}},
            "observability": {
                "service_worker": {"enabled": True},
                "monitoring": {"exporters": [{"kind": "jsonl", "enabled": True}]},
            },
        },
        reply_coordinator=reply,
    )
    produced = service.on_ingress(trace_id="t3", reply_to="http:req-1")
    assert reply.registered == [("t3", "http:req-1")]
    assert isinstance(produced, list)
    assert len(produced) == 1
    assert isinstance(produced[0], MonitorDispatchEvent)

    service.publish_log(event=LogMessage(level="info", message="ok"))
    service.publish_monitoring(event=MonitoringMessage(name="health", status="ok"))
