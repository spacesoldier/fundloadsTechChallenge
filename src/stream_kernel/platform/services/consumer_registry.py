from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.application_context.application_context import ApplicationContext
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.consumer_registry import ConsumerRegistry


@service(name="consumer_registry")
@dataclass(slots=True)
class DiscoveryConsumerRegistry(ConsumerRegistry):
    # Consumer registry service backed by discovered node contracts in ApplicationContext.
    app_context: object = inject.service(ApplicationContext)
    _map: dict[type, list[str]] = field(default_factory=dict)
    _node_set: set[str] = field(default_factory=set)
    _version: int = 0
    _loaded: bool = False

    def get_consumers(self, token: type) -> list[str]:
        self._ensure_loaded()
        return list(self._map.get(token, []))

    def has_node(self, name: str) -> bool:
        self._ensure_loaded()
        return name in self._node_set

    def list_tokens(self) -> list[type]:
        self._ensure_loaded()
        return list(self._map.keys())

    def version(self) -> int:
        self._ensure_loaded()
        return self._version

    def register(self, token: type, consumers) -> None:
        self._ensure_loaded()
        self._map[token] = list(consumers)
        self._node_set = {node for values in self._map.values() for node in values}
        self._version += 1

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        ctx = self._context()
        mapping: dict[type, list[str]] = {}
        for node_def in getattr(ctx, "nodes", []):
            meta = getattr(node_def, "meta", None)
            if meta is None:
                continue
            node_name = getattr(meta, "name", "")
            consumes = getattr(meta, "consumes", [])
            for token in consumes:
                mapping.setdefault(token, []).append(node_name)
        self._map = mapping
        self._node_set = {node for values in mapping.values() for node in values}
        self._version += 1
        self._loaded = True

    def _context(self) -> object:
        # Avoid hard dependency on concrete context type; accept duck-typed object with `nodes`.
        candidate = self.app_context
        if hasattr(candidate, "nodes"):
            return candidate
        raise ValueError("DiscoveryConsumerRegistry app_context is not resolved via DI")
