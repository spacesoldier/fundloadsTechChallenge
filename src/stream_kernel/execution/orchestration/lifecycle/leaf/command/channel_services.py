from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.transport.ipc.ipc_lane_routing_service import (
    ExecutionIpcLaneRoutingService,
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
)


@runtime_checkable
class LeafCommandChannelIngressService(Protocol):
    def configure_poll_timeout_seconds(self, timeout_seconds: float) -> None:
        raise NotImplementedError

    def poll_next_message(self, *, worker_id: str) -> object | None:
        raise NotImplementedError

    def poll_next_message_for_lane(
        self,
        *,
        worker_id: str,
        lane: str,
        timeout_seconds: float = 0.0,
    ) -> object | None:
        raise NotImplementedError

    async def poll_next_message_for_lane_async(
        self,
        *,
        worker_id: str,
        lane: str,
        timeout_seconds: float = 0.0,
    ) -> object | None:
        raise NotImplementedError


@runtime_checkable
class LeafControlReplyDispatchService(Protocol):
    def dispatch_reply(self, *, worker_id: str, payload: object) -> bool:
        raise NotImplementedError


@runtime_checkable
class LeafRunnerControlService(Protocol):
    def bind_runner_stop(self, callback: object) -> None:
        raise NotImplementedError

    def clear_runner_stop(self) -> None:
        raise NotImplementedError

    def request_stop(self) -> None:
        raise NotImplementedError

    def stop_requested(self) -> bool:
        raise NotImplementedError


@service(name="leaf_command_channel_ingress_service")
@dataclass(slots=True)
class DefaultLeafCommandChannelIngressService(LeafCommandChannelIngressService):
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
    poll_timeout_seconds: float = 0.01

    def configure_poll_timeout_seconds(self, timeout_seconds: float) -> None:
        if isinstance(timeout_seconds, (int, float)) and float(timeout_seconds) >= 0:
            self.poll_timeout_seconds = max(0.0, float(timeout_seconds))

    def poll_next_message(self, *, worker_id: str) -> object | None:
        message = self.poll_next_message_for_lane(
            worker_id=worker_id,
            lane=EXECUTION_IPC_LANE_CONTROL,
            timeout_seconds=max(0.0, float(self.poll_timeout_seconds)),
        )
        if message is None:
            message = self.poll_next_message_for_lane(
                worker_id=worker_id,
                lane=EXECUTION_IPC_LANE_DATA,
                timeout_seconds=0.0,
            )
        if message is None:
            message = self.poll_next_message_for_lane(
                worker_id=worker_id,
                lane=EXECUTION_IPC_LANE_TRACE,
                timeout_seconds=0.0,
            )
        if message is None:
            message = self.poll_next_message_for_lane(
                worker_id=worker_id,
                lane=EXECUTION_IPC_LANE_LOG,
                timeout_seconds=0.0,
            )
        if message is None:
            message = self.poll_next_message_for_lane(
                worker_id=worker_id,
                lane=EXECUTION_IPC_LANE_METRIC,
                timeout_seconds=0.0,
            )
        return message

    def poll_next_message_for_lane(
        self,
        *,
        worker_id: str,
        lane: str,
        timeout_seconds: float = 0.0,
    ) -> object | None:
        message = _recv_from_lane(
            ipc=_lane_adapter_ingress(self, lane),
            worker_id=worker_id,
            lane=lane,
            timeout_seconds=max(0.0, float(timeout_seconds)),
        )
        if message is None:
            return None
        if isinstance(message, ExecutionIpcMessage):
            payload = message.payload
        else:
            payload = message
        if isinstance(payload, ExecutionIpcControlSignal) and payload.kind == "ack":
            return None
        return payload

    async def poll_next_message_for_lane_async(
        self,
        *,
        worker_id: str,
        lane: str,
        timeout_seconds: float = 0.0,
    ) -> object | None:
        return self.poll_next_message_for_lane(
            worker_id=worker_id,
            lane=lane,
            timeout_seconds=timeout_seconds,
        )


@service(name="leaf_control_reply_dispatch_service")
@dataclass(slots=True)
class DefaultLeafControlReplyDispatchService(LeafControlReplyDispatchService):
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
    lane_routing_service: ExecutionIpcLaneRoutingService = inject.service(ExecutionIpcLaneRoutingService)

    def dispatch_reply(self, *, worker_id: str, payload: object) -> bool:
        lane = _resolve_reply_lane(payload=payload, lane_routing=self.lane_routing_service)
        target_id = compose_execution_ipc_worker_target_id(worker_id, lane=lane)
        try:
            _lane_adapter(self, lane).send(target_id, payload, no_reply=True)
            return True
        except Exception:
            return False


@service(name="leaf_runner_control_service")
@dataclass(slots=True)
class DefaultLeafRunnerControlService(LeafRunnerControlService):
    _request_stop_callback: object | None = None
    _stop_requested: bool = False

    def bind_runner_stop(self, callback: object) -> None:
        self._request_stop_callback = callback
        self._stop_requested = False

    def clear_runner_stop(self) -> None:
        self._request_stop_callback = None
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True
        callback = self._request_stop_callback
        if callable(callback):
            try:
                callback()
            except Exception:
                return

    def stop_requested(self) -> bool:
        return bool(self._stop_requested)


def _recv_from_lane(
    *,
    ipc: ExecutionIpcKvStreamPort,
    worker_id: str,
    lane: str,
    timeout_seconds: float,
) -> object | None:
    lane_target = compose_execution_ipc_worker_target_id(worker_id, lane=lane)
    try:
        return ipc.recv(lane_target, timeout=max(0.0, float(timeout_seconds)))
    except Exception:
        return None


def _resolve_reply_lane(*, payload: object, lane_routing: ExecutionIpcLaneRoutingService) -> str:
    fallback = _fallback_reply_lane(payload=payload)
    try:
        return lane_routing.resolve_lane(payload=payload, default_lane=fallback)
    except Exception:
        return fallback


def _lane_adapter(
    service: DefaultLeafControlReplyDispatchService,
    lane: str,
) -> ExecutionIpcKvStreamPort:
    if lane == EXECUTION_IPC_LANE_DATA:
        return service.data_lane_ipc
    if lane == EXECUTION_IPC_LANE_TRACE:
        return service.trace_lane_ipc
    if lane == EXECUTION_IPC_LANE_LOG:
        return service.log_lane_ipc
    if lane == EXECUTION_IPC_LANE_METRIC:
        return service.metric_lane_ipc
    return service.control_lane_ipc


def _lane_adapter_ingress(
    service: DefaultLeafCommandChannelIngressService,
    lane: str,
) -> ExecutionIpcKvStreamPort:
    if lane == EXECUTION_IPC_LANE_DATA:
        return service.data_lane_ipc
    if lane == EXECUTION_IPC_LANE_TRACE:
        return service.trace_lane_ipc
    if lane == EXECUTION_IPC_LANE_LOG:
        return service.log_lane_ipc
    if lane == EXECUTION_IPC_LANE_METRIC:
        return service.metric_lane_ipc
    return service.control_lane_ipc


def _fallback_reply_lane(*, payload: object) -> str:
    if isinstance(payload, ControlPlaneLeafBoundaryResultEvent):
        return EXECUTION_IPC_LANE_DATA
    if isinstance(payload, ControlPlaneLeafDrainReadyEvent):
        return EXECUTION_IPC_LANE_CONTROL
    return EXECUTION_IPC_LANE_CONTROL


__all__ = [
    "LeafCommandChannelIngressService",
    "DefaultLeafCommandChannelIngressService",
    "LeafControlReplyDispatchService",
    "DefaultLeafControlReplyDispatchService",
    "LeafRunnerControlService",
    "DefaultLeafRunnerControlService",
]
