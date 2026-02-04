from __future__ import annotations

import types
from dataclasses import dataclass

import pytest

from stream_kernel.application_context import ApplicationContext, ContextBuildError
from stream_kernel.kernel.node import node


def _make_module() -> types.ModuleType:
    mod = types.ModuleType("fake_nodes")

    @node(name="a", stage="parse")
    @dataclass(frozen=True, slots=True)
    class A:
        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    @node(name="b", stage="features", requires=["a"])
    def b(msg: object, ctx: object | None) -> list[object]:
        return [msg]

    mod.A = A
    mod.b = b
    return mod


def test_context_discovers_nodes() -> None:
    # ApplicationContext should populate nodes via discovery.
    ctx = ApplicationContext()
    ctx.discover([_make_module()])
    names = [n.meta.name for n in ctx.nodes]
    assert set(names) == {"a", "b"}


def test_context_rejects_missing_dependency() -> None:
    # Missing dependency in requires should fail validation.
    ctx = ApplicationContext()

    @node(name="c", requires=["missing"])
    def c(msg: object, ctx_obj: object | None) -> list[object]:
        return [msg]

    mod = types.ModuleType("bad_nodes")
    mod.c = c
    ctx.discover([mod])

    with pytest.raises(ContextBuildError):
        ctx.validate_dependencies()


def test_context_detects_dependency_graph_without_errors() -> None:
    # Valid requires/provides should pass validation.
    ctx = ApplicationContext()
    ctx.discover([_make_module()])
    ctx.validate_dependencies()
