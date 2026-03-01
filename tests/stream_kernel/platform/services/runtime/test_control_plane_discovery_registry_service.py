from __future__ import annotations

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    InMemoryControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryEntityRecord,
)


def _record(kind: str, entity_id: str) -> ControlPlaneDiscoveryEntityRecord:
    return ControlPlaneDiscoveryEntityRecord(
        entity_kind=kind,
        entity_id=entity_id,
        source_scope="platform",
        module="pkg.mod",
        qualname=entity_id.split(":", 1)[-1],
        meta={},
    )


def test_discovery_service_persists_typed_entity_records_and_filters_by_kind() -> None:
    service = InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())

    node = _record("node", "pkg.mod:NodeA")
    svc = _record("service", "pkg.mod:ServiceA")
    adp = _record("adapter", "pkg.mod:AdapterA")

    service.append_item(node)
    service.append_item(svc)
    service.append_item(adp)

    assert service.entity_records(kind="node") == [node]
    assert service.entity_records(kind="service") == [svc]
    assert service.entity_records(kind="adapter") == [adp]
    assert service.entity_records() == [node, svc, adp]


def test_discovery_service_clear_removes_general_and_typed_views() -> None:
    service = InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())
    service.append_item(_record("node", "pkg.mod:NodeA"))

    service.clear()

    assert service.items() == []
    assert service.entity_records() == []
