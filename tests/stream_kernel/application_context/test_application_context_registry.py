from __future__ import annotations

import types
from dataclasses import dataclass

import pytest

from stream_kernel.application_context import ApplicationContext, ContextBuildError
from stream_kernel.kernel.step_registry import StepRegistry
from stream_kernel.kernel.node import node


class RawToken:
    pass


class MidToken:
    pass


class OutToken:
    pass


def _make_module() -> types.ModuleType:
    mod = types.ModuleType("fake_nodes")

    # Source node emits the initial token (external input modeled as a source).
    @node(name="source", emits=[RawToken])
    def source(msg: object, ctx: object | None) -> list[RawToken]:
        return [RawToken()]

    # Registry should accept nodes declared with consumes/emits (ApplicationContext spec).
    @node(name="a", consumes=[RawToken], emits=[MidToken])
    @dataclass(frozen=True, slots=True)
    class A:
        def __call__(self, msg: RawToken, ctx: object | None) -> list[MidToken]:
            return [MidToken()]

    @node(name="b", consumes=[MidToken], emits=[OutToken])
    def b(msg: MidToken, ctx: object | None) -> list[OutToken]:
        return [OutToken()]

    mod.source = source
    mod.A = A
    mod.b = b
    return mod


def test_context_build_registry_registers_nodes() -> None:
    # build_registry should register all discovered nodes by name.
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    registry = ctx.build_registry(strict=True)
    assert isinstance(registry, StepRegistry)
    assert set(registry.names()) == {"source", "a", "b"}


def test_context_build_registry_fails_on_missing_dependency() -> None:
    # build_registry should fail fast when consumed tokens have no providers.
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
        ctx.build_registry(strict=True)
