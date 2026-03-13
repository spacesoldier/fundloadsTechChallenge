from __future__ import annotations

from types import SimpleNamespace

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_discovery_materialization import (
    InMemoryControlPlaneDiscoveryMaterializationService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryEntityRecord,
)


def _entity_record(
    *,
    entity_id: str,
    module: str,
    qualname: str,
    name: str,
) -> ControlPlaneDiscoveryEntityRecord:
    return ControlPlaneDiscoveryEntityRecord(
        entity_kind="service",
        entity_id=entity_id,
        source_scope="platform",
        module=module,
        qualname=qualname,
        meta={"name": name},
    )


def test_materialization_service_resolves_symbol_instantiates_class_and_indexes_by_name() -> None:
    service = InMemoryControlPlaneDiscoveryMaterializationService(store=InMemoryKvStore())
    record = _entity_record(
        entity_id="types:SimpleNamespace",
        module="types",
        qualname="SimpleNamespace",
        name="service.simple_namespace",
    )

    produced = service.materialize((record,))

    assert len(produced) == 1
    entity = produced[0]
    assert entity.record == record
    assert entity.instantiated is True
    assert isinstance(entity.instance, SimpleNamespace)
    assert entity.error is None
    assert service.by_node_name("service.simple_namespace") == entity
    assert service.all_entities() == (entity,)


def test_materialization_service_is_idempotent_by_entity_id() -> None:
    service = InMemoryControlPlaneDiscoveryMaterializationService(store=InMemoryKvStore())
    record = _entity_record(
        entity_id="types:SimpleNamespace",
        module="types",
        qualname="SimpleNamespace",
        name="service.simple_namespace",
    )

    first = service.materialize((record,))
    second = service.materialize((record,))

    assert len(first) == 1
    assert second == ()
    assert len(service.all_entities()) == 1


def test_materialization_service_keeps_failed_entity_with_error() -> None:
    service = InMemoryControlPlaneDiscoveryMaterializationService(store=InMemoryKvStore())
    record = _entity_record(
        entity_id="missing.module:Thing",
        module="missing.module",
        qualname="Thing",
        name="service.missing",
    )

    produced = service.materialize((record,))

    assert len(produced) == 1
    entity = produced[0]
    assert entity.instantiated is False
    assert entity.symbol is None
    assert entity.instance is None
    assert isinstance(entity.error, str)
    assert "module import failed" in entity.error
    assert service.by_node_name("service.missing") == entity
