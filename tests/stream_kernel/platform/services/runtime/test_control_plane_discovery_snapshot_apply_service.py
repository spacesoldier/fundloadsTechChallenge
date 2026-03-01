from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoverySnapshotEvent,
)


@dataclass(slots=True)
class _Discovery:
    seen: list[object] = field(default_factory=list)

    def append_item(self, item: object) -> None:
        self.seen.append(item)


@dataclass(slots=True)
class _Verifier:
    discovered_nodes: tuple[str, ...]
    missing_nodes: tuple[str, ...]
    error: str | None = None

    def verify_snapshot(
        self,
        *,
        runtime: dict[str, object],
        required_nodes: tuple[str, ...],
        snapshot_records: tuple[ControlPlaneDiscoveryEntityRecord, ...],
    ) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
        _ = (runtime, required_nodes, snapshot_records)
        return (self.discovered_nodes, self.missing_nodes, self.error)


def _snapshot_event() -> ControlPlaneLeafDiscoverySnapshotEvent:
    return ControlPlaneLeafDiscoverySnapshotEvent(
        target_group="execution.features",
        worker_id="execution.features#1",
        request_id="req-snapshot-1",
        required_nodes=("compute_features",),
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


def test_leaf_snapshot_apply_service_returns_accepted_ack_and_persists_records() -> None:
    from stream_kernel.platform.services.runtime.control_plane_discovery_snapshot import (
        DefaultControlPlaneLeafDiscoverySnapshotApplyService,
    )

    discovery = _Discovery()
    verifier = _Verifier(discovered_nodes=("compute_features",), missing_nodes=())
    service = DefaultControlPlaneLeafDiscoverySnapshotApplyService(
        discovery=discovery,
        verifier=verifier,
    )
    session = SimpleNamespace(
        group_name="execution.features",
        worker_id="execution.features#1",
        child=SimpleNamespace(runtime={"strict": True}),
    )

    ack = service.apply_snapshot(session=session, snapshot=_snapshot_event())

    assert isinstance(ack, ControlPlaneLeafDiscoveryAckEvent)
    assert ack.status == "accepted"
    assert ack.discovered_nodes == ("compute_features",)
    assert ack.missing_nodes == ()
    assert len(discovery.seen) == 1


def test_leaf_snapshot_apply_service_returns_rejected_ack_when_missing_nodes() -> None:
    from stream_kernel.platform.services.runtime.control_plane_discovery_snapshot import (
        DefaultControlPlaneLeafDiscoverySnapshotApplyService,
    )

    discovery = _Discovery()
    verifier = _Verifier(
        discovered_nodes=("compute_features",),
        missing_nodes=("idempotency_gate",),
        error="missing runtime metadata for nodes: idempotency_gate",
    )
    service = DefaultControlPlaneLeafDiscoverySnapshotApplyService(
        discovery=discovery,
        verifier=verifier,
    )
    session = SimpleNamespace(
        group_name="execution.features",
        worker_id="execution.features#1",
        child=SimpleNamespace(runtime={"strict": True}),
    )

    ack = service.apply_snapshot(session=session, snapshot=_snapshot_event())

    assert isinstance(ack, ControlPlaneLeafDiscoveryAckEvent)
    assert ack.status == "rejected"
    assert ack.missing_nodes == ("idempotency_gate",)
    assert ack.error is not None


def test_default_snapshot_verifier_accepts_transport_alias_without_snapshot_record() -> None:
    from stream_kernel.platform.services.runtime.control_plane_discovery_snapshot import (
        DefaultControlPlaneDiscoverySnapshotVerificationAdapter,
    )

    verifier = DefaultControlPlaneDiscoverySnapshotVerificationAdapter()
    discovered_nodes, missing_nodes, error = verifier.verify_snapshot(
        runtime={},
        required_nodes=("sink:sink",),
        snapshot_records=(),
    )

    assert discovered_nodes == ("sink:sink",)
    assert missing_nodes == ()
    assert error is None


def test_default_snapshot_verifier_accepts_system_observability_alias_without_snapshot_record() -> None:
    from stream_kernel.platform.services.runtime.control_plane_discovery_snapshot import (
        DefaultControlPlaneDiscoverySnapshotVerificationAdapter,
    )

    verifier = DefaultControlPlaneDiscoverySnapshotVerificationAdapter()
    discovered_nodes, missing_nodes, error = verifier.verify_snapshot(
        runtime={},
        required_nodes=("system.obs.trace_dispatch",),
        snapshot_records=(),
    )

    assert discovered_nodes == ("system.obs.trace_dispatch",)
    assert missing_nodes == ()
    assert error is None
