from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Callable

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryResultEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.router import RoutingResult
from stream_kernel.execution.transport.handoff.ipc_route_table_service import (
    InMemoryExecutionIpcRouteTableService,
)


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
        payload = {"request_id": str(kwargs["request_id"]), "worker_id": str(kwargs["worker_id"])}
        return RoutingResult(local_deliveries=[], boundary_deliveries=[], terminal_outputs=[payload])


@dataclass(slots=True)
class _ReplyIngress:
    on_drain: Callable[[str], None] | None = None
    calls: list[dict[str, object]] = field(default_factory=list)

    def drain_worker_replies(self, *, worker_id: str, timeout_seconds: float = 0.0, max_items: int = 64) -> int:
        self.calls.append(
            {
                "worker_id": worker_id,
                "timeout_seconds": timeout_seconds,
                "max_items": max_items,
            }
        )
        if callable(self.on_drain):
            self.on_drain(worker_id)
        return 1


def _preload_routes(
    route_table: InMemoryExecutionIpcRouteTableService,
    groups: dict[str, str],
) -> None:
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
        process_group_router=router,
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

    assert len(terminal) == 1
    assert terminal[0]["worker_id"] == "execution.alpha#1"
    assert isinstance(terminal[0]["request_id"], str)
    assert terminal[0]["request_id"].startswith("boundary:t-1:0:remote.node:")
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
        process_group_router=router,
        root_boundary=boundary,
        route_table=route_table,
        timeout_seconds=1.0,
    )

    _ = service.drain_external_deliveries(
        envelopes=[
            Envelope(payload={"trace": 1}, trace_id="t-obs", target="system.obs.trace_dispatch"),
        ]
    )

    assert len(boundary.calls) == 1
    assert boundary.calls[0]["target_group"] == "system.observability"
    assert boundary.calls[0]["finalize"] is False
    assert boundary.calls[0]["wait_for_result"] is False


def test_root_boundary_handoff_service_batches_observability_dispatches_into_single_boundary_call() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
        DefaultControlPlaneRootBoundaryHandoffService,
    )
    from stream_kernel.execution.orchestration.lifecycle import BoundaryDispatchInput

    router = _ProcessGroupRouter(groups={"system.obs.trace_dispatch": "system.observability"})
    boundary = _BoundaryExec()
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    _preload_routes(route_table, router.groups)
    service = DefaultControlPlaneRootBoundaryHandoffService(
        process_group_router=router,
        root_boundary=boundary,
        route_table=route_table,
        timeout_seconds=1.0,
    )

    _ = service.drain_external_deliveries(
        envelopes=[
            Envelope(payload={"trace": 1}, trace_id="t-obs-1", target="system.obs.trace_dispatch"),
            Envelope(payload={"trace": 2}, trace_id="t-obs-2", target="system.obs.trace_dispatch"),
        ]
    )

    assert len(boundary.calls) == 1
    call = boundary.calls[0]
    assert call["target_group"] == "system.observability"
    assert call["finalize"] is False
    assert call["wait_for_result"] is False
    assert isinstance(call["inputs"], tuple)
    assert len(call["inputs"]) == 2
    assert all(isinstance(item, BoundaryDispatchInput) for item in call["inputs"])
    assert call["inputs"][0].trace_id == "t-obs-1"
    assert call["inputs"][1].trace_id == "t-obs-2"


def test_root_boundary_handoff_service_batches_non_observability_dispatches_by_stream_batch_size() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
        DefaultControlPlaneRootBoundaryHandoffService,
    )
    from stream_kernel.execution.orchestration.lifecycle import BoundaryDispatchInput

    router = _ProcessGroupRouter(groups={"remote.node": "execution.alpha"})
    boundary = _BoundaryExec()
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    _preload_routes(route_table, router.groups)
    service = DefaultControlPlaneRootBoundaryHandoffService(
        process_group_router=router,
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
    assert isinstance(first["inputs"], tuple) and len(first["inputs"]) == 2
    assert isinstance(second["inputs"], tuple) and len(second["inputs"]) == 1
    assert all(isinstance(item, BoundaryDispatchInput) for item in first["inputs"])
    assert all(isinstance(item, BoundaryDispatchInput) for item in second["inputs"])
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
        process_group_router=router,
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


def test_root_boundary_handoff_service_drains_completed_boundary_results_from_state() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
        DefaultControlPlaneRootBoundaryHandoffService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())

    boundary = _BoundaryExec()

    def _append_reply(_worker_id: str) -> None:
        if not boundary.calls:
            return
        request_id = str(boundary.calls[-1]["request_id"])
        state.append_event(
            ControlPlaneLeafBoundaryResultEvent(
                target_group="system.observability",
                worker_id="system.observability#1",
                request_id=request_id,
                status="completed",
                outputs=("out-1",),
            )
        )

    router = _ProcessGroupRouter(groups={"system.obs.trace_dispatch": "system.observability"})
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    _preload_routes(route_table, router.groups)
    service = DefaultControlPlaneRootBoundaryHandoffService(
        process_group_router=router,
        root_boundary=boundary,
        route_table=route_table,
        state=state,
        timeout_seconds=1.0,
    )

    terminal = service.drain_external_deliveries(
        envelopes=[Envelope(payload={"trace": 1}, trace_id="t-obs-1", target="system.obs.trace_dispatch")],
    )

    assert service.has_inflight_deliveries() is False
    assert "out-1" not in terminal


def test_root_boundary_handoff_service_does_not_track_observability_inflight_requests() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
        DefaultControlPlaneRootBoundaryHandoffService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    boundary = _BoundaryExec()

    def _append_reply(_worker_id: str) -> None:
        if not boundary.calls:
            return
        request_id = str(boundary.calls[-1]["request_id"])
        state.append_event(
            ControlPlaneLeafBoundaryResultEvent(
                target_group="system.observability",
                worker_id="system.observability#1",
                request_id=request_id,
                status="completed",
                outputs=(),
            )
        )

    router = _ProcessGroupRouter(groups={"system.obs.trace_dispatch": "system.observability"})
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    _preload_routes(route_table, router.groups)
    service = DefaultControlPlaneRootBoundaryHandoffService(
        process_group_router=router,
        root_boundary=boundary,
        route_table=route_table,
        state=state,
        timeout_seconds=1.0,
    )

    _ = service.drain_external_deliveries(
        envelopes=[
            Envelope(payload={"trace": 1}, trace_id="t-obs", target="system.obs.trace_dispatch"),
        ]
    )

    assert len(boundary.calls) == 1
    assert boundary.calls[0]["finalize"] is False
    assert service.has_inflight_deliveries() is False


def test_root_boundary_handoff_service_keeps_inflight_for_stream_status_until_completed() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
        DefaultControlPlaneRootBoundaryHandoffService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    boundary = _BoundaryExec()
    router = _ProcessGroupRouter(groups={"remote.node": "execution.alpha"})
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    _preload_routes(route_table, router.groups)

    service = DefaultControlPlaneRootBoundaryHandoffService(
        process_group_router=router,
        root_boundary=boundary,
        route_table=route_table,
        state=state,
        timeout_seconds=1.0,
    )

    _ = service.drain_external_deliveries(
        envelopes=[Envelope(payload={"v": 1}, trace_id="t-1", target="remote.node")],
    )
    assert service.has_inflight_deliveries() is True
    assert len(boundary.calls) == 1
    request_id = str(boundary.calls[0]["request_id"])

    state.append_event(
        ControlPlaneLeafBoundaryResultEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            request_id=request_id,
            status="stream",
            outputs=("chunk-1",),
        )
    )
    drained_stream = service.drain_completed_deliveries()
    assert drained_stream == ["chunk-1"]
    assert service.has_inflight_deliveries() is True

    state.append_event(
        ControlPlaneLeafBoundaryResultEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            request_id=request_id,
            status="completed",
            outputs=("chunk-2",),
        )
    )
    drained_final = service.drain_completed_deliveries()
    assert drained_final == ["chunk-2"]
    assert service.has_inflight_deliveries() is False


def test_root_boundary_handoff_service_uses_route_table_without_router_call_when_route_exists() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
        DefaultControlPlaneRootBoundaryHandoffService,
    )

    router = _ProcessGroupRouter(groups={"remote.node": "execution.alpha"})
    boundary = _BoundaryExec()
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    route_table.upsert_route(target="remote.node", target_id="execution.alpha#1")
    service = DefaultControlPlaneRootBoundaryHandoffService(
        process_group_router=router,
        root_boundary=boundary,
        route_table=route_table,
        timeout_seconds=1.0,
    )

    _ = service.drain_external_deliveries(
        envelopes=[Envelope(payload={"v": 1}, trace_id="t-1", target="remote.node")],
    )

    assert router.calls == []
    assert len(boundary.calls) == 1
    assert boundary.calls[0]["target_group"] == "execution.alpha"
    assert boundary.calls[0]["worker_id"] == "execution.alpha#1"


def test_root_boundary_handoff_service_marks_observability_inflight_as_non_blocking_for_replay() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
        DefaultControlPlaneRootBoundaryHandoffService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    router = _ProcessGroupRouter(groups={"system.obs.trace_dispatch": "system.observability"})
    boundary = _BoundaryExec()
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    _preload_routes(route_table, router.groups)
    service = DefaultControlPlaneRootBoundaryHandoffService(
        process_group_router=router,
        root_boundary=boundary,
        route_table=route_table,
        state=state,
        timeout_seconds=1.0,
        inflight_idle_timeout_seconds=10.0,
    )

    _ = service.drain_external_deliveries(
        envelopes=[Envelope(payload={"trace": 1}, trace_id="t-obs-1", target="system.obs.trace_dispatch")],
    )

    assert service.has_inflight_deliveries() is False
    assert service.has_replay_blocking_inflight_deliveries() is False


def test_root_boundary_handoff_service_does_not_create_stale_inflight_requests_for_observability() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
        DefaultControlPlaneRootBoundaryHandoffService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    router = _ProcessGroupRouter(groups={"system.obs.trace_dispatch": "system.observability"})
    boundary = _BoundaryExec()
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    _preload_routes(route_table, router.groups)
    service = DefaultControlPlaneRootBoundaryHandoffService(
        process_group_router=router,
        root_boundary=boundary,
        route_table=route_table,
        state=state,
        timeout_seconds=1.0,
        inflight_idle_timeout_seconds=0.02,
    )

    _ = service.drain_external_deliveries(
        envelopes=[Envelope(payload={"trace": 1}, trace_id="t-obs-1", target="system.obs.trace_dispatch")],
    )
    assert service.has_inflight_deliveries() is False

    time.sleep(0.05)
    drained = service.drain_completed_deliveries()

    assert drained == []
    assert service.has_inflight_deliveries() is False
    timeout_events = [
        item
        for item in state.events()
        if isinstance(item, dict) and item.get("kind") == "control_plane.boundary.inflight_timeout"
    ]
    assert timeout_events == []


def test_root_boundary_handoff_service_generates_unique_request_ids_across_calls() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
        DefaultControlPlaneRootBoundaryHandoffService,
    )

    router = _ProcessGroupRouter(groups={"remote.node": "execution.alpha"})
    boundary = _BoundaryExec()
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    _preload_routes(route_table, router.groups)
    service = DefaultControlPlaneRootBoundaryHandoffService(
        process_group_router=router,
        root_boundary=boundary,
        route_table=route_table,
        timeout_seconds=1.0,
    )

    _ = service.drain_external_deliveries(
        envelopes=[Envelope(payload={"v": 1}, trace_id="t-1", target="remote.node")],
    )
    _ = service.drain_external_deliveries(
        envelopes=[Envelope(payload={"v": 2}, trace_id="t-1", target="remote.node")],
    )

    assert len(boundary.calls) == 2
    request_ids = [str(call["request_id"]) for call in boundary.calls]
    assert request_ids[0] != request_ids[1]
    assert request_ids[0].startswith("boundary:t-1:0:remote.node:")
    assert request_ids[1].startswith("boundary:t-1:0:remote.node:")


def test_root_boundary_handoff_service_cascades_completed_envelopes_without_waiting_for_replay() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
        DefaultControlPlaneRootBoundaryHandoffService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    router = _ProcessGroupRouter(
        groups={
            "compute_time_keys": "execution.features",
            "evaluate_policies": "execution.policy",
        }
    )
    boundary = _BoundaryExec()
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    _preload_routes(route_table, router.groups)

    def _append_reply(worker_id: str) -> None:
        request_id: str | None = None
        for call in reversed(boundary.calls):
            if str(call.get("worker_id")) == worker_id:
                request_id = str(call["request_id"])
                break
        if not isinstance(request_id, str) or not request_id:
            return
        if worker_id == "execution.features#1":
            state.append_event(
                ControlPlaneLeafBoundaryResultEvent(
                    target_group="execution.features",
                    worker_id=worker_id,
                    request_id=request_id,
                    status="completed",
                    outputs=(
                        Envelope(
                            payload={"stage": "policy"},
                            trace_id="t-cascade",
                            target="evaluate_policies",
                        ),
                    ),
                )
            )
            return
        if worker_id == "execution.policy#1":
            state.append_event(
                ControlPlaneLeafBoundaryResultEvent(
                    target_group="execution.policy",
                    worker_id=worker_id,
                    request_id=request_id,
                    status="completed",
                    outputs=("done",),
                )
            )

    service = DefaultControlPlaneRootBoundaryHandoffService(
        process_group_router=router,
        root_boundary=boundary,
        route_table=route_table,
        state=state,
        timeout_seconds=1.0,
    )

    _ = service.drain_external_deliveries(
        envelopes=[
            Envelope(
                payload={"stage": "features"},
                trace_id="t-cascade",
                target="compute_time_keys",
            )
        ],
        source_group="execution.ingress",
    )
    first_request_id = str(boundary.calls[0]["request_id"])
    state.append_event(
        ControlPlaneLeafBoundaryResultEvent(
            target_group="execution.features",
            worker_id="execution.features#1",
            request_id=first_request_id,
            status="completed",
            outputs=(
                Envelope(
                    payload={"stage": "policy"},
                    trace_id="t-cascade",
                    target="evaluate_policies",
                ),
            ),
        )
    )

    _ = service.drain_pending_dispatch()
    second_request_id = str(boundary.calls[1]["request_id"])
    state.append_event(
        ControlPlaneLeafBoundaryResultEvent(
            target_group="execution.policy",
            worker_id="execution.policy#1",
            request_id=second_request_id,
            status="completed",
            outputs=("done",),
        )
    )
    terminal = service.drain_pending_dispatch()

    assert len(boundary.calls) == 2
    assert boundary.calls[0]["worker_id"] == "execution.features#1"
    assert boundary.calls[1]["worker_id"] == "execution.policy#1"
    assert "done" in terminal
