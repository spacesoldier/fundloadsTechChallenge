from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Event
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.lifecycle.leaf.command.finalization_service import (
    DefaultLeafSessionFinalizationService,
    LeafSessionFinalizationService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.debug_logging import leaf_debug_log
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_execution_service import (
    DefaultLeafBoundaryExecutionService,
    LeafBoundaryExecutionService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
    DefaultLeafRuntimeActivationService,
    LeafRuntimeActivationService,
)
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    EXECUTION_IPC_LANE_LOG,
    EXECUTION_IPC_LANE_METRIC,
    EXECUTION_IPC_LANE_TRACE,
    ExecutionIpcControlSignal,
    ExecutionIpcMessage,
    ExecutionIpcTransportService,
    compose_execution_ipc_worker_target_id,
)
from stream_kernel.execution.transport.ipc.ipc_lane_routing_service import (
    ExecutionIpcLaneRoutingService,
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
from stream_kernel.platform.services.runtime.control_plane_bootstrapper import (
    ControlPlaneBootstrapperService,
)
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    ControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafStartWorkEvent,
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneDiscoveryItemEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafShutdownPrepareCommand,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
)
from stream_kernel.platform.services.runtime.control_plane_shutdown_readiness import (
    ControlPlaneLeafShutdownReadinessService,
)
from stream_kernel.routing.envelope import Envelope

if TYPE_CHECKING:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime import (
        LeafWorkerRuntimeSession,
    )


_CONTROL_SEND_RETRY_WAIT = Event()


@runtime_checkable
class LeafWorkerCommandLoopService(Protocol):
    def handle_control_message(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        message: object,
    ) -> str | None:
        raise NotImplementedError

    def run_startup_handshake(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        stop_event: object | None,
        poll_seconds: float,
    ) -> bool:
        raise NotImplementedError

    def run_control_iteration(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        stop_event: object | None,
        poll_seconds: float,
    ) -> str | None:
        raise NotImplementedError


@service(name="leaf_worker_command_loop_service")
@dataclass(slots=True)
class DefaultLeafWorkerCommandLoopService(LeafWorkerCommandLoopService):
    activation_service: object | None = None
    boundary_execution_service: object | None = None
    finalization_service: object | None = None
    bootstrapper: object | None = inject.service(ControlPlaneBootstrapperService)
    discovery: object | None = inject.service(ControlPlaneDiscoveryService)
    execution_ipc: object | None = inject.service(ExecutionIpcTransportService)
    lane_routing_service: object | None = inject.service(ExecutionIpcLaneRoutingService)
    leaf_shutdown_readiness_service: object | None = inject.service(ControlPlaneLeafShutdownReadinessService)
    boundary_result_chunk_items: int = 32
    control_send_attempts: int = 3
    control_send_retry_backoff_seconds: float = 0.01

    def run_startup_handshake(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        stop_event: object | None,
        poll_seconds: float,
    ) -> bool:
        return self.run_control_iteration(
            session=session,
            control_pipe=control_pipe,
            stop_event=stop_event,
            poll_seconds=poll_seconds,
        ) == "configured"

    def run_control_iteration(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        stop_event: object | None,
        poll_seconds: float,
    ) -> str | None:
        _ = control_pipe
        if _stop_requested(stop_event):
            return None
        msg = self._recv_control_message(session=session, timeout_seconds=poll_seconds)
        if msg is None:
            return None
        return self.handle_control_message(
            session=session,
            control_pipe=control_pipe,
            message=msg,
        )

    def handle_control_message(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        message: object,
    ) -> str | None:
        _ = control_pipe
        msg = message
        leaf_debug_log(
            event="leaf.command_loop.handle_message",
            worker_id=session.worker_id,
            message_type=type(msg).__name__,
        )
        if isinstance(msg, ExecutionIpcControlSignal) and msg.kind == "ack":
            leaf_debug_log(
                event="leaf.command_loop.transport_ack_ignored",
                worker_id=session.worker_id,
            )
            return None
        if isinstance(msg, ControlPlaneLeafDiscoveryRequestEvent):
            leaf_debug_log(
                event="leaf.command_loop.discovery_request",
                worker_id=session.worker_id,
                request_id=msg.request_id,
                required_nodes=list(msg.required_nodes),
            )
            ack = self._build_discovery_ack(session=session, request=msg)
            self._send_control_message(session=session, payload=ack)
            return "discovery_acknowledged"
        if isinstance(msg, ControlPlaneLeafConfigCardEvent):
            leaf_debug_log(
                event="leaf.command_loop.config_card_received",
                worker_id=session.worker_id,
                config_id=msg.config_id,
                node_count=len(msg.nodes),
            )
            activation = self._resolve_activation_service(session)
            if activation is None:
                ack = ControlPlaneLeafConfigAckEvent(
                    target_group=session.group_name,
                    worker_id=session.worker_id,
                    config_id=msg.config_id,
                    status="rejected",
                    error="leaf activation service is unavailable",
                )
            else:
                ack = activation.apply_config(session=session, card=msg)
            self._send_control_message(session=session, payload=ack)
            leaf_debug_log(
                event="leaf.command_loop.config_card_processed",
                worker_id=session.worker_id,
                config_id=msg.config_id,
                status=ack.status,
                error=ack.error,
            )
            return "configured"
        if isinstance(msg, ControlPlaneLeafShutdownPrepareCommand):
            leaf_debug_log(
                event="leaf.command_loop.shutdown_prepare_received",
                worker_id=session.worker_id,
                command_id=msg.command_id,
            )
            readiness = self._resolve_leaf_shutdown_readiness_service(session)
            if readiness is not None:
                observe_prepare = getattr(readiness, "observe_prepare_command", None)
                if callable(observe_prepare):
                    try:
                        produced = observe_prepare(msg)
                    except Exception:
                        produced = None
                    if isinstance(produced, ControlPlaneLeafDrainReadyEvent):
                        self._send_control_message(session=session, payload=produced)
                        leaf_debug_log(
                            event="leaf.command_loop.shutdown_prepare_drain_ready_produced",
                            worker_id=session.worker_id,
                            request_id=produced.request_id,
                        )
            return "shutdown_prepare_observed"
        if isinstance(msg, ControlPlaneLeafStopCommand):
            leaf_debug_log(
                event="leaf.command_loop.stop_command_received",
                worker_id=session.worker_id,
                command_id=msg.command_id,
            )
            finalization = self._resolve_finalization_service(session)
            self._send_control_message(
                session=session,
                payload=ControlPlaneLeafStopAckEvent(
                    target_group=session.group_name,
                    worker_id=session.worker_id,
                    command_id=msg.command_id,
                    status="accepted",
                ),
            )
            if finalization is not None:
                try:
                    finalization.finalize(session=session)
                except Exception:
                    leaf_debug_log(
                        event="leaf.command_loop.stop_finalization_error",
                        worker_id=session.worker_id,
                    )
                    return "stop_requested"
            return "stop_requested"
        if isinstance(msg, ControlPlaneLeafBoundaryExecuteCommand):
            leaf_debug_log(
                event="leaf.command_loop.boundary_command_received",
                worker_id=session.worker_id,
                request_id=msg.request_id,
                input_count=len(msg.inputs),
                finalize=msg.finalize,
            )
            responses = self._execute_boundary(session=session, command=msg)
            if responses is not None:
                for response in responses:
                    self._send_control_message(session=session, payload=response)
            leaf_debug_log(
                event="leaf.command_loop.boundary_command_processed",
                worker_id=session.worker_id,
                request_id=msg.request_id,
                response_count=len(responses or ()),
            )
            return "boundary_executed"
        if isinstance(msg, ControlPlaneLeafStartWorkEvent):
            leaf_debug_log(
                event="leaf.command_loop.start_work_received",
                worker_id=session.worker_id,
                source_targets=list(msg.source_targets),
            )
            targets = self._resolve_start_work_targets(
                session=session,
                source_targets=tuple(msg.source_targets),
            )
            if not targets:
                leaf_debug_log(
                    event="leaf.command_loop.start_work_no_targets",
                    worker_id=session.worker_id,
                )
                return None
            for target in targets:
                command = ControlPlaneLeafBoundaryExecuteCommand(
                    target_group=session.group_name,
                    worker_id=session.worker_id,
                    request_id=f"inline-start-work:{session.worker_id}:{target}:{time.time_ns()}",
                    inputs=(
                        {
                            "dispatch_group": session.group_name,
                            "target": target,
                            "payload": BootstrapControl(target=target),
                            "trace_id": None,
                        },
                    ),
                    finalize=True,
                )
                responses = self._execute_boundary(session=session, command=command)
                if responses is not None:
                    for response in responses:
                        self._send_control_message(session=session, payload=response)
            leaf_debug_log(
                event="leaf.command_loop.start_work_processed",
                worker_id=session.worker_id,
                target_count=len(targets),
            )
            return "boundary_executed"
        if isinstance(msg, Envelope):
            leaf_debug_log(
                event="leaf.command_loop.inline_envelope_received",
                worker_id=session.worker_id,
                target=msg.target,
                payload_type=type(msg.payload).__name__,
            )
            inline_command = self._inline_boundary_command_from_envelope(
                session=session,
                envelope=msg,
            )
            if inline_command is None:
                leaf_debug_log(
                    event="leaf.command_loop.inline_envelope_ignored",
                    worker_id=session.worker_id,
                )
                return None
            responses = self._execute_boundary(session=session, command=inline_command)
            if responses is not None:
                for response in responses:
                    self._send_control_message(session=session, payload=response)
            leaf_debug_log(
                event="leaf.command_loop.inline_envelope_processed",
                worker_id=session.worker_id,
                request_id=inline_command.request_id,
                response_count=len(responses or ()),
            )
            return "boundary_executed"
        observability_target = _observability_dispatch_target(msg)
        if isinstance(observability_target, str) and observability_target:
            leaf_debug_log(
                event="leaf.command_loop.observability_dispatch",
                worker_id=session.worker_id,
                target=observability_target,
                payload_type=type(msg).__name__,
            )
            command = ControlPlaneLeafBoundaryExecuteCommand(
                target_group=session.group_name,
                worker_id=session.worker_id,
                request_id=f"obs-inline:{session.worker_id}:{observability_target}",
                inputs=(
                    {
                        "dispatch_group": session.group_name,
                        "target": observability_target,
                        "payload": msg,
                        "trace_id": _trace_id_from_event(msg),
                    },
                ),
                finalize=False,
            )
            _ = self._execute_boundary(session=session, command=command)
            return "boundary_executed"
        leaf_debug_log(
            event="leaf.command_loop.message_unhandled",
            worker_id=session.worker_id,
            message_type=type(msg).__name__,
        )
        return None

    def _inline_boundary_command_from_envelope(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        envelope: Envelope,
    ) -> ControlPlaneLeafBoundaryExecuteCommand | None:
        target = envelope.target
        if not isinstance(target, str) or not target:
            return None
        return ControlPlaneLeafBoundaryExecuteCommand(
            target_group=session.group_name,
            worker_id=session.worker_id,
            request_id=f"inline-envelope:{session.worker_id}:{time.time_ns()}",
            inputs=(
                {
                    "dispatch_group": session.group_name,
                    "target": target,
                    "payload": envelope.payload,
                    "trace_id": envelope.trace_id,
                    "reply_to": envelope.reply_to,
                    "span_id": envelope.span_id,
                    "tombstone": envelope.tombstone,
                },
            ),
            finalize=True,
        )

    @staticmethod
    def _resolve_start_work_targets(
        *,
        session: "LeafWorkerRuntimeSession",
        source_targets: tuple[str, ...],
    ) -> tuple[str, ...]:
        child = getattr(session, "child", None)
        scenario_steps = getattr(child, "scenario_steps", None)
        runtime_nodes = (
            {
                name
                for name in scenario_steps.keys()
                if isinstance(name, str) and name.startswith("source:")
            }
            if isinstance(scenario_steps, dict)
            else set()
        )
        requested = tuple(
            name for name in source_targets if isinstance(name, str) and name
        )
        if not runtime_nodes:
            return ()
        if not requested:
            return tuple(sorted(runtime_nodes))
        filtered: list[str] = []
        seen: set[str] = set()
        for target in requested:
            if target not in runtime_nodes:
                continue
            if target in seen:
                continue
            seen.add(target)
            filtered.append(target)
        return tuple(filtered)

    def _build_discovery_ack(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        request: ControlPlaneLeafDiscoveryRequestEvent,
    ) -> ControlPlaneLeafDiscoveryAckEvent:
        bootstrapper = self._resolve_bootstrapper_service(session)
        discovery = self._resolve_discovery_service(session)
        if bootstrapper is None or discovery is None:
            return ControlPlaneLeafDiscoveryAckEvent(
                target_group=session.group_name,
                worker_id=session.worker_id,
                request_id=request.request_id,
                status="rejected",
                discovered_nodes=(),
                missing_nodes=tuple(request.required_nodes),
                error="leaf discovery services are unavailable",
            )
        discover_all = getattr(bootstrapper, "discover_all", None)
        append_item = getattr(discovery, "append_item", None)
        if not callable(discover_all) or not callable(append_item):
            return ControlPlaneLeafDiscoveryAckEvent(
                target_group=session.group_name,
                worker_id=session.worker_id,
                request_id=request.request_id,
                status="rejected",
                discovered_nodes=(),
                missing_nodes=tuple(request.required_nodes),
                error="leaf discovery services do not implement required contract",
            )
        try:
            items = list(discover_all(runtime=self._runtime_from_session(session)))
            for item in items:
                append_item(item)
            discovered_nodes = self._discovered_node_names(items)
            missing_nodes = tuple(
                name
                for name in request.required_nodes
                if name not in discovered_nodes and not _is_transport_alias(name)
            )
            if missing_nodes:
                return ControlPlaneLeafDiscoveryAckEvent(
                    target_group=session.group_name,
                    worker_id=session.worker_id,
                    request_id=request.request_id,
                    status="rejected",
                    discovered_nodes=discovered_nodes,
                    missing_nodes=missing_nodes,
                    error="missing runtime metadata for nodes: " + ", ".join(sorted(set(missing_nodes))),
                )
            resolved = tuple(
                name
                for name in request.required_nodes
                if name in discovered_nodes or _is_transport_alias(name)
            )
            return ControlPlaneLeafDiscoveryAckEvent(
                target_group=session.group_name,
                worker_id=session.worker_id,
                request_id=request.request_id,
                status="accepted",
                discovered_nodes=resolved,
                missing_nodes=(),
            )
        except Exception as exc:  # noqa: BLE001 - convert to deterministic typed ack
            return ControlPlaneLeafDiscoveryAckEvent(
                target_group=session.group_name,
                worker_id=session.worker_id,
                request_id=request.request_id,
                status="rejected",
                discovered_nodes=(),
                missing_nodes=tuple(request.required_nodes),
                error=str(exc) or exc.__class__.__name__,
            )

    def _send_control_message(self, *, session: "LeafWorkerRuntimeSession", payload: object) -> None:
        ipc = self._resolve_execution_ipc_service(session)
        if ipc is None:
            leaf_debug_log(
                event="leaf.command_loop.send.no_ipc_service",
                worker_id=session.worker_id,
                payload_type=type(payload).__name__,
            )
            return
        max_attempts = max(1, int(self.control_send_attempts))
        last_exc: Exception | None = None
        lane = self._resolve_outbound_lane(payload=payload)
        target_id = compose_execution_ipc_worker_target_id(session.worker_id, lane=lane)
        for attempt in range(max_attempts):
            try:
                ipc.send(target_id, payload, no_reply=True)
                leaf_debug_log(
                    event="leaf.command_loop.send.sent",
                    worker_id=session.worker_id,
                    payload_type=type(payload).__name__,
                    lane=lane,
                )
                return
            except Exception as exc:
                last_exc = exc
                leaf_debug_log(
                    event="leaf.command_loop.send.error",
                    worker_id=session.worker_id,
                    payload_type=type(payload).__name__,
                    error=exc.__class__.__name__,
                    attempt=attempt + 1,
                    attempts=max_attempts,
                    lane=lane,
                )
                if attempt + 1 < max_attempts:
                    _CONTROL_SEND_RETRY_WAIT.wait(max(0.0, float(self.control_send_retry_backoff_seconds)))
                    continue
        if _is_critical_control_message_payload(payload):
            raise RuntimeError(
                "leaf control message send failed after retries"
            ) from last_exc
        return

    def _recv_control_message(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        timeout_seconds: float,
    ) -> object | None:
        ipc = self._resolve_execution_ipc_service(session)
        if ipc is None:
            leaf_debug_log(
                event="leaf.command_loop.recv.no_ipc_service",
                worker_id=session.worker_id,
            )
            return None
        message = _recv_from_worker_lanes(
            ipc=ipc,
            worker_id=session.worker_id,
            timeout_seconds=max(0.0, float(timeout_seconds)),
        )
        if message is None:
            return None
        if isinstance(message, ExecutionIpcMessage):
            return message.payload
        return message

    def _resolve_execution_ipc_service(
        self,
        session: "LeafWorkerRuntimeSession",
    ) -> ExecutionIpcTransportService | None:
        candidate = self.execution_ipc
        if isinstance(candidate, ExecutionIpcTransportService):
            return candidate
        if callable(getattr(candidate, "recv", None)) and callable(getattr(candidate, "send", None)):
            return candidate  # type: ignore[return-value]
        scope = _scenario_scope(session)
        if scope is None:
            return None
        try:
            resolved = scope.resolve("service", ExecutionIpcTransportService)
        except Exception:
            return None
        if isinstance(resolved, ExecutionIpcTransportService):
            return resolved
        if callable(getattr(resolved, "recv", None)) and callable(getattr(resolved, "send", None)):
            return resolved  # type: ignore[return-value]
        return None

    def _resolve_outbound_lane(self, *, payload: object) -> str:
        fallback = _lane_for_outbound_payload(payload)
        candidate = self.lane_routing_service
        if isinstance(candidate, ExecutionIpcLaneRoutingService):
            try:
                return candidate.resolve_lane(payload=payload, default_lane=fallback)
            except Exception:
                return fallback
        if callable(getattr(candidate, "resolve_lane", None)):
            try:
                return candidate.resolve_lane(payload=payload, default_lane=fallback)  # type: ignore[call-arg]
            except Exception:
                return fallback
        return fallback

    def _execute_boundary(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        command: ControlPlaneLeafBoundaryExecuteCommand,
    ) -> list[ControlPlaneLeafBoundaryResultEvent] | None:
        try:
            boundary = self._resolve_boundary_execution_service(session)
            if boundary is None:
                raise RuntimeError("leaf boundary execution service is unavailable")
            leaf_debug_log(
                event="leaf.command_loop.boundary_execution.started",
                worker_id=session.worker_id,
                request_id=command.request_id,
                finalize=command.finalize,
                input_count=len(command.inputs),
            )
            tombstone_input = any(_input_tombstone(item) for item in command.inputs)
            if not command.finalize:
                # Background lanes (for example observability) do not require
                # boundary result replies. Execute and consume outputs without
                # materializing them into a tuple to reduce blocking overhead.
                for _ in boundary.execute(
                    session=session,
                    inputs=list(command.inputs),
                    finalize_runtime=False,
                ):
                    pass
                leaf_debug_log(
                    event="leaf.command_loop.boundary_execution.background_completed",
                    worker_id=session.worker_id,
                    request_id=command.request_id,
                )
                return None
            chunk_size = max(1, int(self.boundary_result_chunk_items))
            chunk: list[object] = []
            events: list[ControlPlaneLeafBoundaryResultEvent] = []
            chunk_has_tombstone = False
            output_has_tombstone = False
            for output in boundary.execute(
                session=session,
                inputs=list(command.inputs),
                finalize_runtime=False,
            ):
                chunk.append(output)
                is_tombstone = isinstance(output, Envelope) and output.tombstone
                chunk_has_tombstone = chunk_has_tombstone or is_tombstone
                output_has_tombstone = output_has_tombstone or is_tombstone
                if len(chunk) < chunk_size:
                    continue
                events.append(
                    ControlPlaneLeafBoundaryResultEvent(
                        target_group=session.group_name,
                        worker_id=session.worker_id,
                        request_id=command.request_id,
                        status="stream",
                        outputs=tuple(chunk),
                        tombstone_input=tombstone_input,
                        tombstone_output=chunk_has_tombstone,
                    )
                )
                chunk = []
                chunk_has_tombstone = False
            events.append(
                ControlPlaneLeafBoundaryResultEvent(
                    target_group=session.group_name,
                    worker_id=session.worker_id,
                    request_id=command.request_id,
                    status="completed",
                    outputs=tuple(chunk),
                    tombstone_input=tombstone_input,
                    tombstone_output=output_has_tombstone or chunk_has_tombstone,
                )
            )
            if tombstone_input or output_has_tombstone:
                drain_ready = self._build_leaf_drain_ready_event(
                    session=session,
                    boundary_result=events[-1],
                )
                if isinstance(drain_ready, ControlPlaneLeafDrainReadyEvent):
                    events.append(drain_ready)
            leaf_debug_log(
                event="leaf.command_loop.boundary_execution.completed",
                worker_id=session.worker_id,
                request_id=command.request_id,
                event_count=len(events),
            )
            return events
        except Exception as exc:  # noqa: BLE001 - convert to typed result event
            leaf_debug_log(
                event="leaf.command_loop.boundary_execution.failed",
                worker_id=session.worker_id,
                request_id=command.request_id,
                error=exc.__class__.__name__,
            )
            if not command.finalize:
                return None
            return [
                ControlPlaneLeafBoundaryResultEvent(
                    target_group=session.group_name,
                    worker_id=session.worker_id,
                    request_id=command.request_id,
                    status="failed",
                    error=str(exc) or exc.__class__.__name__,
                    tombstone_input=any(_input_tombstone(item) for item in command.inputs),
                )
            ]

    def _resolve_activation_service(
        self,
        session: "LeafWorkerRuntimeSession",
    ) -> LeafRuntimeActivationService | None:
        candidate = self.activation_service
        if isinstance(candidate, LeafRuntimeActivationService):
            return candidate
        if callable(getattr(candidate, "apply_config", None)):
            return candidate  # type: ignore[return-value]
        scope = _scenario_scope(session)
        if scope is None:
            return None
        try:
            resolved = scope.resolve("service", LeafRuntimeActivationService)
        except Exception:
            resolved = None
        if isinstance(resolved, LeafRuntimeActivationService):
            return resolved
        if callable(getattr(resolved, "apply_config", None)):
            return resolved  # type: ignore[return-value]
        return DefaultLeafRuntimeActivationService()

    def _resolve_boundary_execution_service(
        self,
        session: "LeafWorkerRuntimeSession",
    ) -> LeafBoundaryExecutionService | None:
        candidate = self.boundary_execution_service
        if isinstance(candidate, LeafBoundaryExecutionService):
            return candidate
        if callable(getattr(candidate, "execute", None)):
            return candidate  # type: ignore[return-value]
        scope = _scenario_scope(session)
        if scope is None:
            return None
        try:
            resolved = scope.resolve("service", LeafBoundaryExecutionService)
        except Exception:
            resolved = None
        if isinstance(resolved, LeafBoundaryExecutionService):
            return resolved
        if callable(getattr(resolved, "execute", None)):
            return resolved  # type: ignore[return-value]
        return DefaultLeafBoundaryExecutionService()

    def _resolve_finalization_service(
        self,
        session: "LeafWorkerRuntimeSession",
    ) -> LeafSessionFinalizationService | None:
        candidate = self.finalization_service
        if isinstance(candidate, LeafSessionFinalizationService):
            return candidate
        if callable(getattr(candidate, "finalize", None)):
            return candidate  # type: ignore[return-value]
        scope = _scenario_scope(session)
        if scope is None:
            return None
        try:
            resolved = scope.resolve("service", LeafSessionFinalizationService)
        except Exception:
            resolved = None
        if isinstance(resolved, LeafSessionFinalizationService):
            return resolved
        if callable(getattr(resolved, "finalize", None)):
            return resolved  # type: ignore[return-value]
        return DefaultLeafSessionFinalizationService()

    def _resolve_bootstrapper_service(
        self,
        session: "LeafWorkerRuntimeSession",
    ) -> ControlPlaneBootstrapperService | None:
        candidate = self.bootstrapper
        if isinstance(candidate, ControlPlaneBootstrapperService):
            return candidate
        if callable(getattr(candidate, "discover_all", None)):
            return candidate  # type: ignore[return-value]
        scope = _scenario_scope(session)
        if scope is None:
            return None
        try:
            resolved = scope.resolve("service", ControlPlaneBootstrapperService)
        except Exception:
            return None
        if isinstance(resolved, ControlPlaneBootstrapperService):
            return resolved
        if callable(getattr(resolved, "discover_all", None)):
            return resolved  # type: ignore[return-value]
        return None

    def _resolve_discovery_service(
        self,
        session: "LeafWorkerRuntimeSession",
    ) -> ControlPlaneDiscoveryService | None:
        candidate = self.discovery
        if isinstance(candidate, ControlPlaneDiscoveryService):
            return candidate
        if callable(getattr(candidate, "append_item", None)):
            return candidate  # type: ignore[return-value]
        scope = _scenario_scope(session)
        if scope is None:
            return None
        try:
            resolved = scope.resolve("service", ControlPlaneDiscoveryService)
        except Exception:
            return None
        if isinstance(resolved, ControlPlaneDiscoveryService):
            return resolved
        if callable(getattr(resolved, "append_item", None)):
            return resolved  # type: ignore[return-value]
        return None

    def _build_leaf_drain_ready_event(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        boundary_result: ControlPlaneLeafBoundaryResultEvent,
    ) -> ControlPlaneLeafDrainReadyEvent | None:
        readiness = self._resolve_leaf_shutdown_readiness_service(session)
        if readiness is None:
            return None
        observe = getattr(readiness, "observe_boundary_result", None)
        if not callable(observe):
            return None
        try:
            produced = observe(boundary_result)
        except Exception:
            return None
        if isinstance(produced, ControlPlaneLeafDrainReadyEvent):
            leaf_debug_log(
                event="leaf.command_loop.leaf_drain_ready.produced",
                worker_id=session.worker_id,
                request_id=produced.request_id,
                target_group=produced.target_group,
            )
            return produced
        return None

    def _resolve_leaf_shutdown_readiness_service(
        self,
        session: "LeafWorkerRuntimeSession",
    ) -> ControlPlaneLeafShutdownReadinessService | None:
        candidate = self.leaf_shutdown_readiness_service
        if isinstance(candidate, ControlPlaneLeafShutdownReadinessService):
            return candidate
        if callable(getattr(candidate, "observe_boundary_result", None)):
            return candidate  # type: ignore[return-value]
        scope = _scenario_scope(session)
        if scope is None:
            return None
        try:
            resolved = scope.resolve("service", ControlPlaneLeafShutdownReadinessService)
        except Exception:
            return None
        if isinstance(resolved, ControlPlaneLeafShutdownReadinessService):
            return resolved
        if callable(getattr(resolved, "observe_boundary_result", None)):
            return resolved  # type: ignore[return-value]
        return None

    @staticmethod
    def _runtime_from_session(session: "LeafWorkerRuntimeSession") -> dict[str, object]:
        child = getattr(session, "child", None)
        runtime = getattr(child, "runtime", None)
        if isinstance(runtime, dict):
            return dict(runtime)
        return {}

    @staticmethod
    def _discovered_node_names(items: list[object]) -> tuple[str, ...]:
        resolved: list[str] = []
        for item in items:
            name: str | None = None
            if isinstance(item, ControlPlaneDiscoveryItemEvent):
                if item.item_kind != "node":
                    continue
                payload_name = item.payload.get("name")
                if isinstance(payload_name, str) and payload_name:
                    name = payload_name
            elif isinstance(item, ControlPlaneDiscoveryEntityRecord):
                if item.entity_kind != "node":
                    continue
                payload_name = item.meta.get("name")
                if isinstance(payload_name, str) and payload_name:
                    name = payload_name
            if not isinstance(name, str) or not name:
                continue
            resolved.append(name)
        return tuple(dict.fromkeys(resolved))

def _stop_requested(stop_event: object | None) -> bool:
    if stop_event is None:
        return False
    checker = getattr(stop_event, "is_set", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    return False


def _scenario_scope(session: object) -> object | None:
    child = getattr(session, "child", None)
    return getattr(child, "scenario_scope", None)


def _is_transport_alias(node_name: object) -> bool:
    if not isinstance(node_name, str) or not node_name:
        return False
    if node_name.startswith("system.obs."):
        return True
    if node_name.startswith("system.debug."):
        return True
    if node_name.startswith("system.transport.handoff."):
        return True
    if node_name.startswith(("source:", "sink:")):
        return True
    if node_name.endswith("_bridge"):
        return True
    if "_line_bridge" in node_name:
        return True
    return False


def _observability_dispatch_target(payload: object) -> str | None:
    targets: dict[type[object], str] = {
        TraceDispatchEvent: "system.obs.trace_dispatch",
        LogDispatchEvent: "system.obs.log_dispatch",
        DebugDispatchEvent: "system.obs.debug_dispatch",
        MetricDispatchEvent: "system.obs.metric_dispatch",
        MonitorDispatchEvent: "system.obs.monitor_dispatch",
        MonitoringMetricsSnapshotEvent: "system.obs.monitoring_metrics_dispatch",
        WorkerQueueTelemetryEvent: "system.obs.worker_queue_dispatch",
    }
    for token, target in targets.items():
        if isinstance(payload, token):
            return target
    return None


def _trace_id_from_event(payload: object) -> str | None:
    trace_id = getattr(payload, "trace_id", None)
    if isinstance(trace_id, str) and trace_id:
        return trace_id
    return None


def _input_tombstone(item: object) -> bool:
    if isinstance(item, dict):
        value = item.get("tombstone", False)
        return bool(value) if isinstance(value, bool) else False
    value = getattr(item, "tombstone", False)
    return bool(value) if isinstance(value, bool) else False


def _is_critical_control_message_payload(payload: object) -> bool:
    return isinstance(
        payload,
        (
            ControlPlaneLeafBoundaryResultEvent,
            ControlPlaneLeafDrainReadyEvent,
            ControlPlaneLeafConfigAckEvent,
            ControlPlaneLeafDiscoveryAckEvent,
            ControlPlaneLeafStopAckEvent,
        ),
    )


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


def _lane_for_outbound_payload(payload: object) -> str:
    if isinstance(payload, ControlPlaneLeafBoundaryResultEvent):
        return EXECUTION_IPC_LANE_DATA
    if isinstance(payload, ControlPlaneLeafDrainReadyEvent):
        # Prioritize shutdown-ready signal to avoid data-lane head-of-line stalls.
        return EXECUTION_IPC_LANE_CONTROL
    if isinstance(payload, TraceDispatchEvent):
        return EXECUTION_IPC_LANE_TRACE
    if isinstance(payload, LogDispatchEvent):
        return EXECUTION_IPC_LANE_LOG
    if isinstance(payload, DebugDispatchEvent):
        return EXECUTION_IPC_LANE_LOG
    if isinstance(
        payload,
        (
            MetricDispatchEvent,
            MonitorDispatchEvent,
            MonitoringMetricsSnapshotEvent,
            WorkerQueueTelemetryEvent,
        ),
    ):
        return EXECUTION_IPC_LANE_METRIC
    return EXECUTION_IPC_LANE_CONTROL


__all__ = [
    "LeafWorkerCommandLoopService",
    "DefaultLeafWorkerCommandLoopService",
]
