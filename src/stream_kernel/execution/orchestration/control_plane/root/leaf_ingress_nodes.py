from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.application_context.inject import inject
from stream_kernel.execution.transport.handoff.system_nodes import (
    OBSERVABILITY_HANDOFF_NODE_NAME,
)
from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
    ControlPlaneRootLeafIngressService,
)
from stream_kernel.execution.orchestration.control_plane.root.channel_services import (
    ControlPlaneRootRunnerControlService,
)
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.kernel.node_annotation import node
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
)
from stream_kernel.platform.services.runtime.platform_scheduler import (
    PlatformSchedulerUpsertCommand,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafStopAckEvent,
)
from stream_kernel.observability.events import (
    DebugDispatchEvent,
    LogDispatchEvent,
    MetricDispatchEvent,
    MonitorDispatchEvent,
    MonitoringMetricsSnapshotEvent,
    TraceDispatchEvent,
    WorkerQueueTelemetryEvent,
)
from stream_kernel.routing.envelope import Envelope

ROOT_LEAF_INGRESS_SOURCE_NODE_NAME = "source:system.cp.root_leaf_ingress"
ROOT_LEAF_INGRESS_SOURCE_NODE_PREFIX = f"{ROOT_LEAF_INGRESS_SOURCE_NODE_NAME}:"
ROOT_BOUNDARY_HANDOFF_SINK_NODE_NAME = "system.cp.root_boundary_handoff_sink"
ROOT_LEAF_CONTROL_DISPATCH_SINK_NODE_NAME = "system.cp.root_leaf_control_dispatch_sink"


@dataclass(frozen=True, slots=True)
class ControlPlaneRootLeafIngressEnvelopeEvent:
    worker_id: str
    lane: str
    envelope: Envelope


def root_leaf_ingress_source_node_name(*, worker_id: str, lane: str) -> str:
    safe_worker = worker_id if isinstance(worker_id, str) and worker_id else "unknown#0"
    safe_lane = lane if isinstance(lane, str) and lane else EXECUTION_IPC_LANE_CONTROL
    return f"{ROOT_LEAF_INGRESS_SOURCE_NODE_PREFIX}{safe_worker}:{safe_lane}"


def is_root_leaf_ingress_source_node_name(node_name: object) -> bool:
    if not isinstance(node_name, str) or not node_name:
        return False
    return node_name == ROOT_LEAF_INGRESS_SOURCE_NODE_NAME or node_name.startswith(
        ROOT_LEAF_INGRESS_SOURCE_NODE_PREFIX
    )


@node(
    name=ROOT_LEAF_INGRESS_SOURCE_NODE_NAME,
    consumes=[BootstrapControl],
    emits=[
        ControlPlaneLeafHelloEvent,
        ControlPlaneLeafDiscoveryAckEvent,
        ControlPlaneLeafConfigAckEvent,
        ControlPlaneLeafStopAckEvent,
        Envelope,
        ControlPlaneLeafDrainReadyEvent,
        TraceDispatchEvent,
        LogDispatchEvent,
        DebugDispatchEvent,
        MetricDispatchEvent,
        MonitorDispatchEvent,
        MonitoringMetricsSnapshotEvent,
        WorkerQueueTelemetryEvent,
    ],
)
@dataclass
class ControlPlaneRootLeafIngressSourceNode:
    ingress: ControlPlaneRootLeafIngressService = inject.service(ControlPlaneRootLeafIngressService)
    runner_control: ControlPlaneRootRunnerControlService = inject.service(
        ControlPlaneRootRunnerControlService
    )
    worker_id: str | None = None
    lane: str = EXECUTION_IPC_LANE_CONTROL
    source_name: str = ROOT_LEAF_INGRESS_SOURCE_NODE_NAME
    poll_interval_seconds: float = 0.01
    _scheduler_registered: bool = field(default=False, init=False, repr=False)

    def initialize(self) -> list[object]:
        if self._scheduler_registered:
            return []
        interval = max(0.001, float(self.poll_interval_seconds))
        source_name = (
            self.source_name
            if isinstance(self.source_name, str) and self.source_name
            else ROOT_LEAF_INGRESS_SOURCE_NODE_NAME
        )
        self._scheduler_registered = True
        return [
            PlatformSchedulerUpsertCommand(
                job_id=f"cp.root.leaf_ingress:{source_name}",
                target=source_name,
                interval_seconds=interval,
                run_immediately=True,
                payload=BootstrapControl(target=source_name),
            )
        ]

    async def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, BootstrapControl):
            return []
        if payload.target != self.source_name:
            return []
        stop_requested = getattr(self.runner_control, "stop_requested", None)
        if callable(stop_requested) and stop_requested():
            return []
        if not isinstance(self.worker_id, str) or not self.worker_id:
            return []
        poll_timeout_seconds = 0.0
        candidate = self.ingress
        poll_next_for_lane_async = getattr(candidate, "poll_next_leaf_ingress_for_worker_lane_async", None)
        poll_next_for_lane = getattr(candidate, "poll_next_leaf_ingress_for_worker_lane", None)
        if callable(poll_next_for_lane_async):
            polled_payload = await poll_next_for_lane_async(
                worker_id=self.worker_id,
                lane=self.lane,
                timeout_seconds=poll_timeout_seconds,
            )
        elif callable(poll_next_for_lane):
            polled_payload = poll_next_for_lane(
                worker_id=self.worker_id,
                lane=self.lane,
                timeout_seconds=poll_timeout_seconds,
            )
        else:
            return []
        if polled_payload is None:
            return []
        if isinstance(polled_payload, Envelope):
            return [
                ControlPlaneRootLeafIngressEnvelopeEvent(
                    worker_id=self.worker_id,
                    lane=self.lane,
                    envelope=polled_payload,
                )
            ]
        if not isinstance(polled_payload, _ROOT_LEAF_INGRESS_ALLOWED_PAYLOAD_TYPES):
            return []
        return [polled_payload]


@node(
    name=ROOT_LEAF_CONTROL_DISPATCH_SINK_NODE_NAME,
    consumes=[
        ControlPlaneLeafHelloEvent,
        ControlPlaneLeafDiscoveryAckEvent,
    ],
    emits=[],
)
@dataclass
class ControlPlaneRootLeafControlDispatchSinkNode:
    ingress: ControlPlaneRootLeafIngressService = inject.service(ControlPlaneRootLeafIngressService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(
            payload,
            (
                ControlPlaneLeafHelloEvent,
                ControlPlaneLeafDiscoveryAckEvent,
            ),
        ):
            return []
        dispatch = getattr(self.ingress, "dispatch_polled_leaf_ingress", None)
        if not callable(dispatch):
            return []
        worker_id = payload.worker_id if isinstance(getattr(payload, "worker_id", None), str) else None
        if not isinstance(worker_id, str) or not worker_id:
            return []
        try:
            dispatch(worker_id=worker_id, payload=payload)
        except TypeError:
            dispatch(worker_id=worker_id, payload=payload, lane=None)
        return []


@node(
    name=ROOT_BOUNDARY_HANDOFF_SINK_NODE_NAME,
    consumes=[ControlPlaneRootLeafIngressEnvelopeEvent],
    emits=[],
)
@dataclass
class ControlPlaneRootBoundaryHandoffSinkNode:
    handoff: object | None = None

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneRootLeafIngressEnvelopeEvent):
            return []
        drain_external = getattr(self.handoff, "drain_external_deliveries", None)
        if not callable(drain_external):
            return []
        envelope = payload.envelope
        if not isinstance(envelope.target, str) or not envelope.target:
            return []
        envelopes = [envelope]
        envelopes, _, _ = _normalize_observability_relay_envelopes(envelopes)
        if not envelopes:
            return []
        source_group = (
            payload.worker_id.rsplit("#", 1)[0]
            if isinstance(payload.worker_id, str) and "#" in payload.worker_id
            else None
        )
        try:
            drain_external(
                envelopes=envelopes,
                source_group=source_group,
                pump_replies=False,
            )
        except TypeError:
            drain_external(
                envelopes=envelopes,
                source_group=source_group,
            )
        return []

_OBSERVABILITY_EVENT_TARGETS: dict[type[object], str] = {
    TraceDispatchEvent: "system.obs.trace_dispatch",
    LogDispatchEvent: "system.obs.log_dispatch",
    DebugDispatchEvent: "system.obs.debug_dispatch",
    MetricDispatchEvent: "system.obs.metric_dispatch",
    MonitorDispatchEvent: "system.obs.monitor_dispatch",
    MonitoringMetricsSnapshotEvent: "system.obs.monitoring_metrics_dispatch",
    WorkerQueueTelemetryEvent: "system.obs.worker_queue_dispatch",
}

_ROOT_LEAF_INGRESS_ALLOWED_PAYLOAD_TYPES: tuple[type[object], ...] = (
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafDrainReadyEvent,
    TraceDispatchEvent,
    LogDispatchEvent,
    DebugDispatchEvent,
    MetricDispatchEvent,
    MonitorDispatchEvent,
    MonitoringMetricsSnapshotEvent,
    WorkerQueueTelemetryEvent,
)


def _normalize_observability_relay_envelopes(
    envelopes: list[Envelope],
) -> tuple[list[Envelope], int, int]:
    normalized: list[Envelope] = []
    remapped = 0
    dropped = 0
    for envelope in envelopes:
        if envelope.target != OBSERVABILITY_HANDOFF_NODE_NAME:
            normalized.append(envelope)
            continue
        target = _resolve_observability_target(envelope.payload)
        if not isinstance(target, str) or not target:
            dropped += 1
            continue
        normalized.append(
            Envelope(
                payload=envelope.payload,
                target=target,
                trace_id=envelope.trace_id,
                span_id=envelope.span_id,
                reply_to=envelope.reply_to,
                tombstone=envelope.tombstone,
            )
        )
        remapped += 1
    return (normalized, remapped, dropped)


def _resolve_observability_target(payload: object) -> str | None:
    for token, target in _OBSERVABILITY_EVENT_TARGETS.items():
        if isinstance(payload, token):
            return target
    return None


__all__ = [
    "ROOT_LEAF_INGRESS_SOURCE_NODE_NAME",
    "ROOT_LEAF_INGRESS_SOURCE_NODE_PREFIX",
    "ROOT_LEAF_CONTROL_DISPATCH_SINK_NODE_NAME",
    "ROOT_BOUNDARY_HANDOFF_SINK_NODE_NAME",
    "ControlPlaneRootLeafIngressSourceNode",
    "ControlPlaneRootLeafControlDispatchSinkNode",
    "ControlPlaneRootBoundaryHandoffSinkNode",
    "ControlPlaneRootLeafIngressEnvelopeEvent",
    "root_leaf_ingress_source_node_name",
    "is_root_leaf_ingress_source_node_name",
]
