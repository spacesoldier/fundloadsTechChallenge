from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Any

from stream_kernel.application_context.application_context import ApplicationContext
from stream_kernel.application_context.injection_registry import (
    InjectionRegistry,
    InjectionRegistryError,
    ScenarioScope,
)
from stream_kernel.execution.transport.bootstrap_keys import BootstrapKeyBundle
from stream_kernel.platform.services.context import ContextService
from stream_kernel.platform.services.lifecycle import RuntimeLifecycleManager
from stream_kernel.platform.services.observability import (
    NoOpObservabilityService,
    ObservabilityService,
)
from stream_kernel.platform.services.transport import RuntimeTransportService
from stream_kernel.routing.envelope import Envelope


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


def build_child_bootstrap_bundle(
    *,
    scenario_id: str,
    process_group: str | None,
    discovery_modules: list[str],
    runtime: dict[str, object],
    key_bundle: BootstrapKeyBundle,
) -> ChildBootstrapBundle:
    return ChildBootstrapBundle(
        scenario_id=scenario_id,
        process_group=process_group,
        discovery_modules=list(discovery_modules),
        runtime=dict(runtime),
        key_bundle=key_bundle,
    )


def bootstrap_child_runtime_from_bundle(bundle: ChildBootstrapBundle) -> ChildRuntimeBootstrap:
    # Build child runtime DI/discovery from metadata bundle only (no serialized object graphs).
    from stream_kernel.execution.orchestration import builder as execution_builder

    if not isinstance(bundle, ChildBootstrapBundle):
        raise ChildRuntimeBootstrapError("child bootstrap bundle must be ChildBootstrapBundle")
    if not isinstance(bundle.scenario_id, str) or not bundle.scenario_id:
        raise ChildRuntimeBootstrapError("child bootstrap bundle.scenario_id must be a non-empty string")
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
    if not isinstance(bundle.key_bundle, BootstrapKeyBundle):
        raise ChildRuntimeBootstrapError("child bootstrap bundle.key_bundle must be BootstrapKeyBundle")

    discovery_modules = list(bundle.discovery_modules)
    execution_builder.ensure_platform_discovery_modules(discovery_modules)
    modules = execution_builder.load_discovery_modules(discovery_modules)
    app_context = ApplicationContext()
    app_context.discover(modules)
    consumer_registry = app_context.build_consumer_registry()

    injection_registry = InjectionRegistry()
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
    scenario_scope = injection_registry.instantiate_for_scenario(bundle.scenario_id)
    step_names = [node_def.meta.name for node_def in app_context.nodes]
    scenario = app_context.build_scenario(
        scenario_id=bundle.scenario_id,
        step_names=step_names,
        wiring={
            "injection_registry": injection_registry,
            "scenario_scope": scenario_scope,
            "strict": True,
        },
    )
    scenario_steps = {spec.name: spec.step for spec in scenario.steps}
    full_context_nodes = {node_def.meta.name for node_def in app_context.nodes if node_def.meta.service}

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
            process_group=first.dispatch_group,
            discovery_modules=list(bundle.discovery_modules),
            runtime=dict(bundle.runtime),
            key_bundle=bundle.key_bundle,
        )
    )
    child = bootstrap_child_runtime_from_bundle(effective_bundle)
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
            node_ctx = dict(raw_ctx)
            observer_state = observability.before_node(
                node_name=node_name,
                payload=item.payload,
                ctx=raw_ctx,
                trace_id=item.trace_id,
            )
            try:
                outputs = list(step(item.payload, node_ctx))
            except Exception as exc:  # noqa: BLE001 - deterministic child boundary category
                observability.on_node_error(
                    node_name=node_name,
                    payload=item.payload,
                    ctx=raw_ctx,
                    trace_id=item.trace_id,
                    error=exc,
                    state=observer_state,
                )
                raise ChildRuntimeBootstrapError(
                    f"child boundary step failed for target '{item.target}'"
                ) from exc
            observability.after_node(
                node_name=node_name,
                payload=item.payload,
                ctx=raw_ctx,
                trace_id=item.trace_id,
                outputs=outputs,
                state=observer_state,
            )
            for output in outputs:
                if isinstance(output, Envelope):
                    emitted.append(
                        Envelope(
                            payload=output.payload,
                            target=output.target,
                            trace_id=output.trace_id or item.trace_id,
                            reply_to=output.reply_to or item.reply_to,
                        )
                    )
                    continue
                emitted.append(
                    Envelope(
                        payload=output,
                        trace_id=item.trace_id,
                        reply_to=item.reply_to,
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
    return ChildBoundaryInput(
        payload=payload,
        dispatch_group=dispatch_group,
        target=target,
        trace_id=trace_id,
        reply_to=reply_to,
    )
