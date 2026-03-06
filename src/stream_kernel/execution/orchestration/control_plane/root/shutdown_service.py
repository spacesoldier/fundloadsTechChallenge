from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.control_plane.root.stop_execution_service import (
    ControlPlaneRootStopExecutionFailedError,
    ControlPlaneRootStopExecutionService,
    ControlPlaneRootStopExecutionTimeoutError,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafStopAckEvent,
)
from stream_kernel.platform.services.runtime.lifecycle import ExecutionWorkerLifecycleService


@dataclass(frozen=True, slots=True)
class ControlPlaneRootShutdownResult:
    target_group: str
    worker_id: str
    command_id: str
    stop_ack: ControlPlaneLeafStopAckEvent | None
    stop_command_timed_out: bool
    fallback_used: bool
    worker_stopped: bool


@runtime_checkable
class ControlPlaneRootShutdownService(Protocol):
    def shutdown_leaf(
        self,
        *,
        target_group: str,
        worker_id: str,
        command_id: str,
        stop_command_timeout_seconds: float,
        graceful_timeout_seconds: float,
        terminate_timeout_seconds: float,
        reason: str | None = None,
        dispatch_command: bool = True,
    ) -> ControlPlaneRootShutdownResult:
        raise NotImplementedError


@service(name="control_plane_root_shutdown_service")
@dataclass(slots=True)
class DefaultControlPlaneRootShutdownService(ControlPlaneRootShutdownService):
    root_stop: ControlPlaneRootStopExecutionService = inject.service(ControlPlaneRootStopExecutionService)
    worker_lifecycle: ExecutionWorkerLifecycleService = inject.service(ExecutionWorkerLifecycleService)
    fallback_graceful_timeout_seconds: float = 0.0

    def shutdown_leaf(
        self,
        *,
        target_group: str,
        worker_id: str,
        command_id: str,
        stop_command_timeout_seconds: float,
        graceful_timeout_seconds: float,
        terminate_timeout_seconds: float,
        reason: str | None = None,
        dispatch_command: bool = True,
    ) -> ControlPlaneRootShutdownResult:
        ack: ControlPlaneLeafStopAckEvent | None = None
        timed_out = False
        fallback_used = False
        try:
            ack = self._root_stop().stop_leaf(
                target_group=target_group,
                worker_id=worker_id,
                command_id=command_id,
                timeout_seconds=stop_command_timeout_seconds,
                reason=reason,
                dispatch_command=dispatch_command,
            )
        except ControlPlaneRootStopExecutionTimeoutError:
            timed_out = True
            fallback_used = True
        except ControlPlaneRootStopExecutionFailedError:
            fallback_used = True
        effective_graceful_timeout_seconds = float(graceful_timeout_seconds)
        if fallback_used and float(self.fallback_graceful_timeout_seconds) > 0:
            effective_graceful_timeout_seconds = min(
                effective_graceful_timeout_seconds,
                max(0.0, float(self.fallback_graceful_timeout_seconds)),
            )
        worker_stopped = bool(
            self._worker_lifecycle().stop_worker(
                worker_id,
                graceful_timeout_seconds=effective_graceful_timeout_seconds,
                terminate_timeout_seconds=terminate_timeout_seconds,
            )
        )
        return ControlPlaneRootShutdownResult(
            target_group=target_group,
            worker_id=worker_id,
            command_id=command_id,
            stop_ack=ack,
            stop_command_timed_out=timed_out,
            fallback_used=fallback_used,
            worker_stopped=worker_stopped,
        )

    def _root_stop(self) -> ControlPlaneRootStopExecutionService:
        candidate = self.root_stop
        if isinstance(candidate, ControlPlaneRootStopExecutionService):
            return candidate
        if callable(getattr(candidate, "stop_leaf", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ControlPlaneRootStopExecutionService binding is required")

    def _worker_lifecycle(self) -> ExecutionWorkerLifecycleService:
        candidate = self.worker_lifecycle
        if isinstance(candidate, ExecutionWorkerLifecycleService):
            return candidate
        if callable(getattr(candidate, "stop_worker", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ExecutionWorkerLifecycleService binding is required")


__all__ = [
    "ControlPlaneRootShutdownService",
    "DefaultControlPlaneRootShutdownService",
    "ControlPlaneRootShutdownResult",
]
