from __future__ import annotations

import time

from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import (
    PipeExecutionIpcTransportAdapter,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    ExecutionIpcTransportService,
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


def leaf_handshake_worker(stop_event: object | None, control_pipe: object | None) -> None:
    ipc = _open_worker_ipc(control_pipe=control_pipe, target_id="execution.alpha#1")
    if ipc is None:
        return
    worker_id = "execution.alpha#1"
    ipc.send(worker_id, ControlPlaneLeafHelloEvent(target_group="execution.alpha", worker_id=worker_id), no_reply=True)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if callable(getattr(stop_event, "is_set", None)) and bool(stop_event.is_set()):
            return
        message = ipc.recv(worker_id, timeout=0.05)
        if message is None:
            continue
        msg = message.payload
        if isinstance(msg, ControlPlaneLeafConfigCardEvent):
            ipc.send(
                worker_id,
                ControlPlaneLeafConfigAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    config_id=msg.config_id,
                    status="applied",
                    resolved_nodes=tuple(msg.nodes),
                ),
                no_reply=True,
            )
            return


def leaf_boundary_worker(stop_event: object | None, control_pipe: object | None) -> None:
    ipc = _open_worker_ipc(control_pipe=control_pipe, target_id="execution.alpha#1")
    if ipc is None:
        return
    worker_id = "execution.alpha#1"
    target_group = "execution.alpha"
    ipc.send(worker_id, ControlPlaneLeafHelloEvent(target_group=target_group, worker_id=worker_id), no_reply=True)
    configured = False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if callable(getattr(stop_event, "is_set", None)) and bool(stop_event.is_set()):
            return
        message = ipc.recv(worker_id, timeout=0.05)
        if message is None:
            continue
        msg = message.payload
        if isinstance(msg, ControlPlaneLeafConfigCardEvent):
            ipc.send(
                worker_id,
                ControlPlaneLeafConfigAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    config_id=msg.config_id,
                    status="applied",
                    resolved_nodes=tuple(msg.nodes),
                ),
                no_reply=True,
            )
            configured = True
            continue
        if configured and isinstance(msg, ControlPlaneLeafBoundaryExecuteCommand):
            payloads = tuple(msg.inputs)
            ipc.send(
                worker_id,
                ControlPlaneLeafBoundaryResultEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    request_id=msg.request_id,
                    status="completed",
                    outputs=({"count": len(payloads), "worker_id": msg.worker_id},),
                ),
                no_reply=True,
            )
            return


def leaf_stop_worker(stop_event: object | None, control_pipe: object | None) -> None:
    ipc = _open_worker_ipc(control_pipe=control_pipe, target_id="execution.alpha#1")
    if ipc is None:
        return
    worker_id = "execution.alpha#1"
    target_group = "execution.alpha"
    ipc.send(worker_id, ControlPlaneLeafHelloEvent(target_group=target_group, worker_id=worker_id), no_reply=True)
    configured = False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if callable(getattr(stop_event, "is_set", None)) and bool(stop_event.is_set()):
            return
        message = ipc.recv(worker_id, timeout=0.05)
        if message is None:
            continue
        msg = message.payload
        if isinstance(msg, ControlPlaneLeafConfigCardEvent):
            ipc.send(
                worker_id,
                ControlPlaneLeafConfigAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    config_id=msg.config_id,
                    status="applied",
                    resolved_nodes=tuple(msg.nodes),
                ),
                no_reply=True,
            )
            configured = True
            continue
        if configured and isinstance(msg, ControlPlaneLeafStopCommand):
            ipc.send(
                worker_id,
                ControlPlaneLeafStopAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    command_id=msg.command_id,
                    status="accepted",
                ),
                no_reply=True,
            )
            return


def leaf_ignores_stop_command_worker(stop_event: object | None, control_pipe: object | None) -> None:
    ipc = _open_worker_ipc(control_pipe=control_pipe, target_id="execution.alpha#1")
    if ipc is None:
        return
    worker_id = "execution.alpha#1"
    target_group = "execution.alpha"
    ipc.send(worker_id, ControlPlaneLeafHelloEvent(target_group=target_group, worker_id=worker_id), no_reply=True)
    configured = False
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not configured:
            message = ipc.recv(worker_id, timeout=0.05)
            if message is None:
                continue
            msg = message.payload
            if isinstance(msg, ControlPlaneLeafConfigCardEvent):
                ipc.send(
                    worker_id,
                    ControlPlaneLeafConfigAckEvent(
                        target_group=msg.target_group,
                        worker_id=msg.worker_id,
                        config_id=msg.config_id,
                        status="applied",
                        resolved_nodes=tuple(msg.nodes),
                    ),
                    no_reply=True,
                )
                configured = True
                continue
        else:
            message = ipc.recv(worker_id, timeout=0.01)
            if message is not None:
                msg = message.payload
                if isinstance(msg, ControlPlaneLeafStopCommand):
                    continue
            time.sleep(0.02)


def leaf_full_flow_worker(stop_event: object | None, control_pipe: object | None) -> None:
    ipc = _open_worker_ipc(control_pipe=control_pipe, target_id="execution.alpha#1")
    if ipc is None:
        return

    worker_id = "execution.alpha#1"
    target_group = "execution.alpha"
    ipc.send(worker_id, ControlPlaneLeafHelloEvent(target_group=target_group, worker_id=worker_id), no_reply=True)
    configured = False
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if callable(getattr(stop_event, "is_set", None)) and bool(stop_event.is_set()):
            return
        message = ipc.recv(worker_id, timeout=0.05)
        if message is None:
            continue
        msg = message.payload
        if isinstance(msg, ControlPlaneLeafConfigCardEvent):
            ipc.send(
                worker_id,
                ControlPlaneLeafConfigAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    config_id=msg.config_id,
                    status="applied",
                    resolved_nodes=tuple(msg.nodes),
                ),
                no_reply=True,
            )
            configured = True
            continue
        if configured and isinstance(msg, ControlPlaneLeafBoundaryExecuteCommand):
            ipc.send(
                worker_id,
                ControlPlaneLeafBoundaryResultEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    request_id=msg.request_id,
                    status="completed",
                    outputs=({"ok": True, "worker_id": msg.worker_id, "count": len(msg.inputs)},),
                ),
                no_reply=True,
            )
            continue
        if configured and isinstance(msg, ControlPlaneLeafStopCommand):
            ipc.send(
                worker_id,
                ControlPlaneLeafStopAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    command_id=msg.command_id,
                    status="accepted",
                ),
                no_reply=True,
            )
            return


def _open_worker_ipc(*, control_pipe: object | None, target_id: str) -> ExecutionIpcTransportService | None:
    if control_pipe is None:
        return None
    endpoint_registry = InMemoryKvStore()
    adapter = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=endpoint_registry)
    ipc = ExecutionIpcTransportCoordinatorService(adapter=adapter, endpoint_registry=endpoint_registry)
    ipc.bind_local_endpoint(target_id, control_pipe)
    return ipc


__all__ = [
    "leaf_boundary_worker",
    "leaf_full_flow_worker",
    "leaf_handshake_worker",
    "leaf_ignores_stop_command_worker",
    "leaf_stop_worker",
]
