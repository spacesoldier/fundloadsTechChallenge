from __future__ import annotations

from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.execution.orchestration.builder import (
    ensure_runtime_control_plane_discovery_bindings,
)
from stream_kernel.platform.services.runtime.control_plane_discovery_stream import (
    ControlPlaneDiscoverySourceAdapter,
)


def test_runtime_discovery_bindings_fallback_to_runtime_discovery_modules_for_project_roots() -> None:
    injection_registry = InjectionRegistry()
    runtime = {
        "discovery_modules": [
            "stream_kernel.platform.services",
            "fund_load.usecases.steps",
            "fund_load.adapters",
        ]
    }

    ensure_runtime_control_plane_discovery_bindings(
        injection_registry=injection_registry,
        runtime=runtime,
    )
    scope = injection_registry.instantiate_for_scenario("scenario")
    project_adapter = scope.resolve(
        "service",
        ControlPlaneDiscoverySourceAdapter,
        qualifier="project_discovery_source_adapter",
    )
    roots = tuple(getattr(project_adapter, "roots", ()))

    assert roots == ("fund_load.adapters", "fund_load.usecases.steps")


def test_runtime_discovery_bindings_prefer_explicit_platform_discovery_project_modules() -> None:
    injection_registry = InjectionRegistry()
    runtime = {
        "discovery_modules": ["fund_load.usecases.steps"],
        "platform": {
            "discovery": {
                "project_modules": ["custom.project.discovery"],
            }
        },
    }

    ensure_runtime_control_plane_discovery_bindings(
        injection_registry=injection_registry,
        runtime=runtime,
    )
    scope = injection_registry.instantiate_for_scenario("scenario")
    project_adapter = scope.resolve(
        "service",
        ControlPlaneDiscoverySourceAdapter,
        qualifier="project_discovery_source_adapter",
    )
    roots = tuple(getattr(project_adapter, "roots", ()))

    assert roots == ("custom.project.discovery",)
