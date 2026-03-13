from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.transport.handoff.ipc_handoff_dispatch_service import (
    DefaultExecutionIpcHandoffDispatchService,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    EXECUTION_IPC_LANE_LOG,
    compose_execution_ipc_worker_target_id,
)
from stream_kernel.execution.transport.handoff.ipc_route_table_service import (
    InMemoryExecutionIpcRouteTableService,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneGroupSpec,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafStopCommand,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class _IpcPort:
    sends: list[dict[str, object]] = field(default_factory=list)
    fail_for: dict[str, int] = field(default_factory=dict)
    pending_for: dict[str, int] = field(default_factory=dict)

    def send(self, target_id: str, payload: object, *, no_reply: bool = False):
        remaining = self.fail_for.get(target_id, 0)
        if remaining > 0:
            self.fail_for[target_id] = remaining - 1
            raise RuntimeError(f"send failed for {target_id}")
        self.sends.append(
            {
                "target_id": target_id,
                "payload": payload,
                "no_reply": no_reply,
            }
        )
        return None

    def metrics(self, target_id: str) -> dict[str, object]:
        return {"pending_outbound": int(self.pending_for.get(target_id, 0))}


@dataclass(slots=True)
class _Router:
    groups: dict[str, str]
    calls: list[dict[str, object]] = field(default_factory=list)

    def resolve_group_for_target(self, *, target: str, source_group: str | None) -> str:
        self.calls.append({"target": target, "source_group": source_group})
        return self.groups[target]


def _state_with_workers() -> InMemoryControlPlaneStateService:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.ingress",
                        workers=1,
                        nodes=("source:source",),
                    ),
                    ControlPlaneGroupSpec(
                        group_name="execution.features",
                        workers=1,
                        nodes=("compute_features",),
                    ),
                    ControlPlaneGroupSpec(
                        group_name="system.observability",
                        workers=1,
                        nodes=("system.obs.log_dispatch",),
                    ),
                )
            )
        )
    )
    state.append_event(
        ControlPlaneLeafConfigAckEvent(
            target_group="execution.ingress",
            worker_id="execution.ingress#1",
            config_id="cfg:ingress",
            status="applied",
        )
    )
    state.append_event(
        ControlPlaneLeafConfigAckEvent(
            target_group="execution.features",
            worker_id="execution.features#1",
            config_id="cfg:features",
            status="applied",
        )
    )
    state.append_event(
        ControlPlaneLeafConfigAckEvent(
            target_group="system.observability",
            worker_id="system.observability#1",
            config_id="cfg:obs",
            status="applied",
        )
    )
    return state


def test_ipc_handoff_dispatch_service_uses_route_table_when_present() -> None:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    route_table.upsert_route(target="remote.node", target_id="execution.alpha#1")
    router = _Router(groups={"remote.node": "execution.alpha"})
    ipc = _IpcPort()
    state = _state_with_workers()
    service = DefaultExecutionIpcHandoffDispatchService(
        execution_ipc=ipc,
        process_group_router=router,
        route_table=route_table,
        control_plane_state=state,
    )

    dispatched = service.dispatch_envelope(
        Envelope(payload={"v": 1}, target="remote.node"),
        source_group="execution.root",
    )

    assert dispatched is True
    assert router.calls == []
    assert ipc.sends == [
        {
            "target_id": compose_execution_ipc_worker_target_id(
                "execution.alpha#1",
                lane=EXECUTION_IPC_LANE_DATA,
            ),
            "payload": {"v": 1},
            "no_reply": True,
        }
    ]


def test_ipc_handoff_dispatch_service_rejects_envelope_when_route_is_missing() -> None:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    router = _Router(groups={"remote.node": "execution.beta"})
    ipc = _IpcPort()
    state = _state_with_workers()
    service = DefaultExecutionIpcHandoffDispatchService(
        execution_ipc=ipc,
        process_group_router=router,
        route_table=route_table,
        control_plane_state=state,
    )

    dispatched = service.dispatch_envelope(
        Envelope(payload={"v": 2}, target="remote.node"),
        source_group="execution.root",
    )

    assert dispatched is False
    assert router.calls == []
    assert route_table.resolve_route(target="remote.node") is None
    assert ipc.sends == []


def test_ipc_handoff_dispatch_service_skips_non_targeted_envelope() -> None:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    router = _Router(groups={})
    ipc = _IpcPort()
    state = _state_with_workers()
    service = DefaultExecutionIpcHandoffDispatchService(
        execution_ipc=ipc,
        process_group_router=router,
        route_table=route_table,
        control_plane_state=state,
    )

    dispatched = service.dispatch_envelope(
        Envelope(payload={"v": 3}, target=None),
        source_group="execution.root",
    )

    assert dispatched is False
    assert router.calls == []
    assert ipc.sends == []


def test_ipc_handoff_dispatch_service_broadcast_filters_group() -> None:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    router = _Router(groups={})
    ipc = _IpcPort()
    state = _state_with_workers()
    service = DefaultExecutionIpcHandoffDispatchService(
        execution_ipc=ipc,
        process_group_router=router,
        route_table=route_table,
        control_plane_state=state,
    )

    result = service.dispatch_broadcast(
        Envelope(payload={"kind": "start"}, target="system.cp.leaf_start_work"),
        target_group="execution.features",
        policy="best_effort",
        broadcast_id="b1",
    )

    assert result.broadcast_id == "b1"
    assert result.total == 1
    assert result.accepted == 1
    assert result.failed == 0
    assert [item["target_id"] for item in ipc.sends] == [
        compose_execution_ipc_worker_target_id(
            "execution.features#1",
            lane=EXECUTION_IPC_LANE_CONTROL,
        )
    ]
    payload = ipc.sends[0]["payload"]
    assert isinstance(payload, dict)
    assert payload["broadcast_id"] == "b1"
    assert payload["worker_id"] == "execution.features#1"


def test_ipc_handoff_dispatch_service_broadcast_excludes_observability_by_default() -> None:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    router = _Router(groups={})
    ipc = _IpcPort()
    state = _state_with_workers()
    service = DefaultExecutionIpcHandoffDispatchService(
        execution_ipc=ipc,
        process_group_router=router,
        route_table=route_table,
        control_plane_state=state,
    )

    result = service.dispatch_broadcast(
        Envelope(payload={"kind": "start"}, target="system.cp.leaf_start_work"),
    )

    assert result.total == 2
    assert sorted(item["target_id"] for item in ipc.sends) == [
        compose_execution_ipc_worker_target_id(
            "execution.features#1",
            lane=EXECUTION_IPC_LANE_CONTROL,
        ),
        compose_execution_ipc_worker_target_id(
            "execution.ingress#1",
            lane=EXECUTION_IPC_LANE_CONTROL,
        ),
    ]


def test_ipc_handoff_dispatch_service_broadcast_all_or_nothing_stops_on_failure() -> None:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    router = _Router(groups={})
    ipc = _IpcPort(
        fail_for={
            compose_execution_ipc_worker_target_id(
                "execution.features#1",
                lane=EXECUTION_IPC_LANE_CONTROL,
            ): 1
        }
    )
    state = _state_with_workers()
    service = DefaultExecutionIpcHandoffDispatchService(
        execution_ipc=ipc,
        process_group_router=router,
        route_table=route_table,
        control_plane_state=state,
        control_retry_attempts=0,
    )

    result = service.dispatch_broadcast(
        Envelope(payload={"kind": "start"}, target="system.cp.leaf_start_work"),
        policy="all_or_nothing",
        broadcast_id="b-stop",
    )

    assert result.policy == "all_or_nothing"
    assert result.total == 2
    assert result.accepted == 0
    assert result.failed == 2
    assert result.failed_workers == ("execution.features#1", "execution.ingress#1")
    assert ipc.sends == []


def test_ipc_handoff_dispatch_service_observability_broadcast_is_best_effort_without_retry() -> None:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    router = _Router(groups={})
    ipc = _IpcPort(
        fail_for={
            compose_execution_ipc_worker_target_id(
                "system.observability#1",
                lane=EXECUTION_IPC_LANE_LOG,
            ): 1
        }
    )
    state = _state_with_workers()
    service = DefaultExecutionIpcHandoffDispatchService(
        execution_ipc=ipc,
        process_group_router=router,
        route_table=route_table,
        control_plane_state=state,
        control_retry_attempts=3,
    )

    result = service.dispatch_broadcast(
        Envelope(payload={"kind": "obs"}, target="system.obs.log_dispatch"),
        target_group="system.observability",
        include_observability=True,
        policy="all_or_nothing",
        broadcast_id="b-obs",
    )

    assert result.policy == "best_effort"
    assert result.total == 1
    assert result.accepted == 0
    assert result.failed == 1
    assert result.failed_workers == ("system.observability#1",)
    assert ipc.sends == []


def test_ipc_handoff_dispatch_service_broadcast_dataclass_payload_is_projected_per_worker() -> None:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    router = _Router(groups={})
    ipc = _IpcPort()
    state = _state_with_workers()
    service = DefaultExecutionIpcHandoffDispatchService(
        execution_ipc=ipc,
        process_group_router=router,
        route_table=route_table,
        control_plane_state=state,
    )

    result = service.dispatch_broadcast(
        Envelope(
            payload=ControlPlaneLeafStopCommand(
                target_group="execution",
                worker_id="template#0",
                command_id="runtime-stop:{worker_id}",
                reason="runtime_lifecycle.stop",
            ),
            target="system.cp.leaf_stop",
        ),
        policy="best_effort",
        broadcast_id="b-stop",
    )

    assert result.total == 2
    assert result.accepted == 2
    assert sorted(item["target_id"] for item in ipc.sends) == [
        compose_execution_ipc_worker_target_id(
            "execution.features#1",
            lane=EXECUTION_IPC_LANE_CONTROL,
        ),
        compose_execution_ipc_worker_target_id(
            "execution.ingress#1",
            lane=EXECUTION_IPC_LANE_CONTROL,
        ),
    ]
    payloads = [item["payload"] for item in ipc.sends]
    assert all(isinstance(item, ControlPlaneLeafStopCommand) for item in payloads)
    by_worker = {item.worker_id: item for item in payloads if isinstance(item, ControlPlaneLeafStopCommand)}
    assert sorted(by_worker.keys()) == ["execution.features#1", "execution.ingress#1"]
    assert by_worker["execution.features#1"].command_id == "runtime-stop:execution.features#1"
    assert by_worker["execution.ingress#1"].command_id == "runtime-stop:execution.ingress#1"


def test_ipc_handoff_dispatch_service_broadcast_control_lane_no_duplicate_on_pending() -> None:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    router = _Router(groups={})
    ipc = _IpcPort(
        pending_for={
            compose_execution_ipc_worker_target_id(
                "execution.features#1",
                lane=EXECUTION_IPC_LANE_CONTROL,
            ): 1
        }
    )
    state = _state_with_workers()
    service = DefaultExecutionIpcHandoffDispatchService(
        execution_ipc=ipc,
        process_group_router=router,
        route_table=route_table,
        control_plane_state=state,
        control_retry_attempts=1,
        control_retry_backoff_seconds=0.0,
    )

    result = service.dispatch_broadcast(
        Envelope(payload={"kind": "start"}, target="system.cp.leaf_start_work"),
        policy="best_effort",
        broadcast_id="b-drain",
    )

    assert result.total == 2
    assert result.accepted == 2
    assert result.failed == 0
    assert result.failed_workers == ()
    assert sorted(item["target_id"] for item in ipc.sends) == [
        compose_execution_ipc_worker_target_id(
            "execution.features#1",
            lane=EXECUTION_IPC_LANE_CONTROL,
        ),
        compose_execution_ipc_worker_target_id(
            "execution.ingress#1",
            lane=EXECUTION_IPC_LANE_CONTROL,
        ),
    ]
