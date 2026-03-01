from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryEntityRecord,
)

_DISCOVERY_KEY = "control_plane.discovery.items"
_DISCOVERY_ENTITY_KEY_PREFIX = "control_plane.discovery.entities."


class ControlPlaneDiscoveryRegistry(KVStore):
    # KV marker contract for discovery registry storage.
    pass


@runtime_checkable
class ControlPlaneDiscoveryService(Protocol):
    def append_item(self, item: object) -> None:
        raise NotImplementedError("ControlPlaneDiscoveryService.append_item must be implemented")

    def items(self) -> list[object]:
        raise NotImplementedError("ControlPlaneDiscoveryService.items must be implemented")

    def clear(self) -> None:
        raise NotImplementedError("ControlPlaneDiscoveryService.clear must be implemented")

    def entity_records(
        self,
        *,
        kind: str | None = None,
    ) -> list[ControlPlaneDiscoveryEntityRecord]:
        raise NotImplementedError("ControlPlaneDiscoveryService.entity_records must be implemented")


@service(name="control_plane_discovery_service")
@dataclass(slots=True)
class InMemoryControlPlaneDiscoveryService(ControlPlaneDiscoveryService):
    store: KVStore = inject.kv(ControlPlaneDiscoveryRegistry)

    def append_item(self, item: object) -> None:
        items = self._load_items()
        items.append(item)
        self.store.set(_DISCOVERY_KEY, items)
        if isinstance(item, ControlPlaneDiscoveryEntityRecord):
            key = self._entity_key(item.entity_kind)
            kind_items = self._load_entity_records(item.entity_kind)
            kind_items.append(item)
            self.store.set(key, kind_items)

    def items(self) -> list[object]:
        return list(self._load_items())

    def clear(self) -> None:
        self.store.delete(_DISCOVERY_KEY)
        self.store.delete(self._entity_key("node"))
        self.store.delete(self._entity_key("service"))
        self.store.delete(self._entity_key("adapter"))

    def _load_items(self) -> list[object]:
        existing = self.store.get(_DISCOVERY_KEY)
        if isinstance(existing, list):
            return list(existing)
        return []

    def entity_records(
        self,
        *,
        kind: str | None = None,
    ) -> list[ControlPlaneDiscoveryEntityRecord]:
        if kind is None:
            return [
                item for item in self._load_items() if isinstance(item, ControlPlaneDiscoveryEntityRecord)
            ]
        if kind not in {"node", "service", "adapter"}:
            return []
        return self._load_entity_records(kind)

    def _load_entity_records(self, kind: str) -> list[ControlPlaneDiscoveryEntityRecord]:
        existing = self.store.get(self._entity_key(kind))
        if isinstance(existing, list):
            return [
                item for item in existing if isinstance(item, ControlPlaneDiscoveryEntityRecord)
            ]
        return []

    @staticmethod
    def _entity_key(kind: str) -> str:
        return f"{_DISCOVERY_ENTITY_KEY_PREFIX}{kind}"


__all__ = [
    "ControlPlaneDiscoveryRegistry",
    "ControlPlaneDiscoveryService",
    "InMemoryControlPlaneDiscoveryService",
]
