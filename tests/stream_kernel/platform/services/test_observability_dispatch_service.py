from __future__ import annotations

import asyncio

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


class _FailingSink:
    def __init__(self, *, error: str = "boom") -> None:
        self.error = error

    def emit(self, _payload: object) -> None:
        raise RuntimeError(self.error)


class _AsyncSink:
    def __init__(self) -> None:
        self.payloads: list[object] = []
        self.loop_ids: list[int] = []
        self.emit_calls = 0

    async def emit_async(self, payload: object) -> None:
        self.payloads.append(payload)
        self.loop_ids.append(id(asyncio.get_running_loop()))

    def emit(self, _payload: object) -> None:
        self.emit_calls += 1


class _ReplyCoordinator:
    def __init__(self) -> None:
        self.registered: list[tuple[str | None, str | None]] = []
        self.completed: list[tuple[str | None, object | None]] = []

    def register_if_requested(self, *, trace_id: str | None, reply_to: str | None) -> None:
        self.registered.append((trace_id, reply_to))

    def complete_if_waiting(self, *, trace_id: str | None, terminal_event: object | None) -> None:
        self.completed.append((trace_id, terminal_event))


class _SlowSink:
    def __init__(self, *, sleep_seconds: float = 0.02) -> None:
        self.sleep_seconds = sleep_seconds
        self.payloads: list[object] = []

    def emit(self, payload: object) -> None:
        import time

        time.sleep(self.sleep_seconds)
        self.payloads.append(payload)


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


def test_dispatching_observability_service_does_not_emit_runner_log_for_log_payload() -> None:
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
        node_name="system.lifecycle.log_dispatch",
        payload=LogMessage(level="info", message="relay"),
        ctx={"__run_id": "run", "__scenario_id": "s1"},
        trace_id="t-log",
    )
    produced = service.after_node(
        node_name="system.lifecycle.log_dispatch",
        payload=LogMessage(level="info", message="relay"),
        ctx={},
        trace_id="t-log",
        outputs=[LogDispatchEvent(payload=LogMessage(level="info", message="out"), trace_id="t-log")],
        state=state,
    )
    assert isinstance(produced, list)
    assert not any(isinstance(item, LogDispatchEvent) for item in produced)
    assert any(isinstance(item, TraceDispatchEvent) for item in produced)
    assert any(isinstance(item, MetricDispatchEvent) for item in produced)
    assert any(isinstance(item, MonitorDispatchEvent) for item in produced)


def test_dispatching_observability_service_does_not_emit_runner_error_log_for_log_payload() -> None:
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
        node_name="system.lifecycle.log_dispatch",
        payload=LogMessage(level="info", message="relay"),
        ctx={"__run_id": "run", "__scenario_id": "s1"},
        trace_id="t-log",
    )
    produced = service.on_node_error(
        node_name="system.lifecycle.log_dispatch",
        payload=LogMessage(level="info", message="relay"),
        ctx={},
        trace_id="t-log",
        error=RuntimeError("boom"),
        state=state,
    )
    assert isinstance(produced, list)
    assert not any(isinstance(item, LogDispatchEvent) for item in produced)
    assert any(isinstance(item, TraceDispatchEvent) for item in produced)
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


def test_dispatching_observability_service_reports_trace_sink_failures_via_log_dispatch_events() -> None:
    service = DispatchingObservabilityService(
        runtime={
            "platform": {"bootstrap": {"mode": "process_supervisor"}},
            "observability": {
                "service_worker": {"enabled": True},
                "tracing": {"exporters": [{"kind": "jsonl", "enabled": True}]},
                "logging": {"exporters": [{"kind": "jsonl", "enabled": True}]},
            },
        },
        trace_sinks=[_FailingSink(error="otlp_post_failed")],
    )

    produced = service.emit_trace_event(
        event={"span": "s1"},
        trace_id="t-obs",
        attributes={"source_node": "system.obs.trace_dispatch"},
    )
    assert len(produced) == 1
    dispatch = produced[0]
    assert isinstance(dispatch, LogDispatchEvent)
    assert isinstance(dispatch.payload, LogMessage)
    assert dispatch.payload.fields.get("event") == "observability.sink.emit_failed"
    assert dispatch.payload.fields.get("channel") == "trace"
    assert dispatch.payload.fields.get("sink_failures_count") == 1


def test_dispatching_observability_service_reports_trace_sink_failures_to_log_sink_in_obs_worker() -> None:
    log_sink = _Sink()
    service = DispatchingObservabilityService(
        runtime={
            "__process_role": "observability_worker",
            "platform": {"bootstrap": {"mode": "process_supervisor"}},
            "observability": {
                "service_worker": {"enabled": True},
                "tracing": {"exporters": [{"kind": "jsonl", "enabled": True}]},
                "logging": {"exporters": [{"kind": "jsonl", "enabled": True}]},
            },
        },
        trace_sinks=[_FailingSink(error="otlp_post_failed")],
        log_sinks=[log_sink],
    )

    produced = service.emit_trace_event(
        event={"span": "s1"},
        trace_id="t-obs",
        attributes={"source_node": "system.obs.trace_dispatch"},
    )
    assert produced == []
    assert log_sink.payloads
    first = log_sink.payloads[0]
    assert isinstance(first, LogMessage)
    assert first.fields.get("event") == "observability.sink.emit_failed"
    assert first.fields.get("channel") == "trace"


def test_dispatching_observability_service_does_not_recurse_on_log_sink_failure() -> None:
    service = DispatchingObservabilityService(
        runtime={
            "__process_role": "observability_worker",
            "platform": {"bootstrap": {"mode": "process_supervisor"}},
            "observability": {
                "service_worker": {"enabled": True},
                "logging": {"exporters": [{"kind": "jsonl", "enabled": True}]},
            },
        },
        log_sinks=[_FailingSink(error="log_sink_failed")],
    )

    produced = service.emit_log_event(
        event=LogMessage(level="error", message="source"),
        trace_id="t-log",
        attributes={"source_node": "system.obs.log_dispatch"},
    )
    assert produced == []


def test_dispatching_observability_service_emit_trace_event_async_uses_async_sink() -> None:
    sink = _AsyncSink()
    service = DispatchingObservabilityService(
        runtime={
            "__process_role": "observability_worker",
            "platform": {"bootstrap": {"mode": "process_supervisor"}},
            "observability": {
                "service_worker": {"enabled": True},
                "tracing": {"exporters": [{"kind": "jsonl", "enabled": True}]},
                "logging": {"exporters": [{"kind": "jsonl", "enabled": True}]},
            },
        },
        trace_sinks=[sink],
    )

    async def _run() -> None:
        produced = await service.emit_trace_event_async(
            event={"span": "s1"},
            trace_id="t-obs",
            attributes={"source_node": "system.obs.trace_dispatch"},
        )
        assert produced == []
        assert sink.payloads
        assert sink.loop_ids == [id(asyncio.get_running_loop())]
        assert sink.emit_calls == 0

    asyncio.run(_run())


def test_dispatching_observability_service_emit_trace_event_async_runs_sinks_concurrently() -> None:
    async def _run() -> None:
        second_started = asyncio.Event()

        class _WaitForSecondSink:
            async def emit_async(self, _payload: object) -> None:
                await second_started.wait()

        class _TriggerSink:
            async def emit_async(self, _payload: object) -> None:
                second_started.set()

        service = DispatchingObservabilityService(
            runtime={
                "__process_role": "observability_worker",
                "platform": {"bootstrap": {"mode": "process_supervisor"}},
                "observability": {
                    "service_worker": {"enabled": True},
                    "tracing": {"exporters": [{"kind": "jsonl", "enabled": True}]},
                    "logging": {"exporters": [{"kind": "jsonl", "enabled": True}]},
                },
            },
            trace_sinks=[_WaitForSecondSink(), _TriggerSink()],
        )
        produced = await asyncio.wait_for(
            service.emit_trace_event_async(
                event={"span": "s1"},
                trace_id="t-obs",
                attributes={"source_node": "system.obs.trace_dispatch"},
            ),
            timeout=0.1,
        )
        assert produced == []

    asyncio.run(_run())


def test_dispatching_observability_service_submit_trace_event_uses_background_dispatch_queue() -> None:
    import time

    sink = _SlowSink(sleep_seconds=0.03)
    service = DispatchingObservabilityService(
        runtime={
            "__process_role": "observability_worker",
            "platform": {"bootstrap": {"mode": "process_supervisor"}},
            "observability": {
                "service_worker": {"enabled": True},
                "tracing": {
                    "exporters": [{"kind": "jsonl", "enabled": True}],
                    "dispatch_queue": {
                        "max_items": 1024,
                        "drop_policy": "non_block",
                        "forward_batch_max_items": 64,
                        "forward_flush_interval_ms": 10,
                        "drain_timeout_seconds": 3.0,
                    },
                },
            },
        },
        trace_sinks=[sink],
    )

    started = time.monotonic()
    for index in range(4):
        produced = service.submit_trace_event(event={"span": index}, trace_id=f"t-{index}")
        assert produced == []
    enqueue_elapsed = time.monotonic() - started
    # submit_* path must not wait for slow sink emit().
    assert enqueue_elapsed < 0.03

    service.on_run_end()
    assert len(sink.payloads) == 4
