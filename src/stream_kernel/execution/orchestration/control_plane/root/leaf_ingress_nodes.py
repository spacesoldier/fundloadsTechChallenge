from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from threading import Lock

from stream_kernel.application_context.inject import inject
from stream_kernel.execution.transport.handoff.ipc_handoff_dispatch_service import (
    ExecutionIpcHandoffDispatchService,
)
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
from stream_kernel.integration.work_queue import QueuePort
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafReplyDispatchDiagEvent,
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
from stream_kernel.observability.domain.logging import LogMessage
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
        BootstrapControl,
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
        LogMessage,
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
    ingress_specs: tuple[tuple[str, str], ...] = ()
    source_name: str = ROOT_LEAF_INGRESS_SOURCE_NODE_NAME
    poll_interval_seconds: float = 0.01
    max_messages_per_poll: int = 64
    work_queue: object = inject.queue(Envelope, qualifier="execution.asyncio")
    _scheduler_registered: bool = field(default=False, init=False, repr=False)
    _wakeup_registered: bool = field(default=False, init=False, repr=False)
    _wakeup_pending: bool = field(default=False, init=False, repr=False)
    _wakeup_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _poll_spec_index: int = field(default=0, init=False, repr=False)

    def initialize(self) -> list[object]:
        self._register_data_wakeup()
        if self._scheduler_registered:
            return []
        source_name = (
            self.source_name
            if isinstance(self.source_name, str) and self.source_name
            else ROOT_LEAF_INGRESS_SOURCE_NODE_NAME
        )
        self._scheduler_registered = True
        # Push-model bootstrap: one initial pulse is enough, next pulses are produced
        # by transport data-available callbacks (and explicit self-rearm when drained
        # exactly to budget boundary).
        return [BootstrapControl(target=source_name)]

    async def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, BootstrapControl):
            return []
        if payload.target != self.source_name:
            return []
        self._register_data_wakeup()
        self._clear_wakeup_pending()
        stop_requested = getattr(self.runner_control, "stop_requested", None)
        if callable(stop_requested) and stop_requested():
            return []
        poll_timeout_seconds = 0.0
        candidate = self.ingress
        poll_next_for_lane_async = getattr(candidate, "poll_next_leaf_ingress_for_worker_lane_async", None)
        poll_next_for_lane = getattr(candidate, "poll_next_leaf_ingress_for_worker_lane", None)
        specs = self._normalized_specs()
        if not specs:
            if not self._wakeup_registered:
                return [BootstrapControl(target=self.source_name)]
            return []
        budget = max(1, int(self.max_messages_per_poll))
        produced: list[object] = []
        had_polled_payload = False
        for _ in range(budget):
            polled = await self._poll_one(
                specs=specs,
                poll_next_for_lane_async=poll_next_for_lane_async,
                poll_next_for_lane=poll_next_for_lane,
                poll_timeout_seconds=poll_timeout_seconds,
            )
            if polled is None:
                break
            had_polled_payload = True
            worker_id, lane, polled_payload = polled
            normalized = self._normalize_polled_payload(
                worker_id=worker_id,
                lane=lane,
                polled_payload=polled_payload,
            )
            if normalized is None:
                continue
            produced.append(normalized)
        if len(produced) >= budget:
            produced.append(BootstrapControl(target=self.source_name))
        elif not produced and not had_polled_payload and not self._wakeup_registered:
            # If callbacks are not registered yet (for example, worker specs become
            # available only after launch plan), keep a lightweight bootstrap pulse
            # alive so source can pick up fresh specs and register wakeups.
            produced.append(BootstrapControl(target=self.source_name))
        return produced

    def _normalized_specs(self) -> tuple[tuple[str, str], ...]:
        resolved: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def _append(worker_id: str, lane: str) -> None:
            if not isinstance(worker_id, str) or not worker_id:
                return
            lane_name = lane.strip().lower() if isinstance(lane, str) and lane else EXECUTION_IPC_LANE_CONTROL
            key = (worker_id, lane_name)
            if key in seen:
                return
            seen.add(key)
            resolved.append(key)

        for worker_id, lane in self.ingress_specs:
            _append(worker_id, lane)

        if not resolved and isinstance(self.worker_id, str) and self.worker_id:
            _append(self.worker_id, self.lane)

        dynamic_specs = getattr(self.ingress, "worker_lane_specs_for_polling", None)
        if callable(dynamic_specs):
            try:
                for worker_id, lane in dynamic_specs():
                    _append(worker_id, lane)
            except Exception:
                pass

        return tuple(resolved)

    def _register_data_wakeup(self) -> None:
        if self._wakeup_registered:
            return
        loop: object | None = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        register = getattr(self.ingress, "register_data_available_callback", None)
        if not callable(register):
            return
        specs = self._normalized_specs()
        if not specs:
            return
        registered_any = False
        for worker_id, lane in specs:
            try:
                result = register(
                    worker_id=worker_id,
                    lane=lane,
                    callback=self._on_data_available,
                    loop=loop,
                )
                if result is False:
                    continue
                registered_any = True
            except Exception:
                continue
        self._wakeup_registered = registered_any

    def _on_data_available(self) -> None:
        queue = self._queue_port()
        if queue is None:
            return
        source_name = (
            self.source_name
            if isinstance(self.source_name, str) and self.source_name
            else ROOT_LEAF_INGRESS_SOURCE_NODE_NAME
        )
        with self._wakeup_lock:
            if self._wakeup_pending:
                return
            self._wakeup_pending = True
        envelope = Envelope(
            payload=BootstrapControl(target=source_name),
            target=source_name,
        )
        try:
            queue.push(envelope)
        except Exception:
            self._clear_wakeup_pending()

    def _clear_wakeup_pending(self) -> None:
        with self._wakeup_lock:
            self._wakeup_pending = False

    def _queue_port(self) -> QueuePort | None:
        candidate = self.work_queue
        if isinstance(candidate, QueuePort):
            return candidate
        if callable(getattr(candidate, "push", None)):
            return candidate  # type: ignore[return-value]
        return None

    async def _poll_one(
        self,
        *,
        specs: tuple[tuple[str, str], ...],
        poll_next_for_lane_async: object,
        poll_next_for_lane: object,
        poll_timeout_seconds: float,
    ) -> tuple[str, str, object] | None:
        if not specs:
            return None
        size = len(specs)
        start = self._poll_spec_index % size
        for offset in range(size):
            index = (start + offset) % size
            worker_id, lane = specs[index]
            if callable(poll_next_for_lane_async):
                polled_payload = await poll_next_for_lane_async(
                    worker_id=worker_id,
                    lane=lane,
                    timeout_seconds=poll_timeout_seconds,
                )
            elif callable(poll_next_for_lane):
                polled_payload = poll_next_for_lane(
                    worker_id=worker_id,
                    lane=lane,
                    timeout_seconds=poll_timeout_seconds,
                )
            else:
                return None
            if polled_payload is None:
                continue
            self._poll_spec_index = (index + 1) % size
            return (worker_id, lane, polled_payload)
        self._poll_spec_index = (start + 1) % size
        return None

    @staticmethod
    def _normalize_polled_payload(
        *,
        worker_id: str,
        lane: str,
        polled_payload: object,
    ) -> object | None:
        if isinstance(polled_payload, Envelope):
            return ControlPlaneRootLeafIngressEnvelopeEvent(
                worker_id=worker_id,
                lane=lane,
                envelope=polled_payload,
            )
        if not isinstance(polled_payload, _ROOT_LEAF_INGRESS_ALLOWED_PAYLOAD_TYPES):
            return None
        return polled_payload


@node(
    name=ROOT_LEAF_CONTROL_DISPATCH_SINK_NODE_NAME,
    consumes=[
        ControlPlaneLeafHelloEvent,
        ControlPlaneLeafDiscoveryAckEvent,
        ControlPlaneLeafReplyDispatchDiagEvent,
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
                ControlPlaneLeafReplyDispatchDiagEvent,
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
    handoff: ExecutionIpcHandoffDispatchService = inject.service(ExecutionIpcHandoffDispatchService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneRootLeafIngressEnvelopeEvent):
            return []
        dispatch_envelope = getattr(self.handoff, "dispatch_envelope", None)
        if not callable(dispatch_envelope):
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
        for item in envelopes:
            dispatch_envelope(item, source_group=source_group)
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
    ControlPlaneLeafReplyDispatchDiagEvent,
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
    LogMessage,
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
