from __future__ import annotations

import types
from dataclasses import dataclass

import pytest

from stream_kernel.application_context import ApplicationContext, ContextBuildError
from stream_kernel.kernel.node import node


def _make_module() -> types.ModuleType:
    mod = types.ModuleType("fake_nodes")

    @node(name="a")
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


def test_build_scenario_preserves_step_order() -> None:
    # Scenario order should match the provided step list.
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    scenario = ctx.build_scenario(
        scenario_id="test",
        step_names=["b", "a"],
        wiring={},
    )

    assert [step.name for step in scenario.steps] == ["b", "a"]


def test_build_scenario_rejects_unknown_steps() -> None:
    # Unknown step names should raise a context error.
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    with pytest.raises(ContextBuildError):
        ctx.build_scenario(
            scenario_id="test",
            step_names=["missing"],
            wiring={},
        )
