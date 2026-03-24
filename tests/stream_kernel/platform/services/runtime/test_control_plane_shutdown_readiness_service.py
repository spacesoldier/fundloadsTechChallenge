from __future__ import annotations

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafRunnerTombstoneEvent,
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


def test_leaf_shutdown_readiness_emits_drain_ready_from_runner_tombstone_quorum() -> None:
    service = InMemoryControlPlaneLeafShutdownReadinessService(store=InMemoryKvStore())
    expected_nodes = ("source:ingress", "transform.features", "sink:egress")

    first = service.observe_runner_tombstone(
        ControlPlaneLeafRunnerTombstoneEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            request_id="req-runner-1",
            observed_node="source:ingress",
            expected_nodes=expected_nodes,
            tombstone_output=True,
        )
    )
    second = service.observe_runner_tombstone(
        ControlPlaneLeafRunnerTombstoneEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            request_id="req-runner-2",
            observed_node="transform.features",
            expected_nodes=expected_nodes,
            tombstone_output=True,
        )
    )
    final = service.observe_runner_tombstone(
        ControlPlaneLeafRunnerTombstoneEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            request_id="req-runner-3",
            observed_node="sink:egress",
            expected_nodes=expected_nodes,
            tombstone_output=True,
        )
    )

    assert first is None
    assert second is None
    assert isinstance(final, ControlPlaneLeafDrainReadyEvent)
    assert final.target_group == "execution.alpha"
    assert final.worker_id == "execution.alpha#1"
