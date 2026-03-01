from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.application_context import ApplicationContext
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
from .runtime_step_assembly_service import DefaultLeafRuntimeStepAssemblyService


@runtime_checkable
class LeafRuntimeBootstrapService(Protocol):
    def bootstrap_runtime(self, *, bundle: object) -> object:
        raise NotImplementedError


@service(name="leaf_runtime_bootstrap_service")
@dataclass(slots=True)
class DefaultLeafRuntimeBootstrapService(LeafRuntimeBootstrapService):
    def bootstrap_runtime(self, *, bundle: object) -> object:
        # Build child runtime DI/discovery from metadata bundle only (no serialized object graphs).
        from stream_kernel.execution.orchestration import builder as execution_builder

        bundle_typed = validate_child_bootstrap_bundle(bundle)

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
        step_assembly = DefaultLeafRuntimeStepAssemblyService().assemble_steps(
            execution_builder=execution_builder,
            bundle=bundle_typed,
            app_context=app_context,
            consumer_registry=consumer_registry,
            scenario_scope=scenario_scope,
            scenario=scenario,
            step_names=step_names,
            adapter_instances=adapter_instances,
            adapter_registry=adapter_registry,
        )
        scenario_steps = dict(step_assembly.scenario_steps)
        planning_steps = select_group_planning_steps(
            scenario_steps=scenario_steps,
            runtime=bundle_typed.runtime,
            process_group=bundle_typed.process_group,
        )
        runner_profile_nodes = plan_pools(planning_steps, injection_registry)
        requested_profile = bundle_typed.runtime.get("__runner_profile_requested")
        if not isinstance(requested_profile, str) or not requested_profile:
            requested_profile = "async"
        runner_profile_effective = "sync" if requested_profile == "sync" else "async"
        async_service_contracts, async_adapter_bindings = classify_async_bindings(injection_registry)
        observability_exporters = collect_observability_exporter_summary(
            runtime=bundle_typed.runtime,
            adapter_instances=adapter_instances,
        )
        full_context_nodes = set(step_assembly.full_context_nodes)

        try:
            runtime_transport_obj = scenario_scope.resolve("service", RuntimeTransportService)
        except InjectionRegistryError as exc:
            raise ChildRuntimeBootstrapError(
                "child bootstrap cannot resolve RuntimeTransportService from DI"
            ) from exc
        if not isinstance(runtime_transport_obj, RuntimeTransportService):
            raise ChildRuntimeBootstrapError(
                "child bootstrap resolved service does not match RuntimeTransportService contract"
            )

        try:
            runtime_lifecycle_obj = scenario_scope.resolve("service", RuntimeLifecycleManager)
        except InjectionRegistryError as exc:
            raise ChildRuntimeBootstrapError(
                "child bootstrap cannot resolve RuntimeLifecycleManager from DI"
            ) from exc
        if not isinstance(runtime_lifecycle_obj, RuntimeLifecycleManager):
            raise ChildRuntimeBootstrapError(
                "child bootstrap resolved service does not match RuntimeLifecycleManager contract"
            )

        return ChildRuntimeBootstrap(
            scenario_id=bundle_typed.scenario_id,
            process_group=bundle_typed.process_group,
            discovery_modules=discovery_modules,
            modules=modules,
            runtime=dict(bundle_typed.runtime),
            app_context=app_context,
            scenario_steps=scenario_steps,
            full_context_nodes=full_context_nodes,
            injection_registry=injection_registry,
            scenario_scope=scenario_scope,
            runtime_transport=runtime_transport_obj,
            runtime_lifecycle=runtime_lifecycle_obj,
            runner_profile_effective=runner_profile_effective,
            runner_profile_nodes=runner_profile_nodes,
            async_service_contracts=async_service_contracts,
            async_adapter_bindings=async_adapter_bindings,
            observability_exporters=observability_exporters,
        )
__all__ = [
    "LeafRuntimeBootstrapService",
    "DefaultLeafRuntimeBootstrapService",
]


def _ensure_leaf_runtime_service_bindings(injection_registry: InjectionRegistry) -> None:
    # Leaf process startup loads project + platform modules, so explicit leaf service
    # bindings are required for contracts declared in execution.* modules.
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
        LeafWorkerCommandLoopService,
    )
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.control_ingress_service import (
        DefaultLeafControlIngressService,
        LeafControlIngressService,
    )
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.finalization_service import (
        DefaultLeafSessionFinalizationService,
        LeafSessionFinalizationService,
    )
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_execution_service import (
        DefaultLeafBoundaryExecutionService,
        LeafBoundaryExecutionService,
    )
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
        DefaultLeafRuntimeActivationService,
        LeafRuntimeActivationService,
    )

    bindings: tuple[tuple[type[object], type[object]], ...] = (
        (LeafRuntimeActivationService, DefaultLeafRuntimeActivationService),
        (LeafBoundaryExecutionService, DefaultLeafBoundaryExecutionService),
        (LeafSessionFinalizationService, DefaultLeafSessionFinalizationService),
        (LeafWorkerCommandLoopService, DefaultLeafWorkerCommandLoopService),
        (LeafControlIngressService, DefaultLeafControlIngressService),
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
