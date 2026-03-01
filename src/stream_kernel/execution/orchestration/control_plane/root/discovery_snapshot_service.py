from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.control_plane.root.system_nodes import (
    find_group_spec_from_state,
)
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    ControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneLeafDiscoverySnapshotEvent,
    ControlPlaneLeafHelloEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneStateService,
)


@runtime_checkable
class ControlPlaneRootDiscoverySnapshotService(Protocol):
    def build_snapshot(
        self,
        *,
        hello: ControlPlaneLeafHelloEvent,
        protocol_revision: int,
    ) -> ControlPlaneLeafDiscoverySnapshotEvent | None:
        raise NotImplementedError


@service(name="control_plane_root_discovery_snapshot_service")
@dataclass(slots=True)
class DefaultControlPlaneRootDiscoverySnapshotService(ControlPlaneRootDiscoverySnapshotService):
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)
    discovery: ControlPlaneDiscoveryService = inject.service(ControlPlaneDiscoveryService)

    def build_snapshot(
        self,
        *,
        hello: ControlPlaneLeafHelloEvent,
        protocol_revision: int,
    ) -> ControlPlaneLeafDiscoverySnapshotEvent | None:
        state_events = self.state.events()
        group = find_group_spec_from_state(state_events, hello.target_group)
        if group is None:
            return None
        required_nodes = tuple(group.nodes)
        records_by_name = self._node_records_by_name()
        scoped_records = tuple(
            records_by_name[name]
            for name in required_nodes
            if isinstance(name, str) and name in records_by_name
        )
        request_id = f"{hello.worker_id}:discover-snapshot:{int(time.time() * 1000)}"
        return ControlPlaneLeafDiscoverySnapshotEvent(
            target_group=hello.target_group,
            worker_id=hello.worker_id,
            request_id=request_id,
            required_nodes=required_nodes,
            snapshot_records=scoped_records,
            protocol_revision=max(1, int(protocol_revision)),
        )

    def _node_records_by_name(self) -> dict[str, ControlPlaneDiscoveryEntityRecord]:
        entity_reader = getattr(self.discovery, "entity_records", None)
        if not callable(entity_reader):
            return {}
        try:
            records = entity_reader(kind="node")
        except Exception:
            return {}
        if not isinstance(records, list):
            return {}
        by_name: dict[str, ControlPlaneDiscoveryEntityRecord] = {}
        for record in records:
            if not isinstance(record, ControlPlaneDiscoveryEntityRecord):
                continue
            name = record.meta.get("name")
            if isinstance(name, str) and name:
                by_name[name] = record
        return by_name


__all__ = [
    "ControlPlaneRootDiscoverySnapshotService",
    "DefaultControlPlaneRootDiscoverySnapshotService",
]
