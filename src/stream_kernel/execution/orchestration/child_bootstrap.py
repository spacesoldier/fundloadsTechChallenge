from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from stream_kernel.application_context.application_context import ApplicationContext
from stream_kernel.application_context.service import discover_services
from stream_kernel.application_context.injection_registry import (
    InjectionRegistry,
    InjectionRegistryError,
    ScenarioScope,
)
from stream_kernel.execution.transport.bootstrap_keys import BootstrapKeyBundle
from stream_kernel.execution.runtime.planning import plan_pools
from stream_kernel.execution.runtime.runner import AsyncRunner, SyncRunner
from stream_kernel.execution.orchestration.observability_system_nodes import (
    build_observability_system_plan,
)
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.platform.services.state.context import ContextService
from stream_kernel.platform.services.runtime.lifecycle import RuntimeLifecycleManager
from stream_kernel.platform.services.observability import (
    NoOpObservabilityService,
    ObservabilityService as ObservabilityServiceContract,
    ObservabilityService,
)
from stream_kernel.platform.services.runtime.transport import RuntimeTransportService
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.routing_service import RoutingService


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


def build_child_bootstrap_bundle(
    *,
    scenario_id: str,
    run_id: str = "run",
    process_group: str | None,
    discovery_modules: list[str],
    runtime: dict[str, object],
    config: dict[str, object] | None = None,
    adapters: dict[str, object] | None = None,
    key_bundle: BootstrapKeyBundle,
) -> ChildBootstrapBundle:
    return ChildBootstrapBundle(
        scenario_id=scenario_id,
        process_group=process_group,
        discovery_modules=list(discovery_modules),
        config=dict(config) if isinstance(config, dict) else None,
        runtime=dict(runtime),
        key_bundle=key_bundle,
        run_id=run_id,
        adapters=dict(adapters or {}),
    )


def bootstrap_child_runtime_from_bundle(bundle: ChildBootstrapBundle) -> ChildRuntimeBootstrap:
    # Build child runtime DI/discovery from metadata bundle only (no serialized object graphs).
    from stream_kernel.execution.orchestration import builder as execution_builder

    if not isinstance(bundle, ChildBootstrapBundle):
        raise ChildRuntimeBootstrapError("child bootstrap bundle must be ChildBootstrapBundle")
    if not isinstance(bundle.scenario_id, str) or not bundle.scenario_id:
        raise ChildRuntimeBootstrapError("child bootstrap bundle.scenario_id must be a non-empty string")
    if not isinstance(bundle.run_id, str) or not bundle.run_id:
        raise ChildRuntimeBootstrapError("child bootstrap bundle.run_id must be a non-empty string")
    if bundle.process_group is not None and (not isinstance(bundle.process_group, str) or not bundle.process_group):
        raise ChildRuntimeBootstrapError("child bootstrap bundle.process_group must be null or non-empty string")
    if not isinstance(bundle.discovery_modules, list) or not all(
        isinstance(item, str) and item for item in bundle.discovery_modules
    ):
        raise ChildRuntimeBootstrapError(
            "child bootstrap bundle.discovery_modules must be list[str] with non-empty entries"
        )
    if not isinstance(bundle.runtime, dict):
        raise ChildRuntimeBootstrapError("child bootstrap bundle.runtime must be a mapping")
    if bundle.config is not None and not isinstance(bundle.config, dict):
        raise ChildRuntimeBootstrapError("child bootstrap bundle.config must be a mapping")
    if bundle.adapters is not None and not isinstance(bundle.adapters, dict):
        raise ChildRuntimeBootstrapError("child bootstrap bundle.adapters must be a mapping")
    if not isinstance(bundle.key_bundle, BootstrapKeyBundle):
        raise ChildRuntimeBootstrapError("child bootstrap bundle.key_bundle must be BootstrapKeyBundle")

    discovery_modules = list(bundle.discovery_modules)
    bundle_adapters = dict(bundle.adapters or {})
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
            runtime=bundle.runtime,
            registry=adapter_registry,
            existing_instances=adapter_instances,
        )
    )
    adapter_async_roles = execution_builder.resolve_async_adapter_roles(
        adapters=bundle_adapters,
        adapter_registry=adapter_registry,
    )
    if adapter_bindings:
        _register_adapter_bindings(
            injection_registry=injection_registry,
            instances=adapter_instances,
            bindings=adapter_bindings,
            adapter_async_roles=adapter_async_roles,
        )

    execution_builder.register_discovered_services(injection_registry, modules)
    execution_builder.ensure_runtime_registry_bindings(
        injection_registry=injection_registry,
        app_context=app_context,
        consumer_registry=consumer_registry,
    )
    execution_builder.ensure_runtime_kv_binding(injection_registry, bundle.runtime)
    execution_builder.ensure_runtime_transport_bindings(
        injection_registry=injection_registry,
        runtime=bundle.runtime,
        bootstrap_key_bundle=bundle.key_bundle,
    )
    step_names = [node_def.meta.name for node_def in app_context.nodes]
    observers = execution_builder.build_execution_observers(
        modules=modules,
        runtime=bundle.runtime,
        adapter_instances=adapter_instances,
        run_id=bundle.run_id,
        scenario_id=bundle.scenario_id,
        node_order=step_names,
    )
    custom_observability_declared = any(
        isinstance(service_cls, type)
        and issubclass(service_cls, ObservabilityServiceContract)
        and service_cls.__module__ != "stream_kernel.platform.services.observability"
        for service_cls in discover_services(modules)
    )
    try:
        execution_builder.ensure_runtime_observability_binding(
            injection_registry=injection_registry,
            observers=observers,
            replace=not custom_observability_declared,
        )
    except InjectionRegistryError:
        if not custom_observability_declared:
            raise
    scenario_scope = injection_registry.instantiate_for_scenario(bundle.scenario_id)
    scenario = app_context.build_scenario(
        scenario_id=bundle.scenario_id,
        step_names=step_names,
        wiring={
            "injection_registry": injection_registry,
            "scenario_scope": scenario_scope,
            "config": dict(bundle.config) if isinstance(bundle.config, dict) else {"runtime": dict(bundle.runtime)},
            "strict": True,
        },
    )
    source_step_names: set[str] = set()
    combined_steps = list(getattr(scenario, "steps", []))
    if bundle_adapters and adapter_registry is not None:
        source_ingress = execution_builder.build_source_ingress_plan(
            adapters=bundle_adapters,
            adapter_instances=adapter_instances,
            adapter_registry=adapter_registry,
            scenario_scope=scenario_scope,
            run_id=bundle.run_id,
            scenario_id=bundle.scenario_id,
            runtime=bundle.runtime,
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
            for node_def in app_context.nodes
            if node_def.meta.name in set(step_names)
            for token in getattr(node_def.meta, "consumes", [])
        }
        sink_nodes, sink_consumers = execution_builder.build_sink_runtime_nodes(
            adapters=bundle_adapters,
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

        combined_steps = [*source_ingress.source_steps, *combined_steps]
        combined_steps.extend(
            [StepSpec(name=name, step=step) for name, step in sink_nodes.items()]
        )
        source_step_names = set(source_ingress.source_node_names)

    observability_system = build_observability_system_plan(
        runtime=bundle.runtime,
        scenario_scope=scenario_scope,
    )
    for token, node_names in observability_system.system_consumers.items():
        get_consumers = getattr(consumer_registry, "get_consumers", None)
        register = getattr(consumer_registry, "register", None)
        if not callable(get_consumers) or not callable(register):
            continue
        existing = list(get_consumers(token))
        register(token, [*existing, *node_names])
    combined_steps.extend(observability_system.system_steps)

    scenario_steps = {spec.name: spec.step for spec in combined_steps}
    planning_steps = _select_group_planning_steps(
        scenario_steps=scenario_steps,
        runtime=bundle.runtime,
        process_group=bundle.process_group,
    )
    runner_profile_nodes = plan_pools(planning_steps, injection_registry)
    runner_profile_effective = (
        "async"
        if any(pool == "async" for pool in runner_profile_nodes.values())
        else "sync"
    )
    async_service_contracts, async_adapter_bindings = _classify_async_bindings(injection_registry)
    observability_exporters = _collect_observability_exporter_summary(
        runtime=bundle.runtime,
        adapter_instances=adapter_instances,
    )
    full_context_nodes = (
        {node_def.meta.name for node_def in app_context.nodes if node_def.meta.service}
        | source_step_names
        | set(observability_system.system_node_names)
    )

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
        scenario_id=bundle.scenario_id,
        process_group=bundle.process_group,
        discovery_modules=discovery_modules,
        modules=modules,
        runtime=dict(bundle.runtime),
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


def execute_child_boundary_loop_from_bundle(
    *,
    bundle: ChildBootstrapBundle,
    inputs: list[object],
) -> list[Envelope]:
    # Helper used by boundary supervisors: bootstrap child runtime and execute one boundary batch.
    if not inputs:
        return []
    first = _normalize_child_boundary_input(inputs[0])
    effective_bundle = (
        bundle
        if bundle.process_group == first.dispatch_group
        else ChildBootstrapBundle(
            scenario_id=bundle.scenario_id,
            run_id=bundle.run_id,
            process_group=first.dispatch_group,
            discovery_modules=list(bundle.discovery_modules),
            runtime=dict(bundle.runtime),
            adapters=dict(bundle.adapters or {}),
            config=dict(bundle.config) if isinstance(bundle.config, dict) else None,
            key_bundle=bundle.key_bundle,
        )
    )
    child = bootstrap_child_runtime_from_bundle(effective_bundle)
    return execute_child_boundary_loop_with_runtime(child=child, inputs=inputs)


def execute_child_boundary_loop_with_runtime(
    *,
    child: ChildRuntimeBootstrap,
    inputs: list[object],
    finalize: bool = True,
) -> list[Envelope]:
    # Execute boundary batch using an already-bootstrapped child runtime (stateful nodes preserved).
    normalized = [_normalize_child_boundary_input(item) for item in inputs]
    return execute_child_boundary_loop(child=child, inputs=normalized, finalize=finalize)


def execute_child_boundary_loop(
    *,
    child: ChildRuntimeBootstrap,
    inputs: list[ChildBoundaryInput],
    finalize: bool = True,
) -> list[Envelope]:
    # Child runtime consume->execute->emit loop for boundary-dispatched workload.
    # Phase C unification: route boundary items through runner engine semantics.
    grouped_nodes = _select_group_planning_steps(
        scenario_steps=dict(child.scenario_steps),
        runtime=child.runtime,
        process_group=child.process_group,
    )
    all_nodes = dict(child.scenario_steps)
    has_group_mapping = _runtime_process_group_is_declared(
        runtime=child.runtime,
        process_group=child.process_group,
    )
    context_service = _resolve_context_service(child.scenario_scope)
    observability = _resolve_observability_service(child.scenario_scope)
    router = _resolve_routing_service(child.scenario_scope)
    work_queue = InMemoryQueue()
    emitted: list[Envelope] = []
    envelope_observability_meta: dict[int, dict[str, object]] = {}
    trace_observability_meta: dict[str, dict[str, object]] = {}
    accepted = 0
    accepted_targets: set[str] = set()

    try:
        for item in inputs:
            if child.process_group is not None and item.dispatch_group != child.process_group:
                continue
            candidate_nodes = grouped_nodes if has_group_mapping else all_nodes
            if item.target not in candidate_nodes:
                raise ChildRuntimeBootstrapError(
                    f"child boundary target '{item.target}' is not discovered in child runtime"
                )
            accepted += 1
            accepted_targets.add(item.target)
            envelope = Envelope(
                payload=item.payload,
                target=item.target,
                trace_id=item.trace_id,
                reply_to=item.reply_to,
                span_id=item.span_id,
            )
            envelope_meta: dict[str, object] = {"__process_group": item.dispatch_group}
            if isinstance(item.source_group, str) and item.source_group:
                envelope_meta["__handoff_from"] = item.source_group
            if item.route_hop is not None:
                envelope_meta["__route_hop"] = item.route_hop
            if isinstance(envelope.trace_id, str) and envelope.trace_id:
                trace_observability_meta[envelope.trace_id] = dict(envelope_meta)
            envelope_observability_meta[id(envelope)] = envelope_meta
            work_queue.push(envelope)

        if accepted == 0:
            return emitted

        def _enrich_observability_ctx(
            envelope: Envelope,
            _ctx: dict[str, object],
        ) -> dict[str, object] | None:
            merged: dict[str, object] = {}
            if isinstance(envelope.trace_id, str) and envelope.trace_id:
                persistent = trace_observability_meta.get(envelope.trace_id)
                if isinstance(persistent, dict):
                    merged.update(persistent)
            envelope_once = envelope_observability_meta.pop(id(envelope), None)
            if isinstance(envelope_once, dict):
                merged.update(envelope_once)
            if "__process_group" not in merged and isinstance(child.process_group, str) and child.process_group:
                merged["__process_group"] = child.process_group
            return merged or None

        if has_group_mapping:
            nodes = grouped_nodes or all_nodes
        else:
            # Compatibility mode: when process-group mapping is not configured in runtime,
            # boundary execution keeps one-hop semantics by executing only explicit targets.
            nodes = {name: all_nodes[name] for name in accepted_targets}
            # Keep framework observability rails available even in one-hop mode.
            for node_name, step in all_nodes.items():
                if node_name.startswith("system.obs."):
                    nodes[node_name] = step

        full_context_nodes = {
            node_name
            for node_name in child.full_context_nodes
            if node_name in nodes
        }
        runner_kwargs = {
            "nodes": nodes,
            "work_queue": work_queue,
            "router": router,
            "context_service": context_service,
            "observability": observability,
            "full_context_nodes": full_context_nodes,
            "allow_external_deliveries": True,
            "boundary_outputs": emitted,
            "observability_context_enricher": _enrich_observability_ctx,
        }
        use_async_runner = (
            child.runner_profile_effective == "async"
            or any(
                child.runner_profile_nodes.get(node_name) == "async"
                for node_name in nodes
            )
            or any(
                inspect.iscoroutinefunction(step)
                or inspect.iscoroutinefunction(getattr(step, "__call__", None))
                for step in nodes.values()
            )
        )
        if use_async_runner:
            runner = AsyncRunner(**runner_kwargs)
        else:
            runner = SyncRunner(**runner_kwargs)
        runner.run()
    except ChildRuntimeBootstrapError:
        raise
    except Exception as exc:  # noqa: BLE001 - deterministic child-boundary category.
        detail = f"{type(exc).__name__}: {exc}"
        raise ChildRuntimeBootstrapError(
            f"child boundary step failed: {detail}"
        ) from exc
    finally:
        try:
            if "runner" in locals() and finalize:
                runner.on_run_end()
            elif finalize:
                observability.on_run_end()
        except Exception:  # noqa: BLE001 - must not hide primary execution errors.
            pass

    return emitted


def _runtime_process_group_is_declared(
    *,
    runtime: dict[str, object],
    process_group: str | None,
) -> bool:
    if not isinstance(process_group, str) or not process_group:
        return False
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return False
    groups = platform.get("process_groups", [])
    if not isinstance(groups, list):
        return False
    for group in groups:
        if not isinstance(group, dict):
            continue
        if group.get("name") == process_group:
            return True
    return False


def _resolve_context_service(scope: ScenarioScope) -> ContextService:
    try:
        context_service_obj = scope.resolve("service", ContextService)
    except InjectionRegistryError as exc:
        raise ChildRuntimeBootstrapError(
            "child bootstrap cannot resolve ContextService from DI"
        ) from exc
    if isinstance(context_service_obj, ContextService):
        return context_service_obj
    if callable(getattr(context_service_obj, "metadata", None)):
        return context_service_obj  # type: ignore[return-value]
    raise ChildRuntimeBootstrapError(
        "child bootstrap resolved service does not match ContextService contract"
    )


def _resolve_observability_service(scope: ScenarioScope) -> ObservabilityService:
    try:
        observability_obj = scope.resolve("service", ObservabilityService)
    except InjectionRegistryError:
        return NoOpObservabilityService()
    if isinstance(observability_obj, ObservabilityService):
        return observability_obj
    if (
        callable(getattr(observability_obj, "before_node", None))
        and callable(getattr(observability_obj, "after_node", None))
        and callable(getattr(observability_obj, "on_node_error", None))
        and callable(getattr(observability_obj, "on_run_end", None))
    ):
        return observability_obj  # type: ignore[return-value]
    return NoOpObservabilityService()


def _resolve_routing_service(scope: ScenarioScope) -> RoutingService:
    try:
        routing_obj = scope.resolve("service", RoutingService)
    except InjectionRegistryError as exc:
        raise ChildRuntimeBootstrapError(
            "child bootstrap cannot resolve RoutingService from DI"
        ) from exc
    if isinstance(routing_obj, RoutingService):
        return routing_obj
    if callable(getattr(routing_obj, "route", None)):
        return routing_obj  # type: ignore[return-value]
    raise ChildRuntimeBootstrapError(
        "child bootstrap resolved service does not match RoutingService contract"
    )


def _normalize_child_boundary_input(item: object) -> ChildBoundaryInput:
    dispatch_group = getattr(item, "dispatch_group", None)
    if not isinstance(dispatch_group, str) or not dispatch_group:
        raise ChildRuntimeBootstrapError("child boundary input dispatch_group must be a non-empty string")
    target = getattr(item, "target", None)
    if not isinstance(target, str) or not target:
        raise ChildRuntimeBootstrapError("child boundary input target must be a non-empty string")
    trace_id = getattr(item, "trace_id", None)
    if trace_id is not None and (not isinstance(trace_id, str) or not trace_id):
        raise ChildRuntimeBootstrapError("child boundary input trace_id must be null or non-empty string")
    reply_to = getattr(item, "reply_to", None)
    if reply_to is not None and (not isinstance(reply_to, str) or not reply_to):
        raise ChildRuntimeBootstrapError("child boundary input reply_to must be null or non-empty string")
    payload = getattr(item, "payload", None)
    source_group = getattr(item, "source_group", None)
    if source_group is not None and (not isinstance(source_group, str) or not source_group):
        raise ChildRuntimeBootstrapError(
            "child boundary input source_group must be null or non-empty string"
        )
    route_hop = getattr(item, "route_hop", None)
    if route_hop is not None and (not isinstance(route_hop, int) or route_hop < 0):
        raise ChildRuntimeBootstrapError(
            "child boundary input route_hop must be null or non-negative int"
        )
    span_id = getattr(item, "span_id", None)
    if span_id is not None and (not isinstance(span_id, str) or not span_id):
        raise ChildRuntimeBootstrapError("child boundary input span_id must be null or non-empty string")
    return ChildBoundaryInput(
        payload=payload,
        dispatch_group=dispatch_group,
        target=target,
        trace_id=trace_id,
        reply_to=reply_to,
        source_group=source_group,
        route_hop=route_hop,
        span_id=span_id,
    )


def _register_adapter_bindings(
    *,
    injection_registry: InjectionRegistry,
    instances: dict[str, object],
    bindings: dict[str, object],
    adapter_async_roles: set[str] | None = None,
) -> None:
    async_roles = set(adapter_async_roles or ())
    for role, binding in bindings.items():
        if role not in instances:
            raise ChildRuntimeBootstrapError(f"Missing adapter instance for role: {role}")
        adapter = instances[role]
        is_async = role in async_roles
        if isinstance(binding, list):
            for port_type, data_type in binding:
                injection_registry.register_factory(
                    port_type,
                    data_type,
                    lambda _a=adapter: _a,
                    is_async=is_async,
                )
            continue
        port_type, data_type = binding
        injection_registry.register_factory(
            port_type,
            data_type,
            lambda _a=adapter: _a,
            is_async=is_async,
        )


def _classify_async_bindings(
    registry: InjectionRegistry,
) -> tuple[list[str], list[str]]:
    service_contracts: set[str] = set()
    adapter_bindings: set[str] = set()
    for port_type, data_type, qualifier in registry.list_async_bindings():
        contract_name = getattr(data_type, "__name__", str(data_type))
        if port_type == "service":
            service_contracts.add(contract_name)
            continue
        suffix = f"#{qualifier}" if isinstance(qualifier, str) and qualifier else ""
        adapter_bindings.add(f"{port_type}<{contract_name}>{suffix}")
    return (sorted(service_contracts), sorted(adapter_bindings))


def _collect_observability_exporter_summary(
    *,
    runtime: dict[str, object],
    adapter_instances: dict[str, object],
) -> list[str]:
    observability = runtime.get("observability", {})
    if not isinstance(observability, dict):
        return []
    summary: list[str] = []
    tracing = observability.get("tracing", {})
    if isinstance(tracing, dict):
        exporters = tracing.get("exporters", [])
        if isinstance(exporters, list):
            alias_map = {
                "jsonl": "trace_jsonl",
                "stdout": "trace_stdout",
                "otel_otlp": "trace_otel_otlp",
                "otel_otlp_logical": "trace_otel_otlp",
                "otel_otlp_topology": "trace_otel_otlp",
                "opentracing_bridge": "trace_opentracing_bridge",
            }
            for index, exporter in enumerate(exporters):
                if not isinstance(exporter, dict):
                    continue
                if exporter.get("enabled") is False:
                    continue
                kind = exporter.get("kind")
                if not isinstance(kind, str) or not kind:
                    continue
                settings = exporter.get("settings", {})
                if not isinstance(settings, dict):
                    settings = {}
                transport = settings.get("transport")
                if not isinstance(transport, dict):
                    transport = {}
                backend = transport.get("backend", settings.get("backend", "-"))
                httpx = transport.get("httpx")
                if not isinstance(httpx, dict):
                    httpx = settings.get("httpx", {})
                if not isinstance(httpx, dict):
                    httpx = {}
                mode = httpx.get("mode", "-")
                alias = alias_map.get(kind)
                candidate = adapter_instances.get(f"{alias}#{index}") if isinstance(alias, str) else None
                if candidate is None:
                    candidate = adapter_instances.get(alias) if isinstance(alias, str) else None
                supports_emit_async = callable(getattr(candidate, "emit_async", None))
                summary.append(
                    "tracing[{index}] kind={kind} backend={backend} httpx_mode={mode} emit_async={emit_async}".format(
                        index=index,
                        kind=kind,
                        backend=backend if isinstance(backend, str) and backend else "-",
                        mode=mode if isinstance(mode, str) and mode else "-",
                        emit_async="yes" if supports_emit_async else "no",
                    )
                )
    return summary


def _select_group_planning_steps(
    *,
    scenario_steps: dict[str, Any],
    runtime: dict[str, object],
    process_group: str | None,
) -> dict[str, object]:
    # Runner profile diagnostics should reflect current worker group, not full topology.
    if not isinstance(process_group, str) or not process_group:
        return dict(scenario_steps)
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return dict(scenario_steps)
    groups = platform.get("process_groups", [])
    if not isinstance(groups, list):
        return dict(scenario_steps)

    selected_nodes: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        name = group.get("name")
        if name != process_group:
            continue
        nodes = group.get("nodes", [])
        if not isinstance(nodes, list):
            continue
        for node_name in nodes:
            if isinstance(node_name, str) and node_name:
                selected_nodes.add(node_name)
        break
    if not selected_nodes:
        return dict(scenario_steps)
    # System observability nodes are framework-internal rails and must be available
    # in each worker group so trace/log/metric dispatch does not leak as remote handoff.
    for node_name in scenario_steps:
        if node_name.startswith("system.obs."):
            selected_nodes.add(node_name)
    return {
        name: step
        for name, step in scenario_steps.items()
        if name in selected_nodes
    }
