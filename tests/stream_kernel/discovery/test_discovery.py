from __future__ import annotations

import types
from dataclasses import dataclass

import pytest

from stream_kernel.kernel.discovery import NodeDiscoveryError, discover_nodes
from stream_kernel.kernel.node import node


def _make_module() -> types.ModuleType:
    mod = types.ModuleType("fake_module")

    @node(name="a", stage="parse")
    @dataclass(frozen=True, slots=True)
    class A:
        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    @node(name="b")
    def b(msg: object, ctx: object | None) -> list[object]:
        return [msg]

    mod.A = A
    mod.b = b
    return mod


def test_discover_nodes_finds_decorated_callables() -> None:
    # Discovery should find only annotated nodes.
    mod = _make_module()
    nodes = discover_nodes([mod])
    names = [n.meta.name for n in nodes]
    assert set(names) == {"a", "b"}


def test_discover_nodes_rejects_duplicate_names() -> None:
    # Duplicate node names must raise an error.
    mod = types.ModuleType("dup_module")

    @node(name="dup")
    def f1(msg: object, ctx: object | None) -> list[object]:
        return [msg]

    @node(name="dup")
    def f2(msg: object, ctx: object | None) -> list[object]:
        return [msg]

    mod.f1 = f1
    mod.f2 = f2

    with pytest.raises(NodeDiscoveryError):
        discover_nodes([mod])


def test_discover_nodes_ignores_undecorated_symbols() -> None:
    # Plain callables without @node should be ignored.
    mod = types.ModuleType("plain_module")

    def plain(msg: object, ctx: object | None) -> list[object]:
        return [msg]

    mod.plain = plain

    nodes = discover_nodes([mod])
    assert nodes == []


def test_discover_nodes_allows_reexport_of_same_node_target() -> None:
    # Re-export of the same node object from another module should not be treated as duplicate declaration.
    mod_a = types.ModuleType("mod_a")
    mod_b = types.ModuleType("mod_b")

    @node(name="same")
    def same(msg: object, ctx: object | None) -> list[object]:
        return [msg]

    mod_a.same = same
    mod_b.same_alias = same

    nodes = discover_nodes([mod_a, mod_b])
    assert [n.meta.name for n in nodes] == ["same"]


def test_discover_nodes_marks_framework_observer_nodes_as_service() -> None:
    # Framework observability observer modules are auto-marked as service nodes.
    mod = types.ModuleType("stream_kernel.observability.observers.fake")

    @node(name="trace_observer")
    def trace_observer(msg: object, ctx: object | None) -> list[object]:
        return [msg]

    mod.trace_observer = trace_observer
    nodes = discover_nodes([mod])
    assert len(nodes) == 1
    assert nodes[0].meta.service is True
