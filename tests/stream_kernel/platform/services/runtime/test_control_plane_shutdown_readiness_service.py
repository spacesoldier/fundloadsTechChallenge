from __future__ import annotations

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryOutputsEvent,
    ControlPlaneLeafDrainReadyEvent,
)
from stream_kernel.platform.services.runtime.control_plane_shutdown_readiness import (
    InMemoryControlPlaneLeafShutdownReadinessService,
    InMemoryControlPlaneShutdownReadinessService,
)


def test_shutdown_readiness_emits_shutdown_ready_when_all_expected_leaf_ready() -> None:
    service = InMemoryControlPlaneShutdownReadinessService(store=InMemoryKvStore())
    service.configure_expected_groups(("execution.features", "execution.policy"))

    emitted_1, snapshot_1 = service.mark_leaf_ready(
        ControlPlaneLeafDrainReadyEvent(
            target_group="execution.features",
            worker_id="execution.features#1",
            request_id="req-1000",
            tombstone_output=True,
        )
    )
    emitted_2, snapshot_2 = service.mark_leaf_ready(
        ControlPlaneLeafDrainReadyEvent(
            target_group="execution.policy",
            worker_id="execution.policy#1",
            request_id="req-1001",
            tombstone_output=True,
        )
    )

    assert emitted_1 is False
    assert snapshot_1.shutdown_ready is False
    assert emitted_2 is True
    assert snapshot_2.shutdown_ready is True
    assert set(snapshot_2.ready_groups) == {"execution.features", "execution.policy"}


def test_shutdown_readiness_dedupes_same_group_request_id() -> None:
    service = InMemoryControlPlaneShutdownReadinessService(store=InMemoryKvStore())
    service.configure_expected_groups(("execution.features",))

    emitted_1, snapshot_1 = service.mark_leaf_ready(
        ControlPlaneLeafDrainReadyEvent(
            target_group="execution.features",
            worker_id="execution.features#1",
            request_id="req-dup",
            tombstone_output=True,
        )
    )
    emitted_2, snapshot_2 = service.mark_leaf_ready(
        ControlPlaneLeafDrainReadyEvent(
            target_group="execution.features",
            worker_id="execution.features#1",
            request_id="req-dup",
            tombstone_output=True,
        )
    )

    assert emitted_1 is True
    assert emitted_2 is False
    assert snapshot_1.shutdown_ready is True
    assert snapshot_2.shutdown_ready is True
    assert snapshot_2.ready_groups == ("execution.features",)


def test_shutdown_readiness_requires_observability_group_when_in_expected_quorum() -> None:
    service = InMemoryControlPlaneShutdownReadinessService(store=InMemoryKvStore())
    service.configure_expected_groups(("execution.ingress", "system.observability"))

    emitted_1, snapshot_1 = service.mark_leaf_ready(
        ControlPlaneLeafDrainReadyEvent(
            target_group="execution.ingress",
            worker_id="execution.ingress#1",
            request_id="req-ingress",
            tombstone_output=True,
        )
    )
    emitted_2, snapshot_2 = service.mark_leaf_ready(
        ControlPlaneLeafDrainReadyEvent(
            target_group="system.observability",
            worker_id="system.observability#1",
            request_id="req-observability",
            tombstone_output=True,
        )
    )

    assert emitted_1 is False
    assert snapshot_1.shutdown_ready is False
    assert set(snapshot_1.missing_groups) == {"system.observability"}
    assert emitted_2 is True
    assert snapshot_2.shutdown_ready is True
    assert set(snapshot_2.ready_groups) == {"execution.ingress", "system.observability"}


def test_leaf_shutdown_readiness_emits_drain_ready_on_terminal_tombstone() -> None:
    service = InMemoryControlPlaneLeafShutdownReadinessService(store=InMemoryKvStore())

    drain_ready = service.observe_boundary_outputs(
        ControlPlaneLeafBoundaryOutputsEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            request_id="req-tomb-1",
            outputs=(),
            tombstone_output=True,
        )
    )

    assert isinstance(drain_ready, ControlPlaneLeafDrainReadyEvent)
    assert drain_ready.target_group == "execution.alpha"
    assert drain_ready.worker_id == "execution.alpha#1"


def test_leaf_shutdown_readiness_dedupes_terminal_tombstone_by_request_id() -> None:
    service = InMemoryControlPlaneLeafShutdownReadinessService(store=InMemoryKvStore())

    first = service.observe_boundary_outputs(
        ControlPlaneLeafBoundaryOutputsEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            request_id="req-tomb-2",
            outputs=(),
            tombstone_output=True,
        )
    )
    duplicate = service.observe_boundary_outputs(
        ControlPlaneLeafBoundaryOutputsEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            request_id="req-tomb-2",
            outputs=(),
            tombstone_output=True,
        )
    )

    assert isinstance(first, ControlPlaneLeafDrainReadyEvent)
    assert duplicate is None


def test_leaf_shutdown_readiness_ignores_input_only_tombstone() -> None:
    service = InMemoryControlPlaneLeafShutdownReadinessService(store=InMemoryKvStore())
    no_event = service.observe_boundary_outputs(
        ControlPlaneLeafBoundaryOutputsEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            request_id="req-input-only",
            outputs=(),
            tombstone_output=False,
        )
    )
    assert no_event is None
