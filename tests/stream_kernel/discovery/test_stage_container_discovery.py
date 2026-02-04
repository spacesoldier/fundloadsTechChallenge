from __future__ import annotations

import types
from dataclasses import dataclass

import pytest

from stream_kernel.kernel.node import node
from stream_kernel.kernel.stage import stage
from stream_kernel.kernel.discovery import discover_nodes, NodeDiscoveryError


def test_stage_container_propagates_stage_to_nodes() -> None:
    # Stage container should set default stage for inner nodes.
    mod = types.ModuleType("stage_container")

    @stage(name="parse")
    class ParseStage:
        @node(name="a")
        @dataclass(frozen=True, slots=True)
        class A:
            def __call__(self, msg: object, ctx: object | None) -> list[object]:
                return [msg]

        @node(name="b", stage="features")
        def b(msg: object, ctx: object | None) -> list[object]:
            return [msg]

    mod.ParseStage = ParseStage

    nodes = discover_nodes([mod])
    by_name = {n.meta.name: n.meta.stage for n in nodes}
    assert by_name["a"] == "parse"
    assert by_name["b"] == "features"


def test_stage_container_keeps_explicit_node_stage() -> None:
    # Node-level stage must override container stage.
    mod = types.ModuleType("stage_container_override")

    @stage(name="policy")
    class PolicyStage:
        @node(name="c", stage="audit")
        def c(msg: object, ctx: object | None) -> list[object]:
            return [msg]

    mod.PolicyStage = PolicyStage

    nodes = discover_nodes([mod])
    assert {n.meta.name: n.meta.stage for n in nodes} == {"c": "audit"}


def test_stage_container_duplicate_node_names_fail() -> None:
    # Duplicate names across stage containers must fail.
    mod = types.ModuleType("dup_stage_container")

    @stage(name="one")
    class One:
        @node(name="dup")
        def a(msg: object, ctx: object | None) -> list[object]:
            return [msg]

    @stage(name="two")
    class Two:
        @node(name="dup")
        def b(msg: object, ctx: object | None) -> list[object]:
            return [msg]

    mod.One = One
    mod.Two = Two

    with pytest.raises(NodeDiscoveryError):
        discover_nodes([mod])

def test_stage_container_conflicts_with_top_level_node_name() -> None:
    # Top-level and container node name collisions must fail.
    mod = types.ModuleType("dup_stage_top")

    @node(name="dup")
    def top(msg: object, ctx: object | None) -> list[object]:
        return [msg]

    @stage(name="one")
    class One:
        @node(name="dup")
        def a(msg: object, ctx: object | None) -> list[object]:
            return [msg]

    mod.top = top
    mod.One = One

    with pytest.raises(NodeDiscoveryError):
        discover_nodes([mod])
