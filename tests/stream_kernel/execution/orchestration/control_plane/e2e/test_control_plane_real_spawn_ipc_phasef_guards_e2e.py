from __future__ import annotations

import multiprocessing as mp
import os
import time

from stream_kernel.execution.orchestration.control_plane.e2e_workers import (
    leaf_boundary_worker,
    leaf_handshake_worker,
)
from stream_kernel.execution.orchestration.control_plane.root.boundary_execution_service import (
    DefaultControlPlaneRootBoundaryExecutionService,
)
from stream_kernel.execution.orchestration.control_plane.root.leaf_command_service import (
    DefaultControlPlaneRootLeafCommandService,
)
from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
    DefaultControlPlaneRootLeafIngressService,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import PipeExecutionIpcTransportAdapter
from stream_kernel.execution.transport.ipc.ipc_transport_service import (
    ExecutionIpcTransportCoordinatorService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneGroupSpec,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
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
    poll_interval_seconds: float = 0.002,
) -> dict[str, ControlPlaneLeafConfigAckEvent]:
    pending = {worker_id for worker_id in worker_ids if isinstance(worker_id, str) and worker_id}
    if not pending:
        return {}
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    resolved: dict[str, ControlPlaneLeafConfigAckEvent] = {}
    while pending:
        remaining = max(0.0, deadline - time.monotonic())
        for worker_id in list(pending):
            drain_worker_replies(ingress, 
                worker_id=worker_id,
                timeout_seconds=min(remaining, max(0.0, float(poll_interval_seconds))),
                max_items=64,
            )
        events = state.events()
        for worker_id in list(pending):
            ack = _find_latest_ack(events=events, target_group=target_group, worker_id=worker_id)
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
            missing = ", ".join(sorted(pending))
            raise RuntimeError(f"timed out waiting for leaf config acks: {missing}")
        time.sleep(max(0.0, float(poll_interval_seconds)))
    return resolved


def _find_latest_ack(
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


def _build_state() -> InMemoryControlPlaneStateService:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(ControlPlaneGroupSpec(group_name="execution.alpha", workers=1, nodes=("node.a",)),)
            )
        )
    )
    return state


def test_real_spawn_ipc_handshake_repeats_stably_for_multiple_rounds() -> None:
    # Phase F stability gate: repeated root<->leaf startup handshake must remain deterministic.
    for _ in range(3):
        endpoint_registry = InMemoryKvStore()
        worker_registry = InMemoryKvStore()
        state = _build_state()
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
        handle = lifecycle.spawn_worker(
            target_id="execution.alpha#1",
            target=leaf_handshake_worker,
            args=(),
            name="sk:test-phasef-handshake",
            daemon=True,
            start=True,
            stop_event_position=0,
            child_endpoint_position=1,
            close_child_in_parent=True,
        )
        try:
            acks = _wait_group_config_applied(
                state=state,
                ingress=ingress,
                target_group="execution.alpha",
                worker_ids=("execution.alpha#1",),
                timeout_seconds=2.0,
                poll_interval_seconds=0.002,
            )
            assert acks["execution.alpha#1"].status == "applied"
        finally:
            lifecycle.stop_worker(handle.target_id, graceful_timeout_seconds=0.1, terminate_timeout_seconds=0.5)
            adapter.close()


def test_real_spawn_ipc_boundary_roundtrip_respects_latency_guard() -> None:
    # Phase F latency guard: detect severe regressions in root<->leaf boundary roundtrip.
    max_latency_seconds = float(os.getenv("SK_PHASEF_BOUNDARY_LATENCY_MAX_SECONDS", "8.0"))

    endpoint_registry = InMemoryKvStore()
    worker_registry = InMemoryKvStore()
    state = _build_state()
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
    handle = lifecycle.spawn_worker(
        target_id="execution.alpha#1",
        target=leaf_boundary_worker,
        args=(),
        name="sk:test-phasef-latency",
        daemon=True,
        start=True,
        stop_event_position=0,
        child_endpoint_position=1,
        close_child_in_parent=True,
    )
    try:
        _wait_group_config_applied(
            state=state,
            ingress=ingress,
            target_group="execution.alpha",
            worker_ids=("execution.alpha#1",),
            timeout_seconds=2.0,
            poll_interval_seconds=0.002,
        )
        started = time.monotonic()
        routing = root_boundary.execute_boundary_on_leaf(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            request_id="phasef-latency-req",
            inputs=({"payload": 1}, {"payload": 2}),
            timeout_seconds=2.0,
            finalize=True,
            wait_for_result=False,
        )
        result = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            drain_worker_replies(ingress, 
                worker_id="execution.alpha#1",
                timeout_seconds=0.002,
                max_items=64,
            )
            result = reply_waiter.wait_for_leaf_boundary_result(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                request_id="phasef-latency-req",
                timeout_seconds=0.0,
            )
            if result is not None:
                break
            time.sleep(0.002)
        assert result is not None
        elapsed = time.monotonic() - started

        assert routing.terminal_outputs == []
        assert elapsed <= max_latency_seconds
    finally:
        lifecycle.stop_worker(handle.target_id, graceful_timeout_seconds=0.1, terminate_timeout_seconds=0.5)
        adapter.close()
