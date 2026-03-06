from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_DATA,
    compose_execution_ipc_worker_target_id,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneRootLeafBoundaryExecuteRequestEvent,
)


@dataclass(slots=True)
class _IpcPort:
    sends: list[dict[str, object]] = field(default_factory=list)

    def send(self, target_id: str, payload: object, *, no_reply: bool = False):
        self.sends.append(
            {"target_id": target_id, "payload": payload, "no_reply": no_reply}
        )
        return None


@dataclass(slots=True)
class _RootLeafCommands:
    requests: list[dict[str, object]] = field(default_factory=list)
    waits: list[dict[str, object]] = field(default_factory=list)
    result: object | None = None

    def make_boundary_execute_request(self, **kwargs: object) -> ControlPlaneRootLeafBoundaryExecuteRequestEvent:
        self.requests.append(dict(kwargs))
        return ControlPlaneRootLeafBoundaryExecuteRequestEvent(
            target_group=str(kwargs["target_group"]),
            worker_id=str(kwargs["worker_id"]),
            request_id=str(kwargs["request_id"]),
            inputs=tuple(kwargs["inputs"]),  # type: ignore[arg-type]
            finalize=bool(kwargs.get("finalize", True)),
        )

    def wait_boundary_result(self, **kwargs: object):
        self.waits.append(dict(kwargs))
        return self.result


def test_root_boundary_execution_service_sends_typed_command_and_returns_routing_result() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_execution_service import (
        DefaultControlPlaneRootBoundaryExecutionService,
    )
    from stream_kernel.platform.services.runtime.control_plane_events import (
        ControlPlaneLeafBoundaryExecuteCommand,
    )

    ipc = _IpcPort()
    commands = _RootLeafCommands(
        result=ControlPlaneLeafBoundaryResultEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            request_id="req-1",
            status="completed",
            outputs=("out-1", "out-2"),
        )
    )
    service = DefaultControlPlaneRootBoundaryExecutionService(
        root_leaf_commands=commands,
        execution_ipc=ipc,
    )

    result = service.execute_boundary_on_leaf(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-1",
        inputs=({"payload": 1},),
        timeout_seconds=0.1,
        finalize=True,
    )

    assert len(ipc.sends) == 1
    sent = ipc.sends[0]
    assert sent["target_id"] == compose_execution_ipc_worker_target_id(
        "execution.alpha#1",
        lane=EXECUTION_IPC_LANE_DATA,
    )
    assert sent["no_reply"] is True
    assert isinstance(sent["payload"], ControlPlaneLeafBoundaryExecuteCommand)
    assert result.local_deliveries == []
    assert result.boundary_deliveries == []
    assert result.terminal_outputs == ["out-1", "out-2"]


def test_root_boundary_execution_service_raises_on_timeout() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_execution_service import (
        DefaultControlPlaneRootBoundaryExecutionService,
        ControlPlaneRootBoundaryExecutionTimeoutError,
    )

    service = DefaultControlPlaneRootBoundaryExecutionService(
        root_leaf_commands=_RootLeafCommands(result=None),
        execution_ipc=_IpcPort(),
    )

    with pytest.raises(ControlPlaneRootBoundaryExecutionTimeoutError):
        service.execute_boundary_on_leaf(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            request_id="req-timeout",
            inputs=(),
            timeout_seconds=0.01,
        )


def test_root_boundary_execution_service_does_not_wait_for_result_when_finalize_is_false() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_execution_service import (
        DefaultControlPlaneRootBoundaryExecutionService,
    )
    from stream_kernel.platform.services.runtime.control_plane_events import (
        ControlPlaneLeafBoundaryExecuteCommand,
    )

    ipc = _IpcPort()
    commands = _RootLeafCommands()
    service = DefaultControlPlaneRootBoundaryExecutionService(
        root_leaf_commands=commands,
        execution_ipc=ipc,
    )

    result = service.execute_boundary_on_leaf(
        target_group="system.observability",
        worker_id="system.observability#1",
        request_id="req-obs-1",
        inputs=({"trace": 1},),
        timeout_seconds=0.25,
        finalize=False,
    )

    assert len(ipc.sends) == 1
    sent = ipc.sends[0]
    assert sent["target_id"] == compose_execution_ipc_worker_target_id(
        "system.observability#1",
        lane=EXECUTION_IPC_LANE_DATA,
    )
    assert sent["no_reply"] is True
    assert isinstance(sent["payload"], ControlPlaneLeafBoundaryExecuteCommand)
    assert sent["payload"].finalize is False
    assert commands.requests[0]["finalize"] is False
    assert commands.waits == []
    assert result.local_deliveries == []
    assert result.boundary_deliveries == []
    assert result.terminal_outputs == []


def test_root_boundary_execution_service_does_not_wait_for_result_when_wait_for_result_is_false() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_execution_service import (
        DefaultControlPlaneRootBoundaryExecutionService,
    )

    ipc = _IpcPort()
    commands = _RootLeafCommands()
    service = DefaultControlPlaneRootBoundaryExecutionService(
        root_leaf_commands=commands,
        execution_ipc=ipc,
    )

    result = service.execute_boundary_on_leaf(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-non-blocking-1",
        inputs=({"payload": 1},),
        timeout_seconds=0.25,
        finalize=True,
        wait_for_result=False,
    )

    assert len(ipc.sends) == 1
    assert commands.requests[0]["finalize"] is True
    assert commands.waits == []
    assert result.terminal_outputs == []


def test_root_boundary_execution_service_raises_on_failed_leaf_result() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_execution_service import (
        DefaultControlPlaneRootBoundaryExecutionService,
        ControlPlaneRootBoundaryExecutionFailedError,
    )

    service = DefaultControlPlaneRootBoundaryExecutionService(
        root_leaf_commands=_RootLeafCommands(
            result=ControlPlaneLeafBoundaryResultEvent(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                request_id="req-fail",
                status="failed",
                error="boom",
            )
        ),
        execution_ipc=_IpcPort(),
    )

    with pytest.raises(ControlPlaneRootBoundaryExecutionFailedError, match="boom"):
        service.execute_boundary_on_leaf(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            request_id="req-fail",
            inputs=(),
            timeout_seconds=0.1,
        )
