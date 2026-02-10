from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.consumer_registry import ConsumerRegistry
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.router import Router


@service(name="routing_port")
@dataclass(slots=True)
class RoutingPort:
    # Adapter that bridges ConsumerRegistry with Router (Execution runtime and routing integration §6).
    registry: object = inject.service(ConsumerRegistry)
    strict: bool = True
    _cache_version: int | None = None
    _cache_map: dict[type, list[str]] = field(default_factory=dict)

    def route(self, outputs: Iterable[object], *, source: str | None = None) -> list[tuple[str, object]]:
        # Build a fresh consumer map per call to reflect dynamic registry state.
        consumer_map = self._build_consumer_map()
        router = Router(consumers=consumer_map, strict=self.strict)
        return router.route(outputs, source=source)

    def _build_consumer_map(self) -> dict[type, list[str]]:
        # Cache consumer map based on registry version for O(1) reuse on hot paths.
        registry = self._registry()
        version = registry.version()
        if self._cache_version == version:
            return self._cache_map

        consumer_map: dict[type, list[str]] = {}
        for token in registry.list_tokens():
            consumer_map[token] = registry.get_consumers(token)

        self._cache_version = version
        self._cache_map = consumer_map
        return consumer_map

    def _registry(self) -> ConsumerRegistry:
        if isinstance(self.registry, ConsumerRegistry):
            return self.registry
        # Support duck-typed test doubles with protocol methods.
        if (
            callable(getattr(self.registry, "get_consumers", None))
            and callable(getattr(self.registry, "has_node", None))
            and callable(getattr(self.registry, "list_tokens", None))
            and callable(getattr(self.registry, "version", None))
            and callable(getattr(self.registry, "register", None))
        ):
            return self.registry  # type: ignore[return-value]
        raise ValueError("RoutingPort registry is not resolved via DI")
