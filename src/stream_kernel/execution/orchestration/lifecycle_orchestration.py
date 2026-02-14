from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from stream_kernel.application_context.injection_registry import ScenarioScope
from stream_kernel.execution.transport.bootstrap_keys import (
    OneShotBootstrapChannel,
    build_bootstrap_key_bundle,
)
from stream_kernel.execution.orchestration.child_bootstrap import build_child_bootstrap_bundle
from stream_kernel.platform.services.bootstrap import BootstrapSupervisor
from stream_kernel.platform.services.lifecycle import RuntimeLifecycleManager
from stream_kernel.platform.services.reply_coordinator import ReplyCoordinatorService
from stream_kernel.platform.services.reply_waiter import TerminalEvent
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.router import RoutingResult


class RuntimeExecutionError(RuntimeError):
    # Base runtime execution error for lifecycle/worker orchestration path.
    pass


class RuntimeLifecycleResolutionError(RuntimeExecutionError):
    # Raised when lifecycle service contract cannot be resolved from DI.
    pass


class RuntimeLifecycleReadyError(RuntimeExecutionError):
    # Raised when lifecycle reports not-ready before runner start.
    pass


class RuntimeBootstrapResolutionError(RuntimeExecutionError):
    # Raised when bootstrap supervisor cannot be resolved from DI.
    pass


class RuntimeBootstrapStartError(RuntimeExecutionError):
    # Raised when bootstrap supervisor fails to start configured process groups.
    pass


class RuntimeBootstrapStopTimeoutError(RuntimeExecutionError):
    # Raised when graceful stop timed out and no deterministic fallback is available.
    pass


class RuntimeBootstrapStopError(RuntimeExecutionError):
    # Raised when bootstrap supervisor stop/fallback execution fails.
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
            "runtime.platform.execution_ipc transport 'tcp_local' requires "
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

    ready_timeout_seconds = lifecycle.get("ready_timeout_seconds", 5)
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
    mode = bootstrap.get("mode", "inline")
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


def execute_with_runtime_lifecycle(
    *,
    runtime: dict[str, object],
    scenario_scope: ScenarioScope,
    run: Callable[[], None],
) -> None:
    # Execute runner call under lifecycle manager semantics.
    lifecycle = resolve_runtime_lifecycle_manager(scenario_scope)
    policy = runtime_lifecycle_policy(runtime)
    started = False
    try:
        lifecycle.start()
        started = True
        if not lifecycle.ready(policy.ready_timeout_seconds):
            raise RuntimeLifecycleReadyError("execution lifecycle ready check failed")
        try:
            run()
        except Exception as exc:
            raise RuntimeWorkerFailedError("execution worker failed") from exc
    finally:
        if started:
            lifecycle.stop(
                graceful_timeout_seconds=policy.graceful_timeout_seconds,
                drain_inflight=policy.drain_inflight,
            )


def resolve_bootstrap_supervisor(scope: ScenarioScope) -> BootstrapSupervisor:
    # Resolve bootstrap supervisor contract from scenario DI scope.
    try:
        candidate = scope.resolve("service", BootstrapSupervisor)
    except Exception as exc:  # noqa: BLE001 - convert to deterministic runtime category
        raise RuntimeBootstrapResolutionError(
            "runtime.platform.bootstrap.mode=process_supervisor requires "
            "a registered BootstrapSupervisor service"
        ) from exc
    if isinstance(candidate, BootstrapSupervisor):
        return candidate
    if (
        callable(getattr(candidate, "start_groups", None))
        and callable(getattr(candidate, "wait_ready", None))
        and callable(getattr(candidate, "stop_groups", None))
    ):
        return candidate  # type: ignore[return-value]
    raise RuntimeBootstrapResolutionError(
        "Resolved bootstrap supervisor service does not match BootstrapSupervisor contract"
    )


def execute_with_bootstrap_supervisor(
    *,
    config: dict[str, object],
    runtime: dict[str, object],
    scenario_id: str,
    run_id: str,
    inputs: list[object],
    scenario_scope: ScenarioScope,
    adapters: dict[str, object] | None = None,
    run: Callable[[], None],
) -> None:
    # Execute runner call under process-supervisor lifecycle semantics.
    supervisor = resolve_bootstrap_supervisor(scenario_scope)
    policy = runtime_lifecycle_policy(runtime)
    group_names = runtime_process_group_names(runtime)
    key_bundle = build_bootstrap_key_bundle(runtime)
    bootstrap_channel = OneShotBootstrapChannel()
    bootstrap_channel.publish_once(key_bundle)
    loader = getattr(supervisor, "load_bootstrap_channel", None)
    if callable(loader):
        loader(bootstrap_channel)
    discovery_modules_raw = runtime.get("discovery_modules", [])
    discovery_modules = (
        list(discovery_modules_raw)
        if isinstance(discovery_modules_raw, list) and all(isinstance(item, str) for item in discovery_modules_raw)
        else []
    )
    child_bootstrap_bundle = build_child_bootstrap_bundle(
        scenario_id=scenario_id,
        run_id=run_id,
        process_group=None,
        discovery_modules=discovery_modules,
        config=config,
        runtime=runtime,
        adapters=dict(adapters or {}),
        key_bundle=key_bundle,
    )
    bundle_loader = getattr(supervisor, "load_child_bootstrap_bundle", None)
    if callable(bundle_loader):
        bundle_loader(child_bootstrap_bundle)
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        platform = {}
    configure_groups = getattr(supervisor, "configure_process_groups", None)
    if callable(configure_groups):
        process_groups = []
        process_groups_raw = platform.get("process_groups", [])
        if isinstance(process_groups_raw, list):
            process_groups = [group for group in process_groups_raw if isinstance(group, dict)]
        configure_groups(process_groups)
    configure_routing_cache = getattr(supervisor, "configure_routing_cache", None)
    if callable(configure_routing_cache):
        routing_cache = platform.get("routing_cache", {})
        if not isinstance(routing_cache, dict):
            routing_cache = {}
        configure_routing_cache(dict(routing_cache))
    configure_lifecycle_logging = getattr(supervisor, "configure_lifecycle_logging", None)
    if callable(configure_lifecycle_logging):
        observability = runtime.get("observability", {})
        if not isinstance(observability, dict):
            observability = {}
        logging_cfg = observability.get("logging", {})
        if not isinstance(logging_cfg, dict):
            logging_cfg = {}
        configure_lifecycle_logging(dict(logging_cfg))
    boundary_inputs, dispatch_group, trace_aliases = _build_boundary_dispatch_inputs(
        runtime=runtime,
        inputs=inputs,
    )
    boundary_active = dispatch_group is not None
    started = False
    primary_error: RuntimeExecutionError | None = None
    stop_error: RuntimeExecutionError | None = None
    try:
        try:
            supervisor.start_groups(group_names)
            started = True
        except Exception as exc:
            raise RuntimeBootstrapStartError(
                "bootstrap supervisor failed to start process groups"
            ) from exc
        if not supervisor.wait_ready(policy.ready_timeout_seconds):
            raise RuntimeLifecycleReadyError("bootstrap supervisor ready check failed")
        try:
            execute_boundary = getattr(supervisor, "execute_boundary", None)
            if callable(execute_boundary) and boundary_active:
                boundary_result_raw = execute_boundary(
                    run=run,
                    run_id=run_id,
                    scenario_id=scenario_id,
                    inputs=list(boundary_inputs),
                )
                boundary_result = _normalize_boundary_result(boundary_result_raw)
                _validate_boundary_result_channels(boundary_result)
                terminal_outputs = _extract_boundary_terminal_outputs(boundary_result)
            else:
                run()
                terminal_outputs = []
            _complete_reply_waiters_from_terminal_outputs(
                scope=scenario_scope,
                outputs=terminal_outputs,
                trace_aliases=trace_aliases,
            )
        except Exception as exc:
            mapped = _map_worker_failure(
                exc=exc,
                boundary_active=boundary_active,
                dispatch_group=dispatch_group,
                supervisor=supervisor,
            )
            mapped.__cause__ = exc
            primary_error = mapped
    except RuntimeExecutionError as exc:
        primary_error = exc
    finally:
        if started:
            try:
                _wait_boundary_drain_if_available(
                    supervisor=supervisor,
                    policy=policy,
                )
                _stop_mode = _stop_supervisor_with_fallback(
                    supervisor=supervisor,
                    group_names=group_names,
                    policy=policy,
                )
                _emit_stop_events(supervisor=supervisor, group_names=group_names, mode=_stop_mode)
            except RuntimeExecutionError as exc:
                stop_error = exc

    if primary_error is not None:
        if boundary_active and dispatch_group is not None and stop_error is not None:
            _emit_handoff_failure_diagnostic(
                supervisor=supervisor,
                group_name=dispatch_group,
                category="shutdown",
            )
        raise primary_error
    if stop_error is not None:
        raise stop_error


def _stop_supervisor_with_fallback(
    *,
    supervisor: BootstrapSupervisor,
    group_names: list[str],
    policy: RuntimeLifecyclePolicy,
) -> str:
    # Return stop mode used for lifecycle event emission: graceful|forced.
    try:
        result = supervisor.stop_groups(
            graceful_timeout_seconds=policy.graceful_timeout_seconds,
            drain_inflight=policy.drain_inflight,
        )
    except TimeoutError:
        return _force_terminate_groups(supervisor=supervisor, group_names=group_names)
    except Exception as exc:
        raise RuntimeBootstrapStopError("bootstrap supervisor stop failed") from exc

    if isinstance(result, bool) and not result:
        return _force_terminate_groups(supervisor=supervisor, group_names=group_names)
    return "graceful"


def _force_terminate_groups(
    *,
    supervisor: BootstrapSupervisor,
    group_names: list[str],
) -> str:
    terminator = getattr(supervisor, "force_terminate_groups", None)
    if not callable(terminator):
        raise RuntimeBootstrapStopTimeoutError(
            "bootstrap supervisor graceful stop timed out and force terminate hook is unavailable"
        )
    try:
        terminator(list(group_names))
    except Exception as exc:
        raise RuntimeBootstrapStopError("bootstrap supervisor force terminate failed") from exc
    return "forced"


def _emit_stop_events(
    *,
    supervisor: BootstrapSupervisor,
    group_names: list[str],
    mode: str,
) -> None:
    emitter = getattr(supervisor, "emit_stop_event", None)
    if not callable(emitter):
        return
    seen: set[str] = set()
    for group_name in group_names:
        if group_name in seen:
            continue
        seen.add(group_name)
        emitter(group_name=group_name, mode=mode)


def _build_boundary_dispatch_inputs(
    *,
    runtime: dict[str, object],
    inputs: list[object],
) -> tuple[list[BoundaryDispatchInput], str | None, dict[str, str]]:
    # Cross-group handoff is active only when multiple process groups are configured.
    group_names = runtime_process_group_names(runtime)
    if len(group_names) < 2:
        return ([], None, {})
    default_dispatch_group = _select_dispatch_group(group_names)
    placement_map = _runtime_target_group_map(runtime, group_names=group_names)
    trace_aliases: dict[str, str] = {}
    boundary_inputs: list[BoundaryDispatchInput] = []
    for item in inputs:
        if isinstance(item, Envelope):
            target_name = item.target if isinstance(item.target, str) else None
            dispatch_group = _resolve_dispatch_group_for_target(
                target=target_name,
                default_dispatch_group=default_dispatch_group,
                placement_map=placement_map,
            )
            boundary_inputs.append(
                BoundaryDispatchInput(
                    payload=item.payload,
                    dispatch_group=dispatch_group,
                    target=target_name,
                    trace_id=item.trace_id,
                    reply_to=item.reply_to,
                    source_group="supervisor.entry",
                    route_hop=0,
                    span_id=item.span_id,
                )
            )
            if isinstance(item.trace_id, str) and item.trace_id:
                trace_aliases[f"child:{item.trace_id}"] = item.trace_id
            continue
        boundary_inputs.append(
            BoundaryDispatchInput(
                payload=item,
                dispatch_group=default_dispatch_group,
                source_group="supervisor.entry",
                route_hop=0,
            )
        )
    # Transitional aggregate group marker used by existing diagnostics/messages.
    return (boundary_inputs, default_dispatch_group, trace_aliases)


def _select_dispatch_group(group_names: list[str]) -> str:
    # Prefer non-web groups for workload execution; fallback to last configured group.
    for group_name in group_names:
        if group_name != "web":
            return group_name
    return group_names[-1]


def _runtime_target_group_map(
    runtime: dict[str, object],
    *,
    group_names: list[str],
) -> dict[str, str]:
    # Build target->process_group placement map from runtime.platform.process_groups[].nodes.
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        raise ValueError("runtime.platform must be a mapping")
    groups_raw = platform.get("process_groups", [])
    if groups_raw is None:
        groups_raw = []
    if not isinstance(groups_raw, list):
        raise ValueError("runtime.platform.process_groups must be a list")
    known_groups = set(group_names)
    mapping: dict[str, str] = {}
    for index, group in enumerate(groups_raw):
        if not isinstance(group, dict):
            raise ValueError(f"runtime.platform.process_groups[{index}] must be a mapping")
        name = group.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"runtime.platform.process_groups[{index}].name must be a non-empty string")
        if name not in known_groups:
            continue
        nodes = group.get("nodes", [])
        if nodes is None:
            nodes = []
        if not isinstance(nodes, list):
            raise ValueError(f"runtime.platform.process_groups[{index}].nodes must be a list")
        for node in nodes:
            if not isinstance(node, str) or not node:
                raise ValueError(
                    f"runtime.platform.process_groups[{index}].nodes entries must be non-empty strings"
                )
            existing = mapping.get(node)
            if existing is not None and existing != name:
                raise ValueError(
                    f"runtime.platform.process_groups has duplicate placement for node '{node}'"
                )
            mapping[node] = name
    return mapping


def _resolve_dispatch_group_for_target(
    *,
    target: str | None,
    default_dispatch_group: str,
    placement_map: dict[str, str],
) -> str:
    # If placement map is declared, each explicit target must resolve deterministically.
    if not isinstance(target, str) or not target:
        return default_dispatch_group
    if not placement_map:
        return default_dispatch_group
    mapped = placement_map.get(target)
    if isinstance(mapped, str) and mapped:
        return mapped
    raise ValueError(
        f"Missing process-group placement for target '{target}' in runtime.platform.process_groups[].nodes"
    )


def _wait_boundary_drain_if_available(
    *,
    supervisor: BootstrapSupervisor,
    policy: RuntimeLifecyclePolicy,
) -> None:
    waiter = getattr(supervisor, "wait_boundary_drain", None)
    if not callable(waiter):
        return
    try:
        ready = waiter(policy.graceful_timeout_seconds)
    except Exception as exc:
        raise RuntimeBootstrapStopError("bootstrap supervisor boundary drain wait failed") from exc
    if isinstance(ready, bool) and not ready:
        raise RuntimeBootstrapStopTimeoutError("bootstrap supervisor boundary drain wait timed out")


def _map_worker_failure(
    *,
    exc: Exception,
    boundary_active: bool,
    dispatch_group: str | None,
    supervisor: BootstrapSupervisor,
) -> RuntimeWorkerFailedError:
    if boundary_active and dispatch_group is not None:
        if isinstance(exc, TimeoutError):
            category = "timeout"
            message = f"remote handoff timed out for group '{dispatch_group}'"
        elif isinstance(exc, ConnectionError):
            category = "transport"
            message = f"remote handoff transport failed for group '{dispatch_group}'"
        else:
            category = "execution"
            message = f"remote handoff failed for group '{dispatch_group}'"
        _emit_handoff_failure_diagnostic(
            supervisor=supervisor,
            group_name=dispatch_group,
            category=category,
        )
        return RuntimeWorkerFailedError(message)
    return RuntimeWorkerFailedError("execution worker failed")


def _emit_handoff_failure_diagnostic(
    *,
    supervisor: BootstrapSupervisor,
    group_name: str,
    category: str,
) -> None:
    emitter = getattr(supervisor, "emit_handoff_failure", None)
    if not callable(emitter):
        return
    try:
        emitter(group_name=group_name, category=category)
    except Exception:
        # Diagnostic emission should never break execution path.
        return


def _complete_reply_waiters_from_terminal_outputs(
    *,
    scope: ScenarioScope,
    outputs: list[Envelope],
    trace_aliases: dict[str, str] | None = None,
) -> None:
    # Phase 4 Step D + engine Step B: boundary terminal envelopes complete correlation via coordinator.
    coordinator = _resolve_reply_coordinator(scope)
    if coordinator is None:
        return

    aliases = trace_aliases or {}

    for output in outputs:
        if not isinstance(output, Envelope):
            continue
        if not isinstance(output.trace_id, str) or not output.trace_id:
            continue
        resolved_trace_id = aliases.get(output.trace_id, output.trace_id)
        if not isinstance(resolved_trace_id, str) or not resolved_trace_id:
            continue
        terminal = output.payload
        if not isinstance(terminal, TerminalEvent):
            continue
        coordinator.complete_if_waiting(
            trace_id=resolved_trace_id,
            terminal_event=terminal,
        )


def _resolve_reply_coordinator(scope: ScenarioScope) -> ReplyCoordinatorService | None:
    # Reply correlation is coordinator-only in target runtime model.
    try:
        coordinator_obj = scope.resolve("service", ReplyCoordinatorService)
    except Exception:
        coordinator_obj = None

    if isinstance(coordinator_obj, ReplyCoordinatorService):
        return coordinator_obj
    if (
        coordinator_obj is not None
        and callable(getattr(coordinator_obj, "register_if_requested", None))
        and callable(getattr(coordinator_obj, "complete_if_waiting", None))
    ):
        return coordinator_obj  # type: ignore[return-value]
    # Reply correlation is optional for non-web/runtime-only profiles.
    return None


def _normalize_boundary_result(result: object) -> RoutingResult:
    # Canonical process-supervisor boundary contract uses structured routing result.
    # Transitional compatibility: allow list[Envelope] and map to terminal channel.
    if isinstance(result, RoutingResult):
        return result
    if isinstance(result, list):
        return RoutingResult(local_deliveries=[], boundary_deliveries=[], terminal_outputs=list(result))
    raise ValueError(
        "BootstrapSupervisor.execute_boundary must return RoutingResult"
    )


def _validate_boundary_result_channels(result: RoutingResult) -> None:
    # Boundary execution path should emit only terminal outputs to parent runtime.
    if result.local_deliveries:
        raise ValueError(
            "BootstrapSupervisor.execute_boundary returned local_deliveries; "
            "boundary path accepts terminal_outputs only"
        )
    if result.boundary_deliveries:
        raise ValueError(
            "BootstrapSupervisor.execute_boundary returned boundary_deliveries; "
            "nested boundary dispatch is not supported in this phase"
        )


def _extract_boundary_terminal_outputs(result: RoutingResult) -> list[Envelope]:
    outputs: list[Envelope] = []
    for item in result.terminal_outputs:
        if not isinstance(item, Envelope):
            raise ValueError(
                "BootstrapSupervisor.execute_boundary terminal_outputs must contain Envelope items"
            )
        outputs.append(item)
    return outputs
