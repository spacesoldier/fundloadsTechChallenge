from __future__ import annotations

import types
from dataclasses import dataclass

import pytest

from stream_kernel.application_context import ApplicationContext, ContextBuildError
from stream_kernel.kernel.step_registry import StepRegistry
from stream_kernel.kernel.node import node


def _make_module() -> types.ModuleType:
    mod = types.ModuleType("fake_nodes")

    @node(name="a")
    @dataclass(frozen=True, slots=True)
    class A:
        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    @node(name="b", requires=["a"])
    def b(msg: object, ctx: object | None) -> list[object]:
        return [msg]

    mod.A = A
    mod.b = b
    return mod


def test_context_build_registry_registers_nodes() -> None:
    # build_registry should register all discovered nodes by name.
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    registry = ctx.build_registry()
    assert isinstance(registry, StepRegistry)
    assert set(registry.names()) == {"a", "b"}


def test_context_build_registry_fails_on_missing_dependency() -> None:
    # build_registry should fail fast when dependencies are missing.
    ctx = ApplicationContext()

    @node(name="c", requires=["missing"])
    def c(msg: object, ctx_obj: object | None) -> list[object]:
        return [msg]

    mod = types.ModuleType("bad_nodes")
    mod.c = c

    ctx.discover([mod])
    with pytest.raises(ContextBuildError):
        ctx.build_registry()
