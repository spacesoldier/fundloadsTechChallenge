from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from types import ModuleType, SimpleNamespace
from typing import Any

from stream_kernel.adapters.discovery import discover_adapters
from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.app.extensions import framework_discovery_modules
from stream_kernel.application_context import (
    ApplicationContext,
    apply_injection,
    discover_services,
    service_contract_types,
)
from stream_kernel.application_context.inject import Injected, inject
from stream_kernel.application_context.injection_registry import (
    InjectionRegistry,
    InjectionRegistryError,
    ScenarioScope,
)
from stream_kernel.execution.transport.bootstrap_keys import (
    BootstrapKeyBundle,
    resolve_execution_ipc_key_material,
)
from stream_kernel.execution.orchestration.lifecycle_orchestration import (
    execute_with_bootstrap_supervisor,
    execute_with_runtime_lifecycle,
    runtime_bootstrap_mode,
)
from stream_kernel.execution.observers.observer_builder import build_execution_observers
from stream_kernel.execution.runtime.planning import build_execution_plan, plan_pools
from stream_kernel.execution.runtime.runner import AsyncRunner, SyncRunner
from stream_kernel.execution.transport.secure_tcp_transport import SecureTcpConfig, SecureTcpTransport
from stream_kernel.execution.orchestration.source_ingress import (
    WEB_INGRESS_LIMITER_QUALIFIER,
    BootstrapControl,
    build_source_ingress_plan,
)
from stream_kernel.execution.orchestration.observability_system_nodes import (
    build_observability_system_plan,
)
from stream_kernel.integration.consumer_registry import ConsumerRegistry
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore
from stream_kernel.kernel.dag import NodeContract
from stream_kernel.kernel.scenario import Scenario, StepSpec
from stream_kernel.platform.services.observability import (
    FanoutObservabilityService,
    ObservabilityPipelineService,
    ObservabilityService,
    ReplyAwareObservabilityService,
)
from stream_kernel.platform.services.api.policy import (
    ApiPolicyService,
    InMemoryApiPolicyService,
    InMemoryRateLimiterService,
    RateLimiterService,
)
from stream_kernel.platform.services.api.outbound import (
    InMemoryOutboundApiService,
    OutboundApiService,
)
from stream_kernel.platform.services.runtime.transport import (
    MemoryRuntimeTransportService,
    RuntimeTransportService,
    TcpLocalRuntimeTransportService,
)
from stream_kernel.platform.services.runtime.bootstrap import (
    BootstrapSupervisor,
    LocalBootstrapSupervisor,
    MultiprocessBootstrapSupervisor,
)
from stream_kernel.routing.envelope import Envelope

BUILD_TIME_REGISTRY_TYPES = (AdapterRegistry, InjectionRegistry)
RUNTIME_SERVICE_REGISTRY_CONTRACTS = (ApplicationContext,)
DEFAULT_EXECUTION_QUEUE_QUALIFIER = "execution.cpu"
DEFAULT_ASYNC_QUEUE_QUALIFIER = "execution.asyncio"


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


def register_discovered_services(registry: InjectionRegistry, modules: list[object]) -> None:
    # Register services discovered in framework/user modules into DI unless overridden.
    discovered = discover_services(modules)  # type: ignore[arg-type]
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


def run_with_sync_runner(
    *,
    scenario,
    inputs,
    strict: bool,
    run_id: str,
    scenario_id: str,
    scenario_scope: ScenarioScope,
    full_context_nodes: set[str] | None = None,
    ordered_sink_mode: str = "completion",
    queue_qualifier: str = DEFAULT_EXECUTION_QUEUE_QUALIFIER,
) -> None:
    # Build execution components (Execution runtime and routing integration §6).
    nodes = {spec.name: spec.step for spec in scenario.steps}
    runner = SyncRunner(
        nodes=nodes,
        full_context_nodes=set(full_context_nodes or ()),
        ordered_sink_mode=ordered_sink_mode,
    )
    if queue_qualifier != DEFAULT_EXECUTION_QUEUE_QUALIFIER:
        runner.work_queue = inject.queue(Envelope, qualifier=queue_qualifier)
    apply_injection(runner, scenario_scope, strict)
    try:
        runner.run_inputs(inputs, run_id=run_id, scenario_id=scenario_id)
    finally:
        runner.on_run_end()
        close_scenario_scope(scenario_scope)


def run_with_async_runner(
    *,
    scenario,
    inputs,
    strict: bool,
    run_id: str,
    scenario_id: str,
    scenario_scope: ScenarioScope,
    full_context_nodes: set[str] | None = None,
    ordered_sink_mode: str = "completion",
    queue_qualifier: str = DEFAULT_ASYNC_QUEUE_QUALIFIER,
) -> None:
    # Build async execution components under the same DI/discovery rails as SyncRunner.
    nodes = {spec.name: spec.step for spec in scenario.steps}
    runner = AsyncRunner(
        nodes=nodes,
        full_context_nodes=set(full_context_nodes or ()),
        ordered_sink_mode=ordered_sink_mode,
    )
    if queue_qualifier != DEFAULT_ASYNC_QUEUE_QUALIFIER:
        runner.work_queue = inject.queue(Envelope, qualifier=queue_qualifier)
    apply_injection(runner, scenario_scope, strict)
    try:
        runner.run_inputs(inputs, run_id=run_id, scenario_id=scenario_id)
    finally:
        runner.on_run_end()
        close_scenario_scope(scenario_scope)


def execute_runtime_artifacts(artifacts: RuntimeBuildArtifacts) -> None:
    # Single execution entrypoint for runtime-prepared artifacts.
    profile = runtime_execution_transport_profile(artifacts.runtime)
    if profile != "tcp_local":
        _execute_runner(artifacts)
        return
    if runtime_bootstrap_mode(artifacts.runtime) == "process_supervisor":
        execute_with_bootstrap_supervisor(
            config=artifacts.config,
            runtime=artifacts.runtime,
            scenario_id=artifacts.scenario_id,
            run_id=artifacts.run_id,
            inputs=list(artifacts.inputs),
            scenario_scope=artifacts.scenario_scope,
            adapters=dict(artifacts.adapters),
            run=lambda: _execute_runner(artifacts),
        )
        return
    execute_with_runtime_lifecycle(
        runtime=artifacts.runtime,
        scenario_scope=artifacts.scenario_scope,
        run=lambda: _execute_runner(artifacts),
    )


def _execute_runner(artifacts: RuntimeBuildArtifacts) -> None:
    ordered_sink_mode = runtime_ordering_sink_mode(artifacts.runtime)
    runner_profile, queue_qualifier = _resolve_runner_profile_and_queue(artifacts)
    if runner_profile == "async":
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
        return
    run_with_sync_runner(
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


def close_scenario_scope(scope: ScenarioScope) -> None:
    # Finalize scoped resources if the scope exposes lifecycle hooks.
    close = getattr(scope, "close", None)
    if callable(close):
        close()


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
    ctx.discover(modules)
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
    observers = build_execution_observers(
        modules=modules,
        runtime=runtime,
        adapter_instances=adapter_instances,
        run_id=run_id,
        scenario_id=scenario_name(config),
        node_order=step_names,
    )
    ensure_runtime_observability_binding(
        injection_registry=injection_registry,
        observers=observers,
    )
    register_discovered_services(injection_registry, modules)
    ensure_runtime_bootstrap_binding(
        injection_registry=injection_registry,
        runtime=runtime,
    )
    ensure_runtime_api_policy_bindings(
        injection_registry=injection_registry,
        runtime=runtime,
    )

    scenario_id = scenario_name(config)
    ensure_runtime_transport_bindings(
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
    source_ingress = build_source_ingress_plan(
        adapters=adapters,
        adapter_instances=adapter_instances,
        adapter_registry=adapter_registry,
        scenario_scope=scenario_scope,
        run_id=run_id,
        scenario_id=scenario_id,
        runtime=runtime,
    )
    for token, node_names in source_ingress.source_consumers.items():
        get_consumers = getattr(consumer_registry, "get_consumers", None)
        register = getattr(consumer_registry, "register", None)
        if not callable(get_consumers) or not callable(register):
            continue
        existing = list(get_consumers(token))
        register(token, [*existing, *node_names])
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
    for token, node_names in sink_consumers.items():
        get_consumers = getattr(consumer_registry, "get_consumers", None)
        register = getattr(consumer_registry, "register", None)
        if not callable(get_consumers) or not callable(register):
            continue
        existing = list(get_consumers(token))
        register(token, [*existing, *node_names])

    observability_system = build_observability_system_plan(
        runtime=runtime,
        scenario_scope=scenario_scope,
    )
    for token, node_names in observability_system.system_consumers.items():
        get_consumers = getattr(consumer_registry, "get_consumers", None)
        register = getattr(consumer_registry, "register", None)
        if not callable(get_consumers) or not callable(register):
            continue
        existing = list(get_consumers(token))
        register(token, [*existing, *node_names])

    sink_steps = [StepSpec(name=name, step=step) for name, step in sink_nodes.items()]
    existing_steps = list(getattr(scenario, "steps", []))
    if isinstance(scenario, Scenario):
        scenario = Scenario(
            scenario_id=scenario.scenario_id,
            steps=tuple(
                [
                    *source_ingress.source_steps,
                    *existing_steps,
                    *observability_system.system_steps,
                    *sink_steps,
                ]
            ),
        )
    else:
        scenario = SimpleNamespace(
            steps=[
                *source_ingress.source_steps,
                *existing_steps,
                *observability_system.system_steps,
                *sink_steps,
            ]
        )

    return RuntimeBuildArtifacts(
        scenario=scenario,
        inputs=source_ingress.bootstrap_inputs,
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
        | set(observability_system.system_node_names),
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
    observers: list[object],
    replace: bool = True,
) -> None:
    # Bind platform observability service to runtime fan-out implementation for this run.
    requires_async = _observers_require_async_dispatch(observers)
    factory = lambda _observers=list(observers): ReplyAwareObservabilityService(
        inner=FanoutObservabilityService(observers=list(_observers))
    )
    for contract in (ObservabilityService, ObservabilityPipelineService):
        injection_registry.register_factory(
            "service",
            contract,
            factory,
            is_async=requires_async,
            replace=replace,
        )


def _observers_require_async_dispatch(observers: list[object]) -> bool:
    for observer in observers:
        if callable(getattr(observer, "on_trace_event_async", None)):
            return True
        if callable(getattr(observer, "on_log_event_async", None)):
            return True
        if callable(getattr(observer, "on_metric_event_async", None)):
            return True
        if callable(getattr(observer, "on_monitoring_event_async", None)):
            return True
        sink = getattr(observer, "_sink", None)
        if callable(getattr(sink, "emit_async", None)):
            return True
    return False


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

    if consumer_registry is None:
        return
    for contract in {ConsumerRegistry, type(consumer_registry)}:
        injection_registry.register_factory(
            "service",
            contract,
            lambda _registry=consumer_registry: _registry,
            replace=True,
        )


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


def ensure_runtime_bootstrap_binding(
    *,
    injection_registry: InjectionRegistry,
    runtime: dict[str, object],
) -> None:
    # Runtime bootstrap supervisor contract is selected explicitly by bootstrap.mode
    # so discovery order cannot silently switch process-supervisor behavior.
    mode = runtime_bootstrap_mode(runtime)
    supervisor_cls = (
        MultiprocessBootstrapSupervisor if mode == "process_supervisor" else LocalBootstrapSupervisor
    )
    injection_registry.register_factory(
        "service",
        BootstrapSupervisor,
        lambda _cls=supervisor_cls: _cls(),
        replace=True,
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


def _resolve_runner_profile_and_queue(artifacts: RuntimeBuildArtifacts) -> tuple[str, str]:
    runtime = artifacts.runtime
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        inferred = _infer_runner_profile_from_dependencies(artifacts)
        qualifier = (
            DEFAULT_ASYNC_QUEUE_QUALIFIER
            if inferred == "async"
            else DEFAULT_EXECUTION_QUEUE_QUALIFIER
        )
        return (inferred, qualifier)
    process_groups = platform.get("process_groups", [])
    if not isinstance(process_groups, list) or not process_groups:
        inferred = _infer_runner_profile_from_dependencies(artifacts)
        qualifier = (
            DEFAULT_ASYNC_QUEUE_QUALIFIER
            if inferred == "async"
            else DEFAULT_EXECUTION_QUEUE_QUALIFIER
        )
        return (inferred, qualifier)

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
    explicit_profile: str | None = None
    if resolved_group is not None:
        runner_profile = resolved_group.get("runner_profile")
        if isinstance(runner_profile, str) and runner_profile in {"sync", "async"}:
            explicit_profile = runner_profile

    inferred_profile = _infer_runner_profile_from_dependencies(artifacts)
    final_profile = explicit_profile or inferred_profile
    queue_qualifier = resolved_group.get("name") if isinstance(resolved_group, dict) else None
    if not isinstance(queue_qualifier, str) or not queue_qualifier:
        queue_qualifier = (
            DEFAULT_ASYNC_QUEUE_QUALIFIER
            if final_profile == "async"
            else DEFAULT_EXECUTION_QUEUE_QUALIFIER
        )
    return (final_profile, queue_qualifier)


def _infer_runner_profile_from_dependencies(artifacts: RuntimeBuildArtifacts) -> str:
    registry = artifacts.injection_registry
    if not isinstance(registry, InjectionRegistry):
        return "sync"

    scenario_steps = getattr(artifacts.scenario, "steps", ())
    if not isinstance(scenario_steps, (list, tuple)):
        return "sync"

    nodes: dict[str, object] = {}
    for step_spec in scenario_steps:
        name = getattr(step_spec, "name", None)
        step = getattr(step_spec, "step", None)
        if not isinstance(name, str) or not name:
            continue
        if step is None:
            continue
        nodes[name] = step

    if not nodes:
        return "sync"

    pools = plan_pools(nodes, registry)
    if any(pool == "async" for pool in pools.values()):
        return "async"
    return "sync"


def _build_runtime_transport_service(
    runtime: dict[str, object],
    *,
    bootstrap_key_bundle: BootstrapKeyBundle | None = None,
) -> RuntimeTransportService:
    profile = runtime_execution_transport_profile(runtime)
    if profile == "memory":
        return MemoryRuntimeTransportService()
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
        },
    }

    observability = runtime.get("observability", {})
    if not isinstance(observability, dict):
        return built

    for channel, alias_map in kind_to_alias.items():
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

            indexed_key = f"{alias}#{index}"
            built[indexed_key] = instance
            if alias not in existing and alias not in built:
                built[alias] = instance
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

        requested = cfg.get("binds", [])
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
