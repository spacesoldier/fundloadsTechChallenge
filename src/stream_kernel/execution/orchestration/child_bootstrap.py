from __future__ import annotations

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
from stream_kernel.routing.router import RoutingResult
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
    adapter_instances: dict[str, object] = {}
    adapter_registry = None
    adapter_bindings: dict[str, object] = {}
    if bundle_adapters:
        adapter_registry, adapter_bindings = execution_builder.resolve_runtime_adapters(
            adapters=bundle_adapters,
            discovery_modules=discovery_modules,
        )
        adapter_instances = execution_builder.build_adapter_instances_from_registry(
            bundle_adapters,
            adapter_registry,
        )
        _register_adapter_bindings(
            injection_registry=injection_registry,
            instances=adapter_instances,
            bindings=adapter_bindings,
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

    scenario_steps = {spec.name: spec.step for spec in combined_steps}
    full_context_nodes = {node_def.meta.name for node_def in app_context.nodes if node_def.meta.service} | source_step_names

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
) -> list[Envelope]:
    # Execute boundary batch using an already-bootstrapped child runtime (stateful nodes preserved).
    normalized = [_normalize_child_boundary_input(item) for item in inputs]
    return execute_child_boundary_loop(child=child, inputs=normalized)


def execute_child_boundary_loop(
    *,
    child: ChildRuntimeBootstrap,
    inputs: list[ChildBoundaryInput],
) -> list[Envelope]:
    # Child runtime consume->execute->emit loop for boundary-dispatched workload.
    nodes = dict(child.scenario_steps)
    context_service = _resolve_context_service(child.scenario_scope)
    observability = _resolve_observability_service(child.scenario_scope)
    router = _resolve_routing_service(child.scenario_scope)
    emitted: list[Envelope] = []

    try:
        for item in inputs:
            if child.process_group is not None and item.dispatch_group != child.process_group:
                continue
            node_name = item.target
            step = nodes.get(node_name)
            if step is None:
                raise ChildRuntimeBootstrapError(
                    f"child boundary target '{item.target}' is not discovered in child runtime"
                )
            full_ctx = context_service.metadata(item.trace_id, full=True)
            raw_ctx = (
                full_ctx
                if node_name in child.full_context_nodes
                else {key: value for key, value in full_ctx.items() if not key.startswith("__")}
            )
            observability_ctx = dict(raw_ctx)
            observability_ctx["__process_group"] = item.dispatch_group
            if isinstance(item.source_group, str) and item.source_group:
                observability_ctx["__handoff_from"] = item.source_group
            if item.route_hop is not None:
                observability_ctx["__route_hop"] = item.route_hop
            if isinstance(item.span_id, str) and item.span_id:
                observability_ctx["__parent_span_id"] = item.span_id
            node_ctx = dict(raw_ctx)
            observer_state = observability.before_node(
                node_name=node_name,
                payload=item.payload,
                ctx=observability_ctx,
                trace_id=item.trace_id,
            )
            try:
                outputs = list(step(item.payload, node_ctx))
            except Exception as exc:  # noqa: BLE001 - deterministic child boundary category
                observability.on_node_error(
                    node_name=node_name,
                    payload=item.payload,
                    ctx=observability_ctx,
                    trace_id=item.trace_id,
                    error=exc,
                    state=observer_state,
                )
                detail = f"{type(exc).__name__}: {exc}"
                raise ChildRuntimeBootstrapError(
                    f"child boundary step failed for target '{item.target}': {detail}"
                ) from exc
            observability.after_node(
                node_name=node_name,
                payload=item.payload,
                ctx=observability_ctx,
                trace_id=item.trace_id,
                outputs=outputs,
                state=observer_state,
            )
            produced_span_id = _span_id_from_observer_state(observer_state)
            for output in outputs:
                terminal = _terminal_event_from_output(output)
                if terminal is not None:
                    terminal_trace_id = output.trace_id if isinstance(output, Envelope) else None
                    terminal_span_id = output.span_id if isinstance(output, Envelope) else None
                    emitted.append(
                        Envelope(
                            payload=terminal,
                            trace_id=terminal_trace_id or item.trace_id,
                            reply_to=(output.reply_to if isinstance(output, Envelope) else None) or item.reply_to,
                            span_id=terminal_span_id or produced_span_id,
                        )
                    )
                    continue
                explicit_trace_id = output.trace_id if isinstance(output, Envelope) else None
                explicit_reply_to = output.reply_to if isinstance(output, Envelope) else None
                explicit_span_id = output.span_id if isinstance(output, Envelope) else None
                if isinstance(output, Envelope) and output.target is not None:
                    # Explicit cross-node target is already encoded in Envelope.
                    emitted.append(
                        Envelope(
                            payload=output.payload,
                            target=output.target,
                            trace_id=explicit_trace_id or item.trace_id,
                            reply_to=explicit_reply_to or item.reply_to,
                            span_id=explicit_span_id or produced_span_id,
                        )
                    )
                    continue
                try:
                    routing_result = router.route([output], source=node_name)
                except ValueError as exc:
                    # Compatibility path: plain outputs with no consumers are treated as terminal boundary outputs.
                    if "No consumers registered" not in str(exc):
                        raise
                    emitted.append(
                        Envelope(
                            payload=output.payload if isinstance(output, Envelope) else output,
                            trace_id=explicit_trace_id or item.trace_id,
                            reply_to=explicit_reply_to or item.reply_to,
                            span_id=explicit_span_id or produced_span_id,
                        )
                    )
                    continue
                for target_name, payload in _local_deliveries(routing_result):
                    emitted.append(
                        Envelope(
                            payload=payload,
                            target=target_name,
                            trace_id=explicit_trace_id or item.trace_id,
                            reply_to=explicit_reply_to or item.reply_to,
                            span_id=explicit_span_id or produced_span_id,
                        )
                    )
    finally:
        observability.on_run_end()

    return emitted


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


def _terminal_event_from_output(output: object) -> object | None:
    from stream_kernel.platform.services.messaging.reply_waiter import TerminalEvent

    if isinstance(output, TerminalEvent):
        return output
    if isinstance(output, Envelope) and isinstance(output.payload, TerminalEvent):
        return output.payload
    return None


def _local_deliveries(route_result: object) -> list[tuple[str, object]]:
    if not isinstance(route_result, RoutingResult):
        raise ChildRuntimeBootstrapError("RoutingService.route must return RoutingResult")
    return route_result.local_deliveries


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


def _span_id_from_observer_state(state: object) -> str | None:
    states = state if isinstance(state, list) else [state]
    for item in states:
        span = getattr(item, "span", None)
        span_id = getattr(span, "span_id", None)
        if isinstance(span_id, str) and span_id:
            return span_id
        candidate = getattr(item, "span_id", None)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _register_adapter_bindings(
    *,
    injection_registry: InjectionRegistry,
    instances: dict[str, object],
    bindings: dict[str, object],
) -> None:
    for role, binding in bindings.items():
        if role not in instances:
            raise ChildRuntimeBootstrapError(f"Missing adapter instance for role: {role}")
        adapter = instances[role]
        if isinstance(binding, list):
            for port_type, data_type in binding:
                injection_registry.register_factory(port_type, data_type, lambda _a=adapter: _a)
            continue
        port_type, data_type = binding
        injection_registry.register_factory(port_type, data_type, lambda _a=adapter: _a)
