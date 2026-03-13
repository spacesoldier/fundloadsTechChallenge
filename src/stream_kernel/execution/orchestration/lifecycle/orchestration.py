from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from stream_kernel.application_context.injection_registry import ScenarioScope
from stream_kernel.platform.services.observability import (
    ObservabilityService,
    coerce_pipeline_observability,
)
from stream_kernel.platform.services.runtime.lifecycle import RuntimeLifecycleManager


class RuntimeExecutionError(RuntimeError):
    # Base runtime execution error for lifecycle/worker orchestration path.
    pass


class RuntimeLifecycleResolutionError(RuntimeExecutionError):
    # Raised when lifecycle service contract cannot be resolved from DI.
    pass


class RuntimeLifecycleReadyError(RuntimeExecutionError):
    # Raised when lifecycle reports not-ready before runner start.
    pass


class RuntimeWorkerFailedError(RuntimeExecutionError):
    # Raised when runner execution fails under lifecycle-managed profile.
    pass


@dataclass(frozen=True, slots=True)
class BoundaryDispatchInput:
    # Cross-group boundary input contract for process-supervisor handoff.
    payload: object
    dispatch_group: str
    target: str | None = None
    trace_id: str | None = None
    reply_to: str | None = None
    source_group: str | None = None
    route_hop: int | None = None
    span_id: str | None = None
    tombstone: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeLifecyclePolicy:
    ready_timeout_seconds: int
    graceful_timeout_seconds: int
    drain_inflight: bool


def resolve_runtime_lifecycle_manager(scope: ScenarioScope) -> RuntimeLifecycleManager:
    # Resolve lifecycle manager contract from scenario DI scope.
    try:
        candidate = scope.resolve("service", RuntimeLifecycleManager)
    except Exception as exc:  # noqa: BLE001 - convert to deterministic runtime category
        raise RuntimeLifecycleResolutionError(
            "runtime.platform.execution_ipc local transport ('tcp_local' or 'ipc_local') requires "
            "a registered RuntimeLifecycleManager service"
        ) from exc
    if isinstance(candidate, RuntimeLifecycleManager):
        return candidate
    if (
        callable(getattr(candidate, "start", None))
        and callable(getattr(candidate, "ready", None))
        and callable(getattr(candidate, "stop", None))
    ):
        return candidate  # type: ignore[return-value]
    raise RuntimeLifecycleResolutionError(
        "Resolved runtime lifecycle service does not match RuntimeLifecycleManager contract"
    )


def runtime_lifecycle_policy(runtime: dict[str, object]) -> RuntimeLifecyclePolicy:
    # Read startup/shutdown policy from runtime config.
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        raise ValueError("runtime.platform must be a mapping")
    lifecycle = platform.get("lifecycle", {})
    if lifecycle is None:
        lifecycle = {}
    if not isinstance(lifecycle, dict):
        raise ValueError("runtime.platform.lifecycle must be a mapping")

    ready_timeout_default = 5
    readiness = platform.get("readiness", {})
    if isinstance(readiness, dict):
        readiness_timeout = readiness.get("readiness_timeout_seconds")
        if isinstance(readiness_timeout, int) and readiness_timeout > 0:
            ready_timeout_default = readiness_timeout

    ready_timeout_seconds = lifecycle.get("ready_timeout_seconds", ready_timeout_default)
    if not isinstance(ready_timeout_seconds, int) or ready_timeout_seconds <= 0:
        raise ValueError("runtime.platform.lifecycle.ready_timeout_seconds must be > 0")

    graceful_timeout_seconds = lifecycle.get("graceful_timeout_seconds", 10)
    if not isinstance(graceful_timeout_seconds, int) or graceful_timeout_seconds <= 0:
        raise ValueError("runtime.platform.lifecycle.graceful_timeout_seconds must be > 0")

    drain_inflight = lifecycle.get("drain_inflight", True)
    if not isinstance(drain_inflight, bool):
        raise ValueError("runtime.platform.lifecycle.drain_inflight must be a boolean")

    return RuntimeLifecyclePolicy(
        ready_timeout_seconds=ready_timeout_seconds,
        graceful_timeout_seconds=graceful_timeout_seconds,
        drain_inflight=drain_inflight,
    )


def runtime_bootstrap_mode(runtime: dict[str, object]) -> str:
    # Read bootstrap mode with inline fallback for backward-compatible profiles.
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        raise ValueError("runtime.platform must be a mapping")
    bootstrap = platform.get("bootstrap", {})
    if bootstrap is None:
        bootstrap = {}
    if not isinstance(bootstrap, dict):
        raise ValueError("runtime.platform.bootstrap must be a mapping")
    mode_raw = bootstrap.get("mode")
    if mode_raw is None:
        process_groups = platform.get("process_groups", [])
        mode = (
            "process_supervisor"
            if isinstance(process_groups, list) and len(process_groups) > 0
            else "inline"
        )
    else:
        mode = mode_raw
    if not isinstance(mode, str) or not mode:
        raise ValueError("runtime.platform.bootstrap.mode must be a non-empty string")
    return mode


def runtime_process_group_names(runtime: dict[str, object]) -> list[str]:
    # Preserve process-group order declared in runtime config for deterministic startup semantics.
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        raise ValueError("runtime.platform must be a mapping")
    groups = platform.get("process_groups", [])
    if groups is None:
        groups = []
    if not isinstance(groups, list):
        raise ValueError("runtime.platform.process_groups must be a list")
    names: list[str] = []
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            raise ValueError(f"runtime.platform.process_groups[{index}] must be a mapping")
        name = group.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"runtime.platform.process_groups[{index}].name must be a non-empty string")
        names.append(name)
    return names


def _build_boundary_dispatch_inputs(
    *,
    runtime: dict[str, object],
    inputs: list[object],
) -> tuple[list[BoundaryDispatchInput], str | None, dict[str, str]]:
    # Build per-target boundary dispatch records using explicit process-group placement.
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        raise ValueError("runtime.platform must be a mapping")
    groups = platform.get("process_groups", [])
    if not isinstance(groups, list):
        raise ValueError("runtime.platform.process_groups must be a list")

    placement: dict[str, str] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_name = group.get("name")
        nodes = group.get("nodes", [])
        if not isinstance(group_name, str) or not group_name:
            continue
        if not isinstance(nodes, list):
            continue
        for node_name in nodes:
            if isinstance(node_name, str) and node_name:
                placement[node_name] = group_name

    boundary_inputs: list[BoundaryDispatchInput] = []
    dispatch_groups_seen: set[str] = set()
    for item in inputs:
        target = item.get("target") if isinstance(item, dict) else getattr(item, "target", None)
        if not isinstance(target, str) or not target:
            raise ValueError("Boundary handoff target must be a non-empty string")
        dispatch_group = placement.get(target)
        if not isinstance(dispatch_group, str) or not dispatch_group:
            raise ValueError(f"Missing process-group placement for target '{target}'")
        payload = item.get("payload") if isinstance(item, dict) else getattr(item, "payload", None)
        trace_id = item.get("trace_id") if isinstance(item, dict) else getattr(item, "trace_id", None)
        reply_to = item.get("reply_to") if isinstance(item, dict) else getattr(item, "reply_to", None)
        source_group = item.get("source_group") if isinstance(item, dict) else getattr(item, "source_group", None)
        route_hop = item.get("route_hop") if isinstance(item, dict) else getattr(item, "route_hop", None)
        span_id = item.get("span_id") if isinstance(item, dict) else getattr(item, "span_id", None)
        tombstone = item.get("tombstone") if isinstance(item, dict) else getattr(item, "tombstone", None)
        boundary_inputs.append(
            BoundaryDispatchInput(
                payload=payload,
                dispatch_group=dispatch_group,
                target=target,
                trace_id=trace_id if isinstance(trace_id, str) and trace_id else None,
                reply_to=reply_to if isinstance(reply_to, str) and reply_to else None,
                source_group=source_group if isinstance(source_group, str) and source_group else None,
                route_hop=route_hop if isinstance(route_hop, int) and route_hop >= 0 else None,
                span_id=span_id if isinstance(span_id, str) and span_id else None,
                tombstone=bool(tombstone) if isinstance(tombstone, bool) else False,
            )
        )
        dispatch_groups_seen.add(dispatch_group)

    dispatch_group_single = next(iter(dispatch_groups_seen)) if len(dispatch_groups_seen) == 1 else None
    aliases: dict[str, str] = {}
    return (boundary_inputs, dispatch_group_single, aliases)


def execute_with_runtime_lifecycle(
    *,
    runtime: dict[str, object],
    scenario_scope: ScenarioScope,
    run: Callable[[], None],
) -> None:
    # Execute runner call under lifecycle manager semantics.
    lifecycle = resolve_runtime_lifecycle_manager(scenario_scope)
    _configure_runtime_lifecycle_shutdown_policy(lifecycle=lifecycle, runtime=runtime)
    observability = _resolve_pipeline_observability(scenario_scope)
    policy = runtime_lifecycle_policy(runtime)
    started = False
    try:
        _emit_runtime_lifecycle_event(
            observability=observability,
            event="runtime_lifecycle_starting",
            process_group="local",
            details={"mode": "runtime_lifecycle"},
        )
        lifecycle.start()
        started = True
        if not lifecycle.ready(policy.ready_timeout_seconds):
            raise RuntimeLifecycleReadyError("execution lifecycle ready check failed")
        _emit_runtime_lifecycle_event(
            observability=observability,
            event="runtime_lifecycle_ready",
            process_group="local",
            details={"ready_timeout_seconds": policy.ready_timeout_seconds},
        )
        try:
            _emit_runtime_lifecycle_event(
                observability=observability,
                event="runtime_run_started",
                process_group="local",
                details=None,
            )
            run()
            _emit_runtime_lifecycle_event(
                observability=observability,
                event="runtime_run_completed",
                process_group="local",
                details=None,
            )
        except Exception as exc:
            _emit_runtime_lifecycle_event(
                observability=observability,
                event="runtime_run_failed",
                process_group="local",
                details={"error_type": type(exc).__name__},
            )
            raise RuntimeWorkerFailedError("execution worker failed") from exc
    finally:
        if started:
            lifecycle.stop(
                graceful_timeout_seconds=policy.graceful_timeout_seconds,
                drain_inflight=policy.drain_inflight,
            )
            _emit_runtime_lifecycle_event(
                observability=observability,
                event="runtime_lifecycle_stopped",
                process_group="local",
                details={
                    "graceful_timeout_seconds": policy.graceful_timeout_seconds,
                    "drain_inflight": policy.drain_inflight,
                },
            )



def _resolve_pipeline_observability(scope: ScenarioScope):
    try:
        resolved = scope.resolve("service", ObservabilityService)
    except Exception:  # noqa: BLE001 - observability is optional in this orchestration path.
        resolved = None
    return coerce_pipeline_observability(resolved)


def _emit_runtime_lifecycle_event(
    *,
    observability: object,
    event: str,
    process_group: str | None,
    details: dict[str, object] | None,
) -> None:
    try:
        coerce_pipeline_observability(observability).on_runtime_lifecycle_event(
            event=event,
            process_group=process_group,
            details=dict(details or {}),
        )
    except Exception:
        # Observability callbacks must never break runtime lifecycle orchestration.
        return


def _configure_runtime_lifecycle_shutdown_policy(
    *,
    lifecycle: RuntimeLifecycleManager,
    runtime: dict[str, object],
) -> None:
    configure = getattr(lifecycle, "configure_shutdown_policy", None)
    if not callable(configure):
        return
    lifecycle_cfg = _runtime_lifecycle_settings(runtime)
    base_stop_timeout_seconds = lifecycle_cfg.get("stop_command_timeout_seconds")
    base_stop_timeout_value = (
        float(base_stop_timeout_seconds)
        if isinstance(base_stop_timeout_seconds, (int, float))
        and float(base_stop_timeout_seconds) >= 0
        else None
    )
    group_name, stop_timeout_seconds = _resolve_observability_shutdown_settings(
        runtime,
        default_timeout_seconds=None,
    )
    fallback_graceful_timeout_seconds = lifecycle_cfg.get("fallback_graceful_timeout_seconds")
    try:
        configure(
            observability_group_name=group_name,
            observability_stop_command_timeout_seconds=stop_timeout_seconds,
            stop_command_timeout_seconds=(
                base_stop_timeout_value
            ),
            fallback_graceful_timeout_seconds=(
                float(fallback_graceful_timeout_seconds)
                if isinstance(fallback_graceful_timeout_seconds, (int, float))
                and float(fallback_graceful_timeout_seconds) > 0
                else None
            ),
        )
    except TypeError:
        try:
            configure(
                observability_group_name=group_name,
                observability_stop_command_timeout_seconds=stop_timeout_seconds,
            )
        except Exception:
            return
    except Exception:
        return


def _resolve_observability_shutdown_settings(
    runtime: dict[str, object],
    *,
    default_timeout_seconds: float | None = None,
) -> tuple[str, float]:
    default_group = "system.observability"
    resolved_default_timeout_seconds = (
        float(default_timeout_seconds)
        if isinstance(default_timeout_seconds, (int, float)) and float(default_timeout_seconds) >= 0
        else 0.0
    )
    observability = runtime.get("observability", {})
    if not isinstance(observability, dict):
        return (default_group, resolved_default_timeout_seconds)
    service_process = observability.get("service_process")
    if not isinstance(service_process, dict):
        service_process = observability.get("service_worker")
    if not isinstance(service_process, dict):
        return (default_group, resolved_default_timeout_seconds)
    group_name = service_process.get("group_name")
    if not isinstance(group_name, str) or not group_name:
        group_name = default_group
    stop_command_timeout_seconds = service_process.get("stop_command_timeout_seconds")
    if isinstance(stop_command_timeout_seconds, (int, float)) and float(stop_command_timeout_seconds) >= 0:
        return (group_name, float(stop_command_timeout_seconds))
    return (group_name, resolved_default_timeout_seconds)


def _runtime_lifecycle_settings(runtime: dict[str, object]) -> dict[str, object]:
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return {}
    lifecycle = platform.get("lifecycle", {})
    if not isinstance(lifecycle, dict):
        return {}
    return dict(lifecycle)
