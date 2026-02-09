from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType
from typing import Any

from stream_kernel.adapters.discovery import discover_adapters
from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.app.cli import apply_cli_overrides, parse_args
from stream_kernel.app.extensions import framework_discovery_modules
from stream_kernel.application_context import (
    ApplicationContext,
    apply_injection,
    discover_services,
    service_contract_types,
)
from stream_kernel.application_context.injection_registry import (
    InjectionRegistry,
    InjectionRegistryError,
    ScenarioScope,
)
from stream_kernel.config.loader import load_yaml_config
from stream_kernel.config.validator import validate_newgen_config
from stream_kernel.execution.observer import ExecutionObserver
from stream_kernel.execution.observer_builder import build_execution_observers
from stream_kernel.execution.planning import build_execution_plan
from stream_kernel.execution.runner import SyncRunner
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore
from stream_kernel.integration.routing_port import RoutingPort
from stream_kernel.integration.work_queue import InMemoryWorkQueue
from stream_kernel.kernel.dag import NodeContract


def run_with_config(
    config: dict[str, object],
    *,
    adapter_registry: AdapterRegistry | None = None,
    adapter_bindings: dict[str, object] | None = None,
    discovery_modules: list[str] | None = None,
    argv_overrides: dict[str, str] | None = None,
    run_id: str = "run",
) -> int:
    # Build and run the pipeline from a validated newgen config.
    if argv_overrides:
        args = SimpleNamespace(
            input=argv_overrides.get("input"),
            output=argv_overrides.get("output"),
            tracing=argv_overrides.get("tracing"),
            trace_path=argv_overrides.get("trace_path"),
        )
        apply_cli_overrides(config, args)

    runtime = config.get("runtime", {})
    if not isinstance(runtime, dict):
        raise ValueError("runtime must be a mapping")
    _reject_runtime_pipeline(runtime)
    if discovery_modules is None:
        discovered_modules = runtime.get("discovery_modules", [])
        if not isinstance(discovered_modules, list) or not all(
            isinstance(item, str) for item in discovered_modules
        ):
            raise ValueError("runtime.discovery_modules must be a list of strings")
        discovery_modules = list(discovered_modules)
    else:
        discovery_modules = list(discovery_modules)
    _ensure_platform_discovery_modules(discovery_modules)

    adapters = config.get("adapters", {})
    if not isinstance(adapters, dict):
        raise ValueError("adapters must be a mapping")
    adapters = dict(adapters)

    if adapter_registry is None and adapter_bindings is not None:
        raise ValueError("adapter_bindings override requires adapter_registry override")
    if adapter_registry is None:
        adapter_registry, resolved_bindings = _resolve_runtime_adapters(
            adapters=adapters,
            discovery_modules=discovery_modules,
        )
        if adapter_bindings is None:
            adapter_bindings = resolved_bindings
    if adapter_bindings is None:
        adapter_bindings = _build_adapter_bindings(adapters, adapter_registry)

    adapter_instances = _build_adapter_instances_from_registry(adapters, adapter_registry)
    # Build injection registry from provided bindings using shared adapter instances.
    injection_registry = _build_injection_registry_from_bindings(adapter_instances, adapter_bindings)
    _ensure_runtime_kv_binding(injection_registry, runtime)

    ctx = ApplicationContext()
    modules = _load_discovery_modules(discovery_modules)
    ctx.discover(modules)
    _register_discovered_services(injection_registry, modules)
    adapter_contracts = _build_adapter_contracts(adapters, adapter_registry=adapter_registry)
    dag = ctx.preflight(
        strict=bool(runtime.get("strict", True)),
        extra_contracts=adapter_contracts,
    )
    consumer_registry = ctx.build_consumer_registry()
    step_names = _resolve_step_names(dag)

    scenario_name = _scenario_name(config)
    scenario_scope = injection_registry.instantiate_for_scenario(scenario_name)
    scenario = ctx.build_scenario(
        scenario_id=scenario_name,
        step_names=step_names,
        wiring={
            "injection_registry": injection_registry,
            "scenario_scope": scenario_scope,
            "consumer_registry": consumer_registry,
            "config": config,
            "strict": bool(runtime.get("strict", True)),
        },
    )

    # Source bootstrap: any read-capable adapter can provide input payloads.
    inputs = _read_inputs_from_sources(adapter_instances)
    observers = build_execution_observers(
        modules=modules,
        runtime=runtime,
        adapter_instances=adapter_instances,
        run_id=run_id,
        scenario_id=scenario_name,
        step_specs=list(scenario.steps),
    )
    strict = bool(runtime.get("strict", True))
    _run_with_sync_runner(
        scenario=scenario,
        inputs=inputs,
        consumer_registry=consumer_registry,
        strict=strict,
        run_id=run_id,
        scenario_id=scenario_name,
        scenario_scope=scenario_scope,
        observers=observers,
        full_context_nodes={
            node_def.meta.name
            for node_def in ctx.nodes
            if bool(getattr(node_def.meta, "service", False))
        },
    )
    return 0


def run_with_registry(
    argv: list[str] | None,
    *,
    adapter_registry: AdapterRegistry,
    adapter_bindings: dict[str, object],
    discovery_modules: list[str],
) -> int:
    # Generic framework entrypoint: parse CLI, load/validate config, apply overrides, run pipeline.
    args = parse_args(argv or [])
    config = validate_newgen_config(load_yaml_config(Path(args.config)))
    apply_cli_overrides(config, args)
    return run_with_config(
        config,
        adapter_registry=adapter_registry,
        adapter_bindings=adapter_bindings,
        discovery_modules=discovery_modules,
        argv_overrides=None,
    )


def run(argv: list[str] | None) -> int:
    # Generic framework entrypoint using discovered adapter kinds from config.
    args = parse_args(argv or [])
    config = validate_newgen_config(load_yaml_config(Path(args.config)))
    apply_cli_overrides(config, args)
    return run_with_config(config, argv_overrides=None, run_id="run")


def _resolve_runtime_adapters(
    *,
    adapters: dict[str, object],
    discovery_modules: list[str],
) -> tuple[AdapterRegistry, dict[str, object]]:
    # Discover adapters by name and bind them to equally named YAML roles.
    modules = _load_discovery_modules(discovery_modules)
    discovered = discover_adapters(modules)

    registry = AdapterRegistry()
    for role, cfg in adapters.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"adapters.{role} must be a mapping")
        if "kind" in cfg:
            raise ValueError(
                f"adapters.{role}.kind is not supported; adapter name is defined by adapters.{role}"
            )
        factory = discovered.get(role)
        if factory is None:
            raise ValueError(f"Unknown adapter name: {role}")
        # Reuse role as registry key-kind to keep AdapterRegistry API unchanged.
        registry.register(role, role, factory)

    bindings = _build_adapter_bindings(adapters, registry)
    return registry, bindings


def _build_adapter_bindings(
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


def _ensure_platform_discovery_modules(discovery_modules: list[str]) -> None:
    # Framework platform modules are resolved from extension providers.
    for module_name in framework_discovery_modules():
        if module_name not in discovery_modules:
            discovery_modules.append(module_name)


def _load_discovery_modules(discovery_modules: list[str]) -> list[ModuleType]:
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


def _finalize_execution_observers(observers: list[ExecutionObserver]) -> None:
    # Finalize observer lifecycle once execution queue is drained.
    for observer in observers:
        observer.on_run_end()


def _register_discovered_services(registry: InjectionRegistry, modules: list[object]) -> None:
    # Register services discovered in framework/user modules into DI unless overridden.
    for service_cls in discover_services(modules):  # type: ignore[arg-type]
        for contract in service_contract_types(service_cls):
            try:
                registry.register_factory("service", contract, lambda _cls=service_cls: _cls())
            except InjectionRegistryError:
                # Explicit bindings win over auto-discovered defaults.
                continue


def _run_with_sync_runner(
    *,
    scenario,
    inputs,
    consumer_registry,
    strict: bool,
    run_id: str,
    scenario_id: str,
    scenario_scope: ScenarioScope,
    observers: list[ExecutionObserver] | None = None,
    full_context_nodes: set[str] | None = None,
) -> None:
    # Build execution components (Execution runtime and routing integration §6).
    work_queue = InMemoryWorkQueue()
    routing_port = RoutingPort(registry=consumer_registry, strict=strict)

    nodes = {spec.name: spec.step for spec in scenario.steps}
    runner = SyncRunner(
        nodes=nodes,
        work_queue=work_queue,
        routing_port=routing_port,
        observers=list(observers or ()),
        full_context_nodes=set(full_context_nodes or ()),
    )
    apply_injection(runner, scenario_scope, strict)
    runner.run_inputs(inputs, run_id=run_id, scenario_id=scenario_id)
    _finalize_execution_observers(list(observers or ()))


def _read_inputs_from_sources(adapter_instances: dict[str, object]) -> list[object]:
    # Build deterministic startup payload list from read-capable adapters.
    payloads: list[object] = []
    source_roles: list[str] = []
    for role in sorted(adapter_instances.keys()):
        adapter = adapter_instances[role]
        read = getattr(adapter, "read", None)
        if not callable(read):
            continue
        source_roles.append(role)
        payloads.extend(read())
    if not source_roles:
        raise ValueError("at least one source adapter with read() must be configured")
    return payloads


def _resolve_step_names(dag: object | None) -> list[str]:
    # Runtime order is execution-plan driven and derived from DAG contracts.
    if dag is None:
        return []
    if not hasattr(dag, "nodes") or not hasattr(dag, "edges"):
        raise ValueError("preflight must return a Dag-like object with nodes and edges")
    # Adapter contracts participate in DAG validation but are not executable scenario steps.
    return [name for name in build_execution_plan(dag) if not name.startswith("adapter:")]


def _build_adapter_contracts(
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
        meta = _resolve_adapter_meta(role, cfg, adapter_registry=adapter_registry)
        if meta is None:
            continue
        consumes = list(meta.consumes)
        emits = list(meta.emits)
        if not consumes and not emits:
            continue
        contracts.append(NodeContract(name=f"adapter:{role}", consumes=consumes, emits=emits))
    return contracts


def _resolve_adapter_meta(
    role: str,
    cfg: dict[str, object],
    *,
    adapter_registry: AdapterRegistry | None,
):
    # Resolve adapter metadata from AdapterRegistry role/kind registrations.
    if adapter_registry is not None:
        meta = adapter_registry.get_meta(role, role)
        if meta is not None:
            return meta
    return None


def _reject_runtime_pipeline(runtime: dict[str, object]) -> None:
    if "pipeline" in runtime:
        raise ValueError(
            "runtime.pipeline is no longer supported; rely on consumes/emits routing contracts"
        )


def _trace_id(run_id: str, _payload: object, index: int) -> str:
    # Keep per-message trace ids deterministic without relying on project payload fields.
    return f"{run_id}:{index}"


def _initial_context(
    _payload: object,
    trace_id: str,
    *,
    run_id: str,
    scenario_id: str,
) -> dict[str, object]:
    # Minimal metadata view exposed to nodes via KV-backed context persistence.
    # Reserved keys are available to service nodes and observers.
    ctx: dict[str, object] = {
        "__trace_id": trace_id,
        "__run_id": run_id,
        "__scenario_id": scenario_id,
    }
    return ctx


def _build_adapter_instances_from_registry(
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


def _build_injection_registry_from_bindings(
    instances: dict[str, object],
    bindings: dict[str, object],
) -> InjectionRegistry:
    # Build InjectionRegistry from explicit bindings using shared instances (runtime wiring).
    injection = InjectionRegistry()
    for role, binding in bindings.items():
        if role not in instances:
            raise ValueError(f"Missing adapter instance for role: {role}")
        adapter = instances[role]
        if isinstance(binding, list):
            for port_type, data_type in binding:
                injection.register_factory(port_type, data_type, lambda _a=adapter: _a)
        else:
            port_type, data_type = binding
            injection.register_factory(port_type, data_type, lambda _a=adapter: _a)
    return injection


def _ensure_runtime_kv_binding(
    injection_registry: InjectionRegistry,
    runtime: dict[str, object],
) -> None:
    # Runtime-level default: provide KVStore binding unless explicitly bound by config/adapters.
    backend = _runtime_kv_backend(runtime)
    if backend != "memory":
        raise ValueError(f"Unsupported runtime.platform.kv.backend: {backend}")
    try:
        injection_registry.register_factory("kv", KVStore, lambda: InMemoryKvStore())
    except InjectionRegistryError:
        # Explicit binding already exists and must win over default provisioning.
        return


def _runtime_kv_backend(runtime: dict[str, object]) -> str:
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


def _scenario_name(config: dict[str, object]) -> str:
    scenario = config.get("scenario", {})
    if isinstance(scenario, dict):
        name = scenario.get("name")
        if isinstance(name, str):
            return name
    return "scenario"
