from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.consumer_registry import ConsumerRegistry
from stream_kernel.integration.kv_store import KVStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConsumerBindingRecord,
    ControlPlaneDiscoveryEntityRecord,
)

_BY_NODE_KEY = "control_plane.consumer_registry.by_node"


class ControlPlaneConsumerRegistryStore(KVStore):
    # KV marker for dynamic control-plane consumer registry synchronization.
    pass


@runtime_checkable
class ControlPlaneDynamicConsumerRoutingService(Protocol):
    def apply_bindings(
        self,
        bindings: tuple[ControlPlaneConsumerBindingRecord, ...] | list[ControlPlaneConsumerBindingRecord],
    ) -> None:
        raise NotImplementedError

    def apply_discovery_records(
        self,
        records: tuple[ControlPlaneDiscoveryEntityRecord, ...] | list[ControlPlaneDiscoveryEntityRecord],
    ) -> None:
        raise NotImplementedError

    def remove_node_bindings(self, node_names: tuple[str, ...] | list[str]) -> None:
        raise NotImplementedError


@service(name="control_plane_dynamic_consumer_routing_service")
@dataclass(slots=True)
class InMemoryControlPlaneDynamicConsumerRoutingService(ControlPlaneDynamicConsumerRoutingService):
    registry: ConsumerRegistry = inject.service(ConsumerRegistry)
    store: KVStore = inject.kv(ControlPlaneConsumerRegistryStore)

    def apply_bindings(
        self,
        bindings: tuple[ControlPlaneConsumerBindingRecord, ...] | list[ControlPlaneConsumerBindingRecord],
    ) -> None:
        by_node = self._load_by_node()
        for binding in bindings:
            if not isinstance(binding, ControlPlaneConsumerBindingRecord):
                continue
            if not isinstance(binding.token, type):
                continue
            token_key = _token_key(binding.token)
            for node_name in binding.node_names:
                if not isinstance(node_name, str) or not node_name:
                    continue
                existing = self.registry.get_consumers(binding.token)
                if node_name not in existing:
                    self.registry.register(binding.token, [*existing, node_name])
                prev = set(by_node.get(node_name, []))
                if token_key not in prev:
                    by_node[node_name] = sorted({*prev, token_key})
        self.store.set(_BY_NODE_KEY, by_node)

    def apply_discovery_records(
        self,
        records: tuple[ControlPlaneDiscoveryEntityRecord, ...] | list[ControlPlaneDiscoveryEntityRecord],
    ) -> None:
        by_node = self._load_by_node()
        for record in records:
            if not isinstance(record, ControlPlaneDiscoveryEntityRecord):
                continue
            if record.entity_kind != "node":
                continue
            node_name = _node_name_from_record(record)
            if not node_name:
                continue
            if not self.registry.has_node(node_name):
                self.remove_node_bindings((node_name,))
                continue
            consumes = _resolve_node_consumes(record)
            if not consumes:
                # Discovery metadata may be partial during startup (for example,
                # when symbol resolution is unavailable for some system nodes).
                # Do not drop already-registered bindings on empty consumes.
                # Keep current routing until an explicit remove event is emitted.
                continue
            new_token_keys = {_token_key(token) for token in consumes}
            previous = set(by_node.get(node_name, []))
            for token in consumes:
                existing = self.registry.get_consumers(token)
                if node_name not in existing:
                    self.registry.register(token, [*existing, node_name])
            for removed_key in previous - new_token_keys:
                token = _resolve_type_from_key(removed_key)
                if not isinstance(token, type):
                    continue
                self._remove_node_from_token(token=token, node_name=node_name)
            by_node[node_name] = sorted(new_token_keys)
        self.store.set(_BY_NODE_KEY, by_node)

    def remove_node_bindings(self, node_names: tuple[str, ...] | list[str]) -> None:
        by_node = self._load_by_node()
        for node_name in node_names:
            if not isinstance(node_name, str) or not node_name:
                continue
            token_keys = tuple(by_node.get(node_name, []))
            for token_key in token_keys:
                token = _resolve_type_from_key(token_key)
                if not isinstance(token, type):
                    continue
                self._remove_node_from_token(token=token, node_name=node_name)
            by_node.pop(node_name, None)
            unregister_node = getattr(self.registry, "unregister_node", None)
            if callable(unregister_node):
                unregister_node(node_name)
        self.store.set(_BY_NODE_KEY, by_node)

    def _remove_node_from_token(self, *, token: type, node_name: str) -> None:
        existing = [name for name in self.registry.get_consumers(token) if name != node_name]
        if existing:
            self.registry.register(token, existing)
            return
        unregister = getattr(self.registry, "unregister", None)
        if callable(unregister):
            unregister(token)
            return
        self.registry.register(token, [])

    def _load_by_node(self) -> dict[str, list[str]]:
        raw = self.store.get(_BY_NODE_KEY)
        if not isinstance(raw, dict):
            return {}
        result: dict[str, list[str]] = {}
        for node_name, token_keys in raw.items():
            if not isinstance(node_name, str):
                continue
            if not isinstance(token_keys, list):
                continue
            normalized = [key for key in token_keys if isinstance(key, str) and key]
            result[node_name] = normalized
        return result


def _node_name_from_record(record: ControlPlaneDiscoveryEntityRecord) -> str:
    meta_name = record.meta.get("name")
    if isinstance(meta_name, str) and meta_name:
        return meta_name
    return ""


def _resolve_node_consumes(record: ControlPlaneDiscoveryEntityRecord) -> tuple[type[object], ...]:
    symbol = _resolve_symbol(record)
    if symbol is None:
        return ()
    node_meta = getattr(symbol, "__node_meta__", None)
    consumes = getattr(node_meta, "consumes", None)
    if not isinstance(consumes, list):
        return ()
    resolved: list[type[object]] = []
    for token in consumes:
        if isinstance(token, type):
            resolved.append(token)
    # Preserve declared order and remove duplicates.
    unique: list[type[object]] = []
    seen: set[str] = set()
    for token in resolved:
        key = _token_key(token)
        if key in seen:
            continue
        seen.add(key)
        unique.append(token)
    return tuple(unique)


def _resolve_symbol(record: ControlPlaneDiscoveryEntityRecord) -> object | None:
    try:
        module = importlib.import_module(record.module)
    except Exception:
        return None
    current: object = module
    for part in record.qualname.split("."):
        if not part or part == "<locals>":
            continue
        current = getattr(current, part, None)
        if current is None:
            return None
    return current


def _token_key(token: type[object]) -> str:
    module_name = getattr(token, "__module__", "")
    qualname = getattr(token, "__qualname__", getattr(token, "__name__", ""))
    return f"{module_name}:{qualname}"


def _resolve_type_from_key(key: str) -> type[object] | None:
    module_name, sep, qualname = key.partition(":")
    if not sep or not module_name or not qualname:
        return None
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return None
    current: object = module
    for part in qualname.split("."):
        current = getattr(current, part, None)
        if current is None:
            return None
    if isinstance(current, type):
        return current
    return None


__all__ = [
    "ControlPlaneConsumerRegistryStore",
    "ControlPlaneDynamicConsumerRoutingService",
    "InMemoryControlPlaneDynamicConsumerRoutingService",
]
