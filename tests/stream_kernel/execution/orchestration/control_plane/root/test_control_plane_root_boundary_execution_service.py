from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_DATA,
    compose_execution_ipc_worker_target_id,
)


@dataclass(slots=True)
class _IpcPort:
    sends: list[dict[str, object]] = field(default_factory=list)

    def send(self, target_id: str, payload: object, *, no_reply: bool = False):
        self.sends.append({"target_id": target_id, "payload": payload, "no_reply": no_reply})
        return None


@dataclass(slots=True)
class _LaneRouting:
    lane: str = "custom"
    raises: bool = False
    calls: list[dict[str, object]] = field(default_factory=list)

    def resolve_lane(self, *, target: str, payload: object, default_lane: str) -> str:
        self.calls.append({"target": target, "payload": payload, "default_lane": default_lane})
        if self.raises:
            raise RuntimeError("lane-failed")
        return self.lane


def test_root_boundary_execution_service_sends_typed_command_fire_and_forget() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_execution_service import (
        DefaultControlPlaneRootBoundaryExecutionService,
    )
    from stream_kernel.platform.services.runtime.control_plane_events import (
        ControlPlaneLeafBoundaryExecuteCommand,
    )

    ipc = _IpcPort()
    service = DefaultControlPlaneRootBoundaryExecutionService(execution_ipc=ipc, lane_routing=None)

    result = service.execute_boundary_on_leaf(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-1",
        inputs=({"payload": 1},),
        timeout_seconds=0.1,
        finalize=True,
        wait_for_result=True,
    )

    assert result.local_deliveries == []
    assert result.boundary_deliveries == []
    assert result.terminal_outputs == []
    assert len(ipc.sends) == 1
    sent = ipc.sends[0]
    assert sent["target_id"] == compose_execution_ipc_worker_target_id(
        "execution.alpha#1",
        lane=EXECUTION_IPC_LANE_DATA,
    )
    assert sent["no_reply"] is True
    assert isinstance(sent["payload"], ControlPlaneLeafBoundaryExecuteCommand)
    assert sent["payload"].request_id == "req-1"
    assert sent["payload"].inputs == ({"payload": 1},)


def test_root_boundary_execution_service_uses_lane_routing_when_available() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_execution_service import (
        DefaultControlPlaneRootBoundaryExecutionService,
    )

    ipc = _IpcPort()
    routing = _LaneRouting(lane="trace")
    service = DefaultControlPlaneRootBoundaryExecutionService(execution_ipc=ipc, lane_routing=routing)

    _ = service.execute_boundary_on_leaf(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-2",
        inputs=(),
        timeout_seconds=0.1,
    )

    assert len(routing.calls) == 1
    assert len(ipc.sends) == 1
    assert ipc.sends[0]["target_id"] == compose_execution_ipc_worker_target_id(
        "execution.alpha#1",
        lane="trace",
    )


def test_root_boundary_execution_service_falls_back_to_data_lane_when_routing_fails() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_execution_service import (
        DefaultControlPlaneRootBoundaryExecutionService,
    )

    ipc = _IpcPort()
    routing = _LaneRouting(raises=True)
    service = DefaultControlPlaneRootBoundaryExecutionService(execution_ipc=ipc, lane_routing=routing)

    _ = service.execute_boundary_on_leaf(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-3",
        inputs=(),
        timeout_seconds=0.1,
    )

    assert len(ipc.sends) == 1
    assert ipc.sends[0]["target_id"] == compose_execution_ipc_worker_target_id(
        "execution.alpha#1",
        lane=EXECUTION_IPC_LANE_DATA,
    )
