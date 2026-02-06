from __future__ import annotations

# ConsumerRegistry contract is documented in docs/framework/initial_stage/Execution runtime and routing integration.md.
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry


class X:
    pass


class Y:
    pass


def test_consumer_registry_returns_consumers_for_token() -> None:
    # Registry should return the registered consumers for a token.
    registry = InMemoryConsumerRegistry({X: ["A", "B"]})
    assert registry.get_consumers(X) == ["A", "B"]


def test_consumer_registry_returns_empty_for_unknown_token() -> None:
    # Unknown tokens map to an empty consumer list (safe default).
    registry = InMemoryConsumerRegistry({X: ["A"]})
    assert registry.get_consumers(Y) == []


def test_consumer_registry_has_node_checks_known_nodes() -> None:
    # has_node should reflect any node present in the registry map.
    registry = InMemoryConsumerRegistry({X: ["A", "B"]})
    assert registry.has_node("A") is True
    assert registry.has_node("Z") is False


def test_consumer_registry_lists_tokens_in_order() -> None:
    # list_tokens should return keys in insertion order for deterministic routing.
    registry = InMemoryConsumerRegistry({X: ["A"], Y: ["B"]})
    assert registry.list_tokens() == [X, Y]


def test_consumer_registry_can_update_mapping() -> None:
    # Dynamic update: registry should reflect new mappings after register().
    registry = InMemoryConsumerRegistry({X: ["A"]})
    registry.register(X, ["C"])
    assert registry.get_consumers(X) == ["C"]


def test_consumer_registry_has_node_reflects_updates() -> None:
    # has_node should reflect node removals after re-registering consumers.
    registry = InMemoryConsumerRegistry({X: ["A", "B"], Y: ["B"]})
    assert registry.has_node("B") is True
    registry.register(Y, [])
    assert registry.has_node("B") is True  # still present via X
    registry.register(X, [])
    assert registry.has_node("B") is False


def test_consumer_registry_version_increments_on_register() -> None:
    # Version should change after each register call for cache invalidation.
    registry = InMemoryConsumerRegistry({X: ["A"]})
    version = registry.version()
    registry.register(X, ["B"])
    assert registry.version() == version + 1
