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
