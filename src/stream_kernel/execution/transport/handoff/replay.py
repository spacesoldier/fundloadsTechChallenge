from __future__ import annotations

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

_REPLAY_MAX_WAIT_SECONDS = 5.0
_REPLAY_MIN_PASSES = 64


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
            return list(external_deliveries)
        try:
            drained = drain(envelopes=list(external_deliveries), source_group=None)
        except Exception as exc:
            # Observability lane is best-effort and may be retried in background.
            if all(_is_observability_envelope(item) for item in external_deliveries):
                return list(external_deliveries)
            # Business/control lanes must fail fast instead of silently spinning replay.
            raise RuntimeError("root boundary handoff dispatch failed") from exc
        if isinstance(drained, list):
            return list(drained)
        if isinstance(drained, tuple):
            return list(drained)
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
    for _ in range(_replay_max_passes(poll_timeout_seconds)):
        drained_observability, has_non_observability_pending = _drain_observability_only_pending(
            runner=runner,
            scenario_scope=scenario_scope,
        )
        has_inflight = root_boundary_handoff_has_inflight(scenario_scope)
        if drained_observability and not has_non_observability_pending and not has_inflight:
            break
        step = _replay_step(
            runner=runner,
            scenario_scope=scenario_scope,
            run_id=run_id,
            scenario_id=scenario_id,
            next_index=next_index,
            poll_timeout_seconds=poll_timeout_seconds,
            idle_timeout_seconds=idle_timeout_seconds,
            enqueue_input=enqueue_runner_input_sync,
        )
        next_index = step.next_index
        has_pending = isinstance(runner.external_deliveries, list) and bool(runner.external_deliveries)
        has_inflight = root_boundary_handoff_has_inflight(scenario_scope)
        if not has_pending and not has_inflight:
            break
        if not step.made_progress and not has_inflight:
            break
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
    for _ in range(_replay_max_passes(poll_timeout_seconds)):
        drained_observability, has_non_observability_pending = _drain_observability_only_pending(
            runner=runner,
            scenario_scope=scenario_scope,
        )
        has_inflight = root_boundary_handoff_has_inflight(scenario_scope)
        if drained_observability and not has_non_observability_pending and not has_inflight:
            break
        step = _replay_step(
            runner=runner,
            scenario_scope=scenario_scope,
            run_id=run_id,
            scenario_id=scenario_id,
            next_index=next_index,
            poll_timeout_seconds=poll_timeout_seconds,
            idle_timeout_seconds=idle_timeout_seconds,
            enqueue_input=enqueue_runner_input_async,
        )
        next_index = step.next_index
        has_pending = isinstance(runner.external_deliveries, list) and bool(runner.external_deliveries)
        has_inflight = root_boundary_handoff_has_inflight(scenario_scope)
        if not has_pending and not has_inflight:
            break
        if not step.made_progress and not has_inflight:
            break
    return next_index


class _ReplayStepResult:
    __slots__ = ("next_index", "made_progress")

    def __init__(self, *, next_index: int, made_progress: bool) -> None:
        self.next_index = next_index
        self.made_progress = made_progress


def _replay_max_passes(poll_timeout_seconds: float) -> int:
    poll = max(0.001, float(poll_timeout_seconds))
    from_wait_budget = int(_REPLAY_MAX_WAIT_SECONDS / poll)
    return max(1, int(_REPLAY_MIN_PASSES), from_wait_budget)


def _replay_step(
    *,
    runner: SyncRunner | AsyncRunner,
    scenario_scope: ScenarioScope,
    run_id: str,
    scenario_id: str,
    next_index: int,
    poll_timeout_seconds: float,
    idle_timeout_seconds: float | None,
    enqueue_input: object,
) -> _ReplayStepResult:
    pending: list[Envelope] = []
    if isinstance(runner.external_deliveries, list) and runner.external_deliveries:
        pending = list(runner.external_deliveries)
        runner.external_deliveries.clear()
    replay_items = _drain_replay_items(
        scenario_scope=scenario_scope,
        pending_non_observability=pending,
        poll_timeout_seconds=poll_timeout_seconds,
    )
    enqueued_count = 0
    for payload in replay_items:
        if _is_observability_envelope(payload):
            continue
        if callable(enqueue_input):
            enqueue_input(
                runner,
                payload,
                run_id=run_id,
                scenario_id=scenario_id,
                index=next_index,
            )
            enqueued_count += 1
            next_index += 1
    if enqueued_count > 0:
        runner.run_until_stopped(
            poll_timeout_seconds=max(0.0, float(poll_timeout_seconds)),
            idle_timeout_seconds=0.0,
        )
    had_inflight_before = root_boundary_handoff_has_inflight(scenario_scope)
    _ = _drain_replay_items(
        scenario_scope=scenario_scope,
        pending_non_observability=[],
        poll_timeout_seconds=poll_timeout_seconds,
    )
    has_inflight_after = root_boundary_handoff_has_inflight(scenario_scope)
    made_progress = (
        bool(pending)
        or bool(replay_items)
        or enqueued_count > 0
        or (had_inflight_before and not has_inflight_after)
    )
    return _ReplayStepResult(next_index=next_index, made_progress=made_progress)


def _drain_observability_only_pending(
    *,
    runner: SyncRunner | AsyncRunner,
    scenario_scope: ScenarioScope,
) -> tuple[bool, bool]:
    pending = getattr(runner, "external_deliveries", None)
    if not isinstance(pending, list) or not pending:
        return (False, False)
    snapshot = list(pending)
    pending_observability, pending_non_observability = _split_observability_envelopes(snapshot)
    if not pending_observability:
        return (False, bool(pending_non_observability))
    pending.clear()
    pending.extend(pending_non_observability)
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
    return (True, bool(pending_non_observability))


def _drain_replay_items(
    *,
    scenario_scope: ScenarioScope,
    pending_non_observability: list[Envelope],
    poll_timeout_seconds: float,
) -> list[object]:
    try:
        return drain_root_boundary_handoff(
            scenario_scope=scenario_scope,
            external_deliveries=pending_non_observability,
            poll_timeout_seconds=max(0.0, float(poll_timeout_seconds)),
        )
    except TypeError:
        return drain_root_boundary_handoff(
            scenario_scope=scenario_scope,
            external_deliveries=pending_non_observability,
        )


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
