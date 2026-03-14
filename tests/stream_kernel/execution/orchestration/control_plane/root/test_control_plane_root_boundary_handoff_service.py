from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.transport.handoff.ipc_route_table_service import (
    InMemoryExecutionIpcRouteTableService,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.router import RoutingResult


@dataclass(slots=True)
class _ProcessGroupRouter:
    groups: dict[str, str]
    calls: list[dict[str, object]] = field(default_factory=list)

    def resolve_group_for_target(self, *, target: str, source_group: str | None) -> str:
        self.calls.append({"target": target, "source_group": source_group})
        return self.groups[target]


@dataclass(slots=True)
class _BoundaryExec:
    calls: list[dict[str, object]] = field(default_factory=list)

    def execute_boundary_on_leaf(self, **kwargs: object) -> RoutingResult:
        self.calls.append(dict(kwargs))
        return RoutingResult(local_deliveries=[], boundary_deliveries=[], terminal_outputs=[])


def _preload_routes(route_table: InMemoryExecutionIpcRouteTableService, groups: dict[str, str]) -> None:
    for target, group in groups.items():
        route_table.upsert_route(target=target, target_id=f"{group}#1")


def test_root_boundary_handoff_service_translates_external_envelopes_to_boundary_dispatch_inputs() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
        DefaultControlPlaneRootBoundaryHandoffService,
    )
    from stream_kernel.execution.orchestration.lifecycle import BoundaryDispatchInput

    router = _ProcessGroupRouter(groups={"remote.node": "execution.alpha"})
    boundary = _BoundaryExec()
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    _preload_routes(route_table, router.groups)
    service = DefaultControlPlaneRootBoundaryHandoffService(
        root_boundary=boundary,
        route_table=route_table,
        timeout_seconds=1.5,
    )

    terminal = service.drain_external_deliveries(
        envelopes=[
            Envelope(
                payload={"amount": 10},
                trace_id="t-1",
                target="remote.node",
                reply_to="http:req-1",
                span_id="s-1",
            )
        ],
        source_group="execution.root",
    )

    assert terminal == []
    assert router.calls == []
    assert route_table.resolve_route(target="remote.node") == "execution.alpha#1"
    assert len(boundary.calls) == 1
    call = boundary.calls[0]
    assert call["target_group"] == "execution.alpha"
    assert call["worker_id"] == "execution.alpha#1"
    assert call["timeout_seconds"] == 1.5
    assert call["finalize"] is True
    assert call["wait_for_result"] is False
    assert isinstance(call["inputs"], tuple)
    assert len(call["inputs"]) == 1
    item = call["inputs"][0]
    assert isinstance(item, BoundaryDispatchInput)
    assert item.dispatch_group == "execution.alpha"
    assert item.target == "remote.node"
    assert item.trace_id == "t-1"
    assert item.reply_to == "http:req-1"
    assert item.span_id == "s-1"


def test_root_boundary_handoff_service_dispatches_observability_batches_as_fire_and_forget() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
        DefaultControlPlaneRootBoundaryHandoffService,
    )

    router = _ProcessGroupRouter(groups={"system.obs.trace_dispatch": "system.observability"})
    boundary = _BoundaryExec()
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    _preload_routes(route_table, router.groups)
    service = DefaultControlPlaneRootBoundaryHandoffService(
        root_boundary=boundary,
        route_table=route_table,
        timeout_seconds=1.0,
    )

    _ = service.drain_external_deliveries(
        envelopes=[Envelope(payload={"trace": 1}, trace_id="t-obs", target="system.obs.trace_dispatch")]
    )

    assert len(boundary.calls) == 1
    assert boundary.calls[0]["target_group"] == "system.observability"
    assert boundary.calls[0]["finalize"] is False
    assert boundary.calls[0]["wait_for_result"] is False


def test_root_boundary_handoff_service_batches_non_observability_dispatches_by_stream_batch_size() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
        DefaultControlPlaneRootBoundaryHandoffService,
    )

    router = _ProcessGroupRouter(groups={"remote.node": "execution.alpha"})
    boundary = _BoundaryExec()
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    _preload_routes(route_table, router.groups)
    service = DefaultControlPlaneRootBoundaryHandoffService(
        root_boundary=boundary,
        route_table=route_table,
        timeout_seconds=1.0,
        stream_batch_max_items=2,
    )

    _ = service.drain_external_deliveries(
        envelopes=[
            Envelope(payload={"v": 1}, trace_id="t-1", target="remote.node"),
            Envelope(payload={"v": 2}, trace_id="t-2", target="remote.node"),
            Envelope(payload={"v": 3}, trace_id="t-3", target="remote.node"),
        ]
    )

    assert len(boundary.calls) == 2
    first, second = boundary.calls
    assert first["target_group"] == "execution.alpha"
    assert second["target_group"] == "execution.alpha"
    assert len(first["inputs"]) == 2
    assert len(second["inputs"]) == 1
    assert first["finalize"] is True
    assert second["finalize"] is True
    assert first["wait_for_result"] is False
    assert second["wait_for_result"] is False


def test_root_boundary_handoff_service_chunks_observability_dispatches_by_observability_batch_size() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
        DefaultControlPlaneRootBoundaryHandoffService,
    )

    router = _ProcessGroupRouter(groups={"system.obs.trace_dispatch": "system.observability"})
    boundary = _BoundaryExec()
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    _preload_routes(route_table, router.groups)
    service = DefaultControlPlaneRootBoundaryHandoffService(
        root_boundary=boundary,
        route_table=route_table,
        timeout_seconds=1.0,
        observability_batch_max_items=2,
    )

    _ = service.drain_external_deliveries(
        envelopes=[
            Envelope(payload={"trace": 1}, trace_id="t-obs-1", target="system.obs.trace_dispatch"),
            Envelope(payload={"trace": 2}, trace_id="t-obs-2", target="system.obs.trace_dispatch"),
            Envelope(payload={"trace": 3}, trace_id="t-obs-3", target="system.obs.trace_dispatch"),
        ]
    )

    assert len(boundary.calls) == 2
    first, second = boundary.calls
    assert first["target_group"] == "system.observability"
    assert second["target_group"] == "system.observability"
    assert first["finalize"] is False
    assert second["finalize"] is False
    assert first["wait_for_result"] is False
    assert second["wait_for_result"] is False
    assert len(first["inputs"]) == 2
    assert len(second["inputs"]) == 1


def test_root_boundary_handoff_service_uses_route_table_without_router_call_when_route_exists() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
        DefaultControlPlaneRootBoundaryHandoffService,
    )

    router = _ProcessGroupRouter(groups={"remote.node": "execution.alpha"})
    boundary = _BoundaryExec()
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    route_table.upsert_route(target="remote.node", target_id="execution.alpha#1")
    service = DefaultControlPlaneRootBoundaryHandoffService(
        root_boundary=boundary,
        route_table=route_table,
        timeout_seconds=1.0,
    )

    _ = service.drain_external_deliveries(
        envelopes=[Envelope(payload={"v": 1}, trace_id="t-1", target="remote.node")]
    )

    assert router.calls == []
    assert len(boundary.calls) == 1
    assert boundary.calls[0]["target_group"] == "execution.alpha"
    assert boundary.calls[0]["worker_id"] == "execution.alpha#1"


def test_root_boundary_handoff_service_generates_unique_request_ids_across_calls() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
        DefaultControlPlaneRootBoundaryHandoffService,
    )

    router = _ProcessGroupRouter(groups={"remote.node": "execution.alpha"})
    boundary = _BoundaryExec()
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    _preload_routes(route_table, router.groups)
    service = DefaultControlPlaneRootBoundaryHandoffService(
        root_boundary=boundary,
        route_table=route_table,
        timeout_seconds=1.0,
    )

    _ = service.drain_external_deliveries(
        envelopes=[Envelope(payload={"v": 1}, trace_id="t-1", target="remote.node")]
    )
    _ = service.drain_external_deliveries(
        envelopes=[Envelope(payload={"v": 2}, trace_id="t-1", target="remote.node")]
    )

    assert len(boundary.calls) == 2
    request_ids = [str(call["request_id"]) for call in boundary.calls]
    assert request_ids[0] != request_ids[1]
    assert request_ids[0].startswith("boundary:t-1:0:remote.node:")
    assert request_ids[1].startswith("boundary:t-1:0:remote.node:")
