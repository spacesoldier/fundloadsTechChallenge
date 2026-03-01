from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from stream_kernel.execution.orchestration.control_plane import (
    control_plane_bootstrap_inputs,
    should_include_business_steps,
)
from stream_kernel.kernel.scenario import Scenario, StepSpec


@dataclass(frozen=True, slots=True)
class RuntimeStartupAssemblyResult:
    scenario: object
    inputs: list[object]


def assemble_runtime_startup_scenario(
    *,
    runtime: dict[str, object],
    scenario: object,
    source_steps: list[StepSpec],
    control_plane_steps: list[StepSpec],
    lifecycle_steps: list[StepSpec],
    observability_steps: list[StepSpec],
    sink_steps: list[StepSpec],
    source_inputs: list[object],
) -> RuntimeStartupAssemblyResult:
    existing_steps = list(getattr(scenario, "steps", []))
    if not should_include_business_steps(runtime):
        existing_steps = []

    merged_steps = [
        *source_steps,
        *control_plane_steps,
        *lifecycle_steps,
        *existing_steps,
        *observability_steps,
        *sink_steps,
    ]
    if isinstance(scenario, Scenario):
        merged_scenario: object = Scenario(
            scenario_id=scenario.scenario_id,
            steps=tuple(merged_steps),
        )
    else:
        merged_scenario = SimpleNamespace(steps=list(merged_steps))

    inputs = control_plane_bootstrap_inputs(
        runtime=runtime,
        inputs=source_inputs,
    )
    return RuntimeStartupAssemblyResult(
        scenario=merged_scenario,
        inputs=inputs,
    )


__all__ = [
    "RuntimeStartupAssemblyResult",
    "assemble_runtime_startup_scenario",
]
