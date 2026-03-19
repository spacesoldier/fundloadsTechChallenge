from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.transport.handoff.ipc_handoff_dispatch_service import (
    DefaultExecutionIpcHandoffDispatchService,
)
from stream_kernel.execution.transport.handoff.ipc_route_table_service import (
    InMemoryExecutionIpcRouteTableService,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    EXECUTION_IPC_LANE_LOG,
    compose_execution_ipc_worker_target_id,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class _IpcPort:
    sends: list[dict[str, object]] = field(default_factory=list)

    def send(self, target_id: str, payload: object, *, no_reply: bool = False):
        self.sends.append(
            {
                "target_id": target_id,
                "payload": payload,
                "no_reply": no_reply,
            }
        )
        return None


@dataclass(slots=True)
class _LaneRouting:
    lane: str = EXECUTION_IPC_LANE_DATA
    raise_error: bool = False

    def resolve_lane(self, *, target: str | None, payload: object, default_lane: str) -> str:
        _ = target, payload, default_lane
        if self.raise_error:
            raise RuntimeError("lane routing failed")
        return self.lane


def test_ipc_handoff_dispatch_service_uses_route_table_and_default_lane() -> None:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    route_table.upsert_route(target="remote.node", target_id="execution.alpha#1")
    ipc = _IpcPort()
    service = DefaultExecutionIpcHandoffDispatchService(
        execution_ipc=ipc,
        route_table=route_table,
        lane_routing=_LaneRouting(lane=EXECUTION_IPC_LANE_DATA),
    )

    dispatched = service.dispatch_envelope(
        Envelope(payload={"v": 1}, target="remote.node"),
        source_group="execution.root",
    )

    expected_envelope = Envelope(payload={"v": 1}, target="remote.node")
    assert dispatched is True
    assert ipc.sends == [
        {
            "target_id": compose_execution_ipc_worker_target_id(
                "execution.alpha#1",
                lane=EXECUTION_IPC_LANE_DATA,
            ),
            "payload": expected_envelope,
            "no_reply": True,
        }
    ]


def test_ipc_handoff_dispatch_service_rejects_envelope_when_target_or_route_missing() -> None:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    ipc = _IpcPort()
    service = DefaultExecutionIpcHandoffDispatchService(
        execution_ipc=ipc,
        route_table=route_table,
        lane_routing=_LaneRouting(lane=EXECUTION_IPC_LANE_DATA),
    )

    missing_target = service.dispatch_envelope(Envelope(payload={"v": 1}, target=None))
    missing_route = service.dispatch_envelope(Envelope(payload={"v": 2}, target="remote.node"))

    assert missing_target is False
    assert missing_route is False
    assert ipc.sends == []


def test_ipc_handoff_dispatch_service_falls_back_to_target_lane_when_lane_routing_binding_is_missing() -> None:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    route_table.upsert_route(target="remote.node", target_id="execution.alpha#1")
    ipc = _IpcPort()
    service = DefaultExecutionIpcHandoffDispatchService(
        execution_ipc=ipc,
        route_table=route_table,
        lane_routing=None,
    )

    dispatched = service.dispatch_envelope(
        Envelope(payload={"v": 1}, target="remote.node"),
        source_group="execution.root",
    )

    assert dispatched is True
    assert ipc.sends[0]["target_id"] == compose_execution_ipc_worker_target_id(
        "execution.alpha#1",
        lane=EXECUTION_IPC_LANE_DATA,
    )


def test_ipc_handoff_dispatch_service_applies_lane_routing_override() -> None:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    route_table.upsert_route(target="remote.node", target_id="execution.alpha#1")
    ipc = _IpcPort()
    service = DefaultExecutionIpcHandoffDispatchService(
        execution_ipc=ipc,
        route_table=route_table,
        lane_routing=_LaneRouting(lane=EXECUTION_IPC_LANE_LOG),
    )

    dispatched = service.dispatch_envelope(
        Envelope(payload={"v": 1}, target="remote.node"),
        source_group="execution.root",
    )

    assert dispatched is True
    assert ipc.sends[0]["target_id"] == compose_execution_ipc_worker_target_id(
        "execution.alpha#1",
        lane=EXECUTION_IPC_LANE_LOG,
    )


def test_ipc_handoff_dispatch_service_returns_false_on_routing_error() -> None:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    route_table.upsert_route(target="system.cp.leaf_stop", target_id="execution.alpha#1")
    ipc = _IpcPort()
    service = DefaultExecutionIpcHandoffDispatchService(
        execution_ipc=ipc,
        route_table=route_table,
        lane_routing=_LaneRouting(raise_error=True),
    )

    dispatched = service.dispatch_envelope(
        Envelope(payload={"kind": "stop"}, target="system.cp.leaf_stop"),
        source_group="execution.root",
    )

    assert dispatched is False
    assert ipc.sends == []
