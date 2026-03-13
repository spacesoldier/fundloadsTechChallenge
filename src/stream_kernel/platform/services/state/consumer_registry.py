from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.application_context.application_context import ApplicationContext
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.consumer_registry import (
    ConsumerRegistry,
    ConsumerRegistryStore,
    InMemoryConsumerRegistry,
)
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore


@service(name="consumer_registry")
@dataclass(slots=True)
class DiscoveryConsumerRegistry(ConsumerRegistry):
    # Consumer registry service backed by discovered node contracts in ApplicationContext.
    app_context: object = inject.service(ApplicationContext)
    store: object = inject.kv(ConsumerRegistryStore)
    _delegate: InMemoryConsumerRegistry | None = field(default=None, init=False, repr=False)
    _loaded: bool = False

    def get_consumers(self, token: type) -> list[str]:
        self._ensure_loaded()
        return self._registry().get_consumers(token)

    def has_node(self, name: str) -> bool:
        self._ensure_loaded()
        return self._registry().has_node(name)

    def list_tokens(self) -> list[type]:
        self._ensure_loaded()
        return self._registry().list_tokens()

    def version(self) -> int:
        self._ensure_loaded()
        return self._registry().version()

    def register(self, token: type, consumers) -> None:
        self._ensure_loaded()
        self._registry().register(token, consumers)

    def unregister(self, token: type) -> None:
        self._ensure_loaded()
        self._registry().unregister(token)

    def unregister_node(self, name: str) -> None:
        self._ensure_loaded()
        self._registry().unregister_node(name)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        registry = self._registry()
        if registry.version() > 0:
            self._loaded = True
            return
        ctx = self._context()
        for node_def in getattr(ctx, "nodes", []):
            meta = getattr(node_def, "meta", None)
            if meta is None:
                continue
            node_name = getattr(meta, "name", "")
            consumes = getattr(meta, "consumes", [])
            for token in consumes:
                existing = registry.get_consumers(token)
                existing.append(node_name)
                registry.register(token, existing)
        self._loaded = True

    def _context(self) -> object:
        # Avoid hard dependency on concrete context type; accept duck-typed object with `nodes`.
        candidate = self.app_context
        if hasattr(candidate, "nodes"):
            return candidate
        raise ValueError("DiscoveryConsumerRegistry app_context is not resolved via DI")

    def _registry(self) -> InMemoryConsumerRegistry:
        if isinstance(self._delegate, InMemoryConsumerRegistry):
            return self._delegate
        candidate = self.store
        kv_store = candidate if isinstance(candidate, KVStore) else InMemoryKvStore()
        self._delegate = InMemoryConsumerRegistry(store=kv_store)
        return self._delegate
