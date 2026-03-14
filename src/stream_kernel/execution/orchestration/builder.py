from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

from stream_kernel.adapters.discovery import discover_adapters
from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.app.extensions import framework_discovery_modules
from stream_kernel.application_context import (
    ApplicationContext,
    discover_services,
    service_contract_types,
)
from stream_kernel.application_context.inject import Injected, inject
from stream_kernel.application_context.injection_registry import (
    InjectionRegistry,
    InjectionRegistryError,
    ScenarioScope,
)
from stream_kernel.execution.orchestration.control_plane import (
    build_control_plane_system_plan,
)
from stream_kernel.execution.orchestration.control_plane.bootstrap_keys import (
    BootstrapKeyBundle,
    resolve_execution_ipc_key_material,
)
from stream_kernel.execution.orchestration.lifecycle.orchestration import (
    execute_with_runtime_lifecycle,
    runtime_bootstrap_mode,
)
from stream_kernel.execution.orchestration.observability_system_nodes import (
    build_observability_system_plan,
)
from stream_kernel.execution.orchestration.debug_system_nodes import (
    build_debug_system_plan,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.debug_logging import (
    DefaultLeafLifecycleDebugLoggingService,
    InMemoryLeafLifecycleDebugStore,
    LeafLifecycleDebugLoggingService,
    LeafLifecycleDebugStore,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_step_assembly_service import (
    DefaultLeafRuntimeIngressEgressPlanningService,
    LeafRuntimeIngressEgressPlanningService,
)
from stream_kernel.execution.orchestration.runtime import (
    assemble_runtime_startup_scenario,
    run_with_async_runner,
    run_with_sync_runner,
)
from stream_kernel.execution.orchestration.runtime.startup_mode import (
    should_include_business_steps as should_include_runtime_business_steps,
)
from stream_kernel.execution.orchestration.source_ingress import (
    WEB_INGRESS_LIMITER_QUALIFIER,
    BootstrapControl,
    SourceIngressPlan,
    build_source_ingress_plan,
)
from stream_kernel.execution.orchestration.startup_bindings import (
    merge_consumer_maps,
    seed_startup_consumer_bindings,
)
from stream_kernel.execution.runtime.planning import build_execution_plan
from stream_kernel.execution.transport.handoff.runtime_wiring import (
    ensure_runtime_ipc_bindings as ensure_runtime_ipc_bindings_via_transport,
)
from stream_kernel.execution.transport.handoff.runtime_wiring import (
    ensure_runtime_ipc_handoff_bindings as ensure_runtime_ipc_handoff_bindings_via_transport,
)
from stream_kernel.execution.transport.handoff.runtime_wiring import (
    resolve_execution_ipc_adapter_from_adapters as resolve_execution_ipc_adapter_from_adapters_via_transport,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    ExecutionIpcKvStreamPort,
    ExecutionIpcTransportService,
)
from stream_kernel.execution.transport.secure_tcp_transport import (
    SecureTcpConfig,
    SecureTcpTransport,
)
from stream_kernel.integration.consumer_registry import (
    ConsumerRegistry,
    ConsumerRegistryStore,
    InMemoryConsumerRegistry,
)
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.kernel.dag import NodeContract
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.platform.services.api.outbound import (
    InMemoryOutboundApiService,
    OutboundApiService,
)
from stream_kernel.platform.services.api.policy import (
    ApiPolicyService,
    InMemoryApiPolicyService,
    InMemoryRateLimiterService,
    RateLimiterService,
)
from stream_kernel.platform.services.observability import (
    ObservabilityPipelineService,
    ObservabilityService,
)
from stream_kernel.platform.services.observability_dispatch import (
    DispatchingObservabilityService,
)
from stream_kernel.observability.domain.debug import DebugMessage
from stream_kernel.platform.services.messaging.reply_waiter import (
    ReplyWaiterRegistryStore,
)
from stream_kernel.platform.services.runtime.control_plane_discovery_adapters import (
    platform_discovery_source_adapter,
    project_discovery_source_adapter,
)
from stream_kernel.platform.services.runtime.control_plane_discovery_stream import (
    ControlPlaneDiscoverySourceAdapter,
)
from stream_kernel.platform.services.runtime.control_plane_discovery_materialization import (
    ControlPlaneDiscoveryMaterializationRegistry,
    ControlPlaneDiscoveryMaterializationService,
    InMemoryControlPlaneDiscoveryMaterializationService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneInitEvent,
    ControlPlaneRootPulse,
)
from stream_kernel.platform.services.runtime.control_plane_consumer_registry import (
    ControlPlaneConsumerRegistryStore,
    ControlPlaneDynamicConsumerRoutingService,
    InMemoryControlPlaneDynamicConsumerRoutingService,
)
from stream_kernel.platform.services.runtime.control_plane_startup_bindings import (
    ControlPlaneStartupConsumerBindingsService,
    ControlPlaneStartupConsumerBindingsStore,
    InMemoryControlPlaneStartupConsumerBindingsService,
)
from stream_kernel.platform.services.runtime.control_plane_deferred_message import (
    ControlPlaneDeferredMessageService,
    ControlPlaneDeferredMessageStore,
    InMemoryControlPlaneDeferredMessageService,
)
from stream_kernel.platform.services.runtime.lifecycle import RuntimeLifecycleManager
from stream_kernel.platform.services.runtime.process_group_router import (
    ProcessGroupRouterStore,
)
from stream_kernel.platform.services.runtime.transport import (
    IpcLocalRuntimeTransportService,
    MemoryRuntimeTransportService,
    RuntimeTransportService,
    TcpLocalRuntimeTransportService,
)
from stream_kernel.platform.services.runtime.debug_buffer import (
    InMemoryRuntimeDebugBufferService,
    RuntimeDebugBufferService,
)
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.routing_service import RoutingService

BUILD_TIME_REGISTRY_TYPES = (AdapterRegistry, InjectionRegistry)
RUNTIME_SERVICE_REGISTRY_CONTRACTS = (ApplicationContext,)
DEFAULT_EXECUTION_QUEUE_QUALIFIER = "execution.cpu"
DEFAULT_ASYNC_QUEUE_QUALIFIER = "execution.asyncio"
_LIFECYCLE_MANAGED_TRANSPORT_PROFILES = {"tcp_local", "ipc_local"}


@dataclass(slots=True)
class RuntimeBuildArtifacts:
    # Build-phase outputs required for actual execution.
    scenario: object
    inputs: list[object]
    strict: bool
    run_id: str
    scenario_id: str
    scenario_scope: ScenarioScope
    full_context_nodes: set[str]
    # Registry/module artifacts kept for diagnostics and testing.
    adapter_registry: AdapterRegistry | None = None
    injection_registry: InjectionRegistry | None = None
    consumer_registry: ConsumerRegistry | None = None
    modules: list[ModuleType] = field(default_factory=list)
    config: dict[str, object] = field(default_factory=dict)
    runtime: dict[str, object] = field(default_factory=dict)
    adapters: dict[str, object] = field(default_factory=dict)


def ensure_platform_discovery_modules(discovery_modules: list[str]) -> None:
    # Framework platform modules are resolved from extension providers.
    for module_name in framework_discovery_modules():
        if module_name not in discovery_modules:
            discovery_modules.append(module_name)


def load_discovery_modules(discovery_modules: list[str]) -> list[ModuleType]:
    # Resolve discovery module names into concrete modules.
    # Package roots are expanded recursively, so users can pass a short root like "fund_load".
    modules: list[ModuleType] = []
    seen: set[str] = set()

    def _append(module: ModuleType) -> None:
        if module.__name__ in seen:
            return
        seen.add(module.__name__)
        modules.append(module)

    for module_name in discovery_modules:
        root_module = importlib.import_module(module_name)
        module_path = getattr(root_module, "__path__", None)
        if module_path is None:
            _append(root_module)
            continue
        expanded_any = False
        for module_info in pkgutil.walk_packages(module_path, prefix=f"{root_module.__name__}."):
            expanded_any = True
            _append(importlib.import_module(module_info.name))
        if not expanded_any:
            # Keep package roots that have no importable children.
            _append(root_module)

    return modules


def _runtime_graph_discovery_modules(
    *,
    runtime: dict[str, object],
    modules: list[ModuleType],
) -> list[ModuleType]:
    # Root supervisor runtime is control-plane driven.
    # Keep graph bootstrap module discovery scoped to framework modules and
    # avoid project off-graph node discovery there.
    if not _is_root_process_supervisor_runtime(runtime):
        return list(modules)
    filtered = [
        module
        for module in modules
        if isinstance(getattr(module, "__name__", None), str)
        and module.__name__.startswith("stream_kernel")
    ]
    return filtered


def _is_root_process_supervisor_runtime(runtime: dict[str, object]) -> bool:
    try:
        if runtime_bootstrap_mode(runtime) != "process_supervisor":
            return False
    except Exception:
        return False
    process_role = runtime.get("__process_role")
    return not (isinstance(process_role, str) and process_role in {"worker", "observability_worker"})


def _build_control_plane_init_discovery(
    *,
    runtime: dict[str, object],
    config: dict[str, object],
    adapters: dict[str, object],
    run_id: str,
    scenario_id: str,
    modules: list[ModuleType],
) -> dict[str, object] | None:
    if not _is_root_process_supervisor_runtime(runtime):
        return None
    return {
        "root_runtime_prepare": {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "config": dict(config),
            "adapters": dict(adapters),
            "discovery_modules": tuple(
                module.__name__
                for module in modules
                if isinstance(getattr(module, "__name__", None), str)
            ),
        }
    }


def register_discovered_services(registry: InjectionRegistry, modules: list[object]) -> None:
    # Register services discovered in framework/user modules into DI unless overridden.
    discovered = [
        service
        for service in discover_services(modules)  # type: ignore[arg-type]
        if not issubclass(service, ExecutionIpcTransportService)
    ]
    contract_to_service: dict[type[object], type[object]] = {}
    for service_cls in discovered:
        for contract in service_contract_types(service_cls):
            contract_to_service.setdefault(contract, service_cls)

    async_cache: dict[type[object], bool] = {}
    for service_cls in discovered:
        service_is_async = _service_requires_async_capability(
            service_cls=service_cls,
            registry=registry,
            contract_to_service=contract_to_service,
            cache=async_cache,
            visiting=set(),
        )
        for contract in service_contract_types(service_cls):
            try:
                registry.register_factory(
                    "service",
                    contract,
                    lambda _cls=service_cls: _cls(),
                    is_async=service_is_async,
                )
            except InjectionRegistryError:
                # Explicit bindings win over auto-discovered defaults.
                continue


def execute_runtime_artifacts(artifacts: RuntimeBuildArtifacts) -> None:
    # Single execution entrypoint for runtime-prepared artifacts.
    profile = runtime_execution_transport_profile(artifacts.runtime)
    if profile not in _LIFECYCLE_MANAGED_TRANSPORT_PROFILES:
        _execute_runner(artifacts)
        return
    execute_with_runtime_lifecycle(
        runtime=artifacts.runtime,
        scenario_scope=artifacts.scenario_scope,
        run=lambda: _execute_runner(artifacts),
    )


def _execute_runner(artifacts: RuntimeBuildArtifacts) -> None:
    ordered_sink_mode = runtime_ordering_sink_mode(artifacts.runtime)
    queue_qualifier = _resolve_async_runner_queue_qualifier(artifacts)
    run_with_async_runner(
        scenario=artifacts.scenario,
        inputs=artifacts.inputs,
        strict=artifacts.strict,
        run_id=artifacts.run_id,
        scenario_id=artifacts.scenario_id,
        scenario_scope=artifacts.scenario_scope,
        full_context_nodes=artifacts.full_context_nodes,
        ordered_sink_mode=ordered_sink_mode,
        queue_qualifier=queue_qualifier,
    )

def build_runtime_artifacts(
    config: dict[str, object],
    *,
    adapter_registry: AdapterRegistry | None = None,
    adapter_bindings: dict[str, object] | None = None,
    discovery_modules: list[str] | None = None,
    run_id: str = "run",
) -> RuntimeBuildArtifacts:
    # Build all runtime execution artifacts from validated config and discovery.
    runtime = config.get("runtime", {})
    if not isinstance(runtime, dict):
        raise ValueError("runtime must be a mapping")

    if discovery_modules is None:
        discovered_modules = runtime.get("discovery_modules", [])
        if not isinstance(discovered_modules, list) or not all(
            isinstance(item, str) for item in discovered_modules
        ):
            raise ValueError("runtime.discovery_modules must be a list of strings")
        discovery_modules = list(discovered_modules)
    else:
        discovery_modules = list(discovery_modules)
    ensure_platform_discovery_modules(discovery_modules)

    adapters = config.get("adapters", {})
    if not isinstance(adapters, dict):
        raise ValueError("adapters must be a mapping")
    adapters = dict(adapters)
    _merge_runtime_source_ingress_settings(runtime=runtime, adapters=adapters)

    if adapter_registry is None and adapter_bindings is not None:
        raise ValueError("adapter_bindings override requires adapter_registry override")
    if adapter_registry is None:
        adapter_registry, resolved_bindings = resolve_runtime_adapters(
            adapters=adapters,
            discovery_modules=discovery_modules,
        )
        if adapter_bindings is None:
            adapter_bindings = resolved_bindings
    if adapter_bindings is None:
        adapter_bindings = build_adapter_bindings(adapters, adapter_registry)

    adapter_instances = build_adapter_instances_from_registry(adapters, adapter_registry)
    adapter_instances.update(
        build_runtime_observability_adapter_instances(
            runtime=runtime,
            registry=adapter_registry,
            existing_instances=adapter_instances,
        )
    )
    injection_registry = build_injection_registry_from_bindings(
        adapter_instances,
        adapter_bindings,
        adapter_async_roles=resolve_async_adapter_roles(
            adapters=adapters,
            adapter_registry=adapter_registry,
        ),
    )
    ensure_runtime_kv_binding(injection_registry, runtime)

    ctx = ApplicationContext()
    modules = load_discovery_modules(discovery_modules)
    graph_modules = _runtime_graph_discovery_modules(runtime=runtime, modules=modules)
    ctx.discover(graph_modules)
    adapter_contracts = build_adapter_contracts(adapters, adapter_registry=adapter_registry)
    strict = bool(runtime.get("strict", True))
    dag = ctx.preflight(strict=strict, extra_contracts=adapter_contracts)
    consumer_registry = ctx.build_consumer_registry()
    ensure_runtime_registry_bindings(
        injection_registry=injection_registry,
        app_context=ctx,
        consumer_registry=consumer_registry,
    )
    step_names = resolve_step_names(dag)
    custom_observability_declared = any(
        isinstance(service_cls, type)
        and issubclass(service_cls, ObservabilityService)
        and service_cls.__module__ != "stream_kernel.platform.services.observability"
        and service_cls.__module__ != "stream_kernel.platform.services.observability_dispatch"
        for service_cls in discover_services(graph_modules)
    )
    if not custom_observability_declared:
        ensure_runtime_observability_binding(
            injection_registry=injection_registry,
            runtime=runtime,
            adapter_instances=adapter_instances,
            replace=not custom_observability_declared,
        )
    register_discovered_services(injection_registry, graph_modules)
    ensure_runtime_api_policy_bindings(
        injection_registry=injection_registry,
        runtime=runtime,
    )
    ensure_runtime_control_plane_discovery_bindings(
        injection_registry=injection_registry,
        runtime=runtime,
    )

    scenario_id = scenario_name(config)
    ensure_runtime_transport_bindings(
        injection_registry=injection_registry,
        runtime=runtime,
    )
    ipc_adapter = resolve_execution_ipc_adapter_from_adapters(
        adapter_bindings=adapter_bindings,
        adapter_instances=adapter_instances,
    )
    ensure_runtime_ipc_bindings(
        injection_registry=injection_registry,
        runtime=runtime,
        adapter=ipc_adapter,
    )
    ensure_runtime_ipc_handoff_bindings_via_transport(
        injection_registry=injection_registry,
        runtime=runtime,
    )
    ensure_runtime_lifecycle_bindings(
        injection_registry=injection_registry,
        runtime=runtime,
    )
    scenario_scope = injection_registry.instantiate_for_scenario(scenario_id)
    scenario = ctx.build_scenario(
        scenario_id=scenario_id,
        step_names=step_names,
        wiring={
            "injection_registry": injection_registry,
            "scenario_scope": scenario_scope,
            "consumer_registry": consumer_registry,
            "config": config,
            "strict": strict,
        },
    )
    source_ingress = SourceIngressPlan()
    if _should_mount_source_ingress_in_current_process(runtime):
        source_ingress = build_source_ingress_plan(
            adapters=adapters,
            adapter_instances=adapter_instances,
            adapter_registry=adapter_registry,
            scenario_scope=scenario_scope,
            run_id=run_id,
            scenario_id=scenario_id,
            runtime=runtime,
        )
    in_graph_consumes = {
        token
        for node_def in ctx.nodes
        if node_def.meta.name in set(step_names)
        for token in getattr(node_def.meta, "consumes", [])
    }
    sink_nodes, sink_consumers = build_sink_runtime_nodes(
        adapters=adapters,
        adapter_instances=adapter_instances,
        adapter_registry=adapter_registry,
        in_graph_consumes=in_graph_consumes,
    )

    observability_system = build_observability_system_plan(
        runtime=runtime,
        scenario_scope=scenario_scope,
    )

    debug_system = build_debug_system_plan(
        runtime=runtime,
        scenario_scope=scenario_scope,
    )

    control_plane_system = build_control_plane_system_plan(
        runtime=runtime,
        scenario_scope=scenario_scope,
    )

    system_bindings = merge_consumer_maps(
        control_plane_system.system_consumers,
        observability_system.system_consumers,
        debug_system.system_consumers,
    )
    ingress_sink_bindings = merge_consumer_maps(
        source_ingress.source_consumers,
        sink_consumers,
    )
    seed_startup_consumer_bindings(
        scenario_scope=scenario_scope,
        consumers=merge_consumer_maps(system_bindings, ingress_sink_bindings),
    )
    sink_steps = [StepSpec(name=name, step=step) for name, step in sink_nodes.items()]
    startup_assembly = assemble_runtime_startup_scenario(
        runtime=runtime,
        scenario=scenario,
        source_steps=list(source_ingress.source_steps),
        control_plane_steps=list(control_plane_system.system_steps),
        lifecycle_steps=[],
        observability_steps=[*list(observability_system.system_steps), *list(debug_system.system_steps)],
        sink_steps=sink_steps,
        source_inputs=[*list(source_ingress.bootstrap_inputs)],
        init_discovery=_build_control_plane_init_discovery(
            runtime=runtime,
            config=config,
            adapters=adapters,
            run_id=run_id,
            scenario_id=scenario_id,
            modules=modules,
        ),
    )

    return RuntimeBuildArtifacts(
        scenario=startup_assembly.scenario,
        inputs=startup_assembly.inputs,
        strict=strict,
        run_id=run_id,
        scenario_id=scenario_id,
        scenario_scope=scenario_scope,
        full_context_nodes={
            node_def.meta.name
            for node_def in ctx.nodes
            if bool(getattr(node_def.meta, "service", False))
        }
        | set(source_ingress.source_node_names)
        | set(control_plane_system.system_node_names)
        | set(observability_system.system_node_names)
        | set(debug_system.system_node_names),
        adapter_registry=adapter_registry,
        injection_registry=injection_registry,
        consumer_registry=consumer_registry,
        modules=modules,
        config=dict(config),
        runtime=runtime,
        adapters=adapters,
    )


def ensure_runtime_observability_binding(
    *,
    injection_registry: InjectionRegistry,
    runtime: dict[str, object],
    adapter_instances: dict[str, object],
    replace: bool = True,
) -> None:
    # Bind platform observability service to dispatch-first implementation for this run.
    requires_async = _sinks_require_async_dispatch(adapter_instances)
    trace_sinks, log_sinks, telemetry_sinks, monitoring_sinks, debug_sinks = _split_observability_sinks(
        adapter_instances
    )
    factory = (
        lambda _runtime=dict(runtime),
        _trace=list(trace_sinks),
        _log=list(log_sinks),
        _telemetry=list(telemetry_sinks),
        _monitoring=list(monitoring_sinks),
        _debug=list(debug_sinks): DispatchingObservabilityService(
            runtime=dict(_runtime),
            trace_sinks=list(_trace),
            log_sinks=list(_log),
            telemetry_sinks=list(_telemetry),
            monitoring_sinks=list(_monitoring),
            debug_sinks=list(_debug),
        )
    )
    for contract in (ObservabilityService, ObservabilityPipelineService):
        injection_registry.register_factory(
            "service",
            contract,
            factory,
            is_async=requires_async,
            replace=replace,
        )
    if debug_sinks:
        injection_registry.register_factory(
            "stream",
            DebugMessage,
            lambda _sink=_fanout_stream_sink(debug_sinks): _sink,
            is_async=True,
            replace=True,
        )


def _merge_runtime_source_ingress_settings(
    *,
    runtime: dict[str, object],
    adapters: dict[str, object],
) -> None:
    platform = runtime.setdefault("platform", {})
    if not isinstance(platform, dict):
        return
    source_ingress = platform.setdefault("source_ingress", {})
    if not isinstance(source_ingress, dict):
        return
    if "emit_tombstone" not in source_ingress:
        source_ingress["emit_tombstone"] = _has_source_emit_tombstone_enabled(adapters)
    runtime["__adapters"] = {
        role: dict(cfg)
        for role, cfg in adapters.items()
        if isinstance(role, str) and role and isinstance(cfg, dict)
    }


def _has_source_emit_tombstone_enabled(adapters: dict[str, object]) -> bool:
    for cfg in adapters.values():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("emit_tombstone") is True:
            return True
    return False


def _sinks_require_async_dispatch(adapter_instances: dict[str, object]) -> bool:
    for candidate in adapter_instances.values():
        if callable(getattr(candidate, "emit_async", None)):
            return True
        if callable(getattr(candidate, "publish_metrics_async", None)):
            return True
    return False


def _split_observability_sinks(
    adapter_instances: dict[str, object],
) -> tuple[list[object], list[object], list[object], list[object], list[object]]:
    trace_sinks: list[object] = []
    log_sinks: list[object] = []
    telemetry_sinks: list[object] = []
    monitoring_sinks: list[object] = []
    debug_sinks: list[object] = []
    seen: set[int] = set()
    for role, candidate in adapter_instances.items():
        if not isinstance(role, str):
            continue
        marker = id(candidate)
        if marker in seen:
            continue
        if role.startswith("trace_"):
            trace_sinks.append(candidate)
            seen.add(marker)
            continue
        if role.startswith("log_"):
            log_sinks.append(candidate)
            seen.add(marker)
            continue
        if role.startswith("debug_"):
            debug_sinks.append(candidate)
            seen.add(marker)
            continue
        if role.startswith("telemetry_"):
            telemetry_sinks.append(candidate)
            seen.add(marker)
            continue
        if role.startswith("monitoring_"):
            monitoring_sinks.append(candidate)
            seen.add(marker)
            continue
    return trace_sinks, log_sinks, telemetry_sinks, monitoring_sinks, debug_sinks


def _fanout_stream_sink(sinks: list[object]) -> object:
    class _FanoutStreamSink:
        def __init__(self, targets: list[object]) -> None:
            self._targets = list(targets)

        def emit(self, payload: object) -> None:
            for sink in self._targets:
                emit = getattr(sink, "emit", None)
                if callable(emit):
                    try:
                        emit(payload)
                    except Exception:
                        continue

        async def emit_async(self, payload: object) -> None:
            for sink in self._targets:
                emit_async = getattr(sink, "emit_async", None)
                if callable(emit_async):
                    try:
                        result = emit_async(payload)
                        if hasattr(result, "__await__"):
                            await result
                        continue
                    except Exception:
                        continue
                emit = getattr(sink, "emit", None)
                if callable(emit):
                    try:
                        emit(payload)
                    except Exception:
                        continue

    return _FanoutStreamSink(sinks)


@dataclass(slots=True)
class AdapterSinkNode:
    # Sink adapter wrapper executed inside runner graph.
    role: str
    adapter: object
    adapter_binding_marker: object | None = None

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        consume = getattr(self.adapter, "consume", None)
        if callable(consume):
            consume(msg)
            return []
        if callable(self.adapter):
            self.adapter(msg)
            return []
        raise ValueError(
            f"Sink adapter '{self.role}' must be callable or expose consume(payload)"
        )


def build_sink_runtime_nodes(
    *,
    adapters: dict[str, object],
    adapter_instances: dict[str, object],
    adapter_registry: AdapterRegistry | None,
    in_graph_consumes: set[type[Any]],
) -> tuple[dict[str, object], dict[type[Any], list[str]]]:
    # Build executable sink nodes from adapter contracts (consumes!=[] and emits=[]).
    sink_nodes: dict[str, object] = {}
    sink_consumers: dict[type[Any], list[str]] = {}
    for role in sorted(adapter_instances.keys()):
        cfg = adapters.get(role)
        if not isinstance(cfg, dict):
            continue
        meta = resolve_adapter_meta(role, cfg, adapter_registry=adapter_registry)
        if meta is None or not meta.consumes or meta.emits:
            continue
        missing_tokens = [token for token in meta.consumes if token not in in_graph_consumes]
        if not missing_tokens:
            continue
        node_name = f"sink:{role}"
        sink_nodes[node_name] = AdapterSinkNode(
            role=role,
            adapter=adapter_instances[role],
            adapter_binding_marker=_build_stream_binding_marker_from_meta(meta),
        )
        for token in missing_tokens:
            sink_consumers.setdefault(token, []).append(node_name)
    return sink_nodes, sink_consumers


def _build_stream_binding_marker_from_meta(meta: object | None) -> object | None:
    if meta is None:
        return None
    binds = getattr(meta, "binds", ())
    if not isinstance(binds, tuple):
        return None
    for binding in binds:
        if (
            isinstance(binding, tuple)
            and len(binding) == 2
            and binding[0] == "stream"
            and isinstance(binding[1], type)
        ):
            return inject.stream(binding[1])
    return None


def resolve_step_names(dag: object | None) -> list[str]:
    # Runtime order is execution-plan driven and derived from DAG contracts.
    if dag is None:
        return []
    if not hasattr(dag, "nodes") or not hasattr(dag, "edges"):
        raise ValueError("preflight must return a Dag-like object with nodes and edges")
    external_nodes = getattr(dag, "external_nodes", set())
    if not isinstance(external_nodes, set):
        raise ValueError("preflight Dag.external_nodes must be a set")
    # External contracts participate in validation, but scenario execution includes only executable nodes.
    return [name for name in build_execution_plan(dag) if name not in external_nodes]


def build_adapter_contracts(
    adapters: dict[str, object],
    *,
    adapter_registry: AdapterRegistry | None,
) -> list[NodeContract]:
    # Adapter contracts model source/sink edges in preflight DAG.
    # Contracts are sourced from @adapter metadata on factory callables.
    contracts: list[NodeContract] = []
    for role, cfg in adapters.items():
        if not isinstance(cfg, dict):
            continue
        meta = resolve_adapter_meta(role, cfg, adapter_registry=adapter_registry)
        if meta is None:
            continue
        consumes = list(meta.consumes)
        emits = list(meta.emits)
        if not consumes and not emits:
            continue
        contracts.append(
            NodeContract(
                # Use configured adapter role as stable graph contract id.
                # Avoid synthetic adapter-prefixed ids in DAG diagnostics/planning.
                name=role,
                consumes=consumes,
                emits=emits,
                external=True,
            )
        )
    return contracts


def resolve_adapter_meta(
    role: str,
    cfg: dict[str, object],
    *,
    adapter_registry: AdapterRegistry | None,
):
    # Resolve adapter metadata from AdapterRegistry role/kind registrations.
    _ = cfg
    if adapter_registry is not None:
        meta = adapter_registry.get_meta(role, role)
        if meta is not None:
            return meta
    return None


def resolve_async_adapter_roles(
    *,
    adapters: dict[str, object],
    adapter_registry: AdapterRegistry | None,
) -> set[str]:
    async_roles: set[str] = set()
    for role, cfg in adapters.items():
        if not isinstance(cfg, dict):
            continue
        meta = resolve_adapter_meta(role, cfg, adapter_registry=adapter_registry)
        mode = getattr(meta, "execution_mode", "sync")
        if mode == "async":
            async_roles.add(role)
    return async_roles


def _service_requires_async_capability(
    *,
    service_cls: type[object],
    registry: InjectionRegistry,
    contract_to_service: dict[type[object], type[object]],
    cache: dict[type[object], bool],
    visiting: set[type[object]],
) -> bool:
    if service_cls in cache:
        return cache[service_cls]
    if service_cls in visiting:
        return False
    visiting.add(service_cls)
    try:
        for marker in _iter_injected_markers_from_type(service_cls):
            if marker.port_type == "service":
                target_service = contract_to_service.get(marker.data_type)
                if isinstance(target_service, type) and _service_requires_async_capability(
                    service_cls=target_service,
                    registry=registry,
                    contract_to_service=contract_to_service,
                    cache=cache,
                    visiting=visiting,
                ):
                    cache[service_cls] = True
                    return True
            try:
                if registry.is_async_binding(
                    marker.port_type,
                    marker.data_type,
                    qualifier=marker.qualifier,
                ):
                    cache[service_cls] = True
                    return True
            except InjectionRegistryError:
                continue
        cache[service_cls] = False
        return False
    finally:
        visiting.discard(service_cls)


def _iter_injected_markers_from_type(component_type: type[object]) -> list[Injected]:
    markers: list[Injected] = []
    for cls in component_type.__mro__:
        if cls is object:
            continue
        for value in getattr(cls, "__dict__", {}).values():
            if isinstance(value, Injected):
                markers.append(value)
        dataclass_fields = getattr(cls, "__dataclass_fields__", {})
        if isinstance(dataclass_fields, dict):
            for field_def in dataclass_fields.values():
                default = getattr(field_def, "default", None)
                if isinstance(default, Injected):
                    markers.append(default)
    deduped: dict[tuple[str, type[object], str | None], Injected] = {}
    for marker in markers:
        deduped[(marker.port_type, marker.data_type, marker.qualifier)] = marker
    return list(deduped.values())


def trace_id(run_id: str, _payload: object, index: int) -> str:
    # Keep per-message trace ids deterministic without relying on project payload fields.
    return f"{run_id}:{index}"


def initial_context(
    _payload: object,
    trace_id_value: str,
    *,
    run_id: str,
    scenario_id: str,
) -> dict[str, object]:
    # Minimal metadata view exposed to nodes via KV-backed context persistence.
    # Reserved keys are available to service nodes and observers.
    return {
        "__trace_id": trace_id_value,
        "__run_id": run_id,
        "__scenario_id": scenario_id,
    }


def build_adapter_instances_from_registry(
    adapters: dict[str, object],
    registry: AdapterRegistry,
) -> dict[str, object]:
    # Build adapter instances using AdapterRegistry (role/kind), used for diagnostics.
    instances: dict[str, object] = {}
    for role, cfg in adapters.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"adapters.{role} must be a mapping")
        # New contract: adapter is selected by YAML role name; bridge to registry API via implicit kind=role.
        effective_cfg = dict(cfg)
        effective_cfg.setdefault("kind", role)
        instances[role] = registry.build(role, effective_cfg)
    return instances


def build_injection_registry_from_bindings(
    instances: dict[str, object],
    bindings: dict[str, object],
    *,
    adapter_async_roles: set[str] | None = None,
) -> InjectionRegistry:
    # Build InjectionRegistry from explicit bindings using shared instances (runtime wiring).
    injection = InjectionRegistry()
    async_roles = set(adapter_async_roles or ())
    for role, binding in bindings.items():
        if role not in instances:
            raise ValueError(f"Missing adapter instance for role: {role}")
        adapter = instances[role]
        is_async = role in async_roles
        if isinstance(binding, list):
            for port_type, data_type in binding:
                injection.register_factory(port_type, data_type, lambda _a=adapter: _a, is_async=is_async)
        else:
            port_type, data_type = binding
            injection.register_factory(port_type, data_type, lambda _a=adapter: _a, is_async=is_async)
    return injection


def ensure_runtime_kv_binding(
    injection_registry: InjectionRegistry,
    runtime: dict[str, object],
) -> None:
    # Runtime-level default: provide KVStore binding unless explicitly bound by config/adapters.
    backend = runtime_kv_backend(runtime)
    if backend != "memory":
        raise ValueError(f"Unsupported runtime.platform.kv.backend: {backend}")
    try:
        injection_registry.register_factory("kv", KVStore, lambda: InMemoryKvStore())
    except InjectionRegistryError:
        # Explicit binding already exists and must win over default provisioning.
        return


def ensure_runtime_api_policy_bindings(
    *,
    injection_registry: InjectionRegistry,
    runtime: dict[str, object],
) -> None:
    # Runtime policy services are provided via DI and profile qualifiers.
    ensure_runtime_kv_binding(injection_registry, runtime)

    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        raise ValueError("runtime.platform must be a mapping")
    api_policies = platform.get("api_policies", {})
    if api_policies is None:
        api_policies = {}
    if not isinstance(api_policies, dict):
        raise ValueError("runtime.platform.api_policies must be a mapping when provided")

    defaults_raw = api_policies.get("defaults", {})
    defaults = dict(defaults_raw) if isinstance(defaults_raw, dict) else {}
    profiles_raw = api_policies.get("profiles", {})
    profiles: dict[str, dict[str, object]] = (
        {
            name: dict(profile)
            for name, profile in profiles_raw.items()
            if isinstance(name, str) and isinstance(profile, dict)
        }
        if isinstance(profiles_raw, dict)
        else {}
    )

    _validate_api_policy_runner_profile_compatibility(runtime=runtime, profiles=profiles)

    api_policy_service = InMemoryApiPolicyService(
        defaults_policy=dict(defaults),
        profiles={name: dict(profile) for name, profile in profiles.items()},
    )
    injection_registry.register_factory(
        "service",
        ApiPolicyService,
        lambda _service=api_policy_service: _service,
        replace=True,
    )

    default_limiter = InMemoryRateLimiterService(
        limiter_profile_name=None,
        limiter_config=_rate_limit_policy_from_profile(api_policy_service.profile(None)),
    )
    injection_registry.register_factory(
        "service",
        RateLimiterService,
        lambda _service=default_limiter: _service,
        replace=True,
    )
    default_outbound = InMemoryOutboundApiService(
        profile=None,
        policy_config=api_policy_service.profile(None),
        limiter=default_limiter,
    )
    injection_registry.register_factory(
        "service",
        OutboundApiService,
        lambda _service=default_outbound: _service,
        replace=True,
    )

    for profile_name, profile in profiles.items():
        resolved_profile = api_policy_service.profile(profile_name)
        limiter = InMemoryRateLimiterService(
            limiter_profile_name=profile_name,
            limiter_config=_rate_limit_policy_from_profile(resolved_profile),
        )
        injection_registry.register_factory(
            "service",
            RateLimiterService,
            lambda _service=limiter: _service,
            qualifier=profile_name,
            replace=True,
        )
        outbound = InMemoryOutboundApiService(
            profile=profile_name,
            policy_config=resolved_profile,
            limiter=limiter,
        )
        injection_registry.register_factory(
            "service",
            OutboundApiService,
            lambda _service=outbound: _service,
            qualifier=profile_name,
            replace=True,
        )

    web_ingress_policy = _web_ingress_rate_limit_policy(runtime)
    if web_ingress_policy is not None:
        ingress_limiter = InMemoryRateLimiterService(
            limiter_profile_name=WEB_INGRESS_LIMITER_QUALIFIER,
            limiter_config=web_ingress_policy,
        )
        injection_registry.register_factory(
            "service",
            RateLimiterService,
            lambda _service=ingress_limiter: _service,
            qualifier=WEB_INGRESS_LIMITER_QUALIFIER,
            replace=True,
        )


def _validate_api_policy_runner_profile_compatibility(
    *,
    runtime: dict[str, object],
    profiles: dict[str, dict[str, object]],
) -> None:
    # Phase B contract: service-profile execution mode must match process-group runner profile.
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return
    groups_raw = platform.get("process_groups", [])
    if not isinstance(groups_raw, list):
        return
    for group in groups_raw:
        if not isinstance(group, dict):
            continue
        if "runner_profile" not in group:
            continue
        runner_profile = group.get("runner_profile")
        if not isinstance(runner_profile, str) or runner_profile not in {"sync", "async"}:
            continue
        services = group.get("services", {})
        if services is None:
            services = {}
        if not isinstance(services, dict):
            continue
        for key in ("api_service_profile", "rate_limiter_profile"):
            profile_name = services.get(key)
            if not isinstance(profile_name, str) or not profile_name:
                continue
            profile = profiles.get(profile_name, {})
            if not isinstance(profile, dict):
                continue
            execution_mode = profile.get("execution_mode", "sync")
            if not isinstance(execution_mode, str) or execution_mode not in {"sync", "async", "any"}:
                continue
            if execution_mode != "any" and execution_mode != runner_profile:
                raise ValueError(
                    "runtime.platform.process_groups[].services."
                    f"{key}='{profile_name}' requires runner_profile='{execution_mode}' "
                    f"but group '{group.get('name', '<unknown>')}' uses '{runner_profile}'"
                )


def _rate_limit_policy_from_profile(profile: dict[str, object]) -> dict[str, object]:
    rate_limit = profile.get("rate_limit", {})
    if isinstance(rate_limit, dict):
        return dict(rate_limit)
    return {}


def _web_ingress_rate_limit_policy(runtime: dict[str, object]) -> dict[str, object] | None:
    web = runtime.get("web")
    if not isinstance(web, dict):
        return None
    interfaces = web.get("interfaces")
    if not isinstance(interfaces, list):
        return None
    for interface in interfaces:
        if not isinstance(interface, dict):
            continue
        policies = interface.get("policies")
        if not isinstance(policies, dict):
            continue
        rate_limit = policies.get("rate_limit")
        if isinstance(rate_limit, dict):
            return dict(rate_limit)
    return None


def _should_mount_source_ingress_in_current_process(runtime: dict[str, object]) -> bool:
    # Root process-supervisor with explicit worker groups should not execute source
    # locally; start-work command is sent to leaf workers after readiness.
    if should_include_runtime_business_steps(runtime):
        return True
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return True
    groups = platform.get("process_groups")
    return not (isinstance(groups, list) and bool(groups))


def runtime_kv_backend(runtime: dict[str, object]) -> str:
    # Read normalized backend path from runtime mapping (validator fills defaults).
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        raise ValueError("runtime.platform must be a mapping")
    kv = platform.get("kv", {})
    if not isinstance(kv, dict):
        raise ValueError("runtime.platform.kv must be a mapping")
    backend = kv.get("backend", "memory")
    if not isinstance(backend, str) or not backend:
        raise ValueError("runtime.platform.kv.backend must be a non-empty string")
    return backend


def runtime_execution_transport_profile(runtime: dict[str, object]) -> str:
    # Runtime execution transport profile defaults to in-process memory queue.
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        raise ValueError("runtime.platform must be a mapping")
    execution_ipc = platform.get("execution_ipc")
    if execution_ipc is None:
        return "memory"
    if not isinstance(execution_ipc, dict):
        raise ValueError("runtime.platform.execution_ipc must be a mapping")
    transport = execution_ipc.get("transport")
    if not isinstance(transport, str) or not transport:
        raise ValueError("runtime.platform.execution_ipc.transport must be a non-empty string")
    return transport


def runtime_ordering_sink_mode(runtime: dict[str, object]) -> str:
    # Read normalized sink ordering mode from runtime mapping.
    ordering = runtime.get("ordering", {})
    if not isinstance(ordering, dict):
        raise ValueError("runtime.ordering must be a mapping")
    sink_mode = ordering.get("sink_mode", "completion")
    if not isinstance(sink_mode, str) or not sink_mode:
        raise ValueError("runtime.ordering.sink_mode must be a non-empty string")
    return sink_mode


def scenario_name(config: dict[str, object]) -> str:
    scenario = config.get("scenario", {})
    if isinstance(scenario, dict):
        name = scenario.get("name")
        if isinstance(name, str):
            return name
    return "scenario"


def ensure_runtime_registry_bindings(
    *,
    injection_registry: InjectionRegistry,
    app_context: ApplicationContext,
    consumer_registry: ConsumerRegistry | None = None,
) -> None:
    # Runtime application context is exposed through service DI contracts.
    # Register both canonical contract and concrete runtime context type to stay resilient in tests/mocks.
    from stream_kernel.application_context.application_context import ApplicationContext as AppContextContract

    for contract in {AppContextContract, type(app_context)}:
        injection_registry.register_factory(
            "service",
            contract,
            lambda _ctx=app_context: _ctx,
            replace=True,
        )
    try:
        injection_registry.register_factory(
            "kv",
            ProcessGroupRouterStore,
            lambda: InMemoryKvStore(),
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "kv",
            ReplyWaiterRegistryStore,
            lambda: InMemoryKvStore(),
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "service",
            RuntimeDebugBufferService,
            lambda: InMemoryRuntimeDebugBufferService(),
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "service",
            LeafLifecycleDebugStore,
            lambda: InMemoryLeafLifecycleDebugStore(),
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "service",
            LeafLifecycleDebugLoggingService,
            lambda: DefaultLeafLifecycleDebugLoggingService(),
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "service",
            LeafRuntimeIngressEgressPlanningService,
            lambda: DefaultLeafRuntimeIngressEgressPlanningService(),
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "stream",
            DebugMessage,
            lambda: _NoOpDebugStreamSink(),
            is_async=True,
        )
    except InjectionRegistryError:
        pass
    for qualifier in {"execution.asyncio"}:
        try:
            injection_registry.register_factory(
                "queue",
                Envelope,
                lambda: InMemoryQueue(),
                qualifier=qualifier,
            )
        except InjectionRegistryError:
            pass
    default_platform_discovery_adapter = platform_discovery_source_adapter({})
    default_project_discovery_adapter = project_discovery_source_adapter({"project_modules": []})
    try:
        injection_registry.register_factory(
            "service",
            ControlPlaneDiscoverySourceAdapter,
            lambda _adapter=default_platform_discovery_adapter: _adapter,
            qualifier="platform_discovery_source_adapter",
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "service",
            ControlPlaneDiscoverySourceAdapter,
            lambda _adapter=default_project_discovery_adapter: _adapter,
            qualifier="project_discovery_source_adapter",
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "kv",
            ControlPlaneDiscoveryMaterializationRegistry,
            lambda: InMemoryKvStore(),
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "service",
            ControlPlaneDiscoveryMaterializationService,
            lambda: InMemoryControlPlaneDiscoveryMaterializationService(),
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "kv",
            ControlPlaneConsumerRegistryStore,
            lambda: InMemoryKvStore(),
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "service",
            ControlPlaneDynamicConsumerRoutingService,
            lambda: InMemoryControlPlaneDynamicConsumerRoutingService(),
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "kv",
            ControlPlaneDeferredMessageStore,
            lambda: InMemoryKvStore(),
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "service",
            ControlPlaneDeferredMessageService,
            lambda: InMemoryControlPlaneDeferredMessageService(),
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "kv",
            ControlPlaneStartupConsumerBindingsStore,
            lambda: InMemoryKvStore(),
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "service",
            ControlPlaneStartupConsumerBindingsService,
            lambda: InMemoryControlPlaneStartupConsumerBindingsService(),
        )
    except InjectionRegistryError:
        pass

    if consumer_registry is None:
        fallback_consumer_registry = InMemoryConsumerRegistry()
        injection_registry.register_factory(
            "service",
            RoutingService,
            lambda _registry=fallback_consumer_registry: RoutingService(
                registry=_registry,
                strict=True,
            ),
            replace=True,
        )
        injection_registry.register_factory(
            "kv",
            ConsumerRegistryStore,
            lambda: InMemoryKvStore(),
            replace=True,
        )
        return
    for contract in {ConsumerRegistry, type(consumer_registry)}:
        injection_registry.register_factory(
            "service",
            contract,
            lambda _registry=consumer_registry: _registry,
            replace=True,
        )
    injection_registry.register_factory(
        "service",
        RoutingService,
        lambda _registry=consumer_registry: RoutingService(registry=_registry, strict=True),
        replace=True,
    )

    store = _resolve_consumer_registry_store(consumer_registry)
    injection_registry.register_factory(
        "kv",
        ConsumerRegistryStore,
        lambda _store=store: _store,
        replace=True,
    )


def _resolve_consumer_registry_store(consumer_registry: ConsumerRegistry) -> KVStore:
    if isinstance(consumer_registry, InMemoryConsumerRegistry):
        candidate = consumer_registry.store
        if isinstance(candidate, KVStore):
            return candidate
    return InMemoryKvStore()


class _NoOpDebugStreamSink:
    def emit(self, payload: object) -> None:
        _ = payload

    async def emit_async(self, payload: object) -> None:
        _ = payload


def ensure_runtime_control_plane_discovery_bindings(
    *,
    injection_registry: InjectionRegistry,
    runtime: dict[str, object],
) -> None:
    # Control-plane discovery stream requires qualified source adapters in DI.
    # Bind platform/project adapter contracts explicitly for runtime startup path.
    project_modules: list[str] = []
    platform = runtime.get("platform", {})
    if isinstance(platform, dict):
        discovery = platform.get("discovery", {})
        if isinstance(discovery, dict):
            configured = discovery.get("project_modules", [])
            if isinstance(configured, list):
                project_modules = [value for value in configured if isinstance(value, str) and value]
    if not project_modules:
        discovered_modules = runtime.get("discovery_modules", [])
        if isinstance(discovered_modules, list):
            project_modules = [
                value
                for value in discovered_modules
                if isinstance(value, str) and value and not value.startswith("stream_kernel")
            ]

    platform_adapter = platform_discovery_source_adapter({})
    project_adapter = project_discovery_source_adapter({"project_modules": project_modules})

    qualified_bindings: tuple[tuple[str, ControlPlaneDiscoverySourceAdapter], ...] = (
        ("platform_discovery_source_adapter", platform_adapter),
        ("project_discovery_source_adapter", project_adapter),
    )
    for qualifier, adapter in qualified_bindings:
        try:
            injection_registry.register_factory(
                "service",
                ControlPlaneDiscoverySourceAdapter,
                lambda _adapter=adapter: _adapter,
                qualifier=qualifier,
                replace=True,
            )
        except InjectionRegistryError:
            continue


def ensure_runtime_transport_bindings(
    *,
    injection_registry: InjectionRegistry,
    runtime: dict[str, object],
    bootstrap_key_bundle: BootstrapKeyBundle | None = None,
) -> None:
    # Runtime-level default queue/topic transport for SyncRunner.
    # Future runners can bind alternative qualifiers (execution.asyncio/celery/gpu).
    qualifiers = _runtime_queue_qualifiers(runtime)
    transport_service = _build_runtime_transport_service(runtime, bootstrap_key_bundle=bootstrap_key_bundle)
    try:
        injection_registry.register_factory(
            "service",
            RuntimeTransportService,
            lambda _service=transport_service: _service,
        )
    except InjectionRegistryError:
        # Explicit project/runtime binding wins.
        pass
    queue_factory = lambda _service=transport_service: _service.build_queue()
    topic_factory = lambda _service=transport_service: _service.build_topic()
    for qualifier in qualifiers:
        try:
            injection_registry.register_factory(
                "queue",
                Envelope,
                queue_factory,
                qualifier=qualifier,
            )
        except InjectionRegistryError:
            # Explicit project/runtime binding wins.
            pass
        try:
            injection_registry.register_factory(
                "topic",
                Envelope,
                topic_factory,
                qualifier=qualifier,
            )
        except InjectionRegistryError:
            # Explicit project/runtime binding wins.
            pass


def ensure_runtime_ipc_bindings(
    *,
    injection_registry: InjectionRegistry,
    runtime: dict[str, object],
    adapter: ExecutionIpcKvStreamPort | None = None,
    service: ExecutionIpcTransportService | None = None,
) -> None:
    # Transport IPC bindings are owned by execution.transport.handoff.runtime_wiring.
    ensure_runtime_ipc_bindings_via_transport(
        injection_registry=injection_registry,
        runtime=runtime,
        adapter=adapter,
        service=service,
    )


def ensure_runtime_lifecycle_bindings(
    *,
    injection_registry: InjectionRegistry,
    runtime: dict[str, object],
) -> None:
    # Process-supervisor root runtime uses control-plane-aware lifecycle manager for shutdown orchestration.
    try:
        mode = runtime_bootstrap_mode(runtime)
    except Exception:
        return
    process_role = runtime.get("__process_role")
    if mode != "process_supervisor":
        return
    if isinstance(process_role, str) and process_role in {"worker", "observability_worker"}:
        return
    from stream_kernel.execution.orchestration.lifecycle.root.runtime.lifecycle_manager import (
        ControlPlaneRootRuntimeLifecycleManager,
    )
    from stream_kernel.execution.orchestration.control_plane.root.channel_services import (
        ControlPlaneRootRunnerControlService,
        DefaultControlPlaneRootRunnerControlService,
    )

    try:
        injection_registry.register_factory(
            "service",
            RuntimeLifecycleManager,
            lambda: ControlPlaneRootRuntimeLifecycleManager(),
            replace=True,
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "service",
            ControlPlaneRootRunnerControlService,
            lambda: DefaultControlPlaneRootRunnerControlService(),
        )
    except InjectionRegistryError:
        pass


def resolve_execution_ipc_adapter_from_adapters(
    *,
    adapter_bindings: dict[str, object],
    adapter_instances: dict[str, object],
) -> ExecutionIpcKvStreamPort | None:
    return resolve_execution_ipc_adapter_from_adapters_via_transport(
        adapter_bindings=adapter_bindings,
        adapter_instances=adapter_instances,
    )

def _runtime_queue_qualifiers(runtime: dict[str, object]) -> list[str]:
    qualifiers: list[str] = [DEFAULT_EXECUTION_QUEUE_QUALIFIER]
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return qualifiers
    process_groups = platform.get("process_groups", [])
    if not isinstance(process_groups, list):
        return qualifiers
    for group in process_groups:
        if not isinstance(group, dict):
            continue
        group_name = group.get("name")
        if isinstance(group_name, str) and group_name and group_name not in qualifiers:
            qualifiers.append(group_name)
    if DEFAULT_ASYNC_QUEUE_QUALIFIER not in qualifiers:
        qualifiers.append(DEFAULT_ASYNC_QUEUE_QUALIFIER)
    return qualifiers


def _resolve_async_runner_queue_qualifier(artifacts: RuntimeBuildArtifacts) -> str:
    runtime = artifacts.runtime
    platform = runtime.get("platform", {})
    if isinstance(platform, dict):
        bootstrap = platform.get("bootstrap", {})
        mode = bootstrap.get("mode") if isinstance(bootstrap, dict) else None
        if mode == "process_supervisor" and _contains_root_pulse_input(artifacts.inputs):
            # Root control-plane scheduler timer emits tick envelopes into
            # the default async queue.
            return DEFAULT_ASYNC_QUEUE_QUALIFIER
    if not isinstance(platform, dict):
        return DEFAULT_ASYNC_QUEUE_QUALIFIER
    process_groups = platform.get("process_groups", [])
    if not isinstance(process_groups, list) or not process_groups:
        return DEFAULT_ASYNC_QUEUE_QUALIFIER

    resolved_group: dict[str, object] | None = None
    for group in process_groups:
        if not isinstance(group, dict):
            continue
        name = group.get("name")
        if isinstance(name, str) and name == "web":
            continue
        resolved_group = group
        break
    if resolved_group is None:
        resolved_group = next((group for group in process_groups if isinstance(group, dict)), None)
    queue_qualifier = resolved_group.get("name") if isinstance(resolved_group, dict) else None
    if not isinstance(queue_qualifier, str) or not queue_qualifier:
        queue_qualifier = DEFAULT_ASYNC_QUEUE_QUALIFIER
    return queue_qualifier


def _contains_root_pulse_input(inputs: list[object] | tuple[object, ...]) -> bool:
    for item in inputs:
        payload = item.payload if isinstance(item, Envelope) else item
        if isinstance(payload, ControlPlaneRootPulse):
            return True
        if isinstance(payload, ControlPlaneInitEvent):
            runtime = payload.runtime if isinstance(payload.runtime, dict) else {}
            process_role = runtime.get("__process_role")
            if not (isinstance(process_role, str) and process_role in {"worker", "observability_worker"}):
                return True
    return False


def _build_runtime_transport_service(
    runtime: dict[str, object],
    *,
    bootstrap_key_bundle: BootstrapKeyBundle | None = None,
) -> RuntimeTransportService:
    profile = runtime_execution_transport_profile(runtime)
    if profile == "memory":
        return MemoryRuntimeTransportService()
    if profile == "ipc_local":
        return IpcLocalRuntimeTransportService()
    if profile == "tcp_local":
        transport = _build_secure_tcp_transport(runtime, bootstrap_key_bundle=bootstrap_key_bundle)
        return TcpLocalRuntimeTransportService(transport=transport)
    raise ValueError(f"Unsupported runtime.platform.execution_ipc.transport: {profile}")


def _build_secure_tcp_transport(
    runtime: dict[str, object],
    *,
    bootstrap_key_bundle: BootstrapKeyBundle | None = None,
) -> SecureTcpTransport:
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        raise ValueError("runtime.platform must be a mapping")
    execution_ipc = platform.get("execution_ipc", {})
    if not isinstance(execution_ipc, dict):
        raise ValueError("runtime.platform.execution_ipc must be a mapping")

    bind_host = execution_ipc.get("bind_host", "127.0.0.1")
    bind_port = execution_ipc.get("bind_port", 0)
    max_payload_bytes = execution_ipc.get("max_payload_bytes", 1048576)
    auth = execution_ipc.get("auth", {})
    if not isinstance(auth, dict):
        raise ValueError("runtime.platform.execution_ipc.auth must be a mapping")
    ttl_seconds = auth.get("ttl_seconds", 30)
    nonce_cache_size = auth.get("nonce_cache_size", 100000)
    key_material = (
        bootstrap_key_bundle.execution_ipc
        if bootstrap_key_bundle is not None
        else resolve_execution_ipc_key_material(runtime)
    )

    return SecureTcpTransport(
        SecureTcpConfig(
            bind_host=bind_host if isinstance(bind_host, str) else "127.0.0.1",
            bind_port=bind_port if isinstance(bind_port, int) else 0,
            secret=key_material.signing_secret,
            ttl_seconds=ttl_seconds if isinstance(ttl_seconds, int) else 30,
            nonce_cache_size=nonce_cache_size if isinstance(nonce_cache_size, int) else 100000,
            max_payload_bytes=max_payload_bytes if isinstance(max_payload_bytes, int) else 1048576,
            allowed_kinds={"event"},
        )
    )


def resolve_runtime_adapters(
    *,
    adapters: dict[str, object],
    discovery_modules: list[str],
) -> tuple[AdapterRegistry, dict[str, object]]:
    # Discover adapters by name and bind them to equally named YAML roles.
    modules = load_discovery_modules(discovery_modules)
    discovered = discover_adapters(modules)

    registry = AdapterRegistry()
    for name, factory in discovered.items():
        registry.register(name, name, factory)

    for role, cfg in adapters.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"adapters.{role} must be a mapping")
        if "kind" in cfg:
            raise ValueError(
                f"adapters.{role}.kind is not supported; adapter name is defined by adapters.{role}"
            )
        if role not in discovered:
            raise ValueError(f"Unknown adapter name: {role}")

    bindings = build_adapter_bindings(adapters, registry)
    return registry, bindings


def _resolve_otel_backend_for_build(
    exporter: dict[str, object],
    settings_for_build: dict[str, object],
) -> str | None:
    backend = exporter.get("backend")
    if isinstance(backend, str) and backend:
        return backend
    transport = settings_for_build.get("transport")
    if isinstance(transport, dict):
        transport_backend = transport.get("backend")
        if isinstance(transport_backend, str) and transport_backend:
            return transport_backend
    settings_backend = settings_for_build.get("backend")
    if isinstance(settings_backend, str) and settings_backend:
        return settings_backend
    return None


def build_runtime_observability_adapter_instances(
    *,
    runtime: dict[str, object],
    registry: AdapterRegistry,
    existing_instances: dict[str, object] | None = None,
) -> dict[str, object]:
    # Build observability adapter instances from runtime config via AdapterRegistry only.
    strict = bool(runtime.get("strict", True))
    process_role = runtime.get("__process_role")
    allowed_roles = {"worker", "observability_worker", "supervisor"}
    normalized_role = "supervisor"
    if process_role is None:
        normalized_role = "supervisor"
    elif isinstance(process_role, str):
        if process_role in allowed_roles:
            normalized_role = process_role
        elif strict:
            raise ValueError(
                "runtime.__process_role must be one of: "
                "['worker', 'observability_worker', 'supervisor'] when provided"
            )
    elif strict:
        raise ValueError(
            "runtime.__process_role must be one of: "
            "['worker', 'observability_worker', 'supervisor'] when provided"
        )
    is_worker_process = normalized_role == "worker"
    is_observability_worker = normalized_role == "observability_worker"
    supervisor_owns_monitoring = False
    bootstrap_mode = ""
    try:
        bootstrap_mode = runtime_bootstrap_mode(runtime)
        supervisor_owns_monitoring = bootstrap_mode == "process_supervisor"
    except Exception:
        bootstrap_mode = ""
        supervisor_owns_monitoring = False
    existing = existing_instances or {}
    built: dict[str, object] = {}

    kind_to_alias = {
        "tracing": {
            "jsonl": "trace_jsonl",
            "stdout": "trace_stdout",
            "otel_otlp": "trace_otel_otlp",
            "otel_otlp_logical": "trace_otel_otlp",
            "otel_otlp_topology": "trace_otel_otlp",
            "opentracing_bridge": "trace_opentracing_bridge",
        },
        "logging": {
            "stdout": "log_stdout",
            "stdout_plain": "log_stdout_plain",
            "jsonl": "log_jsonl",
            "file_plain": "log_file_plain",
            "otel_logs_otlp": "log_otel_otlp",
            "redis_debug": "log_redis_debug",
        },
        "monitoring": {
            "stdout": "monitoring_stdout",
            "jsonl": "monitoring_jsonl",
            "prometheus": "monitoring_prometheus",
        },
    }

    observability = runtime.get("observability", {})
    if not isinstance(observability, dict):
        return built
    built.update(
        _build_runtime_debug_exporter_adapters(
            observability=observability,
            registry=registry,
            strict=strict,
        )
    )
    service_process_cfg = observability.get("service_process")
    service_process_enabled = False
    if isinstance(service_process_cfg, dict) and service_process_cfg:
        enabled_raw = service_process_cfg.get("enabled", False)
        if isinstance(enabled_raw, bool):
            service_process_enabled = enabled_raw
        elif strict:
            raise ValueError(
                "runtime.observability.service_process.enabled must be a boolean when provided"
            )
    elif isinstance(observability.get("service_worker"), dict):
        service_worker_cfg = observability.get("service_worker", {})
        enabled_raw = service_worker_cfg.get("enabled", False)
        if isinstance(enabled_raw, bool):
            service_process_enabled = enabled_raw
        elif strict:
            raise ValueError(
                "runtime.observability.service_worker.enabled must be a boolean when provided"
            )
    elif service_process_cfg is not None and strict:
        raise ValueError("runtime.observability.service_process must be a mapping when provided")

    if bootstrap_mode == "process_supervisor" and service_process_enabled and not is_observability_worker:
        # Dedicated observability process owns all exporter adapters.
        # Supervisor (and non-owner roles) stay transport-only, except local runtime debug sink.
        return built

    for channel, alias_map in kind_to_alias.items():
        if is_worker_process:
            # Business workers must not instantiate observability exporters.
            continue
        if channel == "monitoring" and (
            supervisor_owns_monitoring and not is_observability_worker
        ):
            # Monitoring sinks are supervisor-owned in process-supervisor runtime.
            # Worker/runtime adapter materialization must skip monitoring exporters
            # to avoid duplicate HTTP bind in build phase vs supervisor configure_monitoring().
            continue
        channel_cfg = observability.get(channel, {})
        if not isinstance(channel_cfg, dict):
            continue
        exporters = channel_cfg.get("exporters", [])
        if not isinstance(exporters, list):
            continue
        for index, exporter in enumerate(exporters):
            if not isinstance(exporter, dict):
                continue
            if exporter.get("enabled") is False:
                continue
            kind = exporter.get("kind")
            if not isinstance(kind, str) or not kind:
                continue
            alias = alias_map.get(kind)
            if not isinstance(alias, str) or not alias:
                if strict:
                    raise ValueError(
                        f"runtime.observability.{channel}.exporters[{index}] kind '{kind}' is not supported"
                    )
                continue
            indexed_key = f"{alias}#{index}"
            if indexed_key in built:
                continue
            settings = exporter.get("settings", {})
            settings_for_build = dict(settings) if isinstance(settings, dict) else {}
            if channel == "tracing" and kind in {"otel_otlp", "otel_otlp_logical", "otel_otlp_topology"}:
                backend = _resolve_otel_backend_for_build(exporter, settings_for_build)
                if isinstance(backend, str) and backend:
                    settings_for_build["backend"] = backend
                if kind == "otel_otlp_logical":
                    settings_for_build.setdefault("trace_view", "logical")
                    settings_for_build.setdefault("service_name_by_step", True)
                    settings_for_build.setdefault("service_name_by_process_group", False)
                    settings_for_build.setdefault("logical_include_platform_spans", False)
                    settings_for_build.setdefault("service_name_suffix", ".logical")
                    settings_for_build.setdefault("isolate_view_ids", True)
                elif kind == "otel_otlp_topology":
                    settings_for_build.setdefault("trace_view", "topology")
                    settings_for_build.setdefault("service_name_by_step", False)
                    settings_for_build.setdefault("service_name_by_process_group", True)
                    settings_for_build.setdefault("topology_include_business_spans", False)
                    settings_for_build.setdefault("service_name_suffix", ".topology")
                    settings_for_build.setdefault("isolate_view_ids", True)

            try:
                instance = registry.build(alias, {"kind": alias, "settings": settings_for_build})
            except Exception as exc:
                if strict:
                    detail = str(exc).strip()
                    suffix = f": {detail}" if detail else ""
                    raise ValueError(
                        "runtime.observability."
                        f"{channel}.exporters[{index}] failed to build adapter '{alias}'{suffix}"
                    ) from exc
                continue

            built[indexed_key] = instance
            if alias not in existing and alias not in built:
                built[alias] = instance
    return built


def _build_runtime_debug_exporter_adapters(
    *,
    observability: dict[str, object],
    registry: AdapterRegistry,
    strict: bool,
) -> dict[str, object]:
    built: dict[str, object] = {}
    logging_cfg = observability.get("logging", {})
    if not isinstance(logging_cfg, dict):
        return built
    exporters = logging_cfg.get("exporters", [])
    if not isinstance(exporters, list):
        return built
    for index, exporter in enumerate(exporters):
        if not isinstance(exporter, dict):
            continue
        if exporter.get("enabled") is False:
            continue
        if exporter.get("kind") != "redis_debug":
            continue
        settings = exporter.get("settings", {})
        settings_for_build = dict(settings) if isinstance(settings, dict) else {}
        instance = None
        built_role = ""
        build_errors: list[str] = []
        for role, kind in (("debug_redis", "debug_redis"), ("log_redis_debug", "log_redis_debug")):
            try:
                instance = registry.build(role, {"kind": kind, "settings": settings_for_build})
                built_role = role
                break
            except Exception as exc:
                build_errors.append(f"{role}: {exc}")
                continue
        if instance is None:
            if strict:
                detail = "; ".join(error.strip() for error in build_errors if error.strip())
                suffix = f": {detail}" if detail else ""
                raise ValueError(
                    "runtime.observability.logging.exporters"
                    f"[{index}] failed to build adapter 'debug_redis'{suffix}"
                )
            continue
        indexed_key = f"debug_redis#{index}"
        built[indexed_key] = instance
        if "debug_redis" not in built:
            built["debug_redis"] = instance
        if built_role == "log_redis_debug":
            fallback_key = f"log_redis_debug#{index}"
            built.setdefault(fallback_key, instance)
            built.setdefault("log_redis_debug", instance)
    return built


def build_adapter_bindings(
    adapters: dict[str, object],
    registry: AdapterRegistry,
) -> dict[str, object]:
    # Convert config binds (stable port names) into typed injection bindings from adapter metadata.
    bindings: dict[str, object] = {}
    for role, cfg in adapters.items():
        if not isinstance(cfg, dict):
            continue
        meta = registry.get_meta(role, role)
        if meta is None:
            continue

        if "binds" in cfg:
            requested = cfg.get("binds", [])
        else:
            requested = []
            if any(
                port_type == "kv_stream"
                and isinstance(data_type, type)
                and issubclass(data_type, ExecutionIpcKvStreamPort)
                for port_type, data_type in meta.binds
            ):
                requested = ["kv_stream"]
        if not isinstance(requested, list):
            raise ValueError(f"adapters.{role}.binds must be a list")
        if not all(isinstance(item, str) for item in requested):
            raise ValueError(f"adapters.{role}.binds entries must be strings")

        resolved: list[tuple[str, type[Any]]] = []
        for port_type in requested:
            matches = [(ptype, dtype) for (ptype, dtype) in meta.binds if ptype == port_type]
            if not matches:
                raise ValueError(
                    f"adapters.{role}.binds includes unsupported port_type '{port_type}' for adapter '{role}'"
                )
            resolved.extend(matches)
        if resolved:
            bindings[role] = resolved
    return bindings
