from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import KVStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryEntityRecord,
)

_MATERIALIZED_BY_ENTITY_KEY = "control_plane.discovery.materialized.by_entity"
_MATERIALIZED_BY_NODE_KEY = "control_plane.discovery.materialized.by_node"


class ControlPlaneDiscoveryMaterializationRegistry(KVStore):
    # KV marker contract for discovery materialization cache.
    pass


@dataclass(frozen=True, slots=True)
class ControlPlaneDiscoveredRuntimeEntity:
    record: ControlPlaneDiscoveryEntityRecord
    symbol: object | None
    instance: object | None
    instantiated: bool
    error: str | None = None


@runtime_checkable
class ControlPlaneDiscoveryMaterializationService(Protocol):
    def materialize(
        self,
        records: tuple[ControlPlaneDiscoveryEntityRecord, ...] | list[ControlPlaneDiscoveryEntityRecord],
    ) -> tuple[ControlPlaneDiscoveredRuntimeEntity, ...]:
        raise NotImplementedError

    def all_entities(self) -> tuple[ControlPlaneDiscoveredRuntimeEntity, ...]:
        raise NotImplementedError

    def by_node_name(self, node_name: str) -> ControlPlaneDiscoveredRuntimeEntity | None:
        raise NotImplementedError


@service(name="control_plane_discovery_materialization_service")
@dataclass(slots=True)
class InMemoryControlPlaneDiscoveryMaterializationService(ControlPlaneDiscoveryMaterializationService):
    store: KVStore = inject.kv(ControlPlaneDiscoveryMaterializationRegistry)

    def materialize(
        self,
        records: tuple[ControlPlaneDiscoveryEntityRecord, ...] | list[ControlPlaneDiscoveryEntityRecord],
    ) -> tuple[ControlPlaneDiscoveredRuntimeEntity, ...]:
        by_entity = self._load_by_entity()
        by_node = self._load_by_node()
        produced: list[ControlPlaneDiscoveredRuntimeEntity] = []
        for record in records:
            if not isinstance(record, ControlPlaneDiscoveryEntityRecord):
                continue
            if record.entity_id in by_entity:
                continue
            symbol, error = _resolve_symbol(record)
            instance = None
            instantiated = False
            if error is None and inspect.isclass(symbol):
                try:
                    instance = symbol()
                    instantiated = True
                except Exception as exc:  # noqa: BLE001 - keep materialization tolerant.
                    error = f"instantiation failed: {exc.__class__.__name__}: {exc}"
            materialized = ControlPlaneDiscoveredRuntimeEntity(
                record=record,
                symbol=symbol,
                instance=instance,
                instantiated=instantiated,
                error=error,
            )
            by_entity[record.entity_id] = materialized
            node_name = record.meta.get("name")
            if isinstance(node_name, str) and node_name:
                by_node[node_name] = record.entity_id
            produced.append(materialized)
        self.store.set(_MATERIALIZED_BY_ENTITY_KEY, by_entity)
        self.store.set(_MATERIALIZED_BY_NODE_KEY, by_node)
        return tuple(produced)

    def all_entities(self) -> tuple[ControlPlaneDiscoveredRuntimeEntity, ...]:
        return tuple(self._load_by_entity().values())

    def by_node_name(self, node_name: str) -> ControlPlaneDiscoveredRuntimeEntity | None:
        if not isinstance(node_name, str) or not node_name:
            return None
        by_entity = self._load_by_entity()
        by_node = self._load_by_node()
        entity_id = by_node.get(node_name)
        if not isinstance(entity_id, str) or not entity_id:
            return None
        return by_entity.get(entity_id)

    def _load_by_entity(self) -> dict[str, ControlPlaneDiscoveredRuntimeEntity]:
        loaded = self.store.get(_MATERIALIZED_BY_ENTITY_KEY)
        if not isinstance(loaded, dict):
            return {}
        return {
            key: value
            for key, value in loaded.items()
            if isinstance(key, str) and isinstance(value, ControlPlaneDiscoveredRuntimeEntity)
        }

    def _load_by_node(self) -> dict[str, str]:
        loaded = self.store.get(_MATERIALIZED_BY_NODE_KEY)
        if not isinstance(loaded, dict):
            return {}
        return {
            key: value
            for key, value in loaded.items()
            if isinstance(key, str) and isinstance(value, str)
        }


def _resolve_symbol(record: ControlPlaneDiscoveryEntityRecord) -> tuple[object | None, str | None]:
    try:
        module = importlib.import_module(record.module)
    except Exception as exc:  # noqa: BLE001 - keep materialization tolerant.
        return (None, f"module import failed: {exc.__class__.__name__}: {exc}")
    current: object = module
    for part in record.qualname.split("."):
        if not part or part == "<locals>":
            continue
        current = getattr(current, part, None)
        if current is None:
            return (None, f"symbol resolution failed: {record.qualname}")
    return (current, None)


__all__ = [
    "ControlPlaneDiscoveryMaterializationRegistry",
    "ControlPlaneDiscoveryMaterializationService",
    "ControlPlaneDiscoveredRuntimeEntity",
    "InMemoryControlPlaneDiscoveryMaterializationService",
]

