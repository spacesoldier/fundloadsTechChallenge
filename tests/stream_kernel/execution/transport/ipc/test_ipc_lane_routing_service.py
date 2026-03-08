from __future__ import annotations

from stream_kernel.execution.transport.ipc.ipc_lane_routing_service import (
    InMemoryExecutionIpcLaneRoutingService,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    EXECUTION_IPC_LANE_LOG,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafDrainReadyEvent,
)


def test_ipc_lane_routing_service_prefers_payload_mapping_over_target_prefix() -> None:
    service = InMemoryExecutionIpcLaneRoutingService(store=InMemoryKvStore())
    payload = ControlPlaneLeafBoundaryExecuteCommand(
        target_group="execution.features",
        worker_id="execution.features#1",
        request_id="req-1",
        inputs=(),
        finalize=True,
    )

    lane = service.resolve_lane(
        target="system.cp.leaf_boundary_execute",
        payload=payload,
    )

    assert lane == EXECUTION_IPC_LANE_DATA


def test_ipc_lane_routing_service_uses_target_prefix_for_control_messages() -> None:
    service = InMemoryExecutionIpcLaneRoutingService(store=InMemoryKvStore())

    lane = service.resolve_lane(
        target="system.cp.leaf_stop",
        payload={"kind": "stop"},
    )

    assert lane == EXECUTION_IPC_LANE_CONTROL


def test_ipc_lane_routing_service_accepts_runtime_snapshot_override() -> None:
    service = InMemoryExecutionIpcLaneRoutingService(store=InMemoryKvStore())

    loaded = service.preload_snapshot(
        target_prefix_lanes={"project.audit.": EXECUTION_IPC_LANE_LOG},
    )
    lane = service.resolve_lane(target="project.audit.dispatch")

    assert loaded == 1
    assert lane == EXECUTION_IPC_LANE_LOG


def test_ipc_lane_routing_service_routes_leaf_drain_ready_to_control_lane() -> None:
    service = InMemoryExecutionIpcLaneRoutingService(store=InMemoryKvStore())

    payload = ControlPlaneLeafDrainReadyEvent(
        target_group="execution.features",
        worker_id="execution.features#1",
        request_id="req-1",
        tombstone_output=True,
    )
    lane = service.resolve_lane(
        target="system.cp.leaf_drain_ready",
        payload=payload,
        default_lane=EXECUTION_IPC_LANE_DATA,
    )

    assert lane == EXECUTION_IPC_LANE_CONTROL
