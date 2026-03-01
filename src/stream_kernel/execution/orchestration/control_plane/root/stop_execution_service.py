from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.control_plane.root.leaf_command_service import (
    ControlPlaneRootLeafCommandService,
)
from stream_kernel.execution.transport.ipc.ipc_transport import ExecutionIpcTransportService
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
)


class ControlPlaneRootStopExecutionError(RuntimeError):
    pass


class ControlPlaneRootStopExecutionTimeoutError(ControlPlaneRootStopExecutionError):
    pass


class ControlPlaneRootStopExecutionFailedError(ControlPlaneRootStopExecutionError):
    pass


@runtime_checkable
class ControlPlaneRootStopExecutionService(Protocol):
    def stop_leaf(
        self,
        *,
        target_group: str,
        worker_id: str,
        command_id: str,
        timeout_seconds: float,
        reason: str | None = None,
    ) -> ControlPlaneLeafStopAckEvent:
        raise NotImplementedError


@service(name="control_plane_root_stop_execution_service")
@dataclass(slots=True)
class DefaultControlPlaneRootStopExecutionService(ControlPlaneRootStopExecutionService):
    root_leaf_commands: ControlPlaneRootLeafCommandService = inject.service(ControlPlaneRootLeafCommandService)
    execution_ipc: ExecutionIpcTransportService = inject.service(ExecutionIpcTransportService)

    def stop_leaf(
        self,
        *,
        target_group: str,
        worker_id: str,
        command_id: str,
        timeout_seconds: float,
        reason: str | None = None,
    ) -> ControlPlaneLeafStopAckEvent:
        request = self._commands().make_stop_request(
            target_group=target_group,
            worker_id=worker_id,
            command_id=command_id,
            reason=reason,
        )
        command = ControlPlaneLeafStopCommand(
            target_group=request.target_group,
            worker_id=request.worker_id,
            command_id=request.command_id,
            reason=request.reason,
        )
        self._ipc().send(worker_id, command, no_reply=True)
        ack = self._commands().wait_stop_ack(
            target_group=target_group,
            worker_id=worker_id,
            command_id=command_id,
            timeout_seconds=timeout_seconds,
        )
        if ack is None:
            raise ControlPlaneRootStopExecutionTimeoutError(
                f"leaf stop ack timed out for {worker_id} command_id={command_id}"
            )
        if ack.status not in {"accepted", "completed"}:
            raise ControlPlaneRootStopExecutionFailedError(
                f"leaf stop ack status={ack.status} for {worker_id} command_id={command_id}"
            )
        return ack

    def _commands(self) -> ControlPlaneRootLeafCommandService:
        candidate = self.root_leaf_commands
        if isinstance(candidate, ControlPlaneRootLeafCommandService):
            return candidate
        if callable(getattr(candidate, "make_stop_request", None)) and callable(
            getattr(candidate, "wait_stop_ack", None)
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


__all__ = [
    "ControlPlaneRootStopExecutionService",
    "DefaultControlPlaneRootStopExecutionService",
    "ControlPlaneRootStopExecutionError",
    "ControlPlaneRootStopExecutionTimeoutError",
    "ControlPlaneRootStopExecutionFailedError",
]
