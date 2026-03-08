from __future__ import annotations

import copy
import time
from dataclasses import dataclass, fields, is_dataclass, replace
from threading import Event
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.transport.ipc.ipc_transport import (
    ExecutionIpcTransportService,
    compose_execution_ipc_worker_target_id,
    decompose_execution_ipc_worker_target_id,
    resolve_execution_ipc_lane_for_target,
)
from stream_kernel.execution.transport.ipc.ipc_lane_routing_service import (
    ExecutionIpcLaneRoutingService,
)
from stream_kernel.platform.services.runtime import ProcessGroupRouterService
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLaunchPlanEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafStopAckEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneStateService,
)
from stream_kernel.platform.services.runtime.debug_buffer import (
    debug_instrument_service_methods,
)
from stream_kernel.routing.envelope import Envelope

from .ipc_route_table_service import (
    ExecutionIpcRouteTableService,
)

_RETRY_BACKOFF_WAIT = Event()


@dataclass(frozen=True, slots=True)
class BroadcastDispatchResult:
    broadcast_id: str
    policy: str
    total: int
    accepted: int
    failed: int
    failed_workers: tuple[str, ...] = ()


@runtime_checkable
class ExecutionIpcHandoffDispatchService(Protocol):
    def dispatch_envelope(
        self,
        envelope: Envelope,
        *,
        source_group: str | None = None,
    ) -> bool:
        raise NotImplementedError

    def dispatch_broadcast(
        self,
        envelope: Envelope,
        *,
        target_group: str | None = None,
        include_observability: bool = False,
        policy: str = "best_effort",
        broadcast_id: str | None = None,
    ) -> BroadcastDispatchResult:
        raise NotImplementedError


@service(name="execution_ipc_handoff_dispatch_service")
@debug_instrument_service_methods
@dataclass(slots=True)
class DefaultExecutionIpcHandoffDispatchService(ExecutionIpcHandoffDispatchService):
    execution_ipc: ExecutionIpcTransportService = inject.service(ExecutionIpcTransportService)
    process_group_router: ProcessGroupRouterService = inject.service(ProcessGroupRouterService)
    route_table: ExecutionIpcRouteTableService = inject.service(ExecutionIpcRouteTableService)
    control_plane_state: ControlPlaneStateService = inject.service(ControlPlaneStateService)
    lane_routing: object | None = inject.service(ExecutionIpcLaneRoutingService)
    runtime_debug_buffer: object | None = None
    control_retry_attempts: int = 0
    control_retry_backoff_seconds: float = 0.01

    def dispatch_envelope(
        self,
        envelope: Envelope,
        *,
        source_group: str | None = None,
    ) -> bool:
        target = envelope.target
        if not isinstance(target, str) or not target:
            return False
        target_id = self._route_table().resolve_route(target=target)
        if not isinstance(target_id, str) or not target_id:
            target_group = self._router().resolve_group_for_target(
                target=target,
                source_group=source_group,
            )
            target_id = f"{target_group}#1"
            self._route_table().upsert_route(target=target, target_id=target_id)
        lane = self._resolve_lane(target=target, payload=envelope.payload)
        resolved_target_id = _lane_target_id(target_id=target_id, lane=lane)
        self._ipc().send(resolved_target_id, envelope.payload, no_reply=True)
        return True

    def dispatch_broadcast(
        self,
        envelope: Envelope,
        *,
        target_group: str | None = None,
        include_observability: bool = False,
        policy: str = "best_effort",
        broadcast_id: str | None = None,
    ) -> BroadcastDispatchResult:
        resolved_policy = _normalize_policy(policy)
        target = envelope.target
        lane = self._resolve_lane(
            target=target if isinstance(target, str) else None,
            payload=envelope.payload,
        )
        is_observability_lane = (
            isinstance(target, str) and target.startswith("system.obs.")
        )
        if is_observability_lane:
            # Observability lane is always best-effort.
            resolved_policy = "best_effort"
        worker_ids = self._resolve_live_worker_ids(
            target_group=target_group,
            include_observability=include_observability,
        )
        bid = _resolve_broadcast_id(broadcast_id=broadcast_id)
        total = len(worker_ids)
        if total == 0:
            return BroadcastDispatchResult(
                broadcast_id=bid,
                policy=resolved_policy,
                total=0,
                accepted=0,
                failed=0,
                failed_workers=(),
            )

        accepted = 0
        failed_workers: list[str] = []
        retry_attempts = 0 if is_observability_lane else max(0, int(self.control_retry_attempts))
        require_drained_delivery = not is_observability_lane
        for index, worker_id in enumerate(worker_ids):
            payload = _copy_payload_with_broadcast_metadata(
                envelope.payload,
                broadcast_id=bid,
                worker_id=worker_id,
            )
            lane_target_id = _lane_target_id(target_id=worker_id, lane=lane)
            sent = self._send_with_retry(
                target_id=lane_target_id,
                payload=payload,
                attempts=retry_attempts,
                require_drained_delivery=require_drained_delivery,
            )
            if sent:
                accepted += 1
                continue
            failed_workers.append(worker_id)
            if resolved_policy == "all_or_nothing":
                failed_workers.extend(worker_ids[index + 1 :])
                break

        failed = len(failed_workers)
        return BroadcastDispatchResult(
            broadcast_id=bid,
            policy=resolved_policy,
            total=total,
            accepted=accepted,
            failed=failed,
            failed_workers=tuple(failed_workers),
        )

    def _send_with_retry(
        self,
        *,
        target_id: str,
        payload: object,
        attempts: int,
        require_drained_delivery: bool,
    ) -> bool:
        max_attempts = max(1, attempts + 1)
        sent = False
        for attempt in range(max_attempts):
            try:
                if not sent:
                    self._ipc().send(target_id, payload, no_reply=True)
                    sent = True
                if not require_drained_delivery:
                    return True
                if self._has_pending_outbound(target_id):
                    if attempt + 1 < max_attempts:
                        _RETRY_BACKOFF_WAIT.wait(max(0.0, float(self.control_retry_backoff_seconds)))
                        continue
                    # Payload is already accepted by transport. Keep success semantics
                    # and avoid duplicate sends under transient queue backlog.
                    return True
                return True
            except Exception:
                sent = False
                if attempt + 1 < max_attempts:
                    _RETRY_BACKOFF_WAIT.wait(max(0.0, float(self.control_retry_backoff_seconds)))
                continue
        return False

    def _has_pending_outbound(self, target_id: str) -> bool:
        metrics = getattr(self._ipc(), "metrics", None)
        if not callable(metrics):
            return False
        try:
            payload = metrics(target_id)
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        pending = payload.get("pending_outbound", 0)
        outbound = payload.get("outbound_queue_depth", 0)
        pending_count = pending if isinstance(pending, int) else 0
        outbound_count = outbound if isinstance(outbound, int) else 0
        return pending_count > 0 or outbound_count > 0

    def _resolve_live_worker_ids(
        self,
        *,
        target_group: str | None,
        include_observability: bool,
    ) -> list[str]:
        events = self._state().events()
        planned = _planned_worker_ids(events)
        configured = _configured_worker_ids(events)
        stopped = _stopped_worker_ids(events)
        live = configured if configured else planned
        live -= stopped

        filtered: list[str] = []
        for worker_id in sorted(live):
            group = _group_from_worker_id(worker_id)
            if isinstance(target_group, str) and target_group and group != target_group:
                continue
            if not include_observability and group == "system.observability":
                continue
            filtered.append(worker_id)
        return filtered

    def _ipc(self) -> ExecutionIpcTransportService:
        candidate = self.execution_ipc
        if isinstance(candidate, ExecutionIpcTransportService):
            return candidate
        if callable(getattr(candidate, "send", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ExecutionIpcTransportService binding is required")

    def _router(self) -> ProcessGroupRouterService:
        candidate = self.process_group_router
        if isinstance(candidate, ProcessGroupRouterService):
            return candidate
        if callable(getattr(candidate, "resolve_group_for_target", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ProcessGroupRouterService binding is required")

    def _route_table(self) -> ExecutionIpcRouteTableService:
        candidate = self.route_table
        if isinstance(candidate, ExecutionIpcRouteTableService):
            return candidate
        if callable(getattr(candidate, "resolve_route", None)) and callable(
            getattr(candidate, "upsert_route", None)
        ):
            return candidate  # type: ignore[return-value]
        raise ValueError("ExecutionIpcRouteTableService binding is required")

    def _state(self) -> ControlPlaneStateService:
        candidate = self.control_plane_state
        if isinstance(candidate, ControlPlaneStateService):
            return candidate
        if callable(getattr(candidate, "events", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ControlPlaneStateService binding is required")

    def _resolve_lane(self, *, target: str | None, payload: object) -> str:
        fallback = resolve_execution_ipc_lane_for_target(target)
        routing = self._lane_routing_optional()
        if routing is None:
            return fallback
        try:
            return routing.resolve_lane(
                target=target,
                payload=payload,
                default_lane=fallback,
            )
        except Exception:
            return fallback

    def _lane_routing_optional(self) -> ExecutionIpcLaneRoutingService | None:
        candidate = self.lane_routing
        if isinstance(candidate, ExecutionIpcLaneRoutingService):
            return candidate
        if callable(getattr(candidate, "resolve_lane", None)):
            return candidate  # type: ignore[return-value]
        return None


def _normalize_policy(policy: str) -> str:
    if isinstance(policy, str) and policy.strip().lower() == "all_or_nothing":
        return "all_or_nothing"
    return "best_effort"


def _resolve_broadcast_id(*, broadcast_id: str | None) -> str:
    if isinstance(broadcast_id, str) and broadcast_id:
        return broadcast_id
    return f"broadcast:{int(time.time() * 1000)}"


def _group_from_worker_id(worker_id: str) -> str | None:
    if not isinstance(worker_id, str) or not worker_id:
        return None
    if "#" not in worker_id:
        return None
    group, _sep, _suffix = worker_id.partition("#")
    if not group:
        return None
    return group


def _planned_worker_ids(events: list[object]) -> set[str]:
    for event in reversed(events):
        if not isinstance(event, ControlPlaneLaunchPlanEvent):
            continue
        workers: set[str] = set()
        for group in event.plan.groups:
            for slot in range(max(1, int(group.workers))):
                workers.add(f"{group.group_name}#{slot + 1}")
        return workers
    return set()


def _configured_worker_ids(events: list[object]) -> set[str]:
    return {
        event.worker_id
        for event in events
        if isinstance(event, ControlPlaneLeafConfigAckEvent)
        and event.status == "applied"
        and isinstance(event.worker_id, str)
        and bool(event.worker_id)
    }


def _stopped_worker_ids(events: list[object]) -> set[str]:
    return {
        event.worker_id
        for event in events
        if isinstance(event, ControlPlaneLeafStopAckEvent)
        and isinstance(event.worker_id, str)
        and bool(event.worker_id)
    }


def _copy_payload_with_broadcast_metadata(
    payload: object,
    *,
    broadcast_id: str,
    worker_id: str,
) -> object:
    cloned = copy.deepcopy(payload)
    if isinstance(cloned, dict):
        result = dict(cloned)
        result.setdefault("broadcast_id", broadcast_id)
        result.setdefault("worker_id", worker_id)
        return result
    if is_dataclass(cloned):
        dataclass_fields = {item.name for item in fields(cloned)}
        updates: dict[str, object] = {}
        if "worker_id" in dataclass_fields:
            worker = getattr(cloned, "worker_id", None)
            if isinstance(worker, str):
                updates["worker_id"] = worker_id
        if "command_id" in dataclass_fields:
            command_id = getattr(cloned, "command_id", None)
            if isinstance(command_id, str) and "{worker_id}" in command_id:
                updates["command_id"] = command_id.replace("{worker_id}", worker_id)
        if "broadcast_id" in dataclass_fields:
            current = getattr(cloned, "broadcast_id", None)
            if not isinstance(current, str) or not current:
                updates["broadcast_id"] = broadcast_id
        if updates:
            return replace(cloned, **updates)
    return cloned


def _lane_target_id(*, target_id: str, lane: str) -> str:
    resolved = decompose_execution_ipc_worker_target_id(target_id)
    if resolved is None:
        return target_id
    worker_id, _current_lane = resolved
    return compose_execution_ipc_worker_target_id(worker_id, lane=lane)


__all__ = [
    "BroadcastDispatchResult",
    "ExecutionIpcHandoffDispatchService",
    "DefaultExecutionIpcHandoffDispatchService",
]
