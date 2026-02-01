from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.kernel.scenario import Scenario, StepSpec
from stream_kernel.kernel.step_registry import StepRegistry, UnknownStepError


class InvalidScenarioConfigError(ValueError):
    pass


class StepBuildError(RuntimeError):
    def __init__(self, step_name: str, cause: Exception) -> None:
        super().__init__(f"Failed to build step '{step_name}': {cause}")
        self.step_name = step_name
        self.cause = cause


@dataclass(frozen=True, slots=True)
class ScenarioBuilder:
    # ScenarioBuilder assembles Scenario from config and registry (ScenarioBuilder spec).
    registry: StepRegistry

    def build(self, *, scenario_id: str, steps: list[dict[str, object]], wiring: dict[str, object]) -> Scenario:
        if not steps:
            raise InvalidScenarioConfigError("Scenario steps list is empty")

        built_steps: list[StepSpec] = []
        for idx, step_cfg in enumerate(steps):
            name = step_cfg.get("name")
            if not isinstance(name, str):
                raise InvalidScenarioConfigError(f"steps[{idx}].name must be a string")
            try:
                factory = self.registry.get(name)
            except UnknownStepError as exc:
                raise UnknownStepError(name) from exc

            config = step_cfg.get("config", {})
            if not isinstance(config, dict):
                raise InvalidScenarioConfigError(f"steps[{idx}].config must be a mapping")

            try:
                step = factory(config, wiring)
            except Exception as exc:  # noqa: BLE001 - wrap with explicit error
                raise StepBuildError(name, exc) from exc

            built_steps.append(StepSpec(name=name, step=step))

        return Scenario(scenario_id=scenario_id, steps=built_steps)
