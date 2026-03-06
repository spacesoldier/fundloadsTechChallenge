from __future__ import annotations

import importlib
from collections.abc import Iterable
from dataclasses import dataclass, field

from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore

_REGISTRY_MAP_KEY = "consumer_registry.map"
_REGISTRY_ORDER_KEY = "consumer_registry.order"
_REGISTRY_NODES_KEY = "consumer_registry.nodes"
_REGISTRY_VERSION_KEY = "consumer_registry.version"


class ConsumerRegistryStore(KVStore):
    # KV marker contract for consumer registry storage.
    pass


class ConsumerRegistry:
    # Port contract for dynamic consumer lookup (Execution runtime and routing integration §3.2).
    def get_consumers(self, token: type) -> list[str]:
        raise NotImplementedError("ConsumerRegistry.get_consumers must be implemented")

    def has_node(self, name: str) -> bool:
        raise NotImplementedError("ConsumerRegistry.has_node must be implemented")

    def list_tokens(self) -> list[type]:
        raise NotImplementedError("ConsumerRegistry.list_tokens must be implemented")

    def version(self) -> int:
        raise NotImplementedError("ConsumerRegistry.version must be implemented")

    def register(self, token: type, consumers: Iterable[str]) -> None:
        raise NotImplementedError("ConsumerRegistry.register must be implemented")


@dataclass(slots=True)
class InMemoryConsumerRegistry(ConsumerRegistry):
    # KV-backed consumer registry with in-memory defaults for tests/local runs.
    _initial_map: dict[type, list[str]] = field(default_factory=dict)
    store: KVStore = field(default_factory=InMemoryKvStore)
    _token_refs: dict[str, type] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._ensure_state()
        for token, consumers in self._initial_map.items():
            self.register(token, consumers)

    def get_consumers(self, token: type) -> list[str]:
        key = _token_key(token)
        mapping = self._mapping()
        value = mapping.get(key)
        return list(value) if isinstance(value, list) else []

    def has_node(self, name: str) -> bool:
        nodes = self._nodes()
        return name in nodes

    def list_tokens(self) -> list[type]:
        tokens: list[type] = []
        for key in self._order():
            token = self._resolve_token(key)
            if isinstance(token, type):
                tokens.append(token)
        return tokens

    def version(self) -> int:
        # Monotonic version used for RoutingService cache invalidation.
        raw = self.store.get(_REGISTRY_VERSION_KEY)
        return raw if isinstance(raw, int) else 0

    def register(self, token: type, consumers: Iterable[str]) -> None:
        # Overwrite the consumer list for the token and persist in KV state.
        token_key = _token_key(token)
        self._token_refs[token_key] = token

        mapping = self._mapping()
        order = self._order()

        mapping[token_key] = [name for name in consumers if isinstance(name, str)]
        if token_key not in order:
            order.append(token_key)

        nodes: set[str] = set()
        for names in mapping.values():
            if isinstance(names, list):
                nodes.update(name for name in names if isinstance(name, str))

        self.store.set(_REGISTRY_MAP_KEY, mapping)
        self.store.set(_REGISTRY_ORDER_KEY, order)
        self.store.set(_REGISTRY_NODES_KEY, sorted(nodes))
        self.store.set(_REGISTRY_VERSION_KEY, self.version() + 1)

    def _mapping(self) -> dict[str, list[str]]:
        raw = self.store.get(_REGISTRY_MAP_KEY)
        if not isinstance(raw, dict):
            return {}
        result: dict[str, list[str]] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, list):
                continue
            result[key] = [name for name in value if isinstance(name, str)]
        return result

    def _order(self) -> list[str]:
        raw = self.store.get(_REGISTRY_ORDER_KEY)
        if not isinstance(raw, list):
            return []
        return [key for key in raw if isinstance(key, str)]

    def _nodes(self) -> set[str]:
        raw = self.store.get(_REGISTRY_NODES_KEY)
        if not isinstance(raw, list):
            return set()
        return {name for name in raw if isinstance(name, str)}

    def _resolve_token(self, key: str) -> type | None:
        cached = self._token_refs.get(key)
        if isinstance(cached, type):
            return cached

        module_name, sep, qualname = key.partition(":")
        if not sep or not module_name or not qualname:
            return None
        try:
            module = importlib.import_module(module_name)
        except Exception:
            return None
        candidate: object = module
        for part in qualname.split("."):
            candidate = getattr(candidate, part, None)
            if candidate is None:
                return None
        if not isinstance(candidate, type):
            return None
        self._token_refs[key] = candidate
        return candidate

    def _ensure_state(self) -> None:
        if not isinstance(self.store.get(_REGISTRY_MAP_KEY), dict):
            self.store.set(_REGISTRY_MAP_KEY, {})
        if not isinstance(self.store.get(_REGISTRY_ORDER_KEY), list):
            self.store.set(_REGISTRY_ORDER_KEY, [])
        if not isinstance(self.store.get(_REGISTRY_NODES_KEY), list):
            self.store.set(_REGISTRY_NODES_KEY, [])
        if not isinstance(self.store.get(_REGISTRY_VERSION_KEY), int):
            self.store.set(_REGISTRY_VERSION_KEY, 0)


def _token_key(token: type) -> str:
    module_name = getattr(token, "__module__", "")
    qualname = getattr(token, "__qualname__", getattr(token, "__name__", ""))
    return f"{module_name}:{qualname}"
