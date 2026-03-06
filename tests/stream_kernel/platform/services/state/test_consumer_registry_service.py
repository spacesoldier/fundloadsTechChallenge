from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.state.consumer_registry import DiscoveryConsumerRegistry


class _X:
    pass


class _Y:
    pass


@dataclass(slots=True)
class _Meta:
    name: str
    consumes: list[type]


@dataclass(slots=True)
class _NodeDef:
    meta: _Meta


@dataclass(slots=True)
class _Context:
    nodes: list[_NodeDef]


def test_discovery_consumer_registry_loads_from_context_into_store() -> None:
    ctx = _Context(
        nodes=[
            _NodeDef(meta=_Meta(name="a", consumes=[_X])),
            _NodeDef(meta=_Meta(name="b", consumes=[_X, _Y])),
        ]
    )
    store = InMemoryKvStore()
    registry = DiscoveryConsumerRegistry(app_context=ctx, store=store)

    assert registry.get_consumers(_X) == ["a", "b"]
    assert registry.get_consumers(_Y) == ["b"]
    assert registry.has_node("b") is True
    assert registry.version() == 3


def test_discovery_consumer_registry_reuses_existing_kv_state() -> None:
    store = InMemoryKvStore()
    first = DiscoveryConsumerRegistry(
        app_context=_Context(nodes=[_NodeDef(meta=_Meta(name="a", consumes=[_X]))]),
        store=store,
    )
    assert first.get_consumers(_X) == ["a"]

    second = DiscoveryConsumerRegistry(
        app_context=_Context(nodes=[]),
        store=store,
    )
    assert second.get_consumers(_X) == ["a"]
    assert second.list_tokens() == [_X]

