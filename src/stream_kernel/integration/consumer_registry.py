from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


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
    # In-memory registry for tests and local runs (Execution runtime and routing integration §4.1).
    _map: dict[type, list[str]] = field(default_factory=dict)
    _node_set: set[str] = field(default_factory=set)
    _version: int = 0

    def __post_init__(self) -> None:
        # Initialize node set from the existing map for O(1) membership checks.
        self._rebuild_node_set()

    def get_consumers(self, token: type) -> list[str]:
        # Return a copy to keep registry state encapsulated.
        return list(self._map.get(token, []))

    def has_node(self, name: str) -> bool:
        # O(1) membership check using a cached node set.
        return name in self._node_set

    def list_tokens(self) -> list[type]:
        # Return tokens in insertion order for deterministic routing.
        return list(self._map.keys())

    def version(self) -> int:
        # Monotonic version used for RoutingPort cache invalidation.
        return self._version

    def register(self, token: type, consumers: Iterable[str]) -> None:
        # Overwrite the consumer list for the token.
        self._map[token] = list(consumers)
        self._version += 1
        self._rebuild_node_set()

    def _rebuild_node_set(self) -> None:
        # Rebuild node cache from the mapping (register frequency is low).
        self._node_set = {name for items in self._map.values() for name in items}
