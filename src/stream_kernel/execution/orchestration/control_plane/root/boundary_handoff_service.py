from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.control_plane.root.boundary_execution_service import (
    ControlPlaneRootBoundaryExecutionService,
)
from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
    ControlPlaneRootReplyIngressService,
)
from stream_kernel.execution.orchestration.lifecycle import BoundaryDispatchInput
from stream_kernel.execution.transport.handoff.ipc_route_table_service import (
    ExecutionIpcRouteTableService,
)
from stream_kernel.platform.services.runtime import ProcessGroupRouterService
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryResultEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneStateService,
)
from stream_kernel.routing.envelope import Envelope


@runtime_checkable
class ControlPlaneRootBoundaryHandoffService(Protocol):
    def drain_external_deliveries(
        self,
        *,
        envelopes: list[Envelope],
        source_group: str | None = None,
    ) -> list[object]:
        raise NotImplementedError

    def drain_completed_deliveries(
        self,
        *,
        poll_timeout_seconds: float = 0.0,
    ) -> list[object]:
        raise NotImplementedError

    def has_inflight_deliveries(self) -> bool:
        raise NotImplementedError

    def has_replay_blocking_inflight_deliveries(self) -> bool:
        raise NotImplementedError


@service(name="control_plane_root_boundary_handoff_service")
@dataclass(slots=True)
class DefaultControlPlaneRootBoundaryHandoffService(ControlPlaneRootBoundaryHandoffService):
    process_group_router: ProcessGroupRouterService = inject.service(ProcessGroupRouterService)
    root_boundary: ControlPlaneRootBoundaryExecutionService = inject.service(ControlPlaneRootBoundaryExecutionService)
    route_table: ExecutionIpcRouteTableService = inject.service(ExecutionIpcRouteTableService)
    reply_ingress: ControlPlaneRootReplyIngressService = inject.service(ControlPlaneRootReplyIngressService)
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)
    timeout_seconds: float = 10.0
    stream_batch_max_items: int = 1
    observability_batch_max_items: int = 32
    inflight_idle_timeout_seconds: float = 5.0
    _inflight: dict[tuple[str, str], tuple[float, bool]] = field(default_factory=dict, init=False, repr=False)
    _event_cursor: int = field(default=0, init=False, repr=False)
    _stream_request_seq: int = field(default=0, init=False, repr=False)
    _observability_request_seq: int = field(default=0, init=False, repr=False)

    def configure_dispatch(
        self,
        *,
        timeout_seconds: float | None = None,
        stream_batch_max_items: int | None = None,
        observability_batch_max_items: int | None = None,
        inflight_idle_timeout_seconds: float | None = None,
    ) -> None:
        if isinstance(timeout_seconds, (int, float)) and float(timeout_seconds) > 0:
            self.timeout_seconds = float(timeout_seconds)
        if isinstance(stream_batch_max_items, int) and stream_batch_max_items > 0:
            self.stream_batch_max_items = int(stream_batch_max_items)
        if isinstance(observability_batch_max_items, int) and observability_batch_max_items > 0:
            self.observability_batch_max_items = int(observability_batch_max_items)
        if (
            isinstance(inflight_idle_timeout_seconds, (int, float))
            and float(inflight_idle_timeout_seconds) > 0
        ):
            self.inflight_idle_timeout_seconds = float(inflight_idle_timeout_seconds)

    def drain_external_deliveries(
        self,
        *,
        envelopes: list[Envelope],
        source_group: str | None = None,
    ) -> list[object]:
        terminal_outputs: list[object] = []
        observability_batches: dict[tuple[str, str], list[BoundaryDispatchInput]] = {}
        pending_batch: list[BoundaryDispatchInput] = []
        pending_batch_group: str | None = None
        pending_batch_worker: str | None = None
        pending_batch_index: int = 0
        pending_batch_target: str | None = None
        pending_batch_trace_id: str | None = None

        def _flush_pending_batch() -> None:
            nonlocal pending_batch
            nonlocal pending_batch_group
            nonlocal pending_batch_worker
            nonlocal pending_batch_index
            nonlocal pending_batch_target
            nonlocal pending_batch_trace_id
            if not pending_batch:
                return
            target_group = pending_batch_group
            worker_id = pending_batch_worker
            if not isinstance(target_group, str) or not target_group:
                pending_batch = []
                pending_batch_group = None
                pending_batch_worker = None
                return
            if not isinstance(worker_id, str) or not worker_id:
                pending_batch = []
                pending_batch_group = None
                pending_batch_worker = None
                return
            if len(pending_batch) == 1 and isinstance(pending_batch_target, str) and pending_batch_target:
                request_id = _boundary_request_id_from_values(
                    trace_id=pending_batch_trace_id,
                    index=pending_batch_index,
                    target=pending_batch_target,
                    sequence=self._next_stream_request_seq(),
                )
            else:
                request_id = _boundary_batch_request_id(
                    target_group=target_group,
                    worker_id=worker_id,
                    batch_seq=self._next_stream_request_seq(),
                    batch_size=len(pending_batch),
                )
            routing = self._boundary().execute_boundary_on_leaf(
                target_group=target_group,
                worker_id=worker_id,
                request_id=request_id,
                inputs=tuple(pending_batch),
                timeout_seconds=max(0.001, float(self.timeout_seconds)),
                finalize=True,
                wait_for_result=False,
            )
            self._inflight[(worker_id, request_id)] = (time.monotonic(), False)
            terminal_outputs.extend(list(routing.terminal_outputs))
            # Interleave reply ingress pumping with outbound dispatch to prevent
            # producer-side stalling when credit-based flow control reaches its window.
            terminal_outputs.extend(self.drain_completed_deliveries())
            pending_batch = []
            pending_batch_group = None
            pending_batch_worker = None
            pending_batch_index = 0
            pending_batch_target = None
            pending_batch_trace_id = None

        for index, envelope in enumerate(list(envelopes)):
            target = envelope.target
            if not isinstance(target, str) or not target:
                continue
            target_group, worker_id = self._resolve_dispatch_route(
                target=target,
                source_group=source_group,
            )
            dispatch_input = BoundaryDispatchInput(
                payload=envelope.payload,
                dispatch_group=target_group,
                target=target,
                trace_id=envelope.trace_id,
                reply_to=envelope.reply_to,
                source_group=source_group,
                span_id=envelope.span_id,
            )
            if _is_observability_dispatch_target(target):
                _flush_pending_batch()
                observability_batches.setdefault((target_group, worker_id), []).append(dispatch_input)
                continue
            if (
                pending_batch
                and (pending_batch_group != target_group or pending_batch_worker != worker_id)
            ):
                _flush_pending_batch()
            if not pending_batch:
                pending_batch_group = target_group
                pending_batch_worker = worker_id
                pending_batch_index = index
                pending_batch_target = target
                pending_batch_trace_id = envelope.trace_id if isinstance(envelope.trace_id, str) else None
            pending_batch.append(dispatch_input)
            if len(pending_batch) >= max(1, int(self.stream_batch_max_items)):
                _flush_pending_batch()
        _flush_pending_batch()
        obs_batch_max = max(1, int(self.observability_batch_max_items))
        for (target_group, worker_id), batch_inputs in observability_batches.items():
            for chunk_index, chunk in enumerate(_chunked(batch_inputs, obs_batch_max), start=1):
                request_id = _observability_batch_request_id(
                    target_group=target_group,
                    worker_id=worker_id,
                    batch_size=len(chunk),
                    chunk_index=chunk_index,
                    sequence=self._next_observability_request_seq(),
                )
                routing = self._boundary().execute_boundary_on_leaf(
                    target_group=target_group,
                    worker_id=worker_id,
                    request_id=request_id,
                    inputs=tuple(chunk),
                    timeout_seconds=max(0.001, float(self.timeout_seconds)),
                    # Observability lane is background best-effort: no boundary result
                    # roundtrip and no inflight replay tracking.
                    finalize=False,
                    wait_for_result=False,
                )
                terminal_outputs.extend(list(routing.terminal_outputs))
        terminal_outputs.extend(self.drain_completed_deliveries())
        return terminal_outputs

    def drain_completed_deliveries(
        self,
        *,
        poll_timeout_seconds: float = 0.0,
    ) -> list[object]:
        completed: list[object] = []
        self._pump_reply_ingress(poll_timeout_seconds=poll_timeout_seconds)
        try:
            events = self._state().events()
        except Exception:
            return completed
        start = min(max(0, int(self._event_cursor)), len(events))
        for event in events[start:]:
            if not isinstance(event, ControlPlaneLeafBoundaryResultEvent):
                continue
            key = (event.worker_id, event.request_id)
            if key not in self._inflight:
                continue
            self._inflight.pop(key, None)
            completed.extend(list(event.outputs))
        self._event_cursor = len(events)
        self._expire_stale_inflight()
        return completed

    def has_inflight_deliveries(self) -> bool:
        self._expire_stale_inflight()
        return bool(self._inflight)

    def has_replay_blocking_inflight_deliveries(self) -> bool:
        self._expire_stale_inflight()
        return any(not bool(is_background) for _started_at, is_background in self._inflight.values())

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

    def _resolve_dispatch_route(self, *, target: str, source_group: str | None) -> tuple[str, str]:
        target_id = self._route_table().resolve_route(target=target)
        if isinstance(target_id, str) and target_id:
            target_group = _target_group_from_worker_id(target_id)
            if isinstance(target_group, str) and target_group:
                return (target_group, target_id)
        target_group = self._router().resolve_group_for_target(target=target, source_group=source_group)
        worker_id = f"{target_group}#1"
        self._route_table().upsert_route(target=target, target_id=worker_id)
        return (target_group, worker_id)

    def _boundary(self) -> ControlPlaneRootBoundaryExecutionService:
        candidate = self.root_boundary
        if isinstance(candidate, ControlPlaneRootBoundaryExecutionService):
            return candidate
        if callable(getattr(candidate, "execute_boundary_on_leaf", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ControlPlaneRootBoundaryExecutionService binding is required")

    def _reply_ingress(self) -> ControlPlaneRootReplyIngressService | None:
        candidate = self.reply_ingress
        if isinstance(candidate, ControlPlaneRootReplyIngressService):
            return candidate
        if callable(getattr(candidate, "drain_worker_replies", None)):
            return candidate  # type: ignore[return-value]
        return None

    def _state(self) -> ControlPlaneStateService:
        candidate = self.state
        if isinstance(candidate, ControlPlaneStateService):
            return candidate
        if callable(getattr(candidate, "events", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ControlPlaneStateService binding is required")

    def _pump_reply_ingress(self, *, poll_timeout_seconds: float = 0.0) -> None:
        ingress = self._reply_ingress()
        if ingress is None:
            return
        worker_ids = {worker_id for worker_id, _request_id in self._inflight.keys()}
        for index, worker_id in enumerate(sorted(worker_ids)):
            timeout = max(0.0, float(poll_timeout_seconds)) if index == 0 else 0.0
            ingress.drain_worker_replies(
                worker_id=worker_id,
                timeout_seconds=timeout,
                max_items=256,
            )

    def _expire_stale_inflight(self) -> None:
        if not self._inflight:
            return
        timeout_seconds = max(0.001, float(self.inflight_idle_timeout_seconds))
        now = time.monotonic()
        expired = []
        for key, payload in self._inflight.items():
            started_at = payload[0] if isinstance(payload, tuple) else payload
            if now - float(started_at) >= timeout_seconds:
                expired.append(key)
        if not expired:
            return
        for key in expired:
            self._inflight.pop(key, None)
        try:
            state = self._state()
        except Exception:
            return
        for worker_id, request_id in expired:
            state.append_event(
                {
                    "kind": "control_plane.boundary.inflight_timeout",
                    "worker_id": worker_id,
                    "request_id": request_id,
                    "timeout_seconds": timeout_seconds,
                }
            )

    def _next_stream_request_seq(self) -> int:
        self._stream_request_seq += 1
        return self._stream_request_seq

    def _next_observability_request_seq(self) -> int:
        self._observability_request_seq += 1
        return self._observability_request_seq


def _boundary_request_id(*, envelope: Envelope, index: int, target: str) -> str:
    trace = envelope.trace_id if isinstance(envelope.trace_id, str) and envelope.trace_id else None
    return _boundary_request_id_from_values(
        trace_id=trace,
        index=index,
        target=target,
        sequence=1,
    )


def _boundary_request_id_from_values(
    *,
    trace_id: str | None,
    index: int,
    target: str,
    sequence: int,
) -> str:
    trace = trace_id if isinstance(trace_id, str) and trace_id else "no-trace"
    return f"boundary:{trace}:{max(0, int(index))}:{target}:{max(1, int(sequence))}"


def _boundary_batch_request_id(
    *,
    target_group: str,
    worker_id: str,
    batch_seq: int,
    batch_size: int,
) -> str:
    return (
        f"boundary:batch:{target_group}:{worker_id}:"
        f"{max(1, int(batch_seq))}:{max(1, int(batch_size))}"
    )


def _observability_batch_request_id(
    *,
    target_group: str,
    worker_id: str,
    batch_size: int,
    chunk_index: int,
    sequence: int,
) -> str:
    return (
        f"boundary:obs-batch:{target_group}:{worker_id}:"
        f"{max(1, int(chunk_index))}:{max(1, int(batch_size))}:{max(1, int(sequence))}"
    )


def _chunked(items: list[BoundaryDispatchInput], size: int) -> list[list[BoundaryDispatchInput]]:
    if not items:
        return []
    chunk_size = max(1, int(size))
    return [items[index:index + chunk_size] for index in range(0, len(items), chunk_size)]


def _is_observability_dispatch_target(target: str) -> bool:
    return isinstance(target, str) and target.startswith("system.obs.")


def _target_group_from_worker_id(worker_id: str) -> str | None:
    if not isinstance(worker_id, str) or not worker_id:
        return None
    if "#" not in worker_id:
        return None
    group, _sep, _suffix = worker_id.partition("#")
    if isinstance(group, str) and group:
        return group
    return None


__all__ = [
    "ControlPlaneRootBoundaryHandoffService",
    "DefaultControlPlaneRootBoundaryHandoffService",
]
