from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from stream_kernel.application_context import apply_injection
from stream_kernel.application_context.injection_registry import ScenarioScope
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneSpawnRequestedEvent,
)

from .system_nodes import (
    ControlPlaneGroupStartupWaitNode,
    ControlPlaneLifecycleLogDispatchNode,
    ControlPlaneSpawnDispatchNode,
)


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
    if not isinstance(runtime, dict):
        return LifecycleSystemPlan()
    try:
        if _runtime_bootstrap_mode(runtime) != "process_supervisor":
            return LifecycleSystemPlan()
    except Exception:
        return LifecycleSystemPlan()
    process_role = runtime.get("__process_role")
    if isinstance(process_role, str) and process_role in {"worker", "observability_worker"}:
        return LifecycleSystemPlan()
    node_instance = ControlPlaneSpawnDispatchNode()
    startup_wait_node = ControlPlaneGroupStartupWaitNode()
    log_dispatch_node = ControlPlaneLifecycleLogDispatchNode()
    strict = runtime.get("strict", True)
    apply_injection(node_instance, scenario_scope, strict=bool(strict) if isinstance(strict, bool) else True)
    apply_injection(startup_wait_node, scenario_scope, strict=bool(strict) if isinstance(strict, bool) else True)
    apply_injection(log_dispatch_node, scenario_scope, strict=bool(strict) if isinstance(strict, bool) else True)
    return LifecycleSystemPlan(
        system_steps=[
            StepSpec(name="system.lifecycle.spawn_dispatch", step=node_instance),
            StepSpec(name="system.lifecycle.group_startup_wait", step=startup_wait_node),
            StepSpec(name="system.lifecycle.log_dispatch", step=log_dispatch_node),
        ],
        system_consumers={
            ControlPlaneSpawnRequestedEvent: [
                "system.lifecycle.spawn_dispatch",
                "system.lifecycle.group_startup_wait",
            ],
            ControlPlaneLeafConfigAckEvent: [
                "system.lifecycle.group_startup_wait",
            ],
            LogMessage: ["system.lifecycle.log_dispatch"],
        },
        system_node_names={
            "system.lifecycle.spawn_dispatch",
            "system.lifecycle.group_startup_wait",
            "system.lifecycle.log_dispatch",
        },
    )


def _runtime_bootstrap_mode(runtime: dict[str, object]) -> str:
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        raise ValueError("runtime.platform must be a mapping")
    bootstrap = platform.get("bootstrap", {})
    if bootstrap is None:
        bootstrap = {}
    if not isinstance(bootstrap, dict):
        raise ValueError("runtime.platform.bootstrap must be a mapping")
    mode_raw = bootstrap.get("mode")
    if mode_raw is None:
        groups = platform.get("process_groups", [])
        mode = "process_supervisor" if isinstance(groups, list) and groups else "inline"
    else:
        mode = mode_raw
    if not isinstance(mode, str) or not mode:
        raise ValueError("runtime.platform.bootstrap.mode must be a non-empty string")
    return mode


__all__ = ["LifecycleSystemPlan", "build_lifecycle_system_plan"]
