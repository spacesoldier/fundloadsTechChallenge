from __future__ import annotations

import pytest

# ScenarioBuilder contract is documented in docs/implementation/kernel/ScenarioBuilder Spec.md.
from fund_load.kernel.scenario_builder import (
    InvalidScenarioConfigError,
    ScenarioBuilder,
    StepBuildError,
    UnknownStepError,
)
from fund_load.kernel.step_registry import StepRegistry


def test_scenario_builder_builds_steps_in_order() -> None:
    # Builder must preserve step order from config.
    registry = StepRegistry()
    registry.register("a", lambda cfg, wiring: lambda msg, ctx: [msg])
    registry.register("b", lambda cfg, wiring: lambda msg, ctx: [msg])
    builder = ScenarioBuilder(registry)
    scenario = builder.build(
        scenario_id="test",
        steps=[{"name": "a"}, {"name": "b"}],
        wiring={},
    )
    assert [s.name for s in scenario.steps] == ["a", "b"]


def test_scenario_builder_unknown_step_fails() -> None:
    # Unknown step should raise UnknownStepError with context.
    registry = StepRegistry()
    builder = ScenarioBuilder(registry)
    with pytest.raises(UnknownStepError):
        builder.build(scenario_id="test", steps=[{"name": "missing"}], wiring={})


def test_scenario_builder_missing_steps_fails() -> None:
    # Missing steps list is invalid config.
    registry = StepRegistry()
    builder = ScenarioBuilder(registry)
    with pytest.raises(InvalidScenarioConfigError):
        builder.build(scenario_id="test", steps=[], wiring={})


def test_scenario_builder_wraps_factory_error() -> None:
    # Factory errors should be wrapped for clearer diagnostics.
    registry = StepRegistry()

    def bad_factory(cfg, wiring):
        raise ValueError("boom")

    registry.register("bad", bad_factory)
    builder = ScenarioBuilder(registry)
    with pytest.raises(StepBuildError):
        builder.build(scenario_id="test", steps=[{"name": "bad"}], wiring={})
