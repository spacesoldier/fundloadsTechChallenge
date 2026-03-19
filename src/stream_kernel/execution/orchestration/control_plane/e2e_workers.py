from __future__ import annotations

import time

from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import (
    PipeExecutionIpcTransportAdapter,
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
from stream_kernel.execution.transport.ipc.ipc_transport_service import (
    ExecutionIpcTransportCoordinatorService,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafBoundaryOutputsEvent,
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
                payload=ControlPlaneLeafBoundaryOutputsEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    request_id=msg.request_id,
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
                payload=ControlPlaneLeafBoundaryOutputsEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    request_id=msg.request_id,
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
                payload=ControlPlaneLeafBoundaryOutputsEvent(
                    target_group=target_group,
                    worker_id=worker_id,
                    request_id=message.request_id,
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
                payload=ControlPlaneLeafBoundaryOutputsEvent(
                    target_group=target_group,
                    worker_id=worker_id,
                    request_id=message.request_id,
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
                payload=ControlPlaneLeafBoundaryOutputsEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    request_id=msg.request_id,
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
                payload=ControlPlaneLeafBoundaryOutputsEvent(
                    target_group=target_group,
                    worker_id=worker_id,
                    request_id=message.request_id,
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


def leaf_linear_pipeline_worker_for_target(
    stop_event: object | None,
    control_pipe: object | None,
    worker_id: str,
    target_group: str,
    stage_name: str,
    next_target: str | None,
    observability_target: str | None,
    observability_multiplier: int,
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
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if callable(getattr(stop_event, "is_set", None)) and bool(stop_event.is_set()):
            return
        message = _recv_worker_message(ipc=ipc, worker_id=worker_id, timeout=0.05)
        if message is None:
            continue
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
        if isinstance(message, ControlPlaneLeafStopCommand):
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
        if not configured:
            continue
        inputs: tuple[object, ...]
        if isinstance(message, ControlPlaneLeafBoundaryExecuteCommand):
            inputs = tuple(message.inputs)
        elif isinstance(message, Envelope):
            inputs = (message,)
        else:
            continue
        for item in inputs:
            payload, trace_id, tombstone = _extract_dispatch_input(item)
            if not isinstance(payload, dict):
                continue
            record_id = payload.get("id")
            if tombstone:
                if isinstance(next_target, str) and next_target:
                    _send_worker_payload(
                        ipc=ipc,
                        worker_id=worker_id,
                        payload=Envelope(
                            payload={"id": "tombstone"},
                            target=next_target,
                            trace_id=trace_id,
                            tombstone=True,
                        ),
                        lane=EXECUTION_IPC_LANE_DATA,
                    )
                else:
                    _send_worker_payload(
                        ipc=ipc,
                        worker_id=worker_id,
                        payload=ControlPlaneLeafBoundaryOutputsEvent(
                            target_group=target_group,
                            worker_id=worker_id,
                            request_id=f"egress:{stage_name}:tombstone",
                            outputs=({"stage": stage_name, "kind": "tombstone"},),
                            tombstone_output=True,
                        ),
                        lane=EXECUTION_IPC_LANE_DATA,
                    )
                continue
            if stage_name == "egress":
                if isinstance(record_id, int):
                    _send_worker_payload(
                        ipc=ipc,
                        worker_id=worker_id,
                        payload=ControlPlaneLeafBoundaryOutputsEvent(
                            target_group=target_group,
                            worker_id=worker_id,
                            request_id=f"egress:{record_id}",
                            outputs=({"stage": "egress", "kind": "data", "id": int(record_id)},),
                        ),
                        lane=EXECUTION_IPC_LANE_DATA,
                    )
            elif isinstance(next_target, str) and next_target and isinstance(record_id, int):
                _send_worker_payload(
                    ipc=ipc,
                    worker_id=worker_id,
                    payload=Envelope(
                        payload={"id": int(record_id), "stage": stage_name},
                        target=next_target,
                        trace_id=trace_id,
                    ),
                    lane=EXECUTION_IPC_LANE_DATA,
                )
            if (
                isinstance(observability_target, str)
                and observability_target
                and isinstance(record_id, int)
            ):
                for index in range(max(0, int(observability_multiplier))):
                    _send_worker_payload(
                        ipc=ipc,
                        worker_id=worker_id,
                        payload=Envelope(
                            payload={
                                "kind": "obs",
                                "stage": stage_name,
                                "id": int(record_id),
                                "idx": index,
                            },
                            target=observability_target,
                            trace_id=trace_id,
                        ),
                        lane=EXECUTION_IPC_LANE_DATA,
                    )


def observability_boundary_batch_sink_worker(
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
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if callable(getattr(stop_event, "is_set", None)) and bool(stop_event.is_set()):
            return
        message = _recv_worker_message(ipc=ipc, worker_id=worker_id, timeout=0.05)
        if message is None:
            continue
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
        if isinstance(message, ControlPlaneLeafStopCommand):
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
        if not configured:
            continue
        if isinstance(message, ControlPlaneLeafBoundaryExecuteCommand):
            count = len(tuple(message.inputs))
        elif isinstance(message, Envelope):
            count = 1
        else:
            continue
        _send_worker_payload(
            ipc=ipc,
            worker_id=worker_id,
            payload=ControlPlaneLeafBoundaryOutputsEvent(
                target_group=target_group,
                worker_id=worker_id,
                request_id=f"obs:{int(time.time() * 1000)}",
                outputs=({"kind": "obs_batch", "count": count},),
            ),
            lane=EXECUTION_IPC_LANE_DATA,
        )


def ring_pipeline_worker_for_target(
    stop_event: object | None,
    control_pipe: object | None,
    worker_id: str,
    target_group: str,
    role: str,
    local_transform_steps: int,
    inbound_target: str | None,
    outbound_target: str | None,
    observability_target: str | None,
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
    deadline = time.monotonic() + 120.0
    role_name = role if isinstance(role, str) else "middle"
    step_count = max(0, int(local_transform_steps))

    while time.monotonic() < deadline:
        if callable(getattr(stop_event, "is_set", None)) and bool(stop_event.is_set()):
            return

        control_message = ipc.recv(
            compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_CONTROL),
            timeout=0.0,
        )
        decoded_control = _unwrap_ipc_payload(control_message)
        if isinstance(decoded_control, ControlPlaneLeafConfigCardEvent):
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafConfigAckEvent(
                    target_group=decoded_control.target_group,
                    worker_id=decoded_control.worker_id,
                    config_id=decoded_control.config_id,
                    status="applied",
                    resolved_nodes=tuple(decoded_control.nodes),
                ),
                lane=EXECUTION_IPC_LANE_CONTROL,
            )
            configured = True
            continue
        if isinstance(decoded_control, ControlPlaneLeafStopCommand):
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafStopAckEvent(
                    target_group=decoded_control.target_group,
                    worker_id=decoded_control.worker_id,
                    command_id=decoded_control.command_id,
                    status="accepted",
                ),
                lane=EXECUTION_IPC_LANE_CONTROL,
            )
            return

        if not configured:
            time.sleep(0.001)
            continue

        if role_name == "ingress":
            ingress_message = ipc.recv(
                compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_DATA),
                timeout=0.0,
            )
            decoded_ingress = _unwrap_ipc_payload(ingress_message)
            if isinstance(decoded_ingress, ControlPlaneLeafBoundaryExecuteCommand):
                for item in tuple(decoded_ingress.inputs):
                    _process_ring_item(
                        ipc=ipc,
                        worker_id=worker_id,
                        target_group=target_group,
                        role_name=role_name,
                        step_count=step_count,
                        item=item,
                        outbound_target=outbound_target,
                        observability_target=observability_target,
                    )
            else:
                time.sleep(0.0005)
            continue

        ring_target = inbound_target if isinstance(inbound_target, str) and inbound_target else ""
        if not ring_target:
            time.sleep(0.001)
            continue
        ring_message = ipc.recv(ring_target, timeout=0.0)
        decoded_ring = _unwrap_ipc_payload(ring_message)
        if decoded_ring is None:
            time.sleep(0.0005)
            continue
        _process_ring_item(
            ipc=ipc,
            worker_id=worker_id,
            target_group=target_group,
            role_name=role_name,
            step_count=step_count,
            item=decoded_ring,
            outbound_target=outbound_target,
            observability_target=observability_target,
        )


def observability_ring_sink_worker_for_target(
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
    deadline = time.monotonic() + 120.0
    buffered = 0

    while time.monotonic() < deadline:
        if callable(getattr(stop_event, "is_set", None)) and bool(stop_event.is_set()):
            return

        control_message = ipc.recv(
            compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_CONTROL),
            timeout=0.0,
        )
        decoded_control = _unwrap_ipc_payload(control_message)
        if isinstance(decoded_control, ControlPlaneLeafConfigCardEvent):
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafConfigAckEvent(
                    target_group=decoded_control.target_group,
                    worker_id=decoded_control.worker_id,
                    config_id=decoded_control.config_id,
                    status="applied",
                    resolved_nodes=tuple(decoded_control.nodes),
                ),
                lane=EXECUTION_IPC_LANE_CONTROL,
            )
            configured = True
            continue
        if isinstance(decoded_control, ControlPlaneLeafStopCommand):
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafStopAckEvent(
                    target_group=decoded_control.target_group,
                    worker_id=decoded_control.worker_id,
                    command_id=decoded_control.command_id,
                    status="accepted",
                ),
                lane=EXECUTION_IPC_LANE_CONTROL,
            )
            return
        if not configured:
            time.sleep(0.001)
            continue

        decoded_data: object | None = None
        for lane in (
            EXECUTION_IPC_LANE_DATA,
            EXECUTION_IPC_LANE_TRACE,
            EXECUTION_IPC_LANE_LOG,
            EXECUTION_IPC_LANE_METRIC,
        ):
            lane_message = ipc.recv(
                compose_execution_ipc_worker_target_id(worker_id, lane=lane),
                timeout=0.0,
            )
            decoded_data = _unwrap_ipc_payload(lane_message)
            if decoded_data is not None:
                break
        if decoded_data is None:
            time.sleep(0.0005)
            continue
        items: tuple[object, ...]
        if isinstance(decoded_data, ControlPlaneLeafBoundaryExecuteCommand):
            items = tuple(decoded_data.inputs)
        else:
            items = (decoded_data,)
        flush = False
        for item in items:
            payload, _trace_id, tombstone = _extract_dispatch_input(item)
            if tombstone:
                flush = True
                continue
            if isinstance(payload, dict) and payload.get("kind") == "obs":
                buffered += 1
        if flush:
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafBoundaryOutputsEvent(
                    target_group=target_group,
                    worker_id=worker_id,
                    request_id=f"obs:ring:{int(time.time() * 1000)}",
                    outputs=({"kind": "obs_batch", "count": int(buffered)},),
                    tombstone_output=True,
                ),
                lane=EXECUTION_IPC_LANE_DATA,
            )
            buffered = 0


def _process_ring_item(
    *,
    ipc: ExecutionIpcTransportService,
    worker_id: str,
    target_group: str,
    role_name: str,
    step_count: int,
    item: object,
    outbound_target: str | None,
    observability_target: str | None,
) -> None:
    payload, trace_id, tombstone = _extract_dispatch_input(item)
    if not isinstance(payload, dict):
        return
    raw_id = payload.get("id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, int):
        return
    record_id = int(raw_id)
    stage_sum = int(payload.get("stage_sum")) if isinstance(payload.get("stage_sum"), int) else 0

    if tombstone:
        if role_name == "egress":
            _send_worker_payload(
                ipc=ipc,
                worker_id=worker_id,
                payload=ControlPlaneLeafBoundaryOutputsEvent(
                    target_group=target_group,
                    worker_id=worker_id,
                    request_id=f"egress:ring:tombstone:{record_id}",
                    outputs=({"kind": "tombstone", "id": record_id},),
                    tombstone_output=True,
                ),
                lane=EXECUTION_IPC_LANE_DATA,
            )
            _emit_observability(
                ipc=ipc,
                worker_id=worker_id,
                observability_target=observability_target,
                payload={"kind": "obs_tombstone", "id": record_id},
                trace_id=trace_id,
                tombstone=True,
            )
            return
        if isinstance(outbound_target, str) and outbound_target:
            ipc.send(
                outbound_target,
                Envelope(
                    payload={"id": record_id, "stage_sum": stage_sum},
                    target=outbound_target,
                    trace_id=trace_id,
                    tombstone=True,
                ),
                no_reply=True,
            )
        return

    next_sum = stage_sum + max(0, step_count)
    if role_name == "egress":
        _send_worker_payload(
            ipc=ipc,
            worker_id=worker_id,
            payload=ControlPlaneLeafBoundaryOutputsEvent(
                target_group=target_group,
                worker_id=worker_id,
                request_id=f"egress:ring:data:{record_id}",
                outputs=(
                    {
                        "kind": "data",
                        "id": record_id,
                        "stage_sum": next_sum,
                    },
                ),
            ),
            lane=EXECUTION_IPC_LANE_DATA,
        )
    elif isinstance(outbound_target, str) and outbound_target:
        ipc.send(
            outbound_target,
            Envelope(
                payload={"id": record_id, "stage_sum": next_sum},
                target=outbound_target,
                trace_id=trace_id,
            ),
            no_reply=True,
        )

    _emit_observability(
        ipc=ipc,
        worker_id=worker_id,
        observability_target=observability_target,
        payload={
            "kind": "obs",
            "id": record_id,
            "worker_id": worker_id,
            "stage_sum": next_sum,
        },
        trace_id=trace_id,
        tombstone=False,
    )


def _emit_observability(
    *,
    ipc: ExecutionIpcTransportService,
    worker_id: str,
    observability_target: str | None,
    payload: dict[str, object],
    trace_id: str | None,
    tombstone: bool,
) -> None:
    if not (isinstance(observability_target, str) and observability_target):
        return
    _send_worker_payload(
        ipc=ipc,
        worker_id=worker_id,
        payload=Envelope(
            payload=payload,
            target=observability_target,
            trace_id=trace_id,
            tombstone=bool(tombstone),
        ),
        lane=EXECUTION_IPC_LANE_DATA,
    )


def _unwrap_ipc_payload(message: object | None) -> object | None:
    if message is None:
        return None
    if isinstance(message, ExecutionIpcMessage):
        return message.payload
    return message


def _extract_dispatch_input(item: object) -> tuple[object, str | None, bool]:
    payload = getattr(item, "payload", item)
    trace_id = getattr(item, "trace_id", None)
    tombstone = bool(getattr(item, "tombstone", False))
    return (
        payload,
        trace_id if isinstance(trace_id, str) else None,
        tombstone,
    )


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
            lane_key = str(lane_name)
            if lane_key.startswith("target::"):
                ipc.bind_local_endpoint(lane_key.removeprefix("target::"), endpoint)
                continue
            ipc.bind_local_endpoint(compose_execution_ipc_worker_target_id(target_id, lane=lane_key), endpoint)
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
        compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_TRACE),
        compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_LOG),
        compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_METRIC),
    )
    # Fast-path: probe all lanes without blocking so data/observability traffic
    # is not delayed behind an idle control lane wait.
    for target_id in lane_targets:
        message = ipc.recv(target_id, timeout=0.0)
        if message is None:
            continue
        if isinstance(message, ExecutionIpcMessage):
            return message.payload
        return message

    # Backoff path: if no payload is ready, wait once on control lane.
    control_timeout = max(0.0, float(timeout))
    if control_timeout <= 0.0:
        return None
    for target_id in (lane_targets[0],):
        message = ipc.recv(target_id, timeout=control_timeout)
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
    "leaf_linear_pipeline_worker_for_target",
    "ring_pipeline_worker_for_target",
    "observability_ring_sink_worker_for_target",
    "observability_boundary_batch_sink_worker",
    "leaf_pipeline_stage_worker_for_target",
    "leaf_stop_worker",
]
