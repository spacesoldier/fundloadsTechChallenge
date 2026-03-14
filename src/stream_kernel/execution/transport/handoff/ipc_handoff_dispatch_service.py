from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.transport.ipc.ipc_lane_routing_service import (
    ExecutionIpcLaneRoutingService,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    ExecutionIpcTransportService,
    compose_execution_ipc_worker_target_id,
    decompose_execution_ipc_worker_target_id,
    resolve_execution_ipc_lane_for_target,
)
from stream_kernel.platform.services.runtime.debug_buffer import (
    debug_instrument_service_methods,
)
from stream_kernel.routing.envelope import Envelope

from .ipc_route_table_service import (
    ExecutionIpcRouteTableService,
)


@runtime_checkable
class ExecutionIpcHandoffDispatchService(Protocol):
    def dispatch_envelope(
        self,
        envelope: Envelope,
        *,
        source_group: str | None = None,
    ) -> bool:
        raise NotImplementedError


@service(name="execution_ipc_handoff_dispatch_service")
@debug_instrument_service_methods
@dataclass(slots=True)
class DefaultExecutionIpcHandoffDispatchService(ExecutionIpcHandoffDispatchService):
    execution_ipc: ExecutionIpcTransportService = inject.service(ExecutionIpcTransportService)
    route_table: ExecutionIpcRouteTableService = inject.service(ExecutionIpcRouteTableService)
    lane_routing: object | None = inject.service(ExecutionIpcLaneRoutingService)
    runtime_debug_buffer: object | None = None

    def dispatch_envelope(
        self,
        envelope: Envelope,
        *,
        source_group: str | None = None,
    ) -> bool:
        _ = source_group
        target = envelope.target
        if not isinstance(target, str) or not target:
            return False
        target_id = self._route_table().resolve_route(target=target)
        if not isinstance(target_id, str) or not target_id:
            return False
        lane = self._resolve_lane(target=target, payload=envelope.payload)
        resolved_target_id = _lane_target_id(target_id=target_id, lane=lane)
        self._ipc().send(resolved_target_id, envelope.payload, no_reply=True)
        return True

    def _ipc(self) -> ExecutionIpcTransportService:
        candidate = self.execution_ipc
        if isinstance(candidate, ExecutionIpcTransportService):
            return candidate
        if callable(getattr(candidate, "send", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ExecutionIpcTransportService binding is required")

    def _route_table(self) -> ExecutionIpcRouteTableService:
        candidate = self.route_table
        if isinstance(candidate, ExecutionIpcRouteTableService):
            return candidate
        if callable(getattr(candidate, "resolve_route", None)) and callable(
            getattr(candidate, "upsert_route", None)
        ):
            return candidate  # type: ignore[return-value]
        raise ValueError("ExecutionIpcRouteTableService binding is required")

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


def _lane_target_id(*, target_id: str, lane: str) -> str:
    resolved = decompose_execution_ipc_worker_target_id(target_id)
    if resolved is None:
        return target_id
    worker_id, _current_lane = resolved
    return compose_execution_ipc_worker_target_id(worker_id, lane=lane)


__all__ = [
    "ExecutionIpcHandoffDispatchService",
    "DefaultExecutionIpcHandoffDispatchService",
]
