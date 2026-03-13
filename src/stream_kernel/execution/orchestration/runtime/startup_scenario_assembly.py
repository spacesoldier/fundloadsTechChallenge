from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from stream_kernel.execution.orchestration.runtime.startup_mode import (
    is_process_supervisor_mode,
    should_include_business_steps,
)
from stream_kernel.kernel.scenario import Scenario, StepSpec
from stream_kernel.platform.services.runtime.control_plane_events import ControlPlaneInitEvent
from stream_kernel.routing.envelope import Envelope


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
    init_discovery: dict[str, object] | None = None,
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

    inputs = _append_control_plane_init_input(
        runtime=runtime,
        inputs=source_inputs,
        init_discovery=init_discovery,
    )
    return RuntimeStartupAssemblyResult(
        scenario=merged_scenario,
        inputs=inputs,
    )


def _append_control_plane_init_input(
    *,
    runtime: dict[str, object] | None,
    inputs: list[object] | tuple[object, ...],
    init_discovery: dict[str, object] | None,
) -> list[object]:
    resolved = list(inputs)
    if not is_process_supervisor_mode(runtime):
        return resolved
    payload_runtime = runtime if isinstance(runtime, dict) else {}
    # Root and leaf both start through the same graph entrypoint.
    # Process role filtering is handled by system.cp.bootstrap_dispatch node.
    init_envelope = Envelope(
        payload=ControlPlaneInitEvent(
            runtime=payload_runtime,
            discovery=dict(init_discovery) if isinstance(init_discovery, dict) else None,
        ),
        target="system.cp.consumer_registry_bindings_bootstrap",
    )
    return [init_envelope, *resolved]


__all__ = [
    "RuntimeStartupAssemblyResult",
    "assemble_runtime_startup_scenario",
]
