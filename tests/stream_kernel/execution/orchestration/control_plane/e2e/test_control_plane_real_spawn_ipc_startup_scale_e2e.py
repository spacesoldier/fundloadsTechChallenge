from __future__ import annotations

import multiprocessing as mp
import time

import pytest

from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
    DefaultControlPlaneRootLeafIngressService,
)
from stream_kernel.execution.orchestration.control_plane.e2e_workers import (
    leaf_boundary_echo_worker_for_target,
    leaf_config_ack_worker_for_target,
)
from stream_kernel.execution.orchestration.control_plane.root.boundary_execution_service import (
    DefaultControlPlaneRootBoundaryExecutionService,
)
from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
    DefaultControlPlaneRootBoundaryHandoffService,
)
from stream_kernel.execution.orchestration.control_plane.root.leaf_command_service import (
    DefaultControlPlaneRootLeafCommandService,
)
from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import (
    PipeExecutionIpcTransportAdapter,
)
from stream_kernel.execution.transport.handoff.ipc_route_table_service import (
    InMemoryExecutionIpcRouteTableService,
)
from stream_kernel.execution.transport.ipc.ipc_transport_service import (
    ExecutionIpcTransportCoordinatorService,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneGroupSpec,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafHelloEvent,
)
from stream_kernel.platform.services.runtime.control_plane_reply_waiter import (
    DefaultControlPlaneReplyWaiterService,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)
from stream_kernel.platform.services.runtime.lifecycle import (
    LocalExecutionWorkerLifecycleService,
)
from stream_kernel.platform.services.runtime.process_group_router import (
    InMemoryProcessGroupRouterService,
)
from stream_kernel.routing.envelope import Envelope
from tests.stream_kernel.execution.orchestration.control_plane.leaf_ingress_helpers import (
    drain_worker_replies,
)

def _wait_group_config_applied(
    *,
    state: InMemoryControlPlaneStateService,
    ingress: DefaultControlPlaneRootLeafIngressService,
    target_group: str,
    worker_ids: tuple[str, ...],
    timeout_seconds: float,
) -> dict[str, ControlPlaneLeafConfigAckEvent]:
    pending = {worker_id for worker_id in worker_ids if isinstance(worker_id, str) and worker_id}
    resolved: dict[str, ControlPlaneLeafConfigAckEvent] = {}
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while pending:
        remaining = max(0.0, deadline - time.monotonic())
        for worker_id in list(pending):
            drain_worker_replies(
                ingress,
                worker_id=worker_id,
                timeout_seconds=min(remaining, 0.01),
                max_items=128,
            )
        events = state.events()
        for worker_id in list(pending):
            ack = _latest_ack(events=events, target_group=target_group, worker_id=worker_id)
            if ack is None:
                continue
            if ack.status == "applied":
                resolved[worker_id] = ack
                pending.remove(worker_id)
                continue
            if ack.status == "rejected":
                raise RuntimeError(ack.error or f"leaf config rejected for {worker_id}")
        if not pending:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(f"timed out waiting for acks: {sorted(pending)}")
        time.sleep(0.005)
    return resolved


def _latest_ack(
    *,
    events: list[object],
    target_group: str,
    worker_id: str,
) -> ControlPlaneLeafConfigAckEvent | None:
    for event in reversed(events):
        if not isinstance(event, ControlPlaneLeafConfigAckEvent):
            continue
        if event.target_group != target_group:
            continue
        if event.worker_id != worker_id:
            continue
        return event
    return None


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_real_spawn_ipc_startup_handshake_scales_for_parallel_workers(workers: int) -> None:
    endpoint_registry = InMemoryKvStore()
    worker_registry = InMemoryKvStore()
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.alpha",
                        workers=workers,
                        nodes=("node.simple",),
                    ),
                )
            )
        )
    )
    adapter = PipeExecutionIpcTransportAdapter(
        codec="pickle",
        endpoint_registry=endpoint_registry,
        context=mp.get_context("spawn"),
    )
    ipc = ExecutionIpcTransportCoordinatorService(adapter=adapter, endpoint_registry=endpoint_registry)
    lifecycle = LocalExecutionWorkerLifecycleService(
        worker_registry=worker_registry,
        execution_ipc=ipc,
        context=mp.get_context("spawn"),
    )
    ingress = DefaultControlPlaneRootLeafIngressService(state=state, execution_ipc=ipc)
    worker_ids = tuple(f"execution.alpha#{index + 1}" for index in range(workers))
    handles = []
    try:
        for worker_id in worker_ids:
            handle = lifecycle.spawn_worker(
                target_id=worker_id,
                target=leaf_config_ack_worker_for_target,
                args=(worker_id, "execution.alpha"),
                name=f"sk:test-scale-{worker_id}",
                daemon=True,
                start=True,
                stop_event_position=0,
                child_endpoint_position=1,
                close_child_in_parent=True,
            )
            handles.append(handle)

        resolved = _wait_group_config_applied(
            state=state,
            ingress=ingress,
            target_group="execution.alpha",
            worker_ids=worker_ids,
            timeout_seconds=4.0,
        )
        assert set(resolved.keys()) == set(worker_ids)
        assert all(ack.status == "applied" for ack in resolved.values())
        events = state.events()
        hello_count = sum(isinstance(item, ControlPlaneLeafHelloEvent) for item in events)
        card_count = sum(isinstance(item, ControlPlaneLeafConfigCardEvent) for item in events)
        ack_count = sum(
            isinstance(item, ControlPlaneLeafConfigAckEvent) and item.status == "applied"
            for item in events
        )
        assert hello_count >= workers
        assert card_count >= workers
        assert ack_count >= workers
    finally:
        for handle in handles:
            lifecycle.stop_worker(
                handle.target_id,
                graceful_timeout_seconds=0.05,
                terminate_timeout_seconds=0.2,
            )
        adapter.close()


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_real_spawn_ipc_boundary_roundtrip_scales_for_parallel_workers(workers: int) -> None:
    endpoint_registry = InMemoryKvStore()
    worker_registry = InMemoryKvStore()
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    worker_ids = tuple(f"execution.alpha#{index + 1}" for index in range(workers))
    nodes = tuple(f"node.simple.{index + 1}" for index in range(workers))
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.alpha",
                        workers=workers,
                        nodes=nodes,
                    ),
                )
            )
        )
    )
    adapter = PipeExecutionIpcTransportAdapter(
        codec="pickle",
        endpoint_registry=endpoint_registry,
        context=mp.get_context("spawn"),
    )
    ipc = ExecutionIpcTransportCoordinatorService(adapter=adapter, endpoint_registry=endpoint_registry)
    lifecycle = LocalExecutionWorkerLifecycleService(
        worker_registry=worker_registry,
        execution_ipc=ipc,
        context=mp.get_context("spawn"),
    )
    ingress = DefaultControlPlaneRootLeafIngressService(state=state, execution_ipc=ipc)
    reply_waiter = DefaultControlPlaneReplyWaiterService(state=state)
    root_leaf_commands = DefaultControlPlaneRootLeafCommandService(
        reply_waiter=reply_waiter,
        poll_interval_seconds=0.002,
    )
    root_boundary = DefaultControlPlaneRootBoundaryExecutionService(
        root_leaf_commands=root_leaf_commands,
        execution_ipc=ipc,
    )
    group_router = InMemoryProcessGroupRouterService()
    group_router.configure_process_groups(
        [{"name": "execution.alpha", "nodes": list(nodes)}]
    )
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    for node, worker_id in zip(nodes, worker_ids, strict=True):
        route_table.upsert_route(target=node, target_id=worker_id)
    root_handoff = DefaultControlPlaneRootBoundaryHandoffService(
        process_group_router=group_router,
        root_boundary=root_boundary,
        route_table=route_table,
        state=state,
        timeout_seconds=2.0,
    )
    ingress.root_boundary_handoff = root_handoff
    handles = []
    try:
        for worker_id in worker_ids:
            handle = lifecycle.spawn_worker(
                target_id=worker_id,
                target=leaf_boundary_echo_worker_for_target,
                args=(worker_id, "execution.alpha"),
                name=f"sk:test-boundary-scale-{worker_id}",
                daemon=True,
                start=True,
                stop_event_position=0,
                child_endpoint_position=1,
                close_child_in_parent=True,
            )
            handles.append(handle)

        resolved = _wait_group_config_applied(
            state=state,
            ingress=ingress,
            target_group="execution.alpha",
            worker_ids=worker_ids,
            timeout_seconds=4.0,
        )
        assert set(resolved.keys()) == set(worker_ids)
        envelopes = [
            Envelope(
                payload={"value": index + 1},
                target=node,
                trace_id=f"trace-scale-{index + 1}",
            )
            for index, node in enumerate(nodes)
        ]
        terminal = root_handoff.drain_external_deliveries(
            envelopes=envelopes,
            source_group="execution.root",
        )
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline and len(terminal) < workers:
            for worker_id in worker_ids:
                drain_worker_replies(
                    ingress,
                    worker_id=worker_id,
                    timeout_seconds=0.002,
                    max_items=128,
                )
            completed = root_handoff.drain_completed_deliveries()
            if completed:
                terminal.extend(completed)
            if len(terminal) >= workers:
                break
            time.sleep(0.01)

        assert len(terminal) >= workers
        for index, worker_id in enumerate(worker_ids):
            expected = {
                "worker_id": worker_id,
                "target_group": "execution.alpha",
                "value": index + 1,
            }
            assert expected in terminal
    finally:
        for handle in handles:
            lifecycle.stop_worker(
                handle.target_id,
                graceful_timeout_seconds=0.05,
                terminate_timeout_seconds=0.2,
            )
        adapter.close()
