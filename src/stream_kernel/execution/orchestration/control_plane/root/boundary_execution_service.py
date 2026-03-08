from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.control_plane.root.leaf_command_service import (
    ControlPlaneRootLeafCommandService,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_DATA,
    ExecutionIpcTransportService,
    compose_execution_ipc_worker_target_id,
)
from stream_kernel.execution.transport.ipc.ipc_lane_routing_service import (
    ExecutionIpcLaneRoutingService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryExecuteCommand,
)
from stream_kernel.routing.router import RoutingResult


class ControlPlaneRootBoundaryExecutionError(RuntimeError):
    pass


class ControlPlaneRootBoundaryExecutionTimeoutError(ControlPlaneRootBoundaryExecutionError):
    pass


class ControlPlaneRootBoundaryExecutionFailedError(ControlPlaneRootBoundaryExecutionError):
    pass


@runtime_checkable
class ControlPlaneRootBoundaryExecutionService(Protocol):
    def execute_boundary_on_leaf(
        self,
        *,
        target_group: str,
        worker_id: str,
        request_id: str,
        inputs: tuple[object, ...],
        timeout_seconds: float,
        finalize: bool = True,
        wait_for_result: bool = True,
    ) -> RoutingResult:
        raise NotImplementedError


@service(name="control_plane_root_boundary_execution_service")
@dataclass(slots=True)
class DefaultControlPlaneRootBoundaryExecutionService(ControlPlaneRootBoundaryExecutionService):
    root_leaf_commands: ControlPlaneRootLeafCommandService = inject.service(ControlPlaneRootLeafCommandService)
    execution_ipc: ExecutionIpcTransportService = inject.service(ExecutionIpcTransportService)
    lane_routing: object | None = inject.service(ExecutionIpcLaneRoutingService)

    def execute_boundary_on_leaf(
        self,
        *,
        target_group: str,
        worker_id: str,
        request_id: str,
        inputs: tuple[object, ...],
        timeout_seconds: float,
        finalize: bool = True,
        wait_for_result: bool = True,
    ) -> RoutingResult:
        request = self._commands().make_boundary_execute_request(
            target_group=target_group,
            worker_id=worker_id,
            request_id=request_id,
            inputs=tuple(inputs),
            finalize=finalize,
        )
        command = ControlPlaneLeafBoundaryExecuteCommand(
            target_group=request.target_group,
            worker_id=request.worker_id,
            request_id=request.request_id,
            inputs=tuple(request.inputs),
            finalize=request.finalize,
        )
        lane = self._resolve_boundary_command_lane(command=command)
        self._ipc().send(
            compose_execution_ipc_worker_target_id(worker_id, lane=lane),
            command,
            no_reply=True,
        )
        if not request.finalize or not wait_for_result:
            return RoutingResult(
                local_deliveries=[],
                boundary_deliveries=[],
                terminal_outputs=[],
            )
        result = self._commands().wait_boundary_result(
            target_group=target_group,
            worker_id=worker_id,
            request_id=request_id,
            timeout_seconds=timeout_seconds,
        )
        if result is None:
            raise ControlPlaneRootBoundaryExecutionTimeoutError(
                f"leaf boundary result timed out for {worker_id} request_id={request_id}"
            )
        if result.status != "completed":
            message = result.error or f"leaf boundary execution failed with status={result.status}"
            raise ControlPlaneRootBoundaryExecutionFailedError(message)
        return RoutingResult(
            local_deliveries=[],
            boundary_deliveries=[],
            terminal_outputs=list(result.outputs),
        )

    def _commands(self) -> ControlPlaneRootLeafCommandService:
        candidate = self.root_leaf_commands
        if isinstance(candidate, ControlPlaneRootLeafCommandService):
            return candidate
        if callable(getattr(candidate, "make_boundary_execute_request", None)) and callable(
            getattr(candidate, "wait_boundary_result", None)
        ):
            return candidate  # type: ignore[return-value]
        raise ValueError("ControlPlaneRootLeafCommandService binding is required")

    def _ipc(self) -> ExecutionIpcTransportService:
        candidate = self.execution_ipc
        if isinstance(candidate, ExecutionIpcTransportService):
            return candidate
        if callable(getattr(candidate, "send", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ExecutionIpcTransportService binding is required")

    def _resolve_boundary_command_lane(self, *, command: ControlPlaneLeafBoundaryExecuteCommand) -> str:
        fallback = EXECUTION_IPC_LANE_DATA
        routing = self._lane_routing_optional()
        if routing is None:
            return fallback
        try:
            return routing.resolve_lane(
                target="system.cp.leaf_boundary_execute",
                payload=command,
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


__all__ = [
    "ControlPlaneRootBoundaryExecutionService",
    "DefaultControlPlaneRootBoundaryExecutionService",
    "ControlPlaneRootBoundaryExecutionError",
    "ControlPlaneRootBoundaryExecutionTimeoutError",
    "ControlPlaneRootBoundaryExecutionFailedError",
]
