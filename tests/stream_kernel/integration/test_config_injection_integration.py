from __future__ import annotations

import types
from dataclasses import dataclass

import pytest
from pydantic import BaseModel

from stream_kernel.application_context import ApplicationContext, ContextBuildError
from stream_kernel.application_context.config_inject import config
from stream_kernel.kernel.node import node
from stream_kernel.kernel.stage import stage


def _make_module() -> types.ModuleType:
    mod = types.ModuleType("config_nodes")

    @node(name="a")
    @dataclass(frozen=True, slots=True)
    class A:
        limit: object = config.value("limits.daily")

        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    mod.A = A
    return mod


def test_config_value_injected_into_node_fields() -> None:
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    scenario = ctx.build_scenario(
        scenario_id="s1",
        step_names=["a"],
        wiring={"config": {"limits": {"daily": 5}}},
    )

    a = scenario.steps[0].step
    assert a.limit == 5


def test_pydantic_config_model_is_accepted() -> None:
    # Framework should accept config objects exposing model_dump()/dict() (Pydantic models).
    class _Cfg(BaseModel):
        limits: dict[str, object]

    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    scenario = ctx.build_scenario(
        scenario_id="s1",
        step_names=["a"],
        wiring={"config": _Cfg(limits={"daily": 12})},
    )

    a = scenario.steps[0].step
    assert a.limit == 12




def test_missing_config_raises_in_strict_mode() -> None:
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    with pytest.raises(ContextBuildError):
        ctx.build_scenario(
            scenario_id="s1",
            step_names=["a"],
            wiring={"config": {}},
        )


def test_missing_config_allowed_in_non_strict_mode() -> None:
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    scenario = ctx.build_scenario(
        scenario_id="s1",
        step_names=["a"],
        wiring={"config": {}, "strict": False},
    )

    a = scenario.steps[0].step
    assert a.limit is None


def test_config_must_be_mapping_non_mapping_fails() -> None:
    # Non-mapping config should fail fast during scenario build.
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    with pytest.raises(ContextBuildError):
        ctx.build_scenario(
            scenario_id="s1",
            step_names=["a"],
            wiring={"config": "not-a-mapping"},
        )


def test_config_default_value_is_applied() -> None:
    # Defaults should be used when config path is missing and default is provided.
    ctx = ApplicationContext()

    mod = types.ModuleType("config_nodes_default")

    @node(name="a")
    @dataclass(frozen=True, slots=True)
    class A:
        limit: object = config.value("limits.daily", default=7)

        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    mod.A = A
    ctx.discover([mod])

    scenario = ctx.build_scenario(
        scenario_id="s1",
        step_names=["a"],
        wiring={"config": {}},
    )

    a = scenario.steps[0].step
    assert a.limit == 7


def test_config_injection_applies_to_stage_container_instance() -> None:
    # Config injection should apply to @stage container instances for method nodes.
    ctx = ApplicationContext()

    mod = types.ModuleType("config_stage_nodes")

    @stage(name="parse")
    class ParseStage:
        limit: object = config.value("limits.daily", default=9)

        @node(name="a")
        def do_a(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    mod.ParseStage = ParseStage
    ctx.discover([mod])

    scenario = ctx.build_scenario(
        scenario_id="s1",
        step_names=["a"],
        wiring={"config": {}},
    )

    step = scenario.steps[0].step
    assert step.__self__.limit == 9

def test_config_must_be_mapping() -> None:
    # Non-mapping config should fail fast during scenario build.
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    with pytest.raises(ContextBuildError):
        ctx.build_scenario(
            scenario_id="s1",
            step_names=["a"],
            wiring={"config": "not-a-mapping"},
        )

def test_config_default_value_is_applied() -> None:
    # Defaults should be used when config path is missing and default is provided.
    ctx = ApplicationContext()

    mod = types.ModuleType("config_nodes_default")

    @node(name="a")
    @dataclass(frozen=True, slots=True)
    class A:
        limit: object = config.value("limits.daily", default=7)

        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    mod.A = A
    ctx.discover([mod])

    scenario = ctx.build_scenario(
        scenario_id="s1",
        step_names=["a"],
        wiring={"config": {}},
    )

    a = scenario.steps[0].step
    assert a.limit == 7


def test_config_injection_applies_to_stage_container_instance() -> None:
    # Config injection should apply to @stage container instances for method nodes.
    ctx = ApplicationContext()

    mod = types.ModuleType("config_stage_nodes")

    @stage(name="parse")
    class ParseStage:
        limit: object = config.value("limits.daily", default=9)

        @node(name="a")
        def do_a(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    mod.ParseStage = ParseStage
    ctx.discover([mod])

    scenario = ctx.build_scenario(
        scenario_id="s1",
        step_names=["a"],
        wiring={"config": {}},
    )

    step = scenario.steps[0].step
    assert step.__self__.limit == 9
