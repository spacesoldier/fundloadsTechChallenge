from __future__ import annotations

import types
from dataclasses import dataclass

import pytest

from stream_kernel.application_context import ApplicationContext, ContextBuildError
from stream_kernel.kernel.node import node


class RawToken:
    pass


class MidToken:
    pass


class OutToken:
    pass


def _make_module() -> types.ModuleType:
    mod = types.ModuleType("fake_nodes")

    # Source node emits the initial token (external input is modeled as a source).
    @node(name="source", emits=[RawToken])
    def source(msg: object, ctx: object | None) -> list[RawToken]:
        return [RawToken()]

    # ApplicationContext uses node consumes/emits to validate dependencies
    # (docs/framework/initial_stage/Application context and discovery.md).
    @node(name="a", stage="parse", consumes=[RawToken], emits=[MidToken])
    @dataclass(frozen=True, slots=True)
    class A:
        def __call__(self, msg: RawToken, ctx: object | None) -> list[MidToken]:
            return [MidToken()]

    @node(name="b", stage="features", consumes=[MidToken], emits=[OutToken])
    def b(msg: MidToken, ctx: object | None) -> list[OutToken]:
        return [OutToken()]

    mod.source = source
    mod.A = A
    mod.b = b
    return mod


def test_context_discovers_nodes() -> None:
    # ApplicationContext should populate nodes via discovery.
    ctx = ApplicationContext()
    ctx.discover([_make_module()])
    names = [n.meta.name for n in ctx.nodes]
    assert set(names) == {"source", "a", "b"}


def test_context_rejects_missing_dependency() -> None:
    # Missing provider for a consumed token should fail validation (ApplicationContext spec).
    ctx = ApplicationContext()

    class MissingToken:
        pass

    @node(name="c", consumes=[MissingToken], emits=[OutToken])
    def c(msg: MissingToken, ctx_obj: object | None) -> list[OutToken]:
        return [OutToken()]

    mod = types.ModuleType("bad_nodes")
    mod.c = c
    ctx.discover([mod])

    with pytest.raises(ContextBuildError):
        ctx.validate_dependencies(strict=True)


def test_context_detects_dependency_graph_without_errors() -> None:
    # Valid consumes/emits should pass validation (ApplicationContext spec).
    ctx = ApplicationContext()
    ctx.discover([_make_module()])
    ctx.validate_dependencies(strict=True)
