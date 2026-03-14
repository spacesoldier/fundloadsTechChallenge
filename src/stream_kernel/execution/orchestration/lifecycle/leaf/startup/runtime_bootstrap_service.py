from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.application_context import ApplicationContext
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.injection_registry import (
    InjectionRegistry,
    InjectionRegistryError,
)
from stream_kernel.application_context.service import service
from stream_kernel.application_context.service import discover_services
from stream_kernel.execution.runtime.planning import plan_pools
from stream_kernel.platform.services.observability import (
    ObservabilityService as ObservabilityServiceContract,
)
from stream_kernel.platform.services.runtime.lifecycle import RuntimeLifecycleManager
from stream_kernel.platform.services.runtime.transport import RuntimeTransportService

from .bootstrap_models import (
    ChildBootstrapBundle,
    ChildRuntimeBootstrap,
    ChildRuntimeBootstrapError,
)
from .bootstrap_support import (
    classify_async_bindings,
    collect_observability_exporter_summary,
    register_adapter_bindings,
    validate_child_bootstrap_bundle,
)
from .group_step_selection import select_group_planning_steps
from .runtime_step_assembly_service import (
    DefaultLeafRuntimeStepAssemblyService,
    LeafRuntimeStepAssemblyService,
)


@runtime_checkable
class LeafRuntimeBootstrapService(Protocol):
    def bootstrap_runtime(self, *, bundle: object) -> object:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class LeafRuntimeBootstrapAssemblyResult:
    bundle: ChildBootstrapBundle
    discovery_modules: list[str]
    modules: list[object]
    app_context: ApplicationContext
    injection_registry: InjectionRegistry
    adapter_registry: object | None
    adapter_instances: dict[str, object]
    scenario_scope: object
    scenario: object
    step_names: list[str]


@runtime_checkable
class LeafRuntimeBootstrapAssemblyService(Protocol):
    def assemble(
        self,
        *,
        bundle_typed: ChildBootstrapBundle,
    ) -> LeafRuntimeBootstrapAssemblyResult:
        raise NotImplementedError


@service(name="leaf_runtime_bootstrap_assembly_service")
@dataclass(slots=True)
class DefaultLeafRuntimeBootstrapAssemblyService(LeafRuntimeBootstrapAssemblyService):
    def assemble(
        self,
        *,
        bundle_typed: ChildBootstrapBundle,
    ) -> LeafRuntimeBootstrapAssemblyResult:
        from stream_kernel.execution.orchestration import builder as execution_builder

        discovery_modules = list(bundle_typed.discovery_modules)
        bundle_adapters = dict(bundle_typed.adapters or {})
        execution_builder.ensure_platform_discovery_modules(discovery_modules)
        modules = execution_builder.load_discovery_modules(discovery_modules)
        app_context = ApplicationContext()
        app_context.discover(modules)
        consumer_registry = app_context.build_consumer_registry()

        injection_registry = InjectionRegistry()
        adapter_registry, adapter_bindings = execution_builder.resolve_runtime_adapters(
            adapters=bundle_adapters,
            discovery_modules=discovery_modules,
        )
        adapter_instances = execution_builder.build_adapter_instances_from_registry(
            bundle_adapters,
            adapter_registry,
        )
        adapter_instances.update(
            execution_builder.build_runtime_observability_adapter_instances(
                runtime=bundle_typed.runtime,
                registry=adapter_registry,
                existing_instances=adapter_instances,
            )
        )
        adapter_async_roles = execution_builder.resolve_async_adapter_roles(
            adapters=bundle_adapters,
            adapter_registry=adapter_registry,
        )
        if adapter_bindings:
            register_adapter_bindings(
                injection_registry=injection_registry,
                instances=adapter_instances,
                bindings=adapter_bindings,
                adapter_async_roles=adapter_async_roles,
            )

        execution_builder.register_discovered_services(injection_registry, modules)
        _ensure_leaf_runtime_service_bindings(injection_registry)
        execution_builder.ensure_runtime_registry_bindings(
            injection_registry=injection_registry,
            app_context=app_context,
            consumer_registry=consumer_registry,
        )
        execution_builder.ensure_runtime_control_plane_discovery_bindings(
            injection_registry=injection_registry,
            runtime=bundle_typed.runtime,
        )
        execution_builder.ensure_runtime_kv_binding(injection_registry, bundle_typed.runtime)
        execution_builder.ensure_runtime_transport_bindings(
            injection_registry=injection_registry,
            runtime=bundle_typed.runtime,
            bootstrap_key_bundle=bundle_typed.key_bundle,
        )
        execution_builder.ensure_runtime_ipc_bindings(
            injection_registry=injection_registry,
            runtime=bundle_typed.runtime,
        )
        execution_builder.ensure_runtime_ipc_handoff_bindings_via_transport(
            injection_registry=injection_registry,
            runtime=bundle_typed.runtime,
        )
        step_names = [node_def.meta.name for node_def in app_context.nodes]
        custom_observability_declared = any(
            isinstance(service_cls, type)
            and issubclass(service_cls, ObservabilityServiceContract)
            and service_cls.__module__ != "stream_kernel.platform.services.observability"
            and service_cls.__module__ != "stream_kernel.platform.services.observability_dispatch"
            for service_cls in discover_services(modules)
        )
        try:
            execution_builder.ensure_runtime_observability_binding(
                injection_registry=injection_registry,
                runtime=bundle_typed.runtime,
                adapter_instances=adapter_instances,
                replace=not custom_observability_declared,
            )
        except InjectionRegistryError:
            if not custom_observability_declared:
                raise
        scenario_scope = injection_registry.instantiate_for_scenario(bundle_typed.scenario_id)
        scenario = app_context.build_scenario(
            scenario_id=bundle_typed.scenario_id,
            step_names=step_names,
            wiring={
                "injection_registry": injection_registry,
                "scenario_scope": scenario_scope,
                "config": (
                    dict(bundle_typed.config)
                    if isinstance(bundle_typed.config, dict)
                    else {"runtime": dict(bundle_typed.runtime)}
                ),
                "strict": True,
            },
        )
        return LeafRuntimeBootstrapAssemblyResult(
            bundle=bundle_typed,
            discovery_modules=discovery_modules,
            modules=modules,
            app_context=app_context,
            injection_registry=injection_registry,
            adapter_registry=adapter_registry,
            adapter_instances=adapter_instances,
            scenario_scope=scenario_scope,
            scenario=scenario,
            step_names=step_names,
        )


@service(name="leaf_runtime_bootstrap_service")
@dataclass(slots=True)
class DefaultLeafRuntimeBootstrapService(LeafRuntimeBootstrapService):
    assembly_service: object | None = inject.service(LeafRuntimeBootstrapAssemblyService)
    step_assembly_service: object | None = inject.service(LeafRuntimeStepAssemblyService)

    def bootstrap_runtime(self, *, bundle: object) -> object:
        bundle_typed = validate_child_bootstrap_bundle(bundle)
        bootstrap_artifacts = self._resolve_assembly_service().assemble(
            bundle_typed=bundle_typed,
        )
        step_assembly = self._resolve_step_assembly_service().assemble_steps(
            bundle=bootstrap_artifacts.bundle,
            app_context=bootstrap_artifacts.app_context,
            scenario_scope=bootstrap_artifacts.scenario_scope,
            scenario=bootstrap_artifacts.scenario,
            step_names=bootstrap_artifacts.step_names,
            adapter_instances=bootstrap_artifacts.adapter_instances,
            adapter_registry=bootstrap_artifacts.adapter_registry,
        )
        scenario_steps = dict(step_assembly.scenario_steps)
        planning_steps = select_group_planning_steps(
            scenario_steps=scenario_steps,
            runtime=bundle_typed.runtime,
            process_group=bundle_typed.process_group,
        )
        runner_profile_nodes = plan_pools(planning_steps, bootstrap_artifacts.injection_registry)
        requested_profile = bundle_typed.runtime.get("__runner_profile_requested")
        if not isinstance(requested_profile, str) or not requested_profile:
            requested_profile = "async"
        runner_profile_effective = "sync" if requested_profile == "sync" else "async"
        async_service_contracts, async_adapter_bindings = classify_async_bindings(
            bootstrap_artifacts.injection_registry
        )
        observability_exporters = collect_observability_exporter_summary(
            runtime=bundle_typed.runtime,
            adapter_instances=bootstrap_artifacts.adapter_instances,
        )
        full_context_nodes = set(step_assembly.full_context_nodes)

        runtime_transport_obj = _resolve_runtime_transport_service(
            bootstrap_artifacts.scenario_scope
        )
        runtime_lifecycle_obj = _resolve_runtime_lifecycle_service(
            bootstrap_artifacts.scenario_scope
        )

        return ChildRuntimeBootstrap(
            scenario_id=bundle_typed.scenario_id,
            process_group=bundle_typed.process_group,
            discovery_modules=bootstrap_artifacts.discovery_modules,
            modules=bootstrap_artifacts.modules,
            runtime=dict(bundle_typed.runtime),
            app_context=bootstrap_artifacts.app_context,
            scenario_steps=scenario_steps,
            full_context_nodes=full_context_nodes,
            injection_registry=bootstrap_artifacts.injection_registry,
            scenario_scope=bootstrap_artifacts.scenario_scope,
            runtime_transport=runtime_transport_obj,
            runtime_lifecycle=runtime_lifecycle_obj,
            runner_profile_effective=runner_profile_effective,
            runner_profile_nodes=runner_profile_nodes,
            async_service_contracts=async_service_contracts,
            async_adapter_bindings=async_adapter_bindings,
            observability_exporters=observability_exporters,
        )

    def _resolve_assembly_service(self) -> LeafRuntimeBootstrapAssemblyService:
        candidate = self.assembly_service
        if isinstance(candidate, LeafRuntimeBootstrapAssemblyService):
            return candidate
        return DefaultLeafRuntimeBootstrapAssemblyService()

    def _resolve_step_assembly_service(self) -> LeafRuntimeStepAssemblyService:
        candidate = self.step_assembly_service
        if isinstance(candidate, LeafRuntimeStepAssemblyService):
            return candidate
        return DefaultLeafRuntimeStepAssemblyService()
__all__ = [
    "LeafRuntimeBootstrapService",
    "LeafRuntimeBootstrapAssemblyResult",
    "LeafRuntimeBootstrapAssemblyService",
    "DefaultLeafRuntimeBootstrapAssemblyService",
    "DefaultLeafRuntimeBootstrapService",
]


def _resolve_runtime_transport_service(scope: object) -> RuntimeTransportService:
    resolve = getattr(scope, "resolve", None)
    if not callable(resolve):
        raise ChildRuntimeBootstrapError("child bootstrap scenario scope is missing resolve()")
    try:
        runtime_transport_obj = resolve("service", RuntimeTransportService)
    except InjectionRegistryError as exc:
        raise ChildRuntimeBootstrapError(
            "child bootstrap cannot resolve RuntimeTransportService from DI"
        ) from exc
    if not isinstance(runtime_transport_obj, RuntimeTransportService):
        raise ChildRuntimeBootstrapError(
            "child bootstrap resolved service does not match RuntimeTransportService contract"
        )
    return runtime_transport_obj


def _resolve_runtime_lifecycle_service(scope: object) -> RuntimeLifecycleManager:
    resolve = getattr(scope, "resolve", None)
    if not callable(resolve):
        raise ChildRuntimeBootstrapError("child bootstrap scenario scope is missing resolve()")
    try:
        runtime_lifecycle_obj = resolve("service", RuntimeLifecycleManager)
    except InjectionRegistryError as exc:
        raise ChildRuntimeBootstrapError(
            "child bootstrap cannot resolve RuntimeLifecycleManager from DI"
        ) from exc
    if not isinstance(runtime_lifecycle_obj, RuntimeLifecycleManager):
        raise ChildRuntimeBootstrapError(
            "child bootstrap resolved service does not match RuntimeLifecycleManager contract"
        )
    return runtime_lifecycle_obj


def _ensure_leaf_runtime_service_bindings(injection_registry: InjectionRegistry) -> None:
    # Leaf process startup loads project + platform modules, so explicit leaf service
    # bindings are required for contracts declared in execution.* modules.
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.channel_services import (
        DefaultLeafCommandChannelIngressService,
        DefaultLeafControlReplyDispatchService,
        DefaultLeafRunnerControlService,
        LeafCommandChannelIngressService,
        LeafControlReplyDispatchService,
        LeafRunnerControlService,
    )
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.finalization_service import (
        DefaultLeafSessionFinalizationService,
        LeafSessionFinalizationService,
    )
    from stream_kernel.execution.orchestration.lifecycle.leaf.debug_logging import (
        DefaultLeafLifecycleDebugLoggingService,
        InMemoryLeafLifecycleDebugStore,
        LeafLifecycleDebugLoggingService,
        LeafLifecycleDebugStore,
    )
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_execution_service import (
        DefaultLeafBoundaryExecutionService,
        LeafBoundaryExecutionService,
    )
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
        DefaultLeafRuntimeActivationService,
        LeafRuntimeActivationService,
    )
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.session_state_service import (
        DefaultLeafRuntimeSessionStateService,
        LeafRuntimeSessionStateService,
    )

    bindings: tuple[tuple[type[object], type[object]], ...] = (
        (LeafRuntimeActivationService, DefaultLeafRuntimeActivationService),
        (LeafBoundaryExecutionService, DefaultLeafBoundaryExecutionService),
        (LeafRuntimeSessionStateService, DefaultLeafRuntimeSessionStateService),
        (LeafRunnerControlService, DefaultLeafRunnerControlService),
        (LeafCommandChannelIngressService, DefaultLeafCommandChannelIngressService),
        (LeafControlReplyDispatchService, DefaultLeafControlReplyDispatchService),
        (LeafSessionFinalizationService, DefaultLeafSessionFinalizationService),
        (LeafLifecycleDebugStore, InMemoryLeafLifecycleDebugStore),
        (LeafLifecycleDebugLoggingService, DefaultLeafLifecycleDebugLoggingService),
    )
    for contract, implementation in bindings:
        try:
            injection_registry.register_factory(
                "service",
                contract,
                lambda _impl=implementation: _impl(),
            )
        except InjectionRegistryError:
            continue
