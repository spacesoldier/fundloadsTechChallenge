from __future__ import annotations

import time

from stream_kernel.application_context.injection_registry import ScenarioScope
from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
    ControlPlaneRootBoundaryHandoffService,
)
from stream_kernel.execution.runtime.runner import AsyncRunner, SyncRunner
from stream_kernel.execution.runtime.runner_ingress import (
    enqueue_runner_input_async,
    enqueue_runner_input_sync,
)
from stream_kernel.routing.envelope import Envelope


def _is_observability_envelope(item: object) -> bool:
    return (
        isinstance(item, Envelope)
        and isinstance(item.target, str)
        and item.target.startswith("system.obs.")
    )


def _split_observability_envelopes(items: list[Envelope]) -> tuple[list[Envelope], list[Envelope]]:
    observability: list[Envelope] = []
    other: list[Envelope] = []
    for item in items:
        if _is_observability_envelope(item):
            observability.append(item)
            continue
        other.append(item)
    return (observability, other)


def drain_root_boundary_handoff(
    *,
    scenario_scope: ScenarioScope,
    external_deliveries: list[Envelope],
    poll_timeout_seconds: float = 0.0,
) -> list[object]:
    service = _resolve_root_boundary_handoff_service(scenario_scope)
    if service is None:
        return []
    if external_deliveries:
        drain = getattr(service, "drain_external_deliveries", None)
        if not callable(drain):
            return []
        try:
            drained = drain(envelopes=list(external_deliveries), source_group=None)
            if isinstance(drained, list):
                return list(drained)
            if isinstance(drained, tuple):
                return list(drained)
            return []
        except Exception:
            return []
    drain_completed = getattr(service, "drain_completed_deliveries", None)
    if not callable(drain_completed):
        return []
    try:
        try:
            drained = drain_completed(poll_timeout_seconds=max(0.0, float(poll_timeout_seconds)))
        except TypeError:
            drained = drain_completed()
        if isinstance(drained, list):
            return list(drained)
        if isinstance(drained, tuple):
            return list(drained)
        return []
    except Exception:
        return []


def root_boundary_handoff_has_inflight(scenario_scope: ScenarioScope) -> bool:
    service = _resolve_root_boundary_handoff_service(scenario_scope)
    if service is None:
        return False
    blocking_inflight = getattr(service, "has_replay_blocking_inflight_deliveries", None)
    if callable(blocking_inflight):
        try:
            return bool(blocking_inflight())
        except Exception:
            return False
    has_inflight = getattr(service, "has_inflight_deliveries", None)
    if not callable(has_inflight):
        return False
    try:
        return bool(has_inflight())
    except Exception:
        return False


def replay_root_boundary_handoff_outputs_sync(
    *,
    runner: SyncRunner,
    scenario_scope: ScenarioScope,
    run_id: str,
    scenario_id: str,
    start_index: int,
    poll_timeout_seconds: float,
    idle_timeout_seconds: float | None,
) -> int:
    next_index = max(1, int(start_index))
    replay_poll_timeout_seconds = max(0.0, min(float(poll_timeout_seconds), 0.001))
    while True:
        pending: list[Envelope] = []
        if isinstance(runner.external_deliveries, list) and runner.external_deliveries:
            pending = list(runner.external_deliveries)
            runner.external_deliveries.clear()
        pending_observability, pending_non_observability = _split_observability_envelopes(pending)
        if pending_observability:
            try:
                _ = drain_root_boundary_handoff(
                    scenario_scope=scenario_scope,
                    external_deliveries=pending_observability,
                    poll_timeout_seconds=0.0,
                )
            except TypeError:
                _ = drain_root_boundary_handoff(
                    scenario_scope=scenario_scope,
                    external_deliveries=pending_observability,
                )
        try:
            replay_items = drain_root_boundary_handoff(
                scenario_scope=scenario_scope,
                external_deliveries=pending_non_observability,
                poll_timeout_seconds=(0.0 if pending_non_observability else replay_poll_timeout_seconds),
            )
        except TypeError:
            # Compatibility path for test doubles/legacy call-sites that still expose
            # `drain_root_boundary_handoff(scenario_scope, external_deliveries)` signature.
            replay_items = drain_root_boundary_handoff(
                scenario_scope=scenario_scope,
                external_deliveries=pending_non_observability,
            )
        if replay_items:
            for payload in replay_items:
                if _is_observability_envelope(payload):
                    continue
                enqueue_runner_input_sync(
                    runner,
                    payload,
                    run_id=run_id,
                    scenario_id=scenario_id,
                    index=next_index,
                )
                next_index += 1
            runner.run_until_stopped(
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
            )
        has_pending = isinstance(runner.external_deliveries, list) and bool(runner.external_deliveries)
        has_inflight = root_boundary_handoff_has_inflight(scenario_scope)
        if not has_pending and not has_inflight:
            break
        if not replay_items:
            time.sleep(replay_poll_timeout_seconds)
    return next_index


def replay_root_boundary_handoff_outputs_async(
    *,
    runner: AsyncRunner,
    scenario_scope: ScenarioScope,
    run_id: str,
    scenario_id: str,
    start_index: int,
    poll_timeout_seconds: float,
    idle_timeout_seconds: float | None,
) -> int:
    next_index = max(1, int(start_index))
    replay_poll_timeout_seconds = max(0.0, min(float(poll_timeout_seconds), 0.001))
    while True:
        pending: list[Envelope] = []
        if isinstance(runner.external_deliveries, list) and runner.external_deliveries:
            pending = list(runner.external_deliveries)
            runner.external_deliveries.clear()
        pending_observability, pending_non_observability = _split_observability_envelopes(pending)
        if pending_observability:
            try:
                _ = drain_root_boundary_handoff(
                    scenario_scope=scenario_scope,
                    external_deliveries=pending_observability,
                    poll_timeout_seconds=0.0,
                )
            except TypeError:
                _ = drain_root_boundary_handoff(
                    scenario_scope=scenario_scope,
                    external_deliveries=pending_observability,
                )
        try:
            replay_items = drain_root_boundary_handoff(
                scenario_scope=scenario_scope,
                external_deliveries=pending_non_observability,
                poll_timeout_seconds=(0.0 if pending_non_observability else replay_poll_timeout_seconds),
            )
        except TypeError:
            # Compatibility path for test doubles/legacy call-sites that still expose
            # `drain_root_boundary_handoff(scenario_scope, external_deliveries)` signature.
            replay_items = drain_root_boundary_handoff(
                scenario_scope=scenario_scope,
                external_deliveries=pending_non_observability,
            )
        if replay_items:
            for payload in replay_items:
                if _is_observability_envelope(payload):
                    continue
                enqueue_runner_input_async(
                    runner,
                    payload,
                    run_id=run_id,
                    scenario_id=scenario_id,
                    index=next_index,
                )
                next_index += 1
            runner.run_until_stopped(
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
            )
        has_pending = isinstance(runner.external_deliveries, list) and bool(runner.external_deliveries)
        has_inflight = root_boundary_handoff_has_inflight(scenario_scope)
        if not has_pending and not has_inflight:
            break
        if not replay_items:
            time.sleep(replay_poll_timeout_seconds)
    return next_index


def _resolve_root_boundary_handoff_service(
    scenario_scope: ScenarioScope,
) -> ControlPlaneRootBoundaryHandoffService | None:
    try:
        service = scenario_scope.resolve("service", ControlPlaneRootBoundaryHandoffService)
    except Exception:
        return None
    if isinstance(service, ControlPlaneRootBoundaryHandoffService):
        return service
    if callable(getattr(service, "drain_external_deliveries", None)):
        return service  # type: ignore[return-value]
    return None


__all__ = [
    "drain_root_boundary_handoff",
    "replay_root_boundary_handoff_outputs_async",
    "replay_root_boundary_handoff_outputs_sync",
    "root_boundary_handoff_has_inflight",
]
