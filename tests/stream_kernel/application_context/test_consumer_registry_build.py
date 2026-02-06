from __future__ import annotations

import types
from dataclasses import dataclass

# Consumer registry build is documented in Execution runtime + routing integration §4.2.
from stream_kernel.application_context import ApplicationContext
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.kernel.node import node
from stream_kernel.kernel.stage import stage


class X:
    pass


class Y:
    pass


def _make_module() -> types.ModuleType:
    mod = types.ModuleType("consumer_registry_nodes")

    @node(name="a", consumes=[X], emits=[Y])
    @dataclass(frozen=True, slots=True)
    class A:
        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return []

    @node(name="b", consumes=[X], emits=[])
    @dataclass(frozen=True, slots=True)
    class B:
        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return []

    @node(name="source", consumes=[], emits=[X])
    @dataclass(frozen=True, slots=True)
    class Source:
        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    @stage(name="parse")
    class ParseStage:
        @node(name="d", consumes=[Y], emits=[])
        def do_d(self, msg: object, ctx: object | None) -> list[object]:
            return []

    mod.A = A
    mod.B = B
    mod.Source = Source
    mod.ParseStage = ParseStage
    return mod


def test_build_consumer_registry_from_discovery() -> None:
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    registry = ctx.build_consumer_registry()
    assert isinstance(registry, InMemoryConsumerRegistry)

    # Discovery order should be preserved in the token list and consumer order.
    assert registry.list_tokens() == [X, Y]
    assert registry.get_consumers(X) == ["a", "b"]
    assert registry.get_consumers(Y) == ["d"]

    # Only nodes that consume tokens appear as consumers.
    assert registry.has_node("a") is True
    assert registry.has_node("source") is False


def test_build_consumer_registry_empty_when_no_nodes() -> None:
    ctx = ApplicationContext()
    ctx.nodes = []

    registry = ctx.build_consumer_registry()
    assert registry.list_tokens() == []
