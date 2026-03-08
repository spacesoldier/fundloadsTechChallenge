from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
    LeafWorkerCommandLoopService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.debug_logging import leaf_debug_log
from stream_kernel.execution.orchestration.lifecycle.leaf.command.finalization_service import (
    DefaultLeafSessionFinalizationService,
    LeafSessionFinalizationService,
)
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
from stream_kernel.integration.consumer_registry import ConsumerRegistry
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafStopCommand,
    ControlPlaneLeafStopAckEvent,
)

if TYPE_CHECKING:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime import (
        LeafWorkerRuntimeSession,
    )


@runtime_checkable
class LeafControlIngressService(Protocol):
    def run_until_stopped(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        stop_event: object | None,
        poll_interval_seconds: float,
    ) -> str:
        raise NotImplementedError


@service(name="leaf_control_ingress_service")
@dataclass(slots=True)
class DefaultLeafControlIngressService(LeafControlIngressService):
    command_loop_service: LeafWorkerCommandLoopService = inject.service(LeafWorkerCommandLoopService)
    execution_ipc: object | None = inject.service(ExecutionIpcTransportService)
    lane_routing_service: object | None = inject.service(ExecutionIpcLaneRoutingService)
    finalization_service: object | None = inject.service(LeafSessionFinalizationService)

    def run_until_stopped(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        stop_event: object | None,
        poll_interval_seconds: float,
    ) -> str:
        return _run_async_blocking(
            self.run_until_stopped_async(
                session=session,
                control_pipe=control_pipe,
                stop_event=stop_event,
                poll_interval_seconds=poll_interval_seconds,
            )
        )

    async def run_until_stopped_async(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        stop_event: object | None,
        poll_interval_seconds: float,
    ) -> str:
        _ = control_pipe
        poll_interval = max(0.0, float(poll_interval_seconds))
        leaf_debug_log(
            event="leaf.ingress.loop.started",
            worker_id=session.worker_id,
            poll_interval_seconds=poll_interval,
        )
        idle_loops = 0
        stop_drain_loops = 0
        while True:
            stop_requested = _stop_requested(stop_event)
            message = self._recv_control_message_nonblocking(session=session)
            if message is None:
                idle_loops += 1
                if stop_requested:
                    stop_drain_loops += 1
                    if stop_drain_loops % 1000 == 0:
                        leaf_debug_log(
                            event="leaf.ingress.loop.stop_drain_heartbeat",
                            worker_id=session.worker_id,
                            stop_drain_loops=stop_drain_loops,
                        )
                    if self._has_pending_transport_work(session=session):
                        await asyncio.sleep(poll_interval)
                        continue
                    leaf_debug_log(
                        event="leaf.ingress.loop.stop_event",
                        worker_id=session.worker_id,
                        stop_drain_loops=stop_drain_loops,
                    )
                    self._finalize_session(session=session)
                    return "stop_event"
                if idle_loops % 1000 == 0:
                    leaf_debug_log(
                        event="leaf.ingress.loop.idle_heartbeat",
                        worker_id=session.worker_id,
                        idle_loops=idle_loops,
                    )
                await asyncio.sleep(poll_interval)
                continue
            idle_loops = 0
            stop_drain_loops = 0
            leaf_debug_log(
                event="leaf.ingress.message.received",
                worker_id=session.worker_id,
                message_type=type(message).__name__,
            )
            if stop_requested and isinstance(message, ControlPlaneLeafStopCommand):
                leaf_debug_log(
                    event="leaf.ingress.stop_command.skipped_during_stop_drain",
                    worker_id=session.worker_id,
                    command_id=message.command_id,
                )
                continue
            if _is_transport_ack_signal(message):
                leaf_debug_log(
                    event="leaf.ingress.message.transport_ack_skipped",
                    worker_id=session.worker_id,
                )
                continue
            dispatched, status = self._dispatch_via_leaf_nodes(
                session=session,
                message=message,
            )
            leaf_debug_log(
                event="leaf.ingress.dispatch.result",
                worker_id=session.worker_id,
                dispatched=dispatched,
                status=status,
                message_type=type(message).__name__,
            )
            if not dispatched:
                status = self.command_loop_service.handle_control_message(
                    session=session,
                    control_pipe=control_pipe,
                    message=message,
                )
                leaf_debug_log(
                    event="leaf.ingress.dispatch.command_loop_fallback",
                    worker_id=session.worker_id,
                    status=status,
                    message_type=type(message).__name__,
                )
            if status == "stop_requested":
                if dispatched:
                    self._finalize_session(session=session)
                leaf_debug_log(
                    event="leaf.ingress.loop.stop_requested",
                    worker_id=session.worker_id,
                )
                return "stop_requested"

    def _dispatch_via_leaf_nodes(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        message: object,
    ) -> tuple[bool, str | None]:
        steps = _scenario_steps_for_session(session)
        if not steps:
            return (False, None)
        registry = _consumer_registry_for_session(session)
        if registry is None:
            return (False, None)
        consumers = _consumer_names_for_message(registry, message)
        if not consumers:
            return (False, None)
        dispatched = False
        status: str | None = None
        for node_name in consumers:
            if not node_name.startswith("system.cp.leaf_"):
                continue
            step = steps.get(node_name)
            if not callable(step):
                continue
            dispatched = True
            leaf_debug_log(
                event="leaf.ingress.node.dispatch",
                worker_id=session.worker_id,
                node_name=node_name,
                message_type=type(message).__name__,
            )
            produced = step(message, {"__leaf_session": session})
            for item in _coerce_step_outputs(produced):
                leaf_debug_log(
                    event="leaf.ingress.node.produced",
                    worker_id=session.worker_id,
                    node_name=node_name,
                    item_type=type(item).__name__,
                )
                if _is_leaf_control_reply(item):
                    self._send_control_message(session=session, payload=item)
                if isinstance(item, ControlPlaneLeafConfigAckEvent):
                    status = "configured"
                elif isinstance(item, ControlPlaneLeafDiscoveryAckEvent):
                    status = "discovery_acknowledged"
                elif isinstance(item, ControlPlaneLeafBoundaryResultEvent):
                    status = "boundary_executed"
                elif isinstance(item, ControlPlaneLeafDrainReadyEvent):
                    status = "boundary_executed"
                elif isinstance(item, ControlPlaneLeafStopAckEvent):
                    status = "stop_requested"
                else:
                    nested_status = self.command_loop_service.handle_control_message(
                        session=session,
                        control_pipe=None,
                        message=item,
                    )
                    if isinstance(nested_status, str) and nested_status:
                        status = nested_status
        return (dispatched, status)

    def _recv_control_message_nonblocking(self, *, session: "LeafWorkerRuntimeSession") -> object | None:
        ipc = self._resolve_execution_ipc_service(session)
        if ipc is None:
            leaf_debug_log(
                event="leaf.ingress.recv.no_ipc_service",
                worker_id=session.worker_id,
            )
            return None
        message = _recv_from_worker_lanes(
            ipc=ipc,
            worker_id=session.worker_id,
            timeout_seconds=0.0,
        )
        if message is None:
            return None
        if isinstance(message, ExecutionIpcMessage):
            return message.payload
        return message

    def _send_control_message(self, *, session: "LeafWorkerRuntimeSession", payload: object) -> None:
        ipc = self._resolve_execution_ipc_service(session)
        if ipc is None:
            leaf_debug_log(
                event="leaf.ingress.send.no_ipc_service",
                worker_id=session.worker_id,
                payload_type=type(payload).__name__,
            )
            return
        lane = self._resolve_outbound_lane(payload=payload)
        target_id = compose_execution_ipc_worker_target_id(session.worker_id, lane=lane)
        try:
            ipc.send(target_id, payload, no_reply=True)
            leaf_debug_log(
                event="leaf.ingress.send.sent",
                worker_id=session.worker_id,
                payload_type=type(payload).__name__,
                lane=lane,
            )
        except Exception as exc:
            leaf_debug_log(
                event="leaf.ingress.send.error",
                worker_id=session.worker_id,
                payload_type=type(payload).__name__,
                error=exc.__class__.__name__,
                lane=lane,
            )
            return

    def _resolve_execution_ipc_service(
        self,
        session: "LeafWorkerRuntimeSession",
    ) -> ExecutionIpcTransportService | None:
        candidate = self.execution_ipc
        if isinstance(candidate, ExecutionIpcTransportService):
            return candidate
        if callable(getattr(candidate, "recv", None)) and callable(getattr(candidate, "send", None)):
            return candidate  # type: ignore[return-value]
        child = getattr(session, "child", None)
        scope = getattr(child, "scenario_scope", None)
        if scope is None:
            return None
        resolve = getattr(scope, "resolve", None)
        if not callable(resolve):
            return None
        try:
            resolved = resolve("service", ExecutionIpcTransportService)
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

    def _has_pending_transport_work(self, *, session: "LeafWorkerRuntimeSession") -> bool:
        ipc = self._resolve_execution_ipc_service(session)
        if ipc is None:
            return False
        worker_id = getattr(session, "worker_id", None)
        if not isinstance(worker_id, str) or not worker_id:
            return False
        metrics = getattr(ipc, "metrics", None)
        if not callable(metrics):
            return False
        for lane in (
            EXECUTION_IPC_LANE_CONTROL,
            EXECUTION_IPC_LANE_DATA,
            EXECUTION_IPC_LANE_TRACE,
            EXECUTION_IPC_LANE_LOG,
            EXECUTION_IPC_LANE_METRIC,
        ):
            target_id = compose_execution_ipc_worker_target_id(worker_id, lane=lane)
            try:
                payload = metrics(target_id)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            queue_depth = payload.get("queue_depth", 0)
            pending_outbound = payload.get("pending_outbound", 0)
            outbound_queue_depth = payload.get("outbound_queue_depth", 0)
            if isinstance(queue_depth, int) and queue_depth > 0:
                return True
            if isinstance(pending_outbound, int) and pending_outbound > 0:
                return True
            if isinstance(outbound_queue_depth, int) and outbound_queue_depth > 0:
                return True
        return False

    def _finalize_session(self, *, session: "LeafWorkerRuntimeSession") -> None:
        finalizer = self._resolve_finalization_service(session)
        if finalizer is None:
            return
        try:
            finalizer.finalize(session=session)
        except Exception:
            return

    def _resolve_finalization_service(
        self,
        session: "LeafWorkerRuntimeSession",
    ) -> LeafSessionFinalizationService | None:
        candidate = self.finalization_service
        if isinstance(candidate, LeafSessionFinalizationService):
            return candidate
        if callable(getattr(candidate, "finalize", None)):
            return candidate  # type: ignore[return-value]
        child = getattr(session, "child", None)
        scope = getattr(child, "scenario_scope", None)
        if scope is None:
            return DefaultLeafSessionFinalizationService()
        resolve = getattr(scope, "resolve", None)
        if not callable(resolve):
            return DefaultLeafSessionFinalizationService()
        try:
            resolved = resolve("service", LeafSessionFinalizationService)
        except Exception:
            return DefaultLeafSessionFinalizationService()
        if isinstance(resolved, LeafSessionFinalizationService):
            return resolved
        if callable(getattr(resolved, "finalize", None)):
            return resolved  # type: ignore[return-value]
        return DefaultLeafSessionFinalizationService()


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


def _run_async_blocking(awaitable: object) -> str:
    if asyncio.iscoroutine(awaitable):
        try:
            return asyncio.run(awaitable)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(awaitable)
            finally:
                loop.close()
    raise ValueError("LeafControlIngressService expected coroutine awaitable")


def _scenario_steps_for_session(session: object) -> dict[str, object]:
    child = getattr(session, "child", None)
    steps = getattr(child, "scenario_steps", None)
    return dict(steps) if isinstance(steps, dict) else {}


def _consumer_registry_for_session(session: object) -> ConsumerRegistry | None:
    child = getattr(session, "child", None)
    scope = getattr(child, "scenario_scope", None)
    if scope is None:
        return None
    resolve = getattr(scope, "resolve", None)
    if not callable(resolve):
        return None
    try:
        resolved = resolve("service", ConsumerRegistry)
    except Exception:
        return None
    if isinstance(resolved, ConsumerRegistry):
        return resolved
    if callable(getattr(resolved, "get_consumers", None)):
        return resolved  # type: ignore[return-value]
    return None


def _consumer_names_for_message(registry: ConsumerRegistry, message: object) -> list[str]:
    get_consumers = getattr(registry, "get_consumers", None)
    if not callable(get_consumers):
        return []
    try:
        resolved = get_consumers(type(message))
    except Exception:
        return []
    if not isinstance(resolved, list):
        return []
    return [name for name in resolved if isinstance(name, str) and name]


def _coerce_step_outputs(produced: object) -> list[object]:
    if produced is None:
        return []
    if isinstance(produced, list):
        return list(produced)
    try:
        return list(produced)  # type: ignore[arg-type]
    except Exception:
        return []


def _is_leaf_control_reply(message: object) -> bool:
    return isinstance(
        message,
        (
            ControlPlaneLeafDiscoveryAckEvent,
            ControlPlaneLeafConfigAckEvent,
            ControlPlaneLeafBoundaryResultEvent,
            ControlPlaneLeafDrainReadyEvent,
            ControlPlaneLeafStopAckEvent,
        ),
    )


def _is_transport_ack_signal(message: object) -> bool:
    return isinstance(message, ExecutionIpcControlSignal) and message.kind == "ack"


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
    return EXECUTION_IPC_LANE_CONTROL


__all__ = [
    "LeafControlIngressService",
    "DefaultLeafControlIngressService",
]
