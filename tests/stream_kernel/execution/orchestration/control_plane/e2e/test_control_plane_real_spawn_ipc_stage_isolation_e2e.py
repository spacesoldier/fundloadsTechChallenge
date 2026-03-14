from __future__ import annotations

import multiprocessing as mp
import time
from dataclasses import dataclass

from stream_kernel.execution.orchestration.control_plane.e2e_workers import (
    leaf_linear_pipeline_worker_for_target,
    observability_boundary_batch_sink_worker,
)
from stream_kernel.execution.orchestration.control_plane.root.boundary_execution_service import (
    DefaultControlPlaneRootBoundaryExecutionService,
)
from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
    DefaultControlPlaneRootBoundaryHandoffService,
)
from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
    DefaultControlPlaneRootLeafIngressService,
)
from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import (
    PipeExecutionIpcTransportAdapter,
)
from stream_kernel.execution.transport.handoff.ipc_route_table_service import (
    InMemoryExecutionIpcRouteTableService,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    EXECUTION_IPC_LANE_LOG,
    EXECUTION_IPC_LANE_METRIC,
    EXECUTION_IPC_LANE_TRACE,
    ExecutionIpcControlSignal,
    compose_execution_ipc_worker_target_id,
)
from stream_kernel.execution.transport.ipc.ipc_transport_service import (
    ExecutionIpcTransportCoordinatorService,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneGroupSpec,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafBoundaryOutputsEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)
from stream_kernel.platform.services.runtime.lifecycle import (
    LocalExecutionWorkerLifecycleService,
)
from stream_kernel.routing.envelope import Envelope
from tests.stream_kernel.execution.orchestration.control_plane.leaf_ingress_helpers import (
    drain_worker_replies,
)


@dataclass(slots=True)
class _Runtime:
    adapter: PipeExecutionIpcTransportAdapter
    ipc: ExecutionIpcTransportCoordinatorService
    ingress: DefaultControlPlaneRootLeafIngressService
    state: InMemoryControlPlaneStateService
    lifecycle: LocalExecutionWorkerLifecycleService


def _build_runtime(*, groups: tuple[ControlPlaneGroupSpec, ...]) -> _Runtime:
    endpoint_registry = InMemoryKvStore()
    worker_registry = InMemoryKvStore()
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(ControlPlaneLaunchPlanEvent(plan=ControlPlaneLaunchPlan(groups=groups)))
    adapter = PipeExecutionIpcTransportAdapter(
        codec="pickle",
        endpoint_registry=endpoint_registry,
        context=mp.get_context("spawn"),
    )
    ipc = ExecutionIpcTransportCoordinatorService(adapter=adapter, endpoint_registry=endpoint_registry)
    ingress = DefaultControlPlaneRootLeafIngressService(state=state, execution_ipc=ipc)
    lifecycle = LocalExecutionWorkerLifecycleService(
        worker_registry=worker_registry,
        execution_ipc=ipc,
        context=mp.get_context("spawn"),
    )
    return _Runtime(adapter=adapter, ipc=ipc, ingress=ingress, state=state, lifecycle=lifecycle)


def _spawn_linear_worker(
    *,
    runtime: _Runtime,
    worker_id: str,
    target_group: str,
    stage_name: str,
    next_target: str | None,
    observability_target: str | None = None,
    observability_multiplier: int = 0,
) -> None:
    runtime.lifecycle.spawn_worker(
        target_id=worker_id,
        target=leaf_linear_pipeline_worker_for_target,
        args=(
            worker_id,
            target_group,
            stage_name,
            next_target,
            observability_target,
            int(observability_multiplier),
        ),
        name=f"sk:e2e:{worker_id}",
        daemon=True,
        start=True,
        stop_event_position=0,
        child_endpoint_position=1,
        close_child_in_parent=True,
    )


def _spawn_observability_worker(
    *,
    runtime: _Runtime,
    worker_id: str,
    target_group: str,
) -> None:
    runtime.lifecycle.spawn_worker(
        target_id=worker_id,
        target=observability_boundary_batch_sink_worker,
        args=(worker_id, target_group),
        name=f"sk:e2e:{worker_id}",
        daemon=True,
        start=True,
        stop_event_position=0,
        child_endpoint_position=1,
        close_child_in_parent=True,
    )


def _wait_config_applied(
    *,
    runtime: _Runtime,
    worker_id: str,
    timeout_seconds: float = 3.0,
) -> None:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        drain_worker_replies(
            runtime.ingress,
            worker_id=worker_id,
            timeout_seconds=0.01,
            max_items=64,
        )
        if any(
            isinstance(event, ControlPlaneLeafConfigAckEvent)
            and event.worker_id == worker_id
            and event.status == "applied"
            for event in runtime.state.events()
        ):
            return
        time.sleep(0.001)
    raise AssertionError(f"timed out waiting for config ack: {worker_id}")


def _send_execute_command(
    *,
    runtime: _Runtime,
    worker_id: str,
    target_group: str,
    inputs: tuple[object, ...],
    request_id: str = "test:req",
) -> None:
    runtime.ipc.send(
        compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_DATA),
        ControlPlaneLeafBoundaryExecuteCommand(
            target_group=target_group,
            worker_id=worker_id,
            request_id=request_id,
            inputs=inputs,
            finalize=True,
        ),
        no_reply=True,
    )


def _poll_worker_data_payloads(
    *,
    runtime: _Runtime,
    worker_id: str,
    min_items: int,
    timeout_seconds: float,
) -> list[object]:
    payloads: list[object] = []
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        payload = runtime.ingress.poll_next_leaf_ingress_for_worker_lane(
            worker_id=worker_id,
            lane=EXECUTION_IPC_LANE_DATA,
            timeout_seconds=0.01,
        )
        if payload is None:
            if len(payloads) >= min_items:
                return payloads
            continue
        payloads.append(payload)
        if len(payloads) >= min_items:
            return payloads
    return payloads


def _stop_worker_and_wait_ack(
    *,
    runtime: _Runtime,
    worker_id: str,
    target_group: str,
    timeout_seconds: float = 2.0,
) -> None:
    command_id = f"stop:{worker_id}"
    runtime.ipc.send(
        compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_CONTROL),
        ControlPlaneLeafStopCommand(
            target_group=target_group,
            worker_id=worker_id,
            command_id=command_id,
        ),
        no_reply=True,
    )
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        drain_worker_replies(
            runtime.ingress,
            worker_id=worker_id,
            timeout_seconds=0.01,
            max_items=64,
        )
        if any(
            isinstance(event, ControlPlaneLeafStopAckEvent)
            and event.worker_id == worker_id
            and event.command_id == command_id
            and event.status == "accepted"
            for event in runtime.state.events()
        ):
            return
        time.sleep(0.001)
    raise AssertionError(f"timed out waiting for stop ack: {worker_id}")


def _cleanup_runtime(runtime: _Runtime, worker_ids: tuple[str, ...]) -> None:
    for worker_id in worker_ids:
        _ = runtime.lifecycle.stop_worker(
            worker_id,
            graceful_timeout_seconds=0.1,
            terminate_timeout_seconds=0.2,
        )
        handle = runtime.lifecycle.resolve_worker(worker_id)
        if handle is not None and handle.process.is_alive():
            handle.process.terminate()
            handle.process.join(timeout=0.2)
    runtime.adapter.close()


def _build_root_handoff(runtime: _Runtime) -> DefaultControlPlaneRootBoundaryHandoffService:
    route_table = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    return DefaultControlPlaneRootBoundaryHandoffService(
        root_boundary=DefaultControlPlaneRootBoundaryExecutionService(execution_ipc=runtime.ipc),
        route_table=route_table,
        timeout_seconds=2.0,
        stream_batch_max_items=1,
        observability_batch_max_items=64,
    )


def _pump_root_io(
    *,
    runtime: _Runtime,
    handoff: DefaultControlPlaneRootBoundaryHandoffService,
    worker_ids: tuple[str, ...],
    max_cycles: int = 256,
) -> list[tuple[str, object]]:
    collected: list[tuple[str, object]] = []
    lanes = (
        EXECUTION_IPC_LANE_CONTROL,
        EXECUTION_IPC_LANE_DATA,
        EXECUTION_IPC_LANE_TRACE,
        EXECUTION_IPC_LANE_LOG,
        EXECUTION_IPC_LANE_METRIC,
    )
    for _ in range(max(1, int(max_cycles))):
        had_payload = False
        for worker_id in worker_ids:
            source_group = worker_id.rsplit("#", 1)[0]
            for lane in lanes:
                payload = runtime.ingress.poll_next_leaf_ingress_for_worker_lane(
                    worker_id=worker_id,
                    lane=lane,
                    timeout_seconds=0.0,
                )
                if payload is None:
                    continue
                had_payload = True
                if isinstance(payload, Envelope):
                    handoff.drain_external_deliveries(
                        envelopes=[payload],
                        source_group=source_group,
                    )
                    continue
                if isinstance(payload, ControlPlaneLeafBoundaryOutputsEvent):
                    for item in payload.outputs:
                        collected.append((worker_id, item))
                    continue
                runtime.ingress.dispatch_polled_leaf_ingress(
                    worker_id=worker_id,
                    payload=payload,
                    lane=lane,
                )
        if not had_payload:
            break
    return collected


def test_real_spawn_ingress_stage_isolation_e2e() -> None:
    runtime = _build_runtime(
        groups=(ControlPlaneGroupSpec(group_name="execution.ingress", workers=1, nodes=("ingress.node",)),)
    )
    worker_id = "execution.ingress#1"
    target_group = "execution.ingress"
    _spawn_linear_worker(
        runtime=runtime,
        worker_id=worker_id,
        target_group=target_group,
        stage_name="ingress",
        next_target="transform.node",
    )
    try:
        _wait_config_applied(runtime=runtime, worker_id=worker_id)
        _send_execute_command(
            runtime=runtime,
            worker_id=worker_id,
            target_group=target_group,
            inputs=(
                Envelope(payload={"id": 1}, target="ingress.node", trace_id="trace-1"),
                Envelope(payload={"id": "t"}, target="ingress.node", trace_id="trace-t", tombstone=True),
            ),
        )
        outputs = _poll_worker_data_payloads(runtime=runtime, worker_id=worker_id, min_items=2, timeout_seconds=3.0)
        assert len(outputs) >= 2
        envelopes = [item for item in outputs if isinstance(item, Envelope)]
        assert any(
            envelope.target == "transform.node"
            and isinstance(envelope.payload, dict)
            and envelope.payload.get("id") == 1
            for envelope in envelopes
        )
        assert any(
            envelope.target == "transform.node"
            and envelope.tombstone is True
            for envelope in envelopes
        )
        _stop_worker_and_wait_ack(runtime=runtime, worker_id=worker_id, target_group=target_group)
    finally:
        _cleanup_runtime(runtime, (worker_id,))


def test_real_spawn_transform_stage_isolation_e2e() -> None:
    runtime = _build_runtime(
        groups=(ControlPlaneGroupSpec(group_name="execution.transform", workers=1, nodes=("transform.node",)),)
    )
    worker_id = "execution.transform#1"
    target_group = "execution.transform"
    _spawn_linear_worker(
        runtime=runtime,
        worker_id=worker_id,
        target_group=target_group,
        stage_name="transform",
        next_target="egress.node",
    )
    try:
        _wait_config_applied(runtime=runtime, worker_id=worker_id)
        _send_execute_command(
            runtime=runtime,
            worker_id=worker_id,
            target_group=target_group,
            inputs=(
                Envelope(payload={"id": 5, "stage": "ingress"}, target="transform.node", trace_id="trace-5"),
                Envelope(payload={"id": "t"}, target="transform.node", trace_id="trace-t", tombstone=True),
            ),
        )
        outputs = _poll_worker_data_payloads(runtime=runtime, worker_id=worker_id, min_items=2, timeout_seconds=3.0)
        envelopes = [item for item in outputs if isinstance(item, Envelope)]
        assert any(
            envelope.target == "egress.node"
            and isinstance(envelope.payload, dict)
            and envelope.payload.get("id") == 5
            and envelope.payload.get("stage") == "transform"
            for envelope in envelopes
        )
        assert any(envelope.target == "egress.node" and envelope.tombstone is True for envelope in envelopes)
        _stop_worker_and_wait_ack(runtime=runtime, worker_id=worker_id, target_group=target_group)
    finally:
        _cleanup_runtime(runtime, (worker_id,))


def test_real_spawn_ingress_transform_chain_with_root_e2e() -> None:
    runtime = _build_runtime(
        groups=(
            ControlPlaneGroupSpec(group_name="execution.ingress", workers=1, nodes=("ingress.node",)),
            ControlPlaneGroupSpec(group_name="execution.transform", workers=1, nodes=("transform.node",)),
        )
    )
    ingress_worker_id = "execution.ingress#1"
    transform_worker_id = "execution.transform#1"
    _spawn_linear_worker(
        runtime=runtime,
        worker_id=ingress_worker_id,
        target_group="execution.ingress",
        stage_name="ingress",
        next_target="transform.node",
    )
    _spawn_linear_worker(
        runtime=runtime,
        worker_id=transform_worker_id,
        target_group="execution.transform",
        stage_name="transform",
        next_target="egress.node",
    )
    try:
        _wait_config_applied(runtime=runtime, worker_id=ingress_worker_id)
        _wait_config_applied(runtime=runtime, worker_id=transform_worker_id)

        _send_execute_command(
            runtime=runtime,
            worker_id=ingress_worker_id,
            target_group="execution.ingress",
            inputs=(
                Envelope(payload={"id": 11}, target="ingress.node", trace_id="trace-11"),
                Envelope(payload={"id": 12}, target="ingress.node", trace_id="trace-12"),
                Envelope(payload={"id": "t"}, target="ingress.node", trace_id="trace-t", tombstone=True),
            ),
        )
        ingress_outputs = _poll_worker_data_payloads(
            runtime=runtime,
            worker_id=ingress_worker_id,
            min_items=3,
            timeout_seconds=3.0,
        )
        ingress_envelopes = [item for item in ingress_outputs if isinstance(item, Envelope)]
        assert len(ingress_envelopes) >= 3

        for index, envelope in enumerate(ingress_envelopes, start=1):
            _send_execute_command(
                runtime=runtime,
                worker_id=transform_worker_id,
                target_group="execution.transform",
                request_id=f"transform:{index}",
                inputs=(envelope,),
            )
        transform_outputs = _poll_worker_data_payloads(
            runtime=runtime,
            worker_id=transform_worker_id,
            min_items=3,
            timeout_seconds=3.0,
        )
        transform_envelopes = [item for item in transform_outputs if isinstance(item, Envelope)]
        ids = sorted(
            int(envelope.payload["id"])
            for envelope in transform_envelopes
            if isinstance(envelope.payload, dict) and isinstance(envelope.payload.get("id"), int)
        )
        assert ids == [11, 12]
        assert any(envelope.tombstone is True for envelope in transform_envelopes)

        _stop_worker_and_wait_ack(
            runtime=runtime,
            worker_id=ingress_worker_id,
            target_group="execution.ingress",
        )
        _stop_worker_and_wait_ack(
            runtime=runtime,
            worker_id=transform_worker_id,
            target_group="execution.transform",
        )
    finally:
        _cleanup_runtime(runtime, (ingress_worker_id, transform_worker_id))


def test_real_spawn_egress_stage_isolation_e2e() -> None:
    runtime = _build_runtime(
        groups=(ControlPlaneGroupSpec(group_name="execution.egress", workers=1, nodes=("egress.node",)),)
    )
    worker_id = "execution.egress#1"
    target_group = "execution.egress"
    _spawn_linear_worker(
        runtime=runtime,
        worker_id=worker_id,
        target_group=target_group,
        stage_name="egress",
        next_target=None,
    )
    try:
        _wait_config_applied(runtime=runtime, worker_id=worker_id)
        _send_execute_command(
            runtime=runtime,
            worker_id=worker_id,
            target_group=target_group,
            inputs=(
                Envelope(payload={"id": 21, "stage": "transform"}, target="egress.node", trace_id="trace-21"),
                Envelope(payload={"id": "t"}, target="egress.node", trace_id="trace-t", tombstone=True),
            ),
        )
        outputs = _poll_worker_data_payloads(runtime=runtime, worker_id=worker_id, min_items=2, timeout_seconds=3.0)
        boundary_events = [item for item in outputs if isinstance(item, ControlPlaneLeafBoundaryOutputsEvent)]
        assert any(
            any(
                isinstance(output, dict)
                and output.get("kind") == "data"
                and output.get("id") == 21
                for output in event.outputs
            )
            for event in boundary_events
        )
        assert any(
            any(
                isinstance(output, dict)
                and output.get("kind") == "tombstone"
                for output in event.outputs
            )
            for event in boundary_events
        )
        _stop_worker_and_wait_ack(runtime=runtime, worker_id=worker_id, target_group=target_group)
    finally:
        _cleanup_runtime(runtime, (worker_id,))


def test_real_spawn_observability_stage_isolation_e2e() -> None:
    runtime = _build_runtime(
        groups=(
            ControlPlaneGroupSpec(
                group_name="system.observability",
                workers=1,
                nodes=("system.obs.trace_dispatch",),
            ),
        )
    )
    worker_id = "system.observability#1"
    target_group = "system.observability"
    _spawn_observability_worker(runtime=runtime, worker_id=worker_id, target_group=target_group)
    try:
        _wait_config_applied(runtime=runtime, worker_id=worker_id)
        _send_execute_command(
            runtime=runtime,
            worker_id=worker_id,
            target_group=target_group,
            inputs=tuple({"trace": index} for index in range(40)),
        )
        outputs = _poll_worker_data_payloads(runtime=runtime, worker_id=worker_id, min_items=1, timeout_seconds=3.0)
        boundary_events = [item for item in outputs if isinstance(item, ControlPlaneLeafBoundaryOutputsEvent)]
        assert len(boundary_events) >= 1
        assert any(
            any(
                isinstance(output, dict)
                and output.get("kind") == "obs_batch"
                and output.get("count") == 40
                for output in event.outputs
            )
            for event in boundary_events
        )
        _stop_worker_and_wait_ack(runtime=runtime, worker_id=worker_id, target_group=target_group)
    finally:
        _cleanup_runtime(runtime, (worker_id,))


def test_real_spawn_full_pipeline_ingress_transform_egress_e2e() -> None:
    runtime = _build_runtime(
        groups=(
            ControlPlaneGroupSpec(group_name="execution.ingress", workers=1, nodes=("ingress.node",)),
            ControlPlaneGroupSpec(group_name="execution.transform", workers=1, nodes=("transform.node",)),
            ControlPlaneGroupSpec(group_name="execution.egress", workers=1, nodes=("egress.node",)),
        )
    )
    handoff = _build_root_handoff(runtime)
    handoff.route_table.upsert_route(target="ingress.node", target_id="execution.ingress#1")
    handoff.route_table.upsert_route(target="transform.node", target_id="execution.transform#1")
    handoff.route_table.upsert_route(target="egress.node", target_id="execution.egress#1")
    worker_ids = ("execution.ingress#1", "execution.transform#1", "execution.egress#1")
    _spawn_linear_worker(
        runtime=runtime,
        worker_id="execution.ingress#1",
        target_group="execution.ingress",
        stage_name="ingress",
        next_target="transform.node",
    )
    _spawn_linear_worker(
        runtime=runtime,
        worker_id="execution.transform#1",
        target_group="execution.transform",
        stage_name="transform",
        next_target="egress.node",
    )
    _spawn_linear_worker(
        runtime=runtime,
        worker_id="execution.egress#1",
        target_group="execution.egress",
        stage_name="egress",
        next_target=None,
    )
    try:
        for worker_id in worker_ids:
            _wait_config_applied(runtime=runtime, worker_id=worker_id)
        record_count = 150
        handoff.drain_external_deliveries(
            envelopes=[
                *[
                    Envelope(payload={"id": index}, target="ingress.node", trace_id=f"trace-{index}")
                    for index in range(record_count)
                ],
                Envelope(payload={"id": "tombstone"}, target="ingress.node", trace_id="trace-t", tombstone=True),
            ],
            source_group="execution.root",
        )
        processed_ids: list[int] = []
        tombstone_seen = False
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            outputs = _pump_root_io(runtime=runtime, handoff=handoff, worker_ids=worker_ids)
            for origin_worker_id, item in outputs:
                if origin_worker_id != "execution.egress#1" or not isinstance(item, dict):
                    continue
                if item.get("kind") == "data" and isinstance(item.get("id"), int):
                    processed_ids.append(int(item["id"]))
                if item.get("kind") == "tombstone":
                    tombstone_seen = True
            if tombstone_seen and len(processed_ids) >= record_count:
                break
            time.sleep(0.001)
        assert tombstone_seen is True
        assert len(processed_ids) == record_count
        assert sorted(processed_ids) == list(range(record_count))
        _stop_worker_and_wait_ack(
            runtime=runtime,
            worker_id="execution.ingress#1",
            target_group="execution.ingress",
        )
        _stop_worker_and_wait_ack(
            runtime=runtime,
            worker_id="execution.transform#1",
            target_group="execution.transform",
        )
        _stop_worker_and_wait_ack(
            runtime=runtime,
            worker_id="execution.egress#1",
            target_group="execution.egress",
        )
    finally:
        _cleanup_runtime(runtime, worker_ids)


def test_real_spawn_full_pipeline_with_observability_e2e() -> None:
    runtime = _build_runtime(
        groups=(
            ControlPlaneGroupSpec(group_name="execution.ingress", workers=1, nodes=("ingress.node",)),
            ControlPlaneGroupSpec(group_name="execution.transform", workers=1, nodes=("transform.node",)),
            ControlPlaneGroupSpec(group_name="execution.egress", workers=1, nodes=("egress.node",)),
            ControlPlaneGroupSpec(
                group_name="system.observability",
                workers=1,
                nodes=("system.obs.trace_dispatch",),
            ),
        )
    )
    handoff = _build_root_handoff(runtime)
    handoff.route_table.upsert_route(target="ingress.node", target_id="execution.ingress#1")
    handoff.route_table.upsert_route(target="transform.node", target_id="execution.transform#1")
    handoff.route_table.upsert_route(target="egress.node", target_id="execution.egress#1")
    handoff.route_table.upsert_route(
        target="system.obs.trace_dispatch",
        target_id="system.observability#1",
    )
    worker_ids = (
        "execution.ingress#1",
        "execution.transform#1",
        "execution.egress#1",
        "system.observability#1",
    )
    obs_multiplier = 4
    _spawn_linear_worker(
        runtime=runtime,
        worker_id="execution.ingress#1",
        target_group="execution.ingress",
        stage_name="ingress",
        next_target="transform.node",
        observability_target="system.obs.trace_dispatch",
        observability_multiplier=obs_multiplier,
    )
    _spawn_linear_worker(
        runtime=runtime,
        worker_id="execution.transform#1",
        target_group="execution.transform",
        stage_name="transform",
        next_target="egress.node",
        observability_target="system.obs.trace_dispatch",
        observability_multiplier=obs_multiplier,
    )
    _spawn_linear_worker(
        runtime=runtime,
        worker_id="execution.egress#1",
        target_group="execution.egress",
        stage_name="egress",
        next_target=None,
        observability_target="system.obs.trace_dispatch",
        observability_multiplier=obs_multiplier,
    )
    _spawn_observability_worker(
        runtime=runtime,
        worker_id="system.observability#1",
        target_group="system.observability",
    )
    try:
        for worker_id in worker_ids:
            _wait_config_applied(runtime=runtime, worker_id=worker_id)
        record_count = 20
        handoff.drain_external_deliveries(
            envelopes=[
                *[
                    Envelope(payload={"id": index}, target="ingress.node", trace_id=f"trace-o-{index}")
                    for index in range(record_count)
                ],
                Envelope(payload={"id": "tombstone"}, target="ingress.node", trace_id="trace-o-t", tombstone=True),
            ],
            source_group="execution.root",
        )
        processed_ids: list[int] = []
        tombstone_seen = False
        obs_count_total = 0
        expected_obs_count_total = record_count * 3 * obs_multiplier
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            outputs = _pump_root_io(runtime=runtime, handoff=handoff, worker_ids=worker_ids)
            for origin_worker_id, item in outputs:
                if not isinstance(item, dict):
                    continue
                if origin_worker_id == "execution.egress#1":
                    if item.get("kind") == "data" and isinstance(item.get("id"), int):
                        processed_ids.append(int(item["id"]))
                    if item.get("kind") == "tombstone":
                        tombstone_seen = True
                    continue
                if origin_worker_id == "system.observability#1":
                    if item.get("kind") == "obs_batch" and isinstance(item.get("count"), int):
                        obs_count_total += int(item["count"])
            if (
                tombstone_seen
                and len(processed_ids) >= record_count
                and obs_count_total >= expected_obs_count_total
            ):
                break
            time.sleep(0.001)
        assert tombstone_seen is True
        assert len(processed_ids) == record_count
        assert sorted(processed_ids) == list(range(record_count))
        assert obs_count_total == expected_obs_count_total
        for worker_id, target_group in (
            ("execution.ingress#1", "execution.ingress"),
            ("execution.transform#1", "execution.transform"),
            ("execution.egress#1", "execution.egress"),
            ("system.observability#1", "system.observability"),
        ):
            _stop_worker_and_wait_ack(
                runtime=runtime,
                worker_id=worker_id,
                target_group=target_group,
            )
    finally:
        _cleanup_runtime(runtime, worker_ids)


def test_real_spawn_ack_signal_for_data_lane_arrives_via_control_lane() -> None:
    endpoint_registry = InMemoryKvStore()
    parent = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=endpoint_registry)
    child = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=endpoint_registry)

    worker_id = "execution.alpha#1"
    data_target = compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_DATA)
    control_target = compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_CONTROL)

    _parent_data, child_data = parent.allocate_endpoints(data_target)
    _parent_control, child_control = parent.allocate_endpoints(control_target)
    child.attach_endpoint(child_data)
    child.attach_endpoint(child_control)

    acked: list[ExecutionIpcControlSignal | int] = []

    def _on_ack(payload: object) -> None:
        if isinstance(payload, (ExecutionIpcControlSignal, int)):
            acked.append(payload)

    child.register_ack_handler(control_target, _on_ack)
    parent.enable_ack(True)
    child.enable_ack(True)

    child_data.send({"id": 1})
    _ = parent.recv(data_target, timeout=0.05)

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not acked:
        _ = child.recv(control_target, timeout=0.01)
        time.sleep(0.001)

    parent.close()
    child.close()

    assert len(acked) >= 1
    latest = acked[-1]
    assert isinstance(latest, ExecutionIpcControlSignal)
    assert latest.kind == "ack"
    assert latest.target_id == data_target
    assert latest.count >= 1
