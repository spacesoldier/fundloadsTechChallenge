from __future__ import annotations

import asyncio
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
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafStopAckEvent,
)
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.routing.envelope import Envelope


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

    def register_data_available_callback(
        self,
        *,
        worker_id: str,
        lane: str,
        callback: object,
        loop: object | None = None,
    ) -> bool:
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
        # Cooperative yield keeps ingress drain budget from monopolizing event loop.
        await asyncio.sleep(0)
        return self.poll_next_message_for_lane(
            worker_id=worker_id,
            lane=lane,
            timeout_seconds=timeout_seconds,
        )

    def register_data_available_callback(
        self,
        *,
        worker_id: str,
        lane: str,
        callback: object,
        loop: object | None = None,
    ) -> bool:
        if not isinstance(worker_id, str) or not worker_id:
            return False
        if not callable(callback):
            return False
        target_id = compose_execution_ipc_worker_target_id(worker_id, lane=lane)
        adapter = _lane_adapter_ingress(self, lane)
        register = getattr(adapter, "register_data_available_callback", None)
        if not callable(register):
            return False
        try:
            result = register(target_id, callback, loop=loop)
            if isinstance(result, bool):
                return result
            return True
        except Exception:
            return False


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
        try:
            lane = _resolve_reply_lane(payload=payload, lane_routing=self.lane_routing_service)
            target_id = compose_execution_ipc_worker_target_id(worker_id, lane=lane)
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
        recv_buffered = getattr(ipc, "recv_buffered", None)
        timeout = max(0.0, float(timeout_seconds))
        if not callable(recv_buffered):
            return None
        return recv_buffered(lane_target, timeout=timeout)
    except Exception:
        return None


def _resolve_reply_lane(*, payload: object, lane_routing: ExecutionIpcLaneRoutingService) -> str:
    default_lane = _default_reply_lane(payload=payload)
    return lane_routing.resolve_lane(
        target=_reply_target(payload),
        payload=payload,
        default_lane=default_lane,
    )


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


def _default_reply_lane(*, payload: object) -> str:
    if isinstance(
        payload,
        (
            ControlPlaneLeafHelloEvent,
            ControlPlaneLeafDiscoveryAckEvent,
            ControlPlaneLeafConfigAckEvent,
            ControlPlaneLeafStopAckEvent,
            ControlPlaneLeafDrainReadyEvent,
        ),
    ):
        return EXECUTION_IPC_LANE_CONTROL
    if isinstance(payload, Envelope):
        return EXECUTION_IPC_LANE_DATA
    if isinstance(payload, LogMessage):
        return EXECUTION_IPC_LANE_DATA
    # Unknown payloads are treated as data-plane to avoid silently collapsing
    # business traffic into control lane.
    return EXECUTION_IPC_LANE_DATA


def _reply_target(payload: object) -> str | None:
    if not isinstance(payload, Envelope):
        return None
    target = payload.target
    if isinstance(target, str) and target:
        return target
    return None


__all__ = [
    "LeafCommandChannelIngressService",
    "DefaultLeafCommandChannelIngressService",
    "LeafControlReplyDispatchService",
    "DefaultLeafControlReplyDispatchService",
    "LeafRunnerControlService",
    "DefaultLeafRunnerControlService",
]
