from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.transport.handoff.system_nodes import (
    OBSERVABILITY_LOG_HANDOFF_NODE_NAME,
    OBSERVABILITY_TRACE_HANDOFF_NODE_NAME,
    IpcHandoffDispatchNode,
    IpcObservabilityHandoffDispatchNode,
    build_transport_observability_handoff_plan,
)
from stream_kernel.observability.events import (
    LogDispatchEvent,
    MetricDispatchEvent,
    MonitorDispatchEvent,
    TraceDispatchEvent,
)
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class _DispatchService:
    calls: list[dict[str, object]] = field(default_factory=list)

    def dispatch_envelope(self, envelope: Envelope, *, source_group: str | None = None) -> bool:
        self.calls.append({"envelope": envelope, "source_group": source_group})
        return True


def test_ipc_handoff_dispatch_node_delegates_to_service_and_emits_nothing() -> None:
    service = _DispatchService()
    node = IpcHandoffDispatchNode(dispatch_service=service)
    envelope = Envelope(payload={"x": 1}, target="remote.node")

    outputs = node(envelope, None)

    assert outputs == []
    assert len(service.calls) == 1
    assert service.calls[0]["envelope"] is envelope


def test_ipc_observability_handoff_dispatch_node_maps_log_event_to_observability_target() -> None:
    service = _DispatchService()
    node = IpcObservabilityHandoffDispatchNode(dispatch_service=service)
    event = LogDispatchEvent(payload={"line": "x"}, trace_id="t-1")

    outputs = node(event, {"__process_group": "execution.features"})

    assert outputs == []
    assert len(service.calls) == 1
    envelope = service.calls[0]["envelope"]
    assert isinstance(envelope, Envelope)
    assert envelope.target == "system.obs.log_dispatch"
    assert envelope.payload is event
    assert service.calls[0]["source_group"] == "execution.features"


def test_ipc_observability_handoff_dispatch_node_ignores_unknown_payload_types() -> None:
    service = _DispatchService()
    node = IpcObservabilityHandoffDispatchNode(dispatch_service=service)

    outputs = node({"freeform": True}, None)

    assert outputs == []
    assert service.calls == []


def test_ipc_observability_handoff_dispatch_node_maps_all_supported_dispatch_events() -> None:
    service = _DispatchService()
    node = IpcObservabilityHandoffDispatchNode(dispatch_service=service)
    payloads = [
        TraceDispatchEvent(payload={"k": 1}),
        LogDispatchEvent(payload={"k": 2}),
        MetricDispatchEvent(payload={"k": 3}),
        MonitorDispatchEvent(payload={"k": 4}),
    ]

    for payload in payloads:
        node(payload, None)

    targets = [call["envelope"].target for call in service.calls]
    assert targets == [
        "system.obs.trace_dispatch",
        "system.obs.log_dispatch",
        "system.obs.metric_dispatch",
        "system.obs.monitor_dispatch",
    ]


def test_build_transport_observability_handoff_plan_creates_channel_specific_nodes() -> None:
    steps, consumers, node_names = build_transport_observability_handoff_plan(
        enabled_tokens=[TraceDispatchEvent, LogDispatchEvent]
    )

    assert [step.name for step in steps] == [
        OBSERVABILITY_TRACE_HANDOFF_NODE_NAME,
        OBSERVABILITY_LOG_HANDOFF_NODE_NAME,
    ]
    assert consumers == {
        TraceDispatchEvent: [OBSERVABILITY_TRACE_HANDOFF_NODE_NAME],
        LogDispatchEvent: [OBSERVABILITY_LOG_HANDOFF_NODE_NAME],
    }
    assert node_names == {
        OBSERVABILITY_TRACE_HANDOFF_NODE_NAME,
        OBSERVABILITY_LOG_HANDOFF_NODE_NAME,
    }
