from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from stream_kernel.application_context.injection_registry import ScenarioScope
from stream_kernel.kernel.scenario import StepSpec


@dataclass(frozen=True, slots=True)
class LifecycleSystemPlan:
    system_steps: list[StepSpec] = field(default_factory=list)
    system_consumers: dict[type[Any], list[str]] = field(default_factory=dict)
    system_node_names: set[str] = field(default_factory=set)


def build_lifecycle_system_plan(
    *,
    runtime: dict[str, object] | None,
    scenario_scope: ScenarioScope,
) -> LifecycleSystemPlan:
    _ = (runtime, scenario_scope)
    # Lifecycle startup nodes were merged into the control-plane layer
    # (system.cp.*). Keep this API as a no-op for compatibility.
    return LifecycleSystemPlan()


__all__ = ["LifecycleSystemPlan", "build_lifecycle_system_plan"]
