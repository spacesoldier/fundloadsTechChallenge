from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.execution.orchestration.control_plane.root.system_nodes import (
    ControlPlaneRootLeafDrainReadyNode,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneRootLeafStopRequestEvent,
    ControlPlaneShutdownReadyEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class _Snapshot:
    expected_groups: tuple[str, ...]
    ready_groups: tuple[str, ...]

    @property
    def missing_groups(self) -> tuple[str, ...]:
        expected = set(self.expected_groups)
        ready = set(self.ready_groups)
        return tuple(sorted(expected - ready))


@dataclass(slots=True)
class _Readiness:
    emit_shutdown: bool
    snapshot: _Snapshot
    calls: int = 0

    def mark_leaf_ready(self, event: ControlPlaneLeafDrainReadyEvent) -> tuple[bool, _Snapshot]:
        _ = event
        self.calls += 1
        return (self.emit_shutdown, self.snapshot)


@dataclass(slots=True)
class _Adapter:
    queue_depth: int = 0
    outbound_queue_depth: int = 0
    pending_outbound: int = 0

    def metrics(self, _target: str) -> dict[str, int]:
        return {
            "queue_depth": self.queue_depth,
            "outbound_queue_depth": self.outbound_queue_depth,
            "pending_outbound": self.pending_outbound,
        }


def test_drain_ready_node_emits_stop_request_immediately_and_shutdown_ready_when_quorum_reached() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    node = ControlPlaneRootLeafDrainReadyNode(
        state=state,  # type: ignore[arg-type]
        shutdown_readiness=_Readiness(  # type: ignore[arg-type]
            emit_shutdown=True,
            snapshot=_Snapshot(
                expected_groups=("execution.ingress",),
                ready_groups=("execution.ingress",),
            ),
        ),
    )
    payload = ControlPlaneLeafDrainReadyEvent(
        target_group="execution.ingress",
        worker_id="execution.ingress#1",
        request_id="drain:ingress#1",
        tombstone_output=True,
    )

    produced = node(Envelope(payload=payload, target="system.cp.shutdown_leaf_ready"), None)

    stop_requests = [item for item in produced if isinstance(item, ControlPlaneRootLeafStopRequestEvent)]
    shutdown_events = [item for item in produced if isinstance(item, ControlPlaneShutdownReadyEvent)]
    assert len(stop_requests) == 1
    assert stop_requests[0].worker_id == "execution.ingress#1"
    assert stop_requests[0].reason == "control_plane.leaf_drain_ready"
    assert len(shutdown_events) == 1
    assert shutdown_events[0].expected_groups == ("execution.ingress",)
    assert shutdown_events[0].ready_groups == ("execution.ingress",)


def test_drain_ready_node_does_not_emit_duplicate_stop_request_for_worker() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneRootLeafStopRequestEvent(
            target_group="execution.ingress",
            worker_id="execution.ingress#1",
            command_id="runtime-stop:execution.ingress#1",
            reason="control_plane.leaf_drain_ready",
        )
    )
    node = ControlPlaneRootLeafDrainReadyNode(
        state=state,  # type: ignore[arg-type]
        shutdown_readiness=_Readiness(  # type: ignore[arg-type]
            emit_shutdown=False,
            snapshot=_Snapshot(
                expected_groups=("execution.ingress",),
                ready_groups=("execution.ingress",),
            ),
        ),
    )
    payload = ControlPlaneLeafDrainReadyEvent(
        target_group="execution.ingress",
        worker_id="execution.ingress#1",
        request_id="drain:ingress#1:duplicate",
        tombstone_output=True,
    )

    produced = node(Envelope(payload=payload, target="system.cp.shutdown_leaf_ready"), None)

    assert produced == []


def test_drain_ready_node_finalizes_even_when_ipc_backlog_is_not_empty() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    readiness = _Readiness(
        emit_shutdown=True,
        snapshot=_Snapshot(
            expected_groups=("execution.ingress",),
            ready_groups=("execution.ingress",),
        ),
    )
    control = _Adapter(queue_depth=1)
    data = _Adapter()
    trace = _Adapter()
    log = _Adapter()
    metric = _Adapter()
    node = ControlPlaneRootLeafDrainReadyNode(
        state=state,  # type: ignore[arg-type]
        shutdown_readiness=readiness,  # type: ignore[arg-type]
        control_lane_ipc=control,  # type: ignore[arg-type]
        data_lane_ipc=data,  # type: ignore[arg-type]
        trace_lane_ipc=trace,  # type: ignore[arg-type]
        log_lane_ipc=log,  # type: ignore[arg-type]
        metric_lane_ipc=metric,  # type: ignore[arg-type]
    )
    payload = ControlPlaneLeafDrainReadyEvent(
        target_group="execution.ingress",
        worker_id="execution.ingress#1",
        request_id="drain:ingress#1",
        tombstone_output=True,
    )

    produced = node(Envelope(payload=payload, target="system.cp.shutdown_leaf_ready"), None)

    stop_requests = [item for item in produced if isinstance(item, ControlPlaneRootLeafStopRequestEvent)]
    shutdown_events = [item for item in produced if isinstance(item, ControlPlaneShutdownReadyEvent)]
    assert len(stop_requests) == 1
    assert stop_requests[0].worker_id == "execution.ingress#1"
    assert len(shutdown_events) == 1
    assert readiness.calls == 1


def test_drain_ready_node_ignores_non_ingress_lane_backlog_for_regular_worker() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    readiness = _Readiness(
        emit_shutdown=True,
        snapshot=_Snapshot(
            expected_groups=("execution.policy",),
            ready_groups=("execution.policy",),
        ),
    )
    control = _Adapter()
    data = _Adapter()
    trace = _Adapter(queue_depth=9)
    log = _Adapter(pending_outbound=5)
    metric = _Adapter(outbound_queue_depth=3)
    node = ControlPlaneRootLeafDrainReadyNode(
        state=state,  # type: ignore[arg-type]
        shutdown_readiness=readiness,  # type: ignore[arg-type]
        control_lane_ipc=control,  # type: ignore[arg-type]
        data_lane_ipc=data,  # type: ignore[arg-type]
        trace_lane_ipc=trace,  # type: ignore[arg-type]
        log_lane_ipc=log,  # type: ignore[arg-type]
        metric_lane_ipc=metric,  # type: ignore[arg-type]
    )
    payload = ControlPlaneLeafDrainReadyEvent(
        target_group="execution.policy",
        worker_id="execution.policy#1",
        request_id="drain:policy#1",
        tombstone_output=True,
    )

    produced = node(Envelope(payload=payload, target="system.cp.shutdown_leaf_ready"), None)

    stop_requests = [item for item in produced if isinstance(item, ControlPlaneRootLeafStopRequestEvent)]
    shutdown_events = [item for item in produced if isinstance(item, ControlPlaneShutdownReadyEvent)]
    assert len(stop_requests) == 1
    assert len(shutdown_events) == 1
    assert readiness.calls == 1


def test_drain_ready_node_emits_stop_requests_for_all_ready_workers_on_shutdown_quorum() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        {
            "kind": "control_plane.shutdown.leaf_ready",
            "target_group": "execution.ingress",
            "worker_id": "execution.ingress#1",
            "request_id": "drain:ingress#1",
            "tombstone_output": True,
        }
    )
    readiness = _Readiness(
        emit_shutdown=True,
        snapshot=_Snapshot(
            expected_groups=("execution.ingress", "execution.policy"),
            ready_groups=("execution.ingress", "execution.policy"),
        ),
    )
    node = ControlPlaneRootLeafDrainReadyNode(
        state=state,  # type: ignore[arg-type]
        shutdown_readiness=readiness,  # type: ignore[arg-type]
    )
    payload = ControlPlaneLeafDrainReadyEvent(
        target_group="execution.policy",
        worker_id="execution.policy#1",
        request_id="drain:policy#1",
        tombstone_output=True,
    )

    produced = node(Envelope(payload=payload, target="system.cp.shutdown_leaf_ready"), None)

    stop_requests = [item for item in produced if isinstance(item, ControlPlaneRootLeafStopRequestEvent)]
    shutdown_events = [item for item in produced if isinstance(item, ControlPlaneShutdownReadyEvent)]
    requested_workers = {item.worker_id for item in stop_requests}
    assert requested_workers == {"execution.ingress#1", "execution.policy#1"}
    assert len(shutdown_events) == 1
