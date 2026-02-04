from __future__ import annotations

import types
from dataclasses import dataclass

from stream_kernel.application_context import ApplicationContext
from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.application_context.inject import inject
from stream_kernel.kernel.node import node
from stream_kernel.kernel.stage import stage


class EventA:
    pass


@dataclass
class _StreamPort:
    name: str


def _make_module() -> types.ModuleType:
    mod = types.ModuleType("stage_method_nodes")

    @stage(name="parse")
    class ParseStage:
        stream: object = inject.stream(EventA)

        @node(name="a")
        def do_a(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    mod.ParseStage = ParseStage
    return mod


def test_method_nodes_are_bound_per_scenario() -> None:
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: _StreamPort("A"))

    scenario1 = ctx.build_scenario(scenario_id="s1", step_names=["a"], wiring={"injection_registry": reg})
    scenario2 = ctx.build_scenario(scenario_id="s2", step_names=["a"], wiring={"injection_registry": reg})

    a1 = scenario1.steps[0].step
    a2 = scenario2.steps[0].step

    assert a1 is not a2
    assert a1.__self__ is not a2.__self__
    assert a1.__self__.stream.name == "A"
    assert a2.__self__.stream.name == "A"
