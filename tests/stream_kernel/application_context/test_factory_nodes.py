from __future__ import annotations

import types

import pytest

# Factory-node behavior is defined in docs/framework/initial_stage/Factory and injection model.md.
from stream_kernel.application_context import ApplicationContext, ContextBuildError
from stream_kernel.kernel.node import node
from stream_kernel.kernel.scenario_builder import ScenarioBuilder, StepBuildError


def test_function_node_factory_builds_step_once_with_config() -> None:
    # Function nodes are treated as factories and receive step config at build time.
    calls: list[dict[str, object]] = []

    @node(name="factory_node")
    def factory(cfg: dict[str, object]):
        calls.append(dict(cfg))

        def step(msg: int, ctx: object | None):
            return [msg + int(cfg["inc"])]

        return step

    mod = types.ModuleType("factory_nodes")
    mod.factory = factory

    ctx = ApplicationContext()
    ctx.discover([mod])
    registry = ctx.build_registry()
    builder = ScenarioBuilder(registry)

    scenario = builder.build(
        scenario_id="s1",
        steps=[{"name": "factory_node", "config": {"inc": 2}}],
        wiring={},
    )

    step = scenario.steps[0].step
    assert step(1, None) == [3]
    assert step(2, None) == [4]
    assert calls == [{"inc": 2}]


def test_function_node_factory_must_return_callable() -> None:
    # Factories must return a callable step; non-callable results are rejected.
    @node(name="bad_factory")
    def bad_factory(cfg: dict[str, object]):
        return 123

    mod = types.ModuleType("factory_bad")
    mod.bad_factory = bad_factory

    ctx = ApplicationContext()
    ctx.discover([mod])
    registry = ctx.build_registry()
    builder = ScenarioBuilder(registry)

    with pytest.raises(StepBuildError):
        builder.build(
            scenario_id="s1",
            steps=[{"name": "bad_factory", "config": {}}],
            wiring={},
        )


def test_class_nodes_are_not_treated_as_factories() -> None:
    # Class nodes are instantiated directly (factory rules apply only to functions).
    @node(name="class_node")
    class ClassNode:
        def __call__(self, msg: int, ctx: object | None):
            return [msg + 1]

    mod = types.ModuleType("class_nodes")
    mod.ClassNode = ClassNode

    ctx = ApplicationContext()
    ctx.discover([mod])
    registry = ctx.build_registry()
    builder = ScenarioBuilder(registry)

    scenario = builder.build(
        scenario_id="s1",
        steps=[{"name": "class_node", "config": {"ignored": True}}],
        wiring={},
    )

    assert scenario.steps[0].step(1, None) == [2]


def test_function_step_is_not_called_as_factory() -> None:
    # Plain function steps should be used directly when cfg-call fails (factory heuristic).
    @node(name="plain_step")
    def plain_step(msg: int, ctx: object | None):
        return [msg + 5]

    mod = types.ModuleType("plain_nodes")
    mod.plain_step = plain_step

    ctx = ApplicationContext()
    ctx.discover([mod])
    registry = ctx.build_registry()
    builder = ScenarioBuilder(registry)

    scenario = builder.build(
        scenario_id="s1",
        steps=[{"name": "plain_step", "config": {}}],
        wiring={},
    )

    assert scenario.steps[0].step(1, None) == [6]
