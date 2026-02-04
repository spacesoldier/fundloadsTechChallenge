from __future__ import annotations

import types
from dataclasses import dataclass

import pytest

from stream_kernel.application_context import ApplicationContext, ContextBuildError
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.kernel.node import node


class EventA:
    pass


class EventB:
    pass


@dataclass
class _StreamPort:
    # Simple stream port stub for injection resolution.
    name: str


@dataclass
class _NodeWiring:
    # Minimal wiring object passed into ApplicationContext for injection resolution.
    injection_registry: InjectionRegistry
    strict: bool = True


def _make_module() -> types.ModuleType:
    mod = types.ModuleType("inject_nodes")

    @node(name="a")
    @dataclass(frozen=True, slots=True)
    class A:
        # Injected stream for EventA; will be resolved per scenario scope.
        stream: object = inject.stream(EventA)

        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    @node(name="b")
    @dataclass(frozen=True, slots=True)
    class B:
        # Injected stream for EventB; resolved independently from EventA.
        stream: object = inject.stream(EventB)

        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    mod.A = A
    mod.B = B
    return mod


def test_single_scenario_multiple_nodes() -> None:
    # One scenario with multiple nodes should resolve all injections.
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: _StreamPort("A"))
    reg.register_factory("stream", EventB, lambda: _StreamPort("B"))
    scope = reg.instantiate_for_scenario("s1")

    scenario = ctx.build_scenario(scenario_id="s1", step_names=["a", "b"], wiring=_NodeWiring(reg))

    a = scenario.steps[0].step
    b = scenario.steps[1].step
    assert isinstance(a.stream, _StreamPort)
    assert isinstance(b.stream, _StreamPort)
    assert a.stream.name == "A"
    assert b.stream.name == "B"


def test_two_scenarios_disjoint_nodes() -> None:
    # Two scenarios with disjoint nodes should resolve independently.
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: _StreamPort("A"))
    reg.register_factory("stream", EventB, lambda: _StreamPort("B"))

    scope1 = reg.instantiate_for_scenario("s1")
    scope2 = reg.instantiate_for_scenario("s2")

    scenario1 = ctx.build_scenario(scenario_id="s1", step_names=["a"], wiring=_NodeWiring(reg))
    scenario2 = ctx.build_scenario(scenario_id="s2", step_names=["b"], wiring=_NodeWiring(reg))

    a = scenario1.steps[0].step
    b = scenario2.steps[0].step
    assert a.stream.name == "A"
    assert b.stream.name == "B"


def test_two_scenarios_shared_nodes_have_distinct_instances() -> None:
    # Same node across scenarios should be instantiated separately per scenario.
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: _StreamPort("A"))

    scope1 = reg.instantiate_for_scenario("s1")
    scope2 = reg.instantiate_for_scenario("s2")

    scenario1 = ctx.build_scenario(scenario_id="s1", step_names=["a"], wiring=_NodeWiring(reg))
    scenario2 = ctx.build_scenario(scenario_id="s2", step_names=["a"], wiring=_NodeWiring(reg))

    a1 = scenario1.steps[0].step
    a2 = scenario2.steps[0].step
    assert a1 is not a2
    assert a1.stream is not a2.stream


def test_build_scenario_fails_on_missing_injection_in_strict_mode() -> None:
    # Strict mode must fail fast when a required injection is missing.
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    reg = InjectionRegistry()
    wiring = _NodeWiring(reg, strict=True)

    with pytest.raises(ContextBuildError):
        ctx.build_scenario(scenario_id="s1", step_names=["a"], wiring=wiring)


def test_build_scenario_allows_missing_injection_in_non_strict_mode() -> None:
    # Non-strict mode allows missing injections and uses None.
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    reg = InjectionRegistry()
    wiring = _NodeWiring(reg, strict=False)

    scenario = ctx.build_scenario(scenario_id="s1", step_names=["a"], wiring=wiring)
    a = scenario.steps[0].step
    assert a.stream is None
