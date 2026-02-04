from __future__ import annotations

import types
from dataclasses import dataclass

from stream_kernel.application_context import ApplicationContext
from stream_kernel.kernel.node import node


def _make_module() -> types.ModuleType:
    mod = types.ModuleType("stage_nodes")

    @node(name="a", stage="parse")
    @dataclass(frozen=True, slots=True)
    class A:
        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    @node(name="b", stage="features")
    def b(msg: object, ctx: object | None) -> list[object]:
        return [msg]

    @node(name="c", stage="parse")
    def c(msg: object, ctx: object | None) -> list[object]:
        return [msg]

    mod.A = A
    mod.b = b
    mod.c = c
    return mod


def test_context_groups_nodes_by_stage() -> None:
    # Nodes should be grouped by their stage labels.
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    stages = ctx.group_by_stage()
    names = {stage.name for stage in stages}
    assert names == {"parse", "features"}
    parse = next(s for s in stages if s.name == "parse")
    features = next(s for s in stages if s.name == "features")
    assert [n.meta.name for n in parse.nodes] == ["a", "c"]
    assert [n.meta.name for n in features.nodes] == ["b"]


def test_context_infers_stage_from_symbol_name_when_missing() -> None:
    # Missing stage should be inferred from the symbol name.
    mod = types.ModuleType("infer_stage")

    @node(name="x")
    @dataclass(frozen=True, slots=True)
    class X:
        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    mod.X = X

    ctx = ApplicationContext()
    ctx.discover([mod])
    stages = ctx.group_by_stage()
    assert [s.name for s in stages] == ["X"]
    assert [n.meta.name for n in stages[0].nodes] == ["x"]

def test_context_stage_override_from_config() -> None:
    # Config overrides should reassign stage grouping by node name.
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    overrides = {"b": "policy", "c": "policy"}
    stages = ctx.group_by_stage(stage_overrides=overrides)

    names = {stage.name for stage in stages}
    assert names == {"parse", "policy"}
    parse = next(s for s in stages if s.name == "parse")
    policy = next(s for s in stages if s.name == "policy")
    assert [n.meta.name for n in parse.nodes] == ["a"]
    assert [n.meta.name for n in policy.nodes] == ["b", "c"]
