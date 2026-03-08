from __future__ import annotations

import inspect

from stream_kernel.application_context.injection_registry import (
    InjectionRegistryError,
    ScenarioScope,
)
from stream_kernel.execution.runtime.runner import AsyncRunner, SyncRunner
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.platform.services.observability import (
    NoOpObservabilityService,
    ObservabilityService,
)
from stream_kernel.platform.services.state.context import ContextService
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.routing_service import RoutingService

from ..debug_logging import leaf_debug_log
from ..startup.bootstrap_models import (
    ChildBoundaryInput,
    ChildBootstrapBundle,
    ChildRuntimeBootstrap,
    ChildRuntimeBootstrapError,
)
from ..startup.group_step_selection import select_group_planning_steps


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
    from ..startup.runtime_bootstrap_service import DefaultLeafRuntimeBootstrapService

    child = DefaultLeafRuntimeBootstrapService().bootstrap_runtime(bundle=effective_bundle)
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
    grouped_nodes = select_group_planning_steps(
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
    leaf_debug_log(
        event="leaf.boundary_runtime.execute.started",
        process_group=child.process_group,
        input_count=len(inputs),
        finalize=finalize,
    )

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
                tombstone=item.tombstone,
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
            leaf_debug_log(
                event="leaf.boundary_runtime.execute.no_accepted_inputs",
                process_group=child.process_group,
            )
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
                if node_name.startswith("system.debug."):
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
            leaf_debug_log(
                event="leaf.boundary_runtime.runner.selected",
                process_group=child.process_group,
                runner_type="async",
                node_count=len(nodes),
            )
        else:
            runner = SyncRunner(**runner_kwargs)
            leaf_debug_log(
                event="leaf.boundary_runtime.runner.selected",
                process_group=child.process_group,
                runner_type="sync",
                node_count=len(nodes),
            )
        runner.run()
        leaf_debug_log(
            event="leaf.boundary_runtime.execute.completed",
            process_group=child.process_group,
            accepted_inputs=accepted,
            emitted_count=len(emitted),
        )
    except ChildRuntimeBootstrapError:
        leaf_debug_log(
            event="leaf.boundary_runtime.execute.child_bootstrap_error",
            process_group=child.process_group,
        )
        raise
    except Exception as exc:  # noqa: BLE001 - deterministic child-boundary category.
        leaf_debug_log(
            event="leaf.boundary_runtime.execute.failed",
            process_group=child.process_group,
            error=exc.__class__.__name__,
        )
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
        leaf_debug_log(
            event="leaf.boundary_runtime.execute.finalized",
            process_group=child.process_group,
            finalize=finalize,
        )

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
    if isinstance(item, dict):
        dispatch_group = item.get("dispatch_group")
        target = item.get("target")
        trace_id = item.get("trace_id")
        reply_to = item.get("reply_to")
        payload = item.get("payload")
        source_group = item.get("source_group")
        route_hop = item.get("route_hop")
        span_id = item.get("span_id")
        tombstone = item.get("tombstone")
    else:
        dispatch_group = getattr(item, "dispatch_group", None)
        target = getattr(item, "target", None)
        trace_id = getattr(item, "trace_id", None)
        reply_to = getattr(item, "reply_to", None)
        payload = getattr(item, "payload", None)
        source_group = getattr(item, "source_group", None)
        route_hop = getattr(item, "route_hop", None)
        span_id = getattr(item, "span_id", None)
        tombstone = getattr(item, "tombstone", None)

    if not isinstance(dispatch_group, str) or not dispatch_group:
        raise ChildRuntimeBootstrapError("child boundary input dispatch_group must be a non-empty string")
    if not isinstance(target, str) or not target:
        raise ChildRuntimeBootstrapError("child boundary input target must be a non-empty string")
    if trace_id is not None and (not isinstance(trace_id, str) or not trace_id):
        raise ChildRuntimeBootstrapError("child boundary input trace_id must be null or non-empty string")
    if reply_to is not None and (not isinstance(reply_to, str) or not reply_to):
        raise ChildRuntimeBootstrapError("child boundary input reply_to must be null or non-empty string")
    if source_group is not None and (not isinstance(source_group, str) or not source_group):
        raise ChildRuntimeBootstrapError(
            "child boundary input source_group must be null or non-empty string"
        )
    if route_hop is not None and (not isinstance(route_hop, int) or route_hop < 0):
        raise ChildRuntimeBootstrapError(
            "child boundary input route_hop must be null or non-negative int"
        )
    if span_id is not None and (not isinstance(span_id, str) or not span_id):
        raise ChildRuntimeBootstrapError("child boundary input span_id must be null or non-empty string")
    if tombstone is not None and not isinstance(tombstone, bool):
        raise ChildRuntimeBootstrapError("child boundary input tombstone must be a boolean when provided")
    return ChildBoundaryInput(
        payload=payload,
        dispatch_group=dispatch_group,
        target=target,
        trace_id=trace_id,
        reply_to=reply_to,
        source_group=source_group,
        route_hop=route_hop,
        span_id=span_id,
        tombstone=bool(tombstone) if isinstance(tombstone, bool) else False,
    )


__all__ = [
    "execute_child_boundary_loop_from_bundle",
    "execute_child_boundary_loop_with_runtime",
    "execute_child_boundary_loop",
]
