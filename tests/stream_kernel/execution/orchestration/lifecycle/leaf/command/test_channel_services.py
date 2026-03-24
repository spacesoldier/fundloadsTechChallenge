from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.lifecycle.leaf.command.channel_services import (
    DefaultLeafCommandChannelIngressService,
    DefaultLeafControlReplyDispatchService,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    ExecutionIpcMessage,
    compose_execution_ipc_worker_target_id,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafReplyDispatchDiagEvent,
)
from stream_kernel.observability.domain.logging import LogMessage


@dataclass(slots=True)
class _BufferedIpcPort:
    calls: list[tuple[str, str, float | None]] = field(default_factory=list)

    def recv(self, target_id: str, *, timeout: float | None = None):
        self.calls.append(("recv", target_id, timeout))
        raise AssertionError("recv() should not be used by leaf ingress polling path")

    def recv_buffered(self, target_id: str, *, timeout: float | None = None):
        self.calls.append(("recv_buffered", target_id, timeout))
        return ExecutionIpcMessage(target_id=target_id, payload={"ok": True}, ts_epoch_ms=1)


@dataclass(slots=True)
class _NoBufferedIpcPort:
    calls: list[tuple[str, str, float | None]] = field(default_factory=list)

    def recv(self, target_id: str, *, timeout: float | None = None):
        self.calls.append(("recv", target_id, timeout))
        raise AssertionError("recv() fallback path must not be used")


@dataclass(slots=True)
class _SendIpcPort:
    sends: list[tuple[str, object, bool]] = field(default_factory=list)

    def send(self, target_id: str, payload: object, *, no_reply: bool = False) -> None:
        self.sends.append((target_id, payload, bool(no_reply)))


@dataclass(slots=True)
class _CallbackIpcPort:
    callbacks: list[tuple[str, object]] = field(default_factory=list)

    def register_data_available_callback(
        self,
        target_id: str,
        callback: object,
        *,
        loop: object | None = None,
    ) -> bool:
        _ = loop
        self.callbacks.append((target_id, callback))
        return True


@dataclass(slots=True)
class _FailRoutingService:
    def resolve_lane(self, *, target: str | None = None, payload: object | None = None, default_lane: str) -> str:
        _ = (target, payload, default_lane)
        raise RuntimeError("fallback path")


@dataclass(slots=True)
class _DataRoutingService:
    def resolve_lane(self, *, target: str | None = None, payload: object | None = None, default_lane: str) -> str:
        _ = (target, payload, default_lane)
        return EXECUTION_IPC_LANE_DATA


def test_leaf_channel_ingress_prefers_buffered_recv_when_available() -> None:
    ipc = _BufferedIpcPort()
    service = DefaultLeafCommandChannelIngressService(
        control_lane_ipc=ipc,  # type: ignore[arg-type]
        data_lane_ipc=ipc,  # type: ignore[arg-type]
        trace_lane_ipc=ipc,  # type: ignore[arg-type]
        log_lane_ipc=ipc,  # type: ignore[arg-type]
        metric_lane_ipc=ipc,  # type: ignore[arg-type]
    )

    payload = service.poll_next_message_for_lane(
        worker_id="execution.alpha#1",
        lane=EXECUTION_IPC_LANE_CONTROL,
        timeout_seconds=0.02,
    )

    lane_target = compose_execution_ipc_worker_target_id(
        "execution.alpha#1",
        lane=EXECUTION_IPC_LANE_CONTROL,
    )
    assert payload == {"ok": True}
    assert ipc.calls == [("recv_buffered", lane_target, 0.02)]


def test_leaf_channel_ingress_returns_none_when_buffered_recv_is_unavailable() -> None:
    ipc = _NoBufferedIpcPort()
    service = DefaultLeafCommandChannelIngressService(
        control_lane_ipc=ipc,  # type: ignore[arg-type]
        data_lane_ipc=ipc,  # type: ignore[arg-type]
        trace_lane_ipc=ipc,  # type: ignore[arg-type]
        log_lane_ipc=ipc,  # type: ignore[arg-type]
        metric_lane_ipc=ipc,  # type: ignore[arg-type]
    )

    payload = service.poll_next_message_for_lane(
        worker_id="execution.alpha#1",
        lane=EXECUTION_IPC_LANE_CONTROL,
        timeout_seconds=0.02,
    )

    assert payload is None
    assert ipc.calls == []


def test_leaf_reply_dispatch_log_message_uses_data_lane_fallback() -> None:
    control = _SendIpcPort()
    data = _SendIpcPort()
    trace = _SendIpcPort()
    log = _SendIpcPort()
    metric = _SendIpcPort()
    service = DefaultLeafControlReplyDispatchService(
        control_lane_ipc=control,  # type: ignore[arg-type]
        data_lane_ipc=data,  # type: ignore[arg-type]
        trace_lane_ipc=trace,  # type: ignore[arg-type]
        log_lane_ipc=log,  # type: ignore[arg-type]
        metric_lane_ipc=metric,  # type: ignore[arg-type]
        lane_routing_service=_FailRoutingService(),  # type: ignore[arg-type]
    )

    payload = LogMessage(level="info", message="relay")
    accepted = service.dispatch_reply(worker_id="system.observability#1", payload=payload)

    assert accepted is False
    assert data.sends == []
    assert control.sends == []
    assert trace.sends == []
    assert log.sends == []
    assert metric.sends == []


def test_leaf_channel_ingress_registers_data_available_callback_for_lane_target() -> None:
    control = _CallbackIpcPort()
    data = _CallbackIpcPort()
    trace = _CallbackIpcPort()
    log = _CallbackIpcPort()
    metric = _CallbackIpcPort()
    service = DefaultLeafCommandChannelIngressService(
        control_lane_ipc=control,  # type: ignore[arg-type]
        data_lane_ipc=data,  # type: ignore[arg-type]
        trace_lane_ipc=trace,  # type: ignore[arg-type]
        log_lane_ipc=log,  # type: ignore[arg-type]
        metric_lane_ipc=metric,  # type: ignore[arg-type]
    )
    callback = lambda: None

    accepted = service.register_data_available_callback(
        worker_id="execution.alpha#1",
        lane=EXECUTION_IPC_LANE_DATA,
        callback=callback,
    )

    assert accepted is True
    target = compose_execution_ipc_worker_target_id(
        "execution.alpha#1",
        lane=EXECUTION_IPC_LANE_DATA,
    )
    assert data.callbacks == [(target, callback)]


def test_leaf_reply_dispatch_forces_control_lane_for_control_plane_acks() -> None:
    control = _SendIpcPort()
    data = _SendIpcPort()
    trace = _SendIpcPort()
    log = _SendIpcPort()
    metric = _SendIpcPort()
    service = DefaultLeafControlReplyDispatchService(
        control_lane_ipc=control,  # type: ignore[arg-type]
        data_lane_ipc=data,  # type: ignore[arg-type]
        trace_lane_ipc=trace,  # type: ignore[arg-type]
        log_lane_ipc=log,  # type: ignore[arg-type]
        metric_lane_ipc=metric,  # type: ignore[arg-type]
        lane_routing_service=_DataRoutingService(),  # type: ignore[arg-type]
    )
    payload = ControlPlaneLeafDiscoveryAckEvent(
        target_group="execution.features",
        worker_id="execution.features#1",
        request_id="req-1",
        status="accepted",
        discovered_nodes=("compute_features",),
        missing_nodes=(),
    )

    accepted = service.dispatch_reply(worker_id="execution.features#1", payload=payload)

    assert accepted is True
    assert len(control.sends) == 1
    target_id, sent_payload, no_reply = control.sends[0]
    assert target_id == compose_execution_ipc_worker_target_id(
        "execution.features#1",
        lane=EXECUTION_IPC_LANE_CONTROL,
    )
    assert sent_payload == payload
    assert no_reply is True
    assert data.sends == []
    assert trace.sends == []
    assert log.sends == []
    assert metric.sends == []


def test_leaf_reply_dispatch_forces_control_lane_for_reply_dispatch_diag() -> None:
    control = _SendIpcPort()
    data = _SendIpcPort()
    trace = _SendIpcPort()
    log = _SendIpcPort()
    metric = _SendIpcPort()
    service = DefaultLeafControlReplyDispatchService(
        control_lane_ipc=control,  # type: ignore[arg-type]
        data_lane_ipc=data,  # type: ignore[arg-type]
        trace_lane_ipc=trace,  # type: ignore[arg-type]
        log_lane_ipc=log,  # type: ignore[arg-type]
        metric_lane_ipc=metric,  # type: ignore[arg-type]
        lane_routing_service=_DataRoutingService(),  # type: ignore[arg-type]
    )
    payload = ControlPlaneLeafReplyDispatchDiagEvent(
        target_group="execution.features",
        worker_id="execution.features#1",
        request_id="diag-1",
        stage="leaf_reply_dispatch",
        payload_type="ControlPlaneLeafDiscoveryAckEvent",
        status="accepted",
        detail="accepted",
    )

    accepted = service.dispatch_reply(worker_id="execution.features#1", payload=payload)

    assert accepted is True
    assert len(control.sends) == 1
    target_id, sent_payload, no_reply = control.sends[0]
    assert target_id == compose_execution_ipc_worker_target_id(
        "execution.features#1",
        lane=EXECUTION_IPC_LANE_CONTROL,
    )
    assert sent_payload == payload
    assert no_reply is True
    assert data.sends == []
    assert trace.sends == []
    assert log.sends == []
    assert metric.sends == []
