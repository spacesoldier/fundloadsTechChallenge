from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.execution.runner_port import RunnerPort
from stream_kernel.kernel.scenario import Scenario
from stream_kernel.kernel.scenario_builder import ScenarioBuilder
from stream_kernel.kernel.step_registry import StepRegistry


@dataclass(frozen=True, slots=True)
class AppRuntime:
    # AppRuntime is a small bundle for runner + scenario (Composition Root spec).
    runner: RunnerPort
    scenario: Scenario


@dataclass(frozen=True, slots=True)
class NoopRunner:
    # Transitional composition-root runner placeholder while runtime owns execution wiring.
    def run(self) -> None:
        return None


def build_runtime(*, config: dict[str, object], wiring: dict[str, object]) -> AppRuntime:
    # Composition root wires registry, builder, and runner (docs/implementation/kernel/Composition Root Spec.md).
    steps_cfg = config.get("steps")
    scenario_id = config.get("scenario_id")
    if not isinstance(steps_cfg, list) or not isinstance(scenario_id, str):
        raise ValueError("Invalid config: scenario_id and steps are required")

    registry = StepRegistry()
    # Steps are provided via wiring in tests; real wiring will use adapters and step factories.
    for name, factory in wiring.get("steps", {}).items():
        registry.register(name, factory)

    scenario = ScenarioBuilder(registry).build(
        scenario_id=scenario_id,
        steps=steps_cfg,
        wiring=wiring,
    )
    runner = NoopRunner()
    return AppRuntime(runner=runner, scenario=scenario)
