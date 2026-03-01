from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneGroupSpec,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneLeafHelloEvent,
)
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    InMemoryControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)


@dataclass(slots=True)
class _Discovery:
    records: list[ControlPlaneDiscoveryEntityRecord]

    def entity_records(self, *, kind: str | None = None) -> list[ControlPlaneDiscoveryEntityRecord]:
        if kind is None:
            return list(self.records)
        return [record for record in self.records if record.entity_kind == kind]


def test_root_discovery_snapshot_service_builds_group_scoped_snapshot() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.discovery_snapshot_service import (
        DefaultControlPlaneRootDiscoverySnapshotService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.alpha",
                        workers=1,
                        nodes=("node.a", "node.b"),
                    ),
                )
            )
        )
    )
    discovery = _Discovery(
        records=[
            ControlPlaneDiscoveryEntityRecord(
                entity_kind="node",
                entity_id="node:node.a",
                source_scope="project",
                module="pkg.module",
                qualname="node_a",
                meta={"name": "node.a"},
            ),
            ControlPlaneDiscoveryEntityRecord(
                entity_kind="node",
                entity_id="node:node.b",
                source_scope="project",
                module="pkg.module",
                qualname="node_b",
                meta={"name": "node.b"},
            ),
        ]
    )
    service = DefaultControlPlaneRootDiscoverySnapshotService(state=state, discovery=discovery)
    hello = ControlPlaneLeafHelloEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        pid=123,
    )

    snapshot = service.build_snapshot(hello=hello, protocol_revision=3)

    assert snapshot is not None
    assert snapshot.required_nodes == ("node.a", "node.b")
    assert len(snapshot.snapshot_records) == 2
    assert snapshot.protocol_revision == 3


def test_root_discovery_snapshot_service_returns_none_for_unknown_group() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.discovery_snapshot_service import (
        DefaultControlPlaneRootDiscoverySnapshotService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    discovery = InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())
    service = DefaultControlPlaneRootDiscoverySnapshotService(state=state, discovery=discovery)
    hello = ControlPlaneLeafHelloEvent(
        target_group="execution.unknown",
        worker_id="execution.unknown#1",
        pid=123,
    )

    snapshot = service.build_snapshot(hello=hello, protocol_revision=3)

    assert snapshot is None
