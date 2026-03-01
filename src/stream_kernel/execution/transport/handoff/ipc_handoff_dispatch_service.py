from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.transport.ipc.ipc_transport import ExecutionIpcTransportService
from stream_kernel.platform.services.runtime import ProcessGroupRouterService
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
@dataclass(slots=True)
class DefaultExecutionIpcHandoffDispatchService(ExecutionIpcHandoffDispatchService):
    execution_ipc: ExecutionIpcTransportService = inject.service(ExecutionIpcTransportService)
    process_group_router: ProcessGroupRouterService = inject.service(ProcessGroupRouterService)
    route_table: ExecutionIpcRouteTableService = inject.service(ExecutionIpcRouteTableService)

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
        self._ipc().send(target_id, envelope.payload, no_reply=True)
        return True

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


__all__ = [
    "ExecutionIpcHandoffDispatchService",
    "DefaultExecutionIpcHandoffDispatchService",
]
