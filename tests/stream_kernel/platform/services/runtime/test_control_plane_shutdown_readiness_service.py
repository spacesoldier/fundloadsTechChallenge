from __future__ import annotations

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafShutdownPrepareCommand,
)
from stream_kernel.platform.services.runtime.control_plane_shutdown_readiness import (
    InMemoryControlPlaneLeafShutdownReadinessService,
    InMemoryControlPlaneShutdownReadinessService,
)


def test_shutdown_readiness_emits_prepare_only_after_all_expected_groups_observed() -> None:
    service = InMemoryControlPlaneShutdownReadinessService(store=InMemoryKvStore())
    service.configure_expected_groups(("execution.features", "execution.policy"))

    emit_prepare_1, snapshot_1 = service.observe_tombstone(
        ControlPlaneLeafBoundaryResultEvent(
            target_group="execution.features",
            worker_id="execution.features#1",
            request_id="req-1000",
            status="completed",
            tombstone_input=True,
        )
    )
    emit_prepare_2, snapshot_2 = service.observe_tombstone(
        ControlPlaneLeafBoundaryResultEvent(
            target_group="execution.policy",
            worker_id="execution.policy#1",
            request_id="req-1000",
            status="completed",
            tombstone_input=True,
        )
    )

    assert emit_prepare_1 is False
    assert set(snapshot_1.tombstone_groups) == {"execution.features"}
    assert emit_prepare_2 is True
    assert set(snapshot_2.tombstone_groups) == {"execution.features", "execution.policy"}


def test_shutdown_readiness_emits_shutdown_ready_after_prepare_and_all_leaf_ready() -> None:
    service = InMemoryControlPlaneShutdownReadinessService(store=InMemoryKvStore())
    service.configure_expected_groups(("execution.features", "execution.policy"))
    _emit_prepare_1, _ = service.observe_tombstone(
        ControlPlaneLeafBoundaryResultEvent(
            target_group="execution.features",
            worker_id="execution.features#1",
            request_id="req-1000",
            status="completed",
            tombstone_input=True,
        )
    )
    _emit_prepare_2, _ = service.observe_tombstone(
        ControlPlaneLeafBoundaryResultEvent(
            target_group="execution.policy",
            worker_id="execution.policy#1",
            request_id="req-1000",
            status="completed",
            tombstone_input=True,
        )
    )
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
            request_id="req-1000",
            tombstone_output=True,
        )
    )

    assert emitted_1 is False
    assert snapshot_1.shutdown_ready is False
    assert emitted_2 is True
    assert snapshot_2.shutdown_ready is True
    assert set(snapshot_2.ready_groups) == {"execution.features", "execution.policy"}


def test_leaf_shutdown_readiness_requires_prepare_and_tombstone_before_drain_ready() -> None:
    service = InMemoryControlPlaneLeafShutdownReadinessService(store=InMemoryKvStore())
    # Prepare alone should not emit drain-ready.
    prepare_only = service.observe_prepare_command(
        ControlPlaneLeafShutdownPrepareCommand(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            command_id="prepare-1",
        )
    )
    assert prepare_only is None

    # Tombstone after prepare emits drain-ready.
    drain_ready = service.observe_boundary_result(
        ControlPlaneLeafBoundaryResultEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            request_id="req-tomb-1",
            status="completed",
            tombstone_input=True,
            tombstone_output=True,
        )
    )
    assert isinstance(drain_ready, ControlPlaneLeafDrainReadyEvent)
    assert drain_ready.target_group == "execution.alpha"
    assert drain_ready.worker_id == "execution.alpha#1"


def test_leaf_shutdown_readiness_emits_drain_ready_when_prepare_arrives_after_tombstone() -> None:
    service = InMemoryControlPlaneLeafShutdownReadinessService(store=InMemoryKvStore())
    # Tombstone first: no event yet.
    no_event = service.observe_boundary_result(
        ControlPlaneLeafBoundaryResultEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            request_id="req-tomb-2",
            status="completed",
            tombstone_input=True,
            tombstone_output=False,
        )
    )
    assert no_event is None

    # Prepare later should release drain-ready exactly once.
    ready = service.observe_prepare_command(
        ControlPlaneLeafShutdownPrepareCommand(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            command_id="prepare-2",
        )
    )
    assert isinstance(ready, ControlPlaneLeafDrainReadyEvent)
    assert ready.request_id == "req-tomb-2"
    assert ready.tombstone_output is False

    # Repeated prepare is deduped.
    ready_dup = service.observe_prepare_command(
        ControlPlaneLeafShutdownPrepareCommand(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            command_id="prepare-2-dup",
        )
    )
    assert ready_dup is None
