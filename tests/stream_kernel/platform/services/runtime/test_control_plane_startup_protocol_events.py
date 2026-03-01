from __future__ import annotations

import pytest

from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafDiscoverySnapshotEvent,
)


def test_leaf_discovery_request_event_accepts_valid_payload() -> None:
    event = ControlPlaneLeafDiscoveryRequestEvent(
        target_group="execution.features",
        worker_id="execution.features#1",
        request_id="req-1",
        required_nodes=("compute_features", "idempotency_gate"),
        include_relationships=True,
        protocol_revision=2,
    )
    assert event.target_group == "execution.features"
    assert event.worker_id == "execution.features#1"
    assert event.request_id == "req-1"
    assert event.required_nodes == ("compute_features", "idempotency_gate")
    assert event.include_relationships is True
    assert event.protocol_revision == 2


def test_leaf_discovery_snapshot_event_accepts_valid_payload() -> None:
    event = ControlPlaneLeafDiscoverySnapshotEvent(
        target_group="execution.features",
        worker_id="execution.features#1",
        request_id="req-snapshot-1",
        required_nodes=("compute_features", "idempotency_gate"),
        snapshot_records=(
            ControlPlaneDiscoveryEntityRecord(
                entity_kind="node",
                entity_id="node:compute_features",
                source_scope="project",
                module="fund_load.usecases.steps.compute_features",
                qualname="compute_features",
                meta={"name": "compute_features"},
            ),
        ),
        protocol_revision=3,
    )
    assert event.worker_id == "execution.features#1"
    assert event.required_nodes == ("compute_features", "idempotency_gate")
    assert event.protocol_revision == 3


@pytest.mark.parametrize(
    ("kwargs", "message_part"),
    [
        (
            {
                "target_group": "",
                "worker_id": "execution.features#1",
                "request_id": "req-1",
            },
            "target_group",
        ),
        (
            {
                "target_group": "execution.features",
                "worker_id": "",
                "request_id": "req-1",
            },
            "worker_id",
        ),
        (
            {
                "target_group": "execution.features",
                "worker_id": "execution.features#1",
                "request_id": "",
            },
            "request_id",
        ),
        (
            {
                "target_group": "execution.features",
                "worker_id": "execution.features#1",
                "request_id": "req-1",
                "required_nodes": ("",),
            },
            "required_nodes",
        ),
        (
            {
                "target_group": "execution.features",
                "worker_id": "execution.features#1",
                "request_id": "req-1",
                "protocol_revision": 0,
            },
            "protocol_revision",
        ),
    ],
)
def test_leaf_discovery_request_event_rejects_invalid_payload(
    kwargs: dict[str, object],
    message_part: str,
) -> None:
    with pytest.raises(ValueError, match=message_part):
        ControlPlaneLeafDiscoveryRequestEvent(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kwargs", "message_part"),
    [
        (
            {
                "target_group": "execution.features",
                "worker_id": "execution.features#1",
                "request_id": "req-snapshot-1",
                "required_nodes": ("",),
                "snapshot_records": (),
            },
            "required_nodes",
        ),
        (
            {
                "target_group": "execution.features",
                "worker_id": "execution.features#1",
                "request_id": "req-snapshot-1",
                "required_nodes": ("compute_features",),
                "snapshot_records": ({},),
            },
            "snapshot_records",
        ),
    ],
)
def test_leaf_discovery_snapshot_event_rejects_invalid_payload(
    kwargs: dict[str, object],
    message_part: str,
) -> None:
    with pytest.raises(ValueError, match=message_part):
        ControlPlaneLeafDiscoverySnapshotEvent(**kwargs)  # type: ignore[arg-type]


def test_leaf_discovery_ack_event_accepts_valid_payload() -> None:
    event = ControlPlaneLeafDiscoveryAckEvent(
        target_group="execution.features",
        worker_id="execution.features#1",
        request_id="req-1",
        status="accepted",
        discovered_nodes=("compute_features", "idempotency_gate"),
    )
    assert event.status == "accepted"
    assert event.error is None


def test_leaf_discovery_ack_event_rejects_error_with_accepted_status() -> None:
    with pytest.raises(ValueError, match="status=accepted"):
        ControlPlaneLeafDiscoveryAckEvent(
            target_group="execution.features",
            worker_id="execution.features#1",
            request_id="req-1",
            status="accepted",
            discovered_nodes=("compute_features",),
            error="must not be set",
        )
