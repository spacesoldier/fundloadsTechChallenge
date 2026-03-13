from __future__ import annotations

import multiprocessing as mp
import time

from stream_kernel.execution.orchestration.control_plane.root.boundary_execution_service import (
    DefaultControlPlaneRootBoundaryExecutionService,
)
from stream_kernel.execution.orchestration.control_plane.e2e_workers import (
    leaf_pipeline_stage_worker_for_target,
)
from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
    DefaultControlPlaneRootBoundaryHandoffService,
)
from stream_kernel.execution.orchestration.control_plane.root.leaf_command_service import (
    DefaultControlPlaneRootLeafCommandService,
)
from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
    DefaultControlPlaneRootLeafIngressService,
)
from stream_kernel.execution.transport.handoff.ipc_route_table_service import (
    InMemoryExecutionIpcRouteTableService,
)
from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import (
    PipeExecutionIpcTransportAdapter,
)
from stream_kernel.execution.transport.ipc.ipc_transport_service import (
    ExecutionIpcTransportCoordinatorService,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneGroupSpec,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafConfigAckEvent,
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


def test_real_spawn_ipc_pipeline_cross_process_two_stages() -> None:
    endpoint_registry = InMemoryKvStore()
    worker_registry = InMemoryKvStore()
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.stage1",
                        workers=1,
                        nodes=("stage1.node",),
                    ),
                    ControlPlaneGroupSpec(
                        group_name="execution.stage2",
                        workers=1,
                        nodes=("stage2.node",),
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
        [
            {"name": "execution.stage1", "nodes": ["stage1.node"]},
            {"name": "execution.stage2", "nodes": ["stage2.node"]},
        ]
    )
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    route_table.upsert_route(target="stage1.node", target_id="execution.stage1#1")
    route_table.upsert_route(target="stage2.node", target_id="execution.stage2#1")
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
        for worker_id, target_group, stage_name in (
            ("execution.stage1#1", "execution.stage1", "stage1"),
            ("execution.stage2#1", "execution.stage2", "stage2"),
        ):
            handle = lifecycle.spawn_worker(
                target_id=worker_id,
                target=leaf_pipeline_stage_worker_for_target,
                args=(worker_id, target_group, stage_name),
                name=f"sk:test-pipeline-{worker_id}",
                daemon=True,
                start=True,
                stop_event_position=0,
                child_endpoint_position=1,
                close_child_in_parent=True,
            )
            handles.append(handle)

        acks_stage1 = _wait_group_config_applied(
            state=state,
            ingress=ingress,
            target_group="execution.stage1",
            worker_ids=("execution.stage1#1",),
            timeout_seconds=4.0,
        )
        acks_stage2 = _wait_group_config_applied(
            state=state,
            ingress=ingress,
            target_group="execution.stage2",
            worker_ids=("execution.stage2#1",),
            timeout_seconds=4.0,
        )
        assert acks_stage1["execution.stage1#1"].status == "applied"
        assert acks_stage2["execution.stage2#1"].status == "applied"

        terminal = root_handoff.drain_external_deliveries(
            envelopes=[Envelope(payload={"value": 2}, target="stage1.node", trace_id="trace-pipeline")],
            source_group="execution.root",
        )
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline and not terminal:
            for worker_id in ("execution.stage1#1", "execution.stage2#1"):
                drain_worker_replies(
                    ingress,
                    worker_id=worker_id,
                    timeout_seconds=0.002,
                    max_items=128,
                )
            completed = root_handoff.drain_completed_deliveries()
            if completed:
                terminal.extend(completed)
            if terminal:
                break
            time.sleep(0.01)

        assert terminal, "pipeline produced no terminal outputs"
        assert {"stage": "stage2", "worker_id": "execution.stage2#1", "final_value": 30} in terminal
        events = state.events()
        assert any(isinstance(item, ControlPlaneLeafBoundaryResultEvent) for item in events)
    finally:
        for handle in handles:
            lifecycle.stop_worker(
                handle.target_id,
                graceful_timeout_seconds=0.05,
                terminate_timeout_seconds=0.2,
            )
        adapter.close()
