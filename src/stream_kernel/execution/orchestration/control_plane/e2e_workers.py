from __future__ import annotations

import time

from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import (
    PipeExecutionIpcTransportAdapter,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    ExecutionIpcMessage,
    ExecutionIpcTransportService,
    compose_execution_ipc_worker_target_id,
)
from stream_kernel.execution.transport.ipc.ipc_transport_service import (
    ExecutionIpcTransportCoordinatorService,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
)
from stream_kernel.routing.envelope import Envelope


def leaf_handshake_worker(stop_event: object | None, control_pipe: object | None) -> None:
    ipc = _open_worker_ipc(control_pipe=control_pipe, target_id="execution.alpha#1")
    if ipc is None:
        return
    worker_id = "execution.alpha#1"
    _send_worker_payload(
        ipc=ipc,
        worker_id=worker_id,
        payload=ControlPlaneLeafHelloEvent(target_group="execution.alpha", worker_id=worker_id),
        lane=EXECUTION_IPC_LANE_CONTROL,
    )
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if callable(getattr(stop_event, "is_set", None)) and bool(stop_event.is_set()):
            return
        message = _recv_worker_message(ipc=ipc, worker_id=worker_id, timeout=0.05)
        if message is None:
            continue
        msg = message
        if isinstance(msg, ControlPlaneLeafConfigCardEvent):
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafConfigAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    config_id=msg.config_id,
                    status="applied",
                    resolved_nodes=tuple(msg.nodes),
                ),
                lane=EXECUTION_IPC_LANE_CONTROL,
            )
            return


def leaf_boundary_worker(stop_event: object | None, control_pipe: object | None) -> None:
    ipc = _open_worker_ipc(control_pipe=control_pipe, target_id="execution.alpha#1")
    if ipc is None:
        return
    worker_id = "execution.alpha#1"
    target_group = "execution.alpha"
    _send_worker_payload(
        ipc=ipc,
        worker_id=worker_id,
        payload=ControlPlaneLeafHelloEvent(target_group=target_group, worker_id=worker_id),
        lane=EXECUTION_IPC_LANE_CONTROL,
    )
    configured = False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if callable(getattr(stop_event, "is_set", None)) and bool(stop_event.is_set()):
            return
        message = _recv_worker_message(ipc=ipc, worker_id=worker_id, timeout=0.05)
        if message is None:
            continue
        msg = message
        if isinstance(msg, ControlPlaneLeafConfigCardEvent):
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafConfigAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    config_id=msg.config_id,
                    status="applied",
                    resolved_nodes=tuple(msg.nodes),
                ),
                lane=EXECUTION_IPC_LANE_CONTROL,
            )
            configured = True
            continue
        if configured and isinstance(msg, ControlPlaneLeafBoundaryExecuteCommand):
            payloads = tuple(msg.inputs)
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafBoundaryResultEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    request_id=msg.request_id,
                    status="completed",
                    outputs=({"count": len(payloads), "worker_id": msg.worker_id},),
                ),
                lane=EXECUTION_IPC_LANE_DATA,
            )
            return


def leaf_stop_worker(stop_event: object | None, control_pipe: object | None) -> None:
    ipc = _open_worker_ipc(control_pipe=control_pipe, target_id="execution.alpha#1")
    if ipc is None:
        return
    worker_id = "execution.alpha#1"
    target_group = "execution.alpha"
    _send_worker_payload(
        ipc=ipc,
        worker_id=worker_id,
        payload=ControlPlaneLeafHelloEvent(target_group=target_group, worker_id=worker_id),
        lane=EXECUTION_IPC_LANE_CONTROL,
    )
    configured = False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if callable(getattr(stop_event, "is_set", None)) and bool(stop_event.is_set()):
            return
        message = _recv_worker_message(ipc=ipc, worker_id=worker_id, timeout=0.05)
        if message is None:
            continue
        msg = message
        if isinstance(msg, ControlPlaneLeafConfigCardEvent):
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafConfigAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    config_id=msg.config_id,
                    status="applied",
                    resolved_nodes=tuple(msg.nodes),
                ),
                lane=EXECUTION_IPC_LANE_CONTROL,
            )
            configured = True
            continue
        if configured and isinstance(msg, ControlPlaneLeafStopCommand):
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafStopAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    command_id=msg.command_id,
                    status="accepted",
                ),
                lane=EXECUTION_IPC_LANE_CONTROL,
            )
            return


def leaf_ignores_stop_command_worker(stop_event: object | None, control_pipe: object | None) -> None:
    ipc = _open_worker_ipc(control_pipe=control_pipe, target_id="execution.alpha#1")
    if ipc is None:
        return
    worker_id = "execution.alpha#1"
    target_group = "execution.alpha"
    _send_worker_payload(
        ipc=ipc,
        worker_id=worker_id,
        payload=ControlPlaneLeafHelloEvent(target_group=target_group, worker_id=worker_id),
        lane=EXECUTION_IPC_LANE_CONTROL,
    )
    configured = False
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not configured:
            message = _recv_worker_message(ipc=ipc, worker_id=worker_id, timeout=0.05)
            if message is None:
                continue
            msg = message
            if isinstance(msg, ControlPlaneLeafConfigCardEvent):
                _send_worker_payload(
                    ipc=ipc,
                    worker_id=worker_id,
                    payload=ControlPlaneLeafConfigAckEvent(
                        target_group=msg.target_group,
                        worker_id=msg.worker_id,
                        config_id=msg.config_id,
                        status="applied",
                        resolved_nodes=tuple(msg.nodes),
                    ),
                    lane=EXECUTION_IPC_LANE_CONTROL,
                )
                configured = True
                continue
        else:
            message = _recv_worker_message(ipc=ipc, worker_id=worker_id, timeout=0.01)
            if message is not None:
                msg = message
                if isinstance(msg, ControlPlaneLeafStopCommand):
                    continue
            time.sleep(0.02)


def leaf_full_flow_worker(stop_event: object | None, control_pipe: object | None) -> None:
    ipc = _open_worker_ipc(control_pipe=control_pipe, target_id="execution.alpha#1")
    if ipc is None:
        return

    worker_id = "execution.alpha#1"
    target_group = "execution.alpha"
    _send_worker_payload(
        ipc=ipc,
        worker_id=worker_id,
        payload=ControlPlaneLeafHelloEvent(target_group=target_group, worker_id=worker_id),
        lane=EXECUTION_IPC_LANE_CONTROL,
    )
    configured = False
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if callable(getattr(stop_event, "is_set", None)) and bool(stop_event.is_set()):
            return
        message = _recv_worker_message(ipc=ipc, worker_id=worker_id, timeout=0.05)
        if message is None:
            continue
        msg = message
        if isinstance(msg, ControlPlaneLeafConfigCardEvent):
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafConfigAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    config_id=msg.config_id,
                    status="applied",
                    resolved_nodes=tuple(msg.nodes),
                ),
                lane=EXECUTION_IPC_LANE_CONTROL,
            )
            configured = True
            continue
        if configured and isinstance(msg, ControlPlaneLeafBoundaryExecuteCommand):
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafBoundaryResultEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    request_id=msg.request_id,
                    status="completed",
                    outputs=({"ok": True, "worker_id": msg.worker_id, "count": len(msg.inputs)},),
                ),
                lane=EXECUTION_IPC_LANE_DATA,
            )
            continue
        if configured and isinstance(msg, ControlPlaneLeafStopCommand):
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafStopAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    command_id=msg.command_id,
                    status="accepted",
                ),
                lane=EXECUTION_IPC_LANE_CONTROL,
            )
            return


def leaf_config_ack_worker_for_target(
    stop_event: object | None,
    control_pipe: object | None,
    worker_id: str,
    target_group: str,
) -> None:
    ipc = _open_worker_ipc(control_pipe=control_pipe, target_id=worker_id)
    if ipc is None:
        return
    _send_worker_payload(
        ipc=ipc,
        worker_id=worker_id,
        payload=ControlPlaneLeafHelloEvent(target_group=target_group, worker_id=worker_id),
        lane=EXECUTION_IPC_LANE_CONTROL,
    )
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if callable(getattr(stop_event, "is_set", None)) and bool(stop_event.is_set()):
            return
        message = _recv_worker_message(ipc=ipc, worker_id=worker_id, timeout=0.05)
        if not isinstance(message, ControlPlaneLeafConfigCardEvent):
            continue
        _send_worker_payload(
            ipc=ipc,
            worker_id=worker_id,
            payload=ControlPlaneLeafConfigAckEvent(
                target_group=message.target_group,
                worker_id=message.worker_id,
                config_id=message.config_id,
                status="applied",
                resolved_nodes=tuple(message.nodes),
            ),
            lane=EXECUTION_IPC_LANE_CONTROL,
        )
        return


def leaf_pipeline_stage_worker_for_target(
    stop_event: object | None,
    control_pipe: object | None,
    worker_id: str,
    target_group: str,
    stage_name: str,
) -> None:
    ipc = _open_worker_ipc(control_pipe=control_pipe, target_id=worker_id)
    if ipc is None:
        return
    _send_worker_payload(
        ipc=ipc,
        worker_id=worker_id,
        payload=ControlPlaneLeafHelloEvent(target_group=target_group, worker_id=worker_id),
        lane=EXECUTION_IPC_LANE_CONTROL,
    )
    configured = False
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if callable(getattr(stop_event, "is_set", None)) and bool(stop_event.is_set()):
            return
        message = _recv_worker_message(ipc=ipc, worker_id=worker_id, timeout=0.05)
        if isinstance(message, ControlPlaneLeafConfigCardEvent):
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafConfigAckEvent(
                    target_group=message.target_group,
                    worker_id=message.worker_id,
                    config_id=message.config_id,
                    status="applied",
                    resolved_nodes=tuple(message.nodes),
                ),
                lane=EXECUTION_IPC_LANE_CONTROL,
            )
            configured = True
            continue
        if not configured or not isinstance(message, ControlPlaneLeafBoundaryExecuteCommand):
            continue
        if stage_name == "stage1":
            outputs: list[Envelope] = []
            for item in message.inputs:
                payload = item.payload if isinstance(item, dict) else getattr(item, "payload", None)
                if not isinstance(payload, dict):
                    continue
                value = payload.get("value")
                if not isinstance(value, int):
                    continue
                outputs.append(
                    Envelope(
                        payload={"value": value + 1},
                        target="stage2.node",
                        trace_id=getattr(item, "trace_id", None),
                    )
                )
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafBoundaryResultEvent(
                    target_group=target_group,
                    worker_id=worker_id,
                    request_id=message.request_id,
                    status="completed",
                    outputs=tuple(outputs),
                ),
                lane=EXECUTION_IPC_LANE_DATA,
            )
            continue
        if stage_name == "stage2":
            terminal: list[dict[str, object]] = []
            for item in message.inputs:
                payload = item.payload if isinstance(item, dict) else getattr(item, "payload", None)
                if not isinstance(payload, dict):
                    continue
                value = payload.get("value")
                if not isinstance(value, int):
                    continue
                terminal.append(
                    {
                        "stage": "stage2",
                        "worker_id": worker_id,
                        "final_value": value * 10,
                    }
                )
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafBoundaryResultEvent(
                    target_group=target_group,
                    worker_id=worker_id,
                    request_id=message.request_id,
                    status="completed",
                    outputs=tuple(terminal),
                ),
                lane=EXECUTION_IPC_LANE_DATA,
            )
            return
        message = _recv_worker_message(ipc=ipc, worker_id=worker_id, timeout=0.05)
        if message is None:
            continue
        msg = message
        if isinstance(msg, ControlPlaneLeafConfigCardEvent):
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafConfigAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    config_id=msg.config_id,
                    status="applied",
                    resolved_nodes=tuple(msg.nodes),
                ),
                lane=EXECUTION_IPC_LANE_CONTROL,
            )
            configured = True
            continue
        if configured and isinstance(msg, ControlPlaneLeafBoundaryExecuteCommand):
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafBoundaryResultEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    request_id=msg.request_id,
                    status="completed",
                    outputs=({"ok": True, "worker_id": msg.worker_id, "count": len(msg.inputs)},),
                ),
                lane=EXECUTION_IPC_LANE_DATA,
            )
            continue
        if configured and isinstance(msg, ControlPlaneLeafStopCommand):
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafStopAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    command_id=msg.command_id,
                    status="accepted",
                ),
                lane=EXECUTION_IPC_LANE_CONTROL,
            )
            return


def leaf_boundary_echo_worker_for_target(
    stop_event: object | None,
    control_pipe: object | None,
    worker_id: str,
    target_group: str,
) -> None:
    ipc = _open_worker_ipc(control_pipe=control_pipe, target_id=worker_id)
    if ipc is None:
        return
    _send_worker_payload(
        ipc=ipc,
        worker_id=worker_id,
        payload=ControlPlaneLeafHelloEvent(target_group=target_group, worker_id=worker_id),
        lane=EXECUTION_IPC_LANE_CONTROL,
    )
    configured = False
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if callable(getattr(stop_event, "is_set", None)) and bool(stop_event.is_set()):
            return
        message = _recv_worker_message(ipc=ipc, worker_id=worker_id, timeout=0.05)
        if isinstance(message, ControlPlaneLeafConfigCardEvent):
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafConfigAckEvent(
                    target_group=message.target_group,
                    worker_id=message.worker_id,
                    config_id=message.config_id,
                    status="applied",
                    resolved_nodes=tuple(message.nodes),
                ),
                lane=EXECUTION_IPC_LANE_CONTROL,
            )
            configured = True
            continue
        if configured and isinstance(message, ControlPlaneLeafBoundaryExecuteCommand):
            outputs: list[dict[str, object]] = []
            for item in message.inputs:
                payload = item.payload if isinstance(item, dict) else getattr(item, "payload", None)
                if isinstance(payload, dict):
                    outputs.append(
                        {
                            "worker_id": worker_id,
                            "target_group": target_group,
                            "value": payload.get("value"),
                        }
                    )
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafBoundaryResultEvent(
                    target_group=target_group,
                    worker_id=worker_id,
                    request_id=message.request_id,
                    status="completed",
                    outputs=tuple(outputs),
                ),
                lane=EXECUTION_IPC_LANE_DATA,
            )
            continue
        if configured and isinstance(message, ControlPlaneLeafStopCommand):
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafStopAckEvent(
                    target_group=message.target_group,
                    worker_id=message.worker_id,
                    command_id=message.command_id,
                    status="accepted",
                ),
                lane=EXECUTION_IPC_LANE_CONTROL,
            )
            return


def _open_worker_ipc(*, control_pipe: object | None, target_id: str) -> ExecutionIpcTransportService | None:
    if control_pipe is None:
        return None
    endpoint_registry = InMemoryKvStore()
    adapter = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=endpoint_registry)
    ipc = ExecutionIpcTransportCoordinatorService(adapter=adapter, endpoint_registry=endpoint_registry)
    if isinstance(control_pipe, dict):
        for lane_name, endpoint in control_pipe.items():
            if endpoint is None:
                continue
            ipc.bind_local_endpoint(
                compose_execution_ipc_worker_target_id(target_id, lane=str(lane_name)),
                endpoint,
            )
        return ipc
    ipc.bind_local_endpoint(target_id, control_pipe)
    return ipc


def _send_worker_payload(
    *,
    ipc: ExecutionIpcTransportService,
    worker_id: str,
    payload: object,
    lane: str,
) -> None:
    ipc.send(
        compose_execution_ipc_worker_target_id(worker_id, lane=lane),
        payload,
        no_reply=True,
    )


def _recv_worker_message(
    *,
    ipc: ExecutionIpcTransportService,
    worker_id: str,
    timeout: float,
) -> object | None:
    lane_targets = (
        compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_CONTROL),
        compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_DATA),
    )
    for index, target_id in enumerate(lane_targets):
        lane_timeout = max(0.0, float(timeout)) if index == 0 else 0.0
        message = ipc.recv(target_id, timeout=lane_timeout)
        if message is None:
            continue
        if isinstance(message, ExecutionIpcMessage):
            return message.payload
        return message
    return None


__all__ = [
    "leaf_boundary_worker",
    "leaf_boundary_echo_worker_for_target",
    "leaf_config_ack_worker_for_target",
    "leaf_full_flow_worker",
    "leaf_handshake_worker",
    "leaf_ignores_stop_command_worker",
    "leaf_pipeline_stage_worker_for_target",
    "leaf_stop_worker",
]
