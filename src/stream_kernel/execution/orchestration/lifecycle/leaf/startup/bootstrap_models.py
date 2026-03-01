from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Any

from stream_kernel.application_context.application_context import ApplicationContext
from stream_kernel.application_context.injection_registry import (
    InjectionRegistry,
    ScenarioScope,
)
from stream_kernel.execution.orchestration.control_plane.bootstrap_keys import BootstrapKeyBundle
from stream_kernel.platform.services.runtime.lifecycle import RuntimeLifecycleManager
from stream_kernel.platform.services.runtime.transport import RuntimeTransportService


class ChildRuntimeBootstrapError(RuntimeError):
    # Raised when child runtime bootstrap bundle/flow is malformed.
    pass


@dataclass(frozen=True, slots=True)
class ChildBootstrapBundle:
    # Metadata-only bootstrap payload for child process re-hydration.
    scenario_id: str
    process_group: str | None
    discovery_modules: list[str]
    runtime: dict[str, object]
    key_bundle: BootstrapKeyBundle
    run_id: str = "run"
    adapters: dict[str, object] | None = None
    config: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ChildRuntimeBootstrap:
    # Result of child process bootstrap from metadata bundle.
    scenario_id: str
    process_group: str | None
    discovery_modules: list[str]
    modules: list[ModuleType]
    runtime: dict[str, object]
    app_context: ApplicationContext
    scenario_steps: dict[str, Any]
    full_context_nodes: set[str]
    injection_registry: InjectionRegistry
    scenario_scope: ScenarioScope
    runtime_transport: RuntimeTransportService
    runtime_lifecycle: RuntimeLifecycleManager
    runner_profile_effective: str
    runner_profile_nodes: dict[str, str]
    async_service_contracts: list[str]
    async_adapter_bindings: list[str]
    observability_exporters: list[str]


@dataclass(frozen=True, slots=True)
class ChildBoundaryInput:
    # Normalized boundary input consumed by child runtime loop.
    payload: object
    dispatch_group: str
    target: str
    trace_id: str | None = None
    reply_to: str | None = None
    source_group: str | None = None
    route_hop: int | None = None
    span_id: str | None = None


__all__ = [
    "ChildRuntimeBootstrapError",
    "ChildBootstrapBundle",
    "ChildRuntimeBootstrap",
    "ChildBoundaryInput",
]
