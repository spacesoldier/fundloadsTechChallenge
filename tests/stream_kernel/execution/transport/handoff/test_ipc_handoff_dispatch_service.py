from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.transport.handoff.ipc_handoff_dispatch_service import (
    DefaultExecutionIpcHandoffDispatchService,
)
from stream_kernel.execution.transport.handoff.ipc_route_table_service import (
    InMemoryExecutionIpcRouteTableService,
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
class _Router:
    groups: dict[str, str]
    calls: list[dict[str, object]] = field(default_factory=list)

    def resolve_group_for_target(self, *, target: str, source_group: str | None) -> str:
        self.calls.append({"target": target, "source_group": source_group})
        return self.groups[target]


def test_ipc_handoff_dispatch_service_uses_route_table_when_present() -> None:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    route_table.upsert_route(target="remote.node", target_id="execution.alpha#1")
    router = _Router(groups={"remote.node": "execution.alpha"})
    ipc = _IpcPort()
    service = DefaultExecutionIpcHandoffDispatchService(
        execution_ipc=ipc,
        process_group_router=router,
        route_table=route_table,
    )

    dispatched = service.dispatch_envelope(
        Envelope(payload={"v": 1}, target="remote.node"),
        source_group="execution.root",
    )

    assert dispatched is True
    assert router.calls == []
    assert ipc.sends == [
        {"target_id": "execution.alpha#1", "payload": {"v": 1}, "no_reply": True}
    ]


def test_ipc_handoff_dispatch_service_populates_route_table_when_missing() -> None:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    router = _Router(groups={"remote.node": "execution.beta"})
    ipc = _IpcPort()
    service = DefaultExecutionIpcHandoffDispatchService(
        execution_ipc=ipc,
        process_group_router=router,
        route_table=route_table,
    )

    dispatched = service.dispatch_envelope(
        Envelope(payload={"v": 2}, target="remote.node"),
        source_group="execution.root",
    )

    assert dispatched is True
    assert router.calls == [{"target": "remote.node", "source_group": "execution.root"}]
    assert route_table.resolve_route(target="remote.node") == "execution.beta#1"
    assert ipc.sends == [
        {"target_id": "execution.beta#1", "payload": {"v": 2}, "no_reply": True}
    ]


def test_ipc_handoff_dispatch_service_skips_non_targeted_envelope() -> None:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    router = _Router(groups={})
    ipc = _IpcPort()
    service = DefaultExecutionIpcHandoffDispatchService(
        execution_ipc=ipc,
        process_group_router=router,
        route_table=route_table,
    )

    dispatched = service.dispatch_envelope(
        Envelope(payload={"v": 3}, target=None),
        source_group="execution.root",
    )

    assert dispatched is False
    assert router.calls == []
    assert ipc.sends == []
