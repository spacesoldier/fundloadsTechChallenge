from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.lifecycle.root.startup.console_log_dispatch_service import (
    RootConsoleLogDispatchService,
)
from stream_kernel.execution.orchestration.control_plane.root.discovery_snapshot_service import (
    ControlPlaneRootDiscoverySnapshotService,
)
from stream_kernel.execution.orchestration.control_plane.root.system_nodes import (
    ControlPlaneRootLeafDrainReadyNode,
    ControlPlaneRootLeafBoundaryResultNode,
    ControlPlaneRootLeafConfigAckNode,
    ControlPlaneRootLeafConfigAssignNode,
    ControlPlaneRootLeafStopAckNode,
    ControlPlaneRootTombstoneObservedNode,
    find_group_spec_from_state,
    next_config_revision,
    worker_slot_from_worker_id,
)
from stream_kernel.execution.transport.handoff.system_nodes import (
    OBSERVABILITY_HANDOFF_NODE_NAME,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    EXECUTION_IPC_LANE_LOG,
    EXECUTION_IPC_LANE_METRIC,
    EXECUTION_IPC_LANE_TRACE,
    ExecutionIpcMessage,
    ExecutionIpcTransportService,
    compose_execution_ipc_worker_target_id,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafShutdownPrepareCommand,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafDiscoverySnapshotEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafStopAckEvent,
)
from stream_kernel.platform.services.runtime.control_plane_shutdown_readiness import (
    ControlPlaneShutdownReadinessService,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneStateService,
)
from stream_kernel.observability.domain.logging import LogMessage
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


@runtime_checkable
class ControlPlaneRootReplyIngressService(Protocol):
    def drain_worker_replies(
        self,
        *,
        worker_id: str,
        timeout_seconds: float = 0.0,
        max_items: int = 64,
    ) -> int:
        raise NotImplementedError

    def configure_startup_protocol_revision(self, revision: int) -> None:
        raise NotImplementedError

    def configure_verbose_logging(self, enabled: bool) -> None:
        raise NotImplementedError


@service(name="control_plane_root_reply_ingress_service")
@dataclass(slots=True)
class DefaultControlPlaneRootReplyIngressService(ControlPlaneRootReplyIngressService):
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)
    execution_ipc: ExecutionIpcTransportService = inject.service(ExecutionIpcTransportService)
    root_boundary_handoff: object | None = None
    shutdown_readiness: object | None = inject.service(ControlPlaneShutdownReadinessService)
    snapshot_builder: object | None = inject.service(ControlPlaneRootDiscoverySnapshotService)
    root_console_dispatch: object | None = inject.service(RootConsoleLogDispatchService)
    startup_protocol_revision: int = 1
    verbose_logging: bool = False

    def configure_startup_protocol_revision(self, revision: int) -> None:
        if not isinstance(revision, int):
            return
        self.startup_protocol_revision = max(1, int(revision))

    def configure_verbose_logging(self, enabled: bool) -> None:
        self.verbose_logging = bool(enabled)

    def drain_worker_replies(
        self,
        *,
        worker_id: str,
        timeout_seconds: float = 0.0,
        max_items: int = 64,
    ) -> int:
        if not isinstance(worker_id, str) or not worker_id:
            return 0
        drained = 0
        for index in range(max(1, int(max_items))):
            timeout = max(0.0, float(timeout_seconds)) if index == 0 else 0.0
            message = _recv_from_worker_lanes(
                ipc=self._ipc(),
                worker_id=worker_id,
                timeout_seconds=timeout,
            )
            if message is None:
                break
            payload = message.payload if isinstance(message, ExecutionIpcMessage) else message
            if self._dispatch_reply(worker_id=worker_id, payload=payload):
                drained += 1
        if drained > 0:
            self._emit_debug_log(
                level="debug",
                message="control-plane reply ingress drained worker replies",
                fields={
                    "event": "control_plane.reply_ingress.worker_drained",
                    "worker_id": worker_id,
                    "drained": drained,
                },
            )
        return drained

    def _dispatch_reply(self, *, worker_id: str, payload: object) -> bool:
        if isinstance(payload, ControlPlaneLeafHelloEvent):
            if self.startup_protocol_revision >= 2:
                self._state().append_event(payload)
                if self.startup_protocol_revision >= 3:
                    snapshot = self._build_discovery_snapshot(payload)
                    if snapshot is not None:
                        self._state().append_event(snapshot)
                        self._ipc().send(
                            compose_execution_ipc_worker_target_id(
                                worker_id,
                                lane=EXECUTION_IPC_LANE_CONTROL,
                            ),
                            snapshot,
                            no_reply=True,
                        )
                        return True
                    self._state().append_event(
                        ControlPlaneLeafConfigAckEvent(
                            target_group=payload.target_group,
                            worker_id=payload.worker_id,
                            config_id=f"{payload.worker_id}:cfg:snapshot-missing",
                            status="rejected",
                            error="discovery snapshot unavailable",
                        )
                    )
                    self._emit_debug_log(
                        level="error",
                        message="control-plane discovery snapshot unavailable for leaf hello",
                        fields={
                            "event": "control_plane.reply_ingress.discovery_snapshot_unavailable",
                            "worker_id": payload.worker_id,
                            "target_group": payload.target_group,
                            "startup_protocol_revision": self.startup_protocol_revision,
                        },
                    )
                    return True
                request = self._build_discovery_request(payload)
                if request is not None:
                    self._state().append_event(request)
                    self._ipc().send(
                        compose_execution_ipc_worker_target_id(
                            worker_id,
                            lane=EXECUTION_IPC_LANE_CONTROL,
                        ),
                        request,
                        no_reply=True,
                    )
                return True
            emitted = self._root_assign_node()(payload, None)
            for event in emitted:
                if isinstance(event, ControlPlaneLeafConfigCardEvent):
                    self._ipc().send(
                        compose_execution_ipc_worker_target_id(
                            worker_id,
                            lane=EXECUTION_IPC_LANE_CONTROL,
                        ),
                        event,
                        no_reply=True,
                    )
            return True
        if isinstance(payload, ControlPlaneLeafDiscoveryAckEvent):
            if self.startup_protocol_revision < 2:
                return False
            self._state().append_event(payload)
            if payload.status != "accepted":
                return True
            card = self._build_config_card_from_ack(payload)
            if card is None:
                return True
            self._state().append_event(card)
            self._ipc().send(
                compose_execution_ipc_worker_target_id(
                    worker_id,
                    lane=EXECUTION_IPC_LANE_CONTROL,
                ),
                card,
                no_reply=True,
            )
            return True
        if isinstance(payload, ControlPlaneLeafConfigAckEvent):
            self._root_ack_node()(payload, None)
            return True
        if isinstance(payload, ControlPlaneLeafStopAckEvent):
            self._root_stop_ack_node()(payload, None)
            return True
        if isinstance(payload, ControlPlaneLeafDrainReadyEvent):
            self._state().append_event(payload)
            _ = self._root_leaf_drain_ready_node()(payload, None)
            return True
        if isinstance(payload, ControlPlaneLeafBoundaryResultEvent):
            outputs_total = len(payload.outputs) if isinstance(payload.outputs, tuple) else 0
            envelope_targets = _sample_envelope_targets(payload.outputs, sample=16)
            self._emit_debug_log(
                level="debug",
                message="control-plane reply ingress boundary result received",
                fields={
                    "event": "control_plane.reply_ingress.boundary_result_received",
                    "worker_id": worker_id,
                    "target_group": payload.target_group,
                    "request_id": payload.request_id,
                    "status": payload.status,
                    "error": payload.error,
                    "outputs_total": outputs_total,
                    "sample_targets": envelope_targets,
                },
            )
            status = payload.status.strip().lower()
            if status == "failed":
                self._emit_debug_log(
                    level="warning",
                    message="control-plane reply ingress boundary result failed",
                    fields={
                        "event": "control_plane.reply_ingress.boundary_result_failed",
                        "worker_id": worker_id,
                        "target_group": payload.target_group,
                        "request_id": payload.request_id,
                        "error": payload.error,
                        "outputs_total": outputs_total,
                    },
                )
            self._root_boundary_result_node()(payload, None)
            for event in self._root_tombstone_observed_node()(payload, None):
                self._state().append_event(event)
                if isinstance(event, ControlPlaneLeafShutdownPrepareCommand):
                    self._ipc().send(
                        compose_execution_ipc_worker_target_id(
                            event.worker_id,
                            lane=EXECUTION_IPC_LANE_CONTROL,
                        ),
                        event,
                        no_reply=True,
                    )
                    self._emit_debug_log(
                        level="debug",
                        message="control-plane shutdown prepare dispatched",
                        fields={
                            "event": "control_plane.reply_ingress.shutdown_prepare_dispatched",
                            "worker_id": event.worker_id,
                            "target_group": event.target_group,
                            "command_id": event.command_id,
                        },
                    )
            self._dispatch_leaf_boundary_outputs(payload)
            return True
        return False

    def _root_assign_node(self) -> ControlPlaneRootLeafConfigAssignNode:
        return ControlPlaneRootLeafConfigAssignNode(state=self._state())

    def _root_ack_node(self) -> ControlPlaneRootLeafConfigAckNode:
        return ControlPlaneRootLeafConfigAckNode(state=self._state())

    def _root_stop_ack_node(self) -> ControlPlaneRootLeafStopAckNode:
        return ControlPlaneRootLeafStopAckNode(state=self._state())

    def _root_boundary_result_node(self) -> ControlPlaneRootLeafBoundaryResultNode:
        return ControlPlaneRootLeafBoundaryResultNode(state=self._state())

    def _root_tombstone_observed_node(self) -> ControlPlaneRootTombstoneObservedNode:
        return ControlPlaneRootTombstoneObservedNode(
            state=self._state(),
            shutdown_readiness=self._shutdown_readiness(),
        )

    def _root_leaf_drain_ready_node(self) -> ControlPlaneRootLeafDrainReadyNode:
        return ControlPlaneRootLeafDrainReadyNode(
            state=self._state(),
            shutdown_readiness=self._shutdown_readiness(),
        )

    def _dispatch_leaf_boundary_outputs(self, payload: ControlPlaneLeafBoundaryResultEvent) -> None:
        handoff = self._root_boundary_handoff_optional()
        if handoff is None:
            self._emit_debug_log(
                level="debug",
                message="control-plane reply ingress boundary handoff unavailable",
                fields={
                    "event": "control_plane.reply_ingress.boundary_handoff_unavailable",
                    "worker_id": payload.worker_id,
                    "request_id": payload.request_id,
                },
            )
            return
        if not isinstance(payload.outputs, tuple) or not payload.outputs:
            if payload.status.strip().lower() == "completed" and (payload.tombstone_input or payload.tombstone_output):
                self._state().append_event(
                    {
                        "kind": "leaf_tombstone_completed",
                        "worker_id": payload.worker_id,
                        "target_group": payload.target_group,
                        "request_id": payload.request_id,
                        "tombstone_output": payload.tombstone_output,
                    }
                )
            self._emit_debug_log(
                level="debug",
                message="control-plane reply ingress boundary result has no outputs",
                fields={
                    "event": "control_plane.reply_ingress.boundary_result_empty",
                    "worker_id": payload.worker_id,
                    "request_id": payload.request_id,
                    "status": payload.status,
                    "error": payload.error,
                    "tombstone_input": payload.tombstone_input,
                    "tombstone_output": payload.tombstone_output,
                },
            )
            return
        envelopes: list[Envelope] = []
        for item in payload.outputs:
            if not isinstance(item, Envelope):
                continue
            if not isinstance(item.target, str) or not item.target:
                continue
            envelopes.append(item)
        if not envelopes:
            self._emit_debug_log(
                level="debug",
                message="control-plane reply ingress boundary outputs are non-envelope",
                fields={
                    "event": "control_plane.reply_ingress.boundary_outputs_non_envelope",
                    "worker_id": payload.worker_id,
                    "request_id": payload.request_id,
                    "outputs_total": len(payload.outputs),
                },
            )
            return
        envelopes, relay_remapped, relay_dropped = _normalize_observability_relay_envelopes(envelopes)
        if payload.status.strip().lower() == "completed" and (payload.tombstone_input or payload.tombstone_output):
            self._state().append_event(
                {
                    "kind": "leaf_tombstone_completed",
                    "worker_id": payload.worker_id,
                    "target_group": payload.target_group,
                    "request_id": payload.request_id,
                    "tombstone_output": payload.tombstone_output,
                }
            )
        tombstone_outputs = sum(1 for item in envelopes if isinstance(item, Envelope) and item.tombstone)
        self._emit_debug_log(
            level="debug",
            message="control-plane reply ingress boundary relay normalized",
            fields={
                "event": "control_plane.reply_ingress.boundary_relay_normalized",
                "worker_id": payload.worker_id,
                "request_id": payload.request_id,
                "relay_remapped": relay_remapped,
                "relay_dropped": relay_dropped,
                "tombstone_input": payload.tombstone_input,
                "tombstone_output": payload.tombstone_output,
                "tombstone_outputs_in_envelopes": tombstone_outputs,
                "envelope_count": len(envelopes),
                "sample_targets": _sample_envelope_targets(envelopes, sample=16),
            },
        )
        if not envelopes:
            return
        try:
            routed = handoff.drain_external_deliveries(
                envelopes=envelopes,
                source_group=payload.target_group,
            )
            self._emit_debug_log(
                level="debug",
                message="control-plane reply ingress boundary outputs handed off",
                fields={
                    "event": "control_plane.reply_ingress.boundary_outputs_handed_off",
                    "worker_id": payload.worker_id,
                    "request_id": payload.request_id,
                    "envelope_count": len(envelopes),
                    "sample_targets": _sample_envelope_targets(envelopes, sample=16),
                    "handoff_immediate_outputs": len(routed) if isinstance(routed, list) else 0,
                },
            )
        except Exception as exc:
            self._emit_debug_log(
                level="warning",
                message="control-plane reply ingress boundary handoff failed",
                fields={
                    "event": "control_plane.reply_ingress.boundary_handoff_failed",
                    "worker_id": payload.worker_id,
                    "request_id": payload.request_id,
                    "error": exc.__class__.__name__,
                    "error_message": str(exc),
                    "envelope_count": len(envelopes),
                    "sample_targets": _sample_envelope_targets(envelopes, sample=16),
                },
            )
            return

    def _build_discovery_request(
        self,
        hello: ControlPlaneLeafHelloEvent,
    ) -> ControlPlaneLeafDiscoveryRequestEvent | None:
        state_events = self._state().events()
        group = find_group_spec_from_state(state_events, hello.target_group)
        if group is None:
            return None
        request_id = f"{hello.worker_id}:discover:{int(time.time() * 1000)}"
        return ControlPlaneLeafDiscoveryRequestEvent(
            target_group=hello.target_group,
            worker_id=hello.worker_id,
            request_id=request_id,
            required_nodes=tuple(group.nodes),
            include_relationships=False,
            protocol_revision=max(1, int(self.startup_protocol_revision)),
        )

    def _build_discovery_snapshot(
        self,
        hello: ControlPlaneLeafHelloEvent,
    ) -> ControlPlaneLeafDiscoverySnapshotEvent | None:
        snapshot_builder = self.snapshot_builder
        if isinstance(snapshot_builder, ControlPlaneRootDiscoverySnapshotService):
            return snapshot_builder.build_snapshot(
                hello=hello,
                protocol_revision=max(1, int(self.startup_protocol_revision)),
            )
        build_snapshot = getattr(snapshot_builder, "build_snapshot", None)
        if not callable(build_snapshot):
            return None
        try:
            built = build_snapshot(
                hello=hello,
                protocol_revision=max(1, int(self.startup_protocol_revision)),
            )
        except Exception:
            return None
        if isinstance(built, ControlPlaneLeafDiscoverySnapshotEvent):
            return built
        return None

    def _build_config_card_from_ack(
        self,
        ack: ControlPlaneLeafDiscoveryAckEvent,
    ) -> ControlPlaneLeafConfigCardEvent | None:
        state_events = self._state().events()
        group = find_group_spec_from_state(state_events, ack.target_group)
        if group is None:
            return None
        revision = next_config_revision(state_events, ack.worker_id)
        runner_profile: str | None = None
        for event in reversed(state_events):
            if not isinstance(event, ControlPlaneLeafHelloEvent):
                continue
            if event.worker_id != ack.worker_id:
                continue
            runner_profile = event.runner_profile
            break
        return ControlPlaneLeafConfigCardEvent(
            target_group=group.group_name,
            worker_id=ack.worker_id,
            config_id=f"{ack.worker_id}:cfg:{revision}",
            run_id="run",
            scenario_id="scenario",
            group_name=group.group_name,
            nodes=tuple(group.nodes),
            runner_profile=runner_profile,
            worker_slot=worker_slot_from_worker_id(ack.worker_id),
            config_revision=revision,
        )

    def _state(self) -> ControlPlaneStateService:
        candidate = self.state
        if isinstance(candidate, ControlPlaneStateService):
            return candidate
        if callable(getattr(candidate, "append_event", None)) and callable(getattr(candidate, "events", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ControlPlaneStateService binding is required")

    def _ipc(self) -> ExecutionIpcTransportService:
        candidate = self.execution_ipc
        if isinstance(candidate, ExecutionIpcTransportService):
            return candidate
        if callable(getattr(candidate, "recv", None)) and callable(getattr(candidate, "send", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ExecutionIpcTransportService binding is required")

    def _root_boundary_handoff_optional(self) -> object | None:
        candidate = self.root_boundary_handoff
        if callable(getattr(candidate, "drain_external_deliveries", None)):
            return candidate
        return None

    def _shutdown_readiness(self) -> ControlPlaneShutdownReadinessService:
        candidate = self.shutdown_readiness
        if isinstance(candidate, ControlPlaneShutdownReadinessService):
            return candidate
        if callable(getattr(candidate, "observe_tombstone", None)) and callable(
            getattr(candidate, "mark_leaf_ready", None)
        ):
            return candidate  # type: ignore[return-value]
        from stream_kernel.integration.kv_store import InMemoryKvStore
        from stream_kernel.platform.services.runtime.control_plane_shutdown_readiness import (
            InMemoryControlPlaneShutdownReadinessService,
        )

        fallback = InMemoryControlPlaneShutdownReadinessService(store=InMemoryKvStore())
        self.shutdown_readiness = fallback
        return fallback

    def _emit_debug_log(
        self,
        *,
        level: str,
        message: str,
        fields: dict[str, object],
    ) -> None:
        if not self.verbose_logging:
            return
        dispatch = self.root_console_dispatch
        publish = getattr(dispatch, "publish", None)
        if not callable(publish):
            return
        payload_fields = {"process_name": "supervisor"}
        payload_fields.update(fields)
        try:
            publish(LogMessage(level=level, message=message, fields=payload_fields))
        except Exception:
            return


def _sample_envelope_targets(values: object, *, sample: int) -> list[str]:
    if not isinstance(values, tuple) and not isinstance(values, list):
        return []
    targets: list[str] = []
    for item in values:
        if not isinstance(item, Envelope):
            continue
        if not isinstance(item.target, str) or not item.target:
            continue
        targets.append(item.target)
        if len(targets) >= max(1, int(sample)):
            break
    return targets


def _recv_from_worker_lanes(
    *,
    ipc: ExecutionIpcTransportService,
    worker_id: str,
    timeout_seconds: float,
) -> object | None:
    lane_targets = (
        compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_CONTROL),
        compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_DATA),
        compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_TRACE),
        compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_LOG),
        compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_METRIC),
    )
    for index, lane_target in enumerate(lane_targets):
        timeout = max(0.0, float(timeout_seconds)) if index == 0 else 0.0
        try:
            message = ipc.recv(lane_target, timeout=timeout)
        except Exception:
            continue
        if message is not None:
            return message
    return None


_OBSERVABILITY_EVENT_TARGETS: dict[type[object], str] = {
    TraceDispatchEvent: "system.obs.trace_dispatch",
    LogDispatchEvent: "system.obs.log_dispatch",
    DebugDispatchEvent: "system.obs.debug_dispatch",
    MetricDispatchEvent: "system.obs.metric_dispatch",
    MonitorDispatchEvent: "system.obs.monitor_dispatch",
    MonitoringMetricsSnapshotEvent: "system.obs.monitoring_metrics_dispatch",
    WorkerQueueTelemetryEvent: "system.obs.worker_queue_dispatch",
}


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
    "ControlPlaneRootReplyIngressService",
    "DefaultControlPlaneRootReplyIngressService",
]
