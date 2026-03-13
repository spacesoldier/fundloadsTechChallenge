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
    ExecutionIpcControlSignal,
    ExecutionIpcKvStreamPort,
    ExecutionIpcMessage,
    compose_execution_ipc_worker_target_id,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafDiscoverySnapshotEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafStopAckEvent,
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
class ControlPlaneRootLeafIngressService(Protocol):
    def configure_poll_timeout_seconds(self, timeout_seconds: float) -> None:
        raise NotImplementedError

    def poll_next_leaf_ingress_for_worker_lane(
        self,
        *,
        worker_id: str,
        lane: str,
        timeout_seconds: float = 0.0,
    ) -> object | None:
        raise NotImplementedError

    async def poll_next_leaf_ingress_for_worker_lane_async(
        self,
        *,
        worker_id: str,
        lane: str,
        timeout_seconds: float = 0.0,
    ) -> object | None:
        raise NotImplementedError

    def dispatch_polled_leaf_ingress(
        self,
        *,
        worker_id: str,
        payload: object,
        lane: str | None = None,
    ) -> bool:
        raise NotImplementedError

    def configure_startup_protocol_revision(self, revision: int) -> None:
        raise NotImplementedError

    def configure_verbose_logging(self, enabled: bool) -> None:
        raise NotImplementedError


@service(name="control_plane_root_leaf_ingress_service")
@dataclass(slots=True)
class DefaultControlPlaneRootLeafIngressService(ControlPlaneRootLeafIngressService):
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)
    execution_ipc: object | None = None
    control_lane_ipc: ExecutionIpcKvStreamPort = inject.kv_stream(
        ExecutionIpcKvStreamPort,
        qualifier=EXECUTION_IPC_LANE_CONTROL,
    )
    data_lane_ipc: ExecutionIpcKvStreamPort = inject.kv_stream(
        ExecutionIpcKvStreamPort,
        qualifier=EXECUTION_IPC_LANE_DATA,
    )
    trace_lane_ipc: ExecutionIpcKvStreamPort = inject.kv_stream(
        ExecutionIpcKvStreamPort,
        qualifier=EXECUTION_IPC_LANE_TRACE,
    )
    log_lane_ipc: ExecutionIpcKvStreamPort = inject.kv_stream(
        ExecutionIpcKvStreamPort,
        qualifier=EXECUTION_IPC_LANE_LOG,
    )
    metric_lane_ipc: ExecutionIpcKvStreamPort = inject.kv_stream(
        ExecutionIpcKvStreamPort,
        qualifier=EXECUTION_IPC_LANE_METRIC,
    )
    root_boundary_handoff: object | None = None
    snapshot_builder: object | None = inject.service(ControlPlaneRootDiscoverySnapshotService)
    root_console_dispatch: object | None = inject.service(RootConsoleLogDispatchService)
    startup_protocol_revision: int = 1
    verbose_logging: bool = False
    poll_timeout_seconds: float = 0.01

    def __post_init__(self) -> None:
        legacy_ipc = self.execution_ipc
        if legacy_ipc is None:
            pass
        else:
            self.control_lane_ipc = legacy_ipc  # type: ignore[assignment]
            self.data_lane_ipc = legacy_ipc  # type: ignore[assignment]
            self.trace_lane_ipc = legacy_ipc  # type: ignore[assignment]
            self.log_lane_ipc = legacy_ipc  # type: ignore[assignment]
            self.metric_lane_ipc = legacy_ipc  # type: ignore[assignment]

    def configure_startup_protocol_revision(self, revision: int) -> None:
        if not isinstance(revision, int):
            return
        self.startup_protocol_revision = max(1, int(revision))

    def configure_verbose_logging(self, enabled: bool) -> None:
        self.verbose_logging = bool(enabled)

    def configure_poll_timeout_seconds(self, timeout_seconds: float) -> None:
        if isinstance(timeout_seconds, (int, float)) and float(timeout_seconds) >= 0:
            self.poll_timeout_seconds = max(0.0, float(timeout_seconds))

    def poll_next_leaf_ingress_for_worker_lane(
        self,
        *,
        worker_id: str,
        lane: str,
        timeout_seconds: float = 0.0,
    ) -> object | None:
        if not isinstance(worker_id, str) or not worker_id:
            return None
        message = _recv_from_worker_lane(
            control_lane_ipc=self.control_lane_ipc,
            data_lane_ipc=self.data_lane_ipc,
            trace_lane_ipc=self.trace_lane_ipc,
            log_lane_ipc=self.log_lane_ipc,
            metric_lane_ipc=self.metric_lane_ipc,
            worker_id=worker_id,
            lane=lane,
            timeout_seconds=max(0.0, float(timeout_seconds)),
        )
        return _coerce_leaf_ingress_payload(message)

    async def poll_next_leaf_ingress_for_worker_lane_async(
        self,
        *,
        worker_id: str,
        lane: str,
        timeout_seconds: float = 0.0,
    ) -> object | None:
        return self.poll_next_leaf_ingress_for_worker_lane(
            worker_id=worker_id,
            lane=lane,
            timeout_seconds=timeout_seconds,
        )

    def dispatch_polled_leaf_ingress(
        self,
        *,
        worker_id: str,
        payload: object,
        lane: str | None = None,
    ) -> bool:
        if not isinstance(worker_id, str) or not worker_id:
            return False
        payload_type = type(payload).__name__
        lane_name = lane.strip().lower() if isinstance(lane, str) and lane else None
        handled = False
        try:
            handled = bool(self._dispatch_reply(worker_id=worker_id, payload=payload))
        except Exception as exc:
            self._emit_debug_log(
                level="error",
                message="control-plane leaf ingress dispatch failed",
                fields={
                    "event": "control_plane.leaf_ingress.dispatch_failed",
                    "worker_id": worker_id,
                    "lane": lane_name,
                    "payload_type": payload_type,
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc),
                },
            )
            self._append_rejected_config_ack_for_startup_message(
                worker_id=worker_id,
                payload=payload,
                error=f"leaf ingress dispatch failed: {exc.__class__.__name__}: {exc}",
            )
            handled = True
        if handled:
            self._emit_debug_log(
                level="debug",
                message="control-plane leaf ingress worker reply dispatched",
                fields={
                    "event": "control_plane.leaf_ingress.worker_reply_dispatched",
                    "worker_id": worker_id,
                    "lane": lane_name,
                    "payload_type": payload_type,
                },
            )
        return handled

    def _dispatch_reply(self, *, worker_id: str, payload: object) -> bool:
        if isinstance(payload, ControlPlaneLeafHelloEvent):
            self._emit_debug_log(
                level="debug",
                message="control-plane leaf ingress hello received",
                fields={
                    "event": "control_plane.leaf_ingress.hello_received",
                    "worker_id": payload.worker_id,
                    "target_group": payload.target_group,
                    "startup_protocol_revision": self.startup_protocol_revision,
                },
            )
            if self.startup_protocol_revision >= 2:
                self._state().append_event(payload)
                if self.startup_protocol_revision >= 3:
                    snapshot = self._build_discovery_snapshot(payload)
                    if snapshot is not None:
                        self._emit_debug_log(
                            level="debug",
                            message="control-plane leaf ingress sending discovery snapshot",
                            fields={
                                "event": "control_plane.leaf_ingress.discovery_snapshot_sending",
                                "worker_id": payload.worker_id,
                                "target_group": payload.target_group,
                                "request_id": snapshot.request_id,
                                "required_nodes_count": len(snapshot.required_nodes),
                                "snapshot_records_count": len(snapshot.snapshot_records),
                            },
                        )
                        self._state().append_event(snapshot)
                        self._send_control_lane_command(worker_id=worker_id, payload=snapshot)
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
                            "event": "control_plane.leaf_ingress.discovery_snapshot_unavailable",
                            "worker_id": payload.worker_id,
                            "target_group": payload.target_group,
                            "startup_protocol_revision": self.startup_protocol_revision,
                        },
                    )
                    return True
                request = self._build_discovery_request(payload)
                if request is not None:
                    self._emit_debug_log(
                        level="debug",
                        message="control-plane leaf ingress sending discovery request",
                        fields={
                            "event": "control_plane.leaf_ingress.discovery_request_sending",
                            "worker_id": payload.worker_id,
                            "target_group": payload.target_group,
                            "request_id": request.request_id,
                            "required_nodes_count": len(request.required_nodes),
                        },
                    )
                    self._state().append_event(request)
                    self._send_control_lane_command(worker_id=worker_id, payload=request)
                return True
            self._state().append_event(payload)
            card = self._build_config_card_from_hello(payload)
            if card is not None:
                self._emit_debug_log(
                    level="debug",
                    message="control-plane leaf ingress sending config card from hello",
                    fields={
                        "event": "control_plane.leaf_ingress.config_card_sending",
                        "worker_id": payload.worker_id,
                        "target_group": payload.target_group,
                        "config_id": card.config_id,
                        "node_count": len(card.nodes),
                    },
                )
                self._state().append_event(card)
                self._send_control_lane_command(worker_id=worker_id, payload=card)
            return True
        if isinstance(payload, ControlPlaneLeafDiscoveryAckEvent):
            if self.startup_protocol_revision < 2:
                return False
            self._state().append_event(payload)
            if payload.status != "accepted":
                self._emit_debug_log(
                    level="warning",
                    message="control-plane leaf ingress discovery ack rejected",
                    fields={
                        "event": "control_plane.leaf_ingress.discovery_ack_rejected",
                        "worker_id": payload.worker_id,
                        "target_group": payload.target_group,
                        "request_id": payload.request_id,
                        "error": payload.error,
                        "missing_nodes_count": len(payload.missing_nodes),
                    },
                )
                return True
            card = self._build_config_card_from_ack(payload)
            if card is None:
                return True
            self._emit_debug_log(
                level="debug",
                message="control-plane leaf ingress sending config card from discovery ack",
                fields={
                    "event": "control_plane.leaf_ingress.config_card_sending",
                    "worker_id": payload.worker_id,
                    "target_group": payload.target_group,
                    "config_id": card.config_id,
                    "node_count": len(card.nodes),
                },
            )
            self._state().append_event(card)
            self._send_control_lane_command(worker_id=worker_id, payload=card)
            return True
        if isinstance(payload, ControlPlaneLeafConfigAckEvent):
            self._state().append_event(payload)
            self._emit_debug_log(
                level="debug",
                message="control-plane leaf ingress config ack received",
                fields={
                    "event": "control_plane.leaf_ingress.config_ack_received",
                    "worker_id": payload.worker_id,
                    "target_group": payload.target_group,
                    "config_id": payload.config_id,
                    "status": payload.status,
                    "error": payload.error,
                },
            )
            return True
        if isinstance(payload, ControlPlaneLeafStopAckEvent):
            self._state().append_event(payload)
            return True
        if isinstance(payload, ControlPlaneLeafDrainReadyEvent):
            # Drain-ready state machine is handled by root control-plane nodes
            # (node -> service -> kv-store). Keep ingress side-effect free here.
            return False
        if isinstance(payload, ControlPlaneLeafBoundaryResultEvent):
            outputs_total = len(payload.outputs) if isinstance(payload.outputs, tuple) else 0
            envelope_targets = _sample_envelope_targets(payload.outputs, sample=16)
            self._emit_debug_log(
                level="debug",
                message="control-plane leaf ingress boundary result received",
                fields={
                    "event": "control_plane.leaf_ingress.boundary_result_received",
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
                    message="control-plane leaf ingress boundary result failed",
                    fields={
                        "event": "control_plane.leaf_ingress.boundary_result_failed",
                        "worker_id": worker_id,
                        "target_group": payload.target_group,
                        "request_id": payload.request_id,
                        "error": payload.error,
                        "outputs_total": outputs_total,
                    },
                )
            self._state().append_event(payload)
            self._dispatch_leaf_boundary_outputs(payload)
            return True
        return False

    def _dispatch_leaf_boundary_outputs(self, payload: ControlPlaneLeafBoundaryResultEvent) -> None:
        handoff = self._root_boundary_handoff_optional()
        if handoff is None:
            self._emit_debug_log(
                level="debug",
                message="control-plane leaf ingress boundary handoff unavailable",
                fields={
                    "event": "control_plane.leaf_ingress.boundary_handoff_unavailable",
                    "worker_id": payload.worker_id,
                    "request_id": payload.request_id,
                },
            )
            return
        if not isinstance(payload.outputs, tuple) or not payload.outputs:
            self._emit_debug_log(
                level="debug",
                message="control-plane leaf ingress boundary result has no outputs",
                fields={
                    "event": "control_plane.leaf_ingress.boundary_result_empty",
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
                message="control-plane leaf ingress boundary outputs are non-envelope",
                fields={
                    "event": "control_plane.leaf_ingress.boundary_outputs_non_envelope",
                    "worker_id": payload.worker_id,
                    "request_id": payload.request_id,
                    "outputs_total": len(payload.outputs),
                },
            )
            return
        envelopes, relay_remapped, relay_dropped = _normalize_observability_relay_envelopes(envelopes)
        tombstone_outputs = sum(1 for item in envelopes if isinstance(item, Envelope) and item.tombstone)
        self._emit_debug_log(
            level="debug",
            message="control-plane leaf ingress boundary relay normalized",
            fields={
                "event": "control_plane.leaf_ingress.boundary_relay_normalized",
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
            drain_external = getattr(handoff, "drain_external_deliveries", None)
            if not callable(drain_external):
                raise ValueError("boundary handoff service missing drain_external_deliveries")
            try:
                routed = drain_external(
                    envelopes=envelopes,
                    source_group=payload.target_group,
                    pump_replies=False,
                )
            except TypeError:
                routed = drain_external(
                    envelopes=envelopes,
                    source_group=payload.target_group,
                )
            self._emit_debug_log(
                level="debug",
                message="control-plane leaf ingress boundary outputs handed off",
                fields={
                    "event": "control_plane.leaf_ingress.boundary_outputs_handed_off",
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
                message="control-plane leaf ingress boundary handoff failed",
                fields={
                    "event": "control_plane.leaf_ingress.boundary_handoff_failed",
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

    def _build_config_card_from_hello(
        self,
        hello: ControlPlaneLeafHelloEvent,
    ) -> ControlPlaneLeafConfigCardEvent | None:
        state_events = self._state().events()
        group = find_group_spec_from_state(state_events, hello.target_group)
        if group is None:
            return None
        revision = next_config_revision(state_events, hello.worker_id)
        return ControlPlaneLeafConfigCardEvent(
            target_group=group.group_name,
            worker_id=hello.worker_id,
            config_id=f"{hello.worker_id}:cfg:{revision}",
            run_id="run",
            scenario_id="scenario",
            group_name=group.group_name,
            nodes=tuple(group.nodes),
            runner_profile=hello.runner_profile,
            worker_slot=worker_slot_from_worker_id(hello.worker_id),
            config_revision=revision,
        )

    def _state(self) -> ControlPlaneStateService:
        candidate = self.state
        if isinstance(candidate, ControlPlaneStateService):
            return candidate
        raise ValueError("ControlPlaneStateService binding is required")

    def _send_control_lane_command(self, *, worker_id: str, payload: object) -> None:
        target_id = compose_execution_ipc_worker_target_id(
            worker_id,
            lane=EXECUTION_IPC_LANE_CONTROL,
        )
        self.control_lane_ipc.send(
            target_id,
            payload,
            no_reply=True,
        )
        metrics_fn = getattr(self.control_lane_ipc, "metrics", None)
        if not callable(metrics_fn):
            return
        try:
            metrics = metrics_fn(target_id)
        except Exception:
            return
        if not isinstance(metrics, dict):
            return
        self._emit_debug_log(
            level="debug",
            message="control-plane leaf ingress control command sent",
            fields={
                "event": "control_plane.leaf_ingress.control_command_sent",
                "worker_id": worker_id,
                "payload_type": type(payload).__name__,
                "target_id": target_id,
                "pending_outbound": metrics.get("pending_outbound"),
                "queue_depth": metrics.get("queue_depth"),
                "outbound_queue_depth": metrics.get("outbound_queue_depth"),
            },
        )

    def _root_boundary_handoff_optional(self) -> object | None:
        candidate = self.root_boundary_handoff
        if callable(getattr(candidate, "drain_external_deliveries", None)):
            return candidate
        return None

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

    def _append_rejected_config_ack_for_startup_message(
        self,
        *,
        worker_id: str,
        payload: object,
        error: str,
    ) -> None:
        if not isinstance(payload, (ControlPlaneLeafHelloEvent, ControlPlaneLeafDiscoveryAckEvent)):
            return
        target_group = _resolve_target_group(payload=payload, worker_id=worker_id)
        if target_group is None:
            return
        self._state().append_event(
            ControlPlaneLeafConfigAckEvent(
                target_group=target_group,
                worker_id=worker_id,
                config_id=f"{worker_id}:cfg:dispatch-failed",
                status="rejected",
                error=error,
            )
        )

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


def _recv_from_worker_lane(
    *,
    control_lane_ipc: ExecutionIpcKvStreamPort,
    data_lane_ipc: ExecutionIpcKvStreamPort,
    trace_lane_ipc: ExecutionIpcKvStreamPort,
    log_lane_ipc: ExecutionIpcKvStreamPort,
    metric_lane_ipc: ExecutionIpcKvStreamPort,
    worker_id: str,
    lane: str,
    timeout_seconds: float,
) -> object | None:
    normalized_lane = lane.strip().lower() if isinstance(lane, str) and lane else EXECUTION_IPC_LANE_CONTROL
    if normalized_lane == EXECUTION_IPC_LANE_DATA:
        lane_ipc = data_lane_ipc
    elif normalized_lane == EXECUTION_IPC_LANE_TRACE:
        lane_ipc = trace_lane_ipc
    elif normalized_lane == EXECUTION_IPC_LANE_LOG:
        lane_ipc = log_lane_ipc
    elif normalized_lane == EXECUTION_IPC_LANE_METRIC:
        lane_ipc = metric_lane_ipc
    else:
        lane_ipc = control_lane_ipc
        normalized_lane = EXECUTION_IPC_LANE_CONTROL
    lane_target = compose_execution_ipc_worker_target_id(worker_id, lane=normalized_lane)
    try:
        return lane_ipc.recv(lane_target, timeout=max(0.0, float(timeout_seconds)))
    except Exception:
        return None


def _coerce_leaf_ingress_payload(message: object) -> object | None:
    payload = message.payload if isinstance(message, ExecutionIpcMessage) else message
    if isinstance(payload, ExecutionIpcControlSignal) and payload.kind == "ack":
        return None
    return payload


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


def _resolve_target_group(*, payload: object, worker_id: str) -> str | None:
    candidate = getattr(payload, "target_group", None)
    if isinstance(candidate, str) and candidate:
        return candidate
    if isinstance(worker_id, str) and "#" in worker_id:
        group_name = worker_id.rsplit("#", 1)[0]
        if group_name:
            return group_name
    return None


__all__ = [
    "ControlPlaneRootLeafIngressService",
    "DefaultControlPlaneRootLeafIngressService",
]
