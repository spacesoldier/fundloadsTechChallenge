from __future__ import annotations

# RoutingService adapter rules are in docs/framework/initial_stage/Execution runtime and routing integration.md.
from stream_kernel.integration.consumer_registry import ConsumerRegistry, InMemoryConsumerRegistry
from stream_kernel.routing.routing_service import RoutingService
from stream_kernel.routing.envelope import Envelope


class X:
    def __init__(self, value: str) -> None:
        self.value = value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, X) and other.value == self.value


def test_routing_port_routes_by_type_using_registry() -> None:
    # RoutingService should use registry consumers for fan-out.
    registry = InMemoryConsumerRegistry({X: ["A", "B"]})
    port = RoutingService(registry=registry, strict=True)
    payload = X("x")
    deliveries = port.route([payload])
    assert deliveries == [("A", payload), ("B", payload)]


def test_routing_port_respects_target_override() -> None:
    # Targeted envelopes should bypass fan-out (delegated to Router).
    registry = InMemoryConsumerRegistry({X: ["A", "B"]})
    port = RoutingService(registry=registry, strict=True)
    payload = X("x")
    deliveries = port.route([Envelope(payload=payload, target="B")])
    assert deliveries == [("B", payload)]


def test_routing_port_reflects_registry_updates() -> None:
    # Registry updates should be observed on subsequent routes.
    registry = InMemoryConsumerRegistry({X: ["A"]})
    port = RoutingService(registry=registry, strict=True)
    payload = X("x")
    assert port.route([payload]) == [("A", payload)]
    registry.register(X, ["C"])
    assert port.route([payload]) == [("C", payload)]


def test_routing_port_returns_structured_result() -> None:
    # Route contract is structured and exposes local/boundary/terminal channels.
    registry = InMemoryConsumerRegistry({X: ["A", "B"]})
    port = RoutingService(registry=registry, strict=True)
    payload = X("x")

    result = port.route([payload])
    assert hasattr(result, "local_deliveries")
    assert hasattr(result, "boundary_deliveries")
    assert hasattr(result, "terminal_outputs")
    assert result.local_deliveries == [("A", payload), ("B", payload)]


def test_routing_port_forwards_source_for_self_loop_protection() -> None:
    # RoutingService should pass source to Router to support self-loop filtering.
    registry = InMemoryConsumerRegistry({X: ["A", "B"]})
    port = RoutingService(registry=registry, strict=True)
    payload = X("x")
    deliveries = port.route([payload], source="A")
    assert deliveries == [("B", payload)]


def test_routing_port_uses_cached_consumer_map_until_version_changes() -> None:
    # RoutingService should rebuild the consumer map only when registry version changes.
    class CountingRegistry(ConsumerRegistry):
        def __init__(self) -> None:
            self._map = {X: ["A"]}
            self._version = 0
            self.list_calls = 0

        def get_consumers(self, token: type) -> list[str]:
            return list(self._map.get(token, []))

        def has_node(self, name: str) -> bool:
            return any(name in items for items in self._map.values())

        def list_tokens(self) -> list[type]:
            self.list_calls += 1
            return list(self._map.keys())

        def register(self, token: type, consumers: list[str]) -> None:
            self._map[token] = list(consumers)
            self._version += 1

        def version(self) -> int:
            return self._version

    registry = CountingRegistry()
    port = RoutingService(registry=registry, strict=True)
    payload = X("x")
    port.route([payload])
    port.route([payload])
    assert registry.list_calls == 1
    registry.register(X, ["B"])
    port.route([payload])
    assert registry.list_calls == 2
