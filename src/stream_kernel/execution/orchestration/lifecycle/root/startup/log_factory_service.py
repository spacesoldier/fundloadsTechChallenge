from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.control_plane.root.shutdown_service import (
    ControlPlaneRootShutdownResult,
)
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneSpawnRequestedEvent,
)


@runtime_checkable
class RootLifecycleLogFactory(Protocol):
    def spawn_requested(self, event: ControlPlaneSpawnRequestedEvent) -> LogMessage:
        raise NotImplementedError

    def group_startup_ready(
        self,
        *,
        event: ControlPlaneSpawnRequestedEvent,
        worker_ids: tuple[str, ...],
    ) -> LogMessage:
        raise NotImplementedError

    def group_startup_failed(
        self,
        *,
        event: ControlPlaneSpawnRequestedEvent,
        worker_ids: tuple[str, ...],
        error: Exception,
    ) -> LogMessage:
        raise NotImplementedError

    def runtime_shutdown_started(self, *, total_workers: int) -> LogMessage:
        raise NotImplementedError

    def runtime_shutdown_worker_finished(self, *, result: ControlPlaneRootShutdownResult) -> LogMessage:
        raise NotImplementedError

    def runtime_shutdown_worker_stopping(
        self,
        *,
        target_group: str,
        worker_id: str,
        command_id: str,
        stop_command_timeout_seconds: float,
        graceful_timeout_seconds: float,
        terminate_timeout_seconds: float,
        is_observability_group: bool,
    ) -> LogMessage:
        raise NotImplementedError

    def runtime_shutdown_worker_failed(
        self,
        *,
        target_group: str,
        worker_id: str,
        error: Exception,
    ) -> LogMessage:
        raise NotImplementedError


@service(name="root_lifecycle_log_factory")
@dataclass(slots=True)
class DefaultRootLifecycleLogFactory(RootLifecycleLogFactory):
    level: str = "info"

    def spawn_requested(self, event: ControlPlaneSpawnRequestedEvent) -> LogMessage:
        return LogMessage(
            level=self.level,
            message=f"control-plane lifecycle spawn requested for group '{event.group_name}'",
            fields={
                "event": "control_plane.lifecycle.spawn_requested",
                "process_name": "supervisor",
                "group_name": event.group_name,
                "workers": event.workers,
                "node_count": len(tuple(event.nodes)),
                "nodes": list(event.nodes),
            },
        )

    def group_startup_ready(
        self,
        *,
        event: ControlPlaneSpawnRequestedEvent,
        worker_ids: tuple[str, ...],
    ) -> LogMessage:
        return LogMessage(
            level=self.level,
            message=f"control-plane lifecycle startup ready for group '{event.group_name}'",
            fields={
                "event": "control_plane.lifecycle.group_startup_ready",
                "process_name": "supervisor",
                "group_name": event.group_name,
                "workers": event.workers,
                "worker_ids": list(worker_ids),
            },
        )

    def group_startup_failed(
        self,
        *,
        event: ControlPlaneSpawnRequestedEvent,
        worker_ids: tuple[str, ...],
        error: Exception,
    ) -> LogMessage:
        return LogMessage(
            level="error",
            message=f"control-plane lifecycle startup failed for group '{event.group_name}'",
            fields={
                "event": "control_plane.lifecycle.group_startup_failed",
                "process_name": "supervisor",
                "group_name": event.group_name,
                "workers": event.workers,
                "worker_ids": list(worker_ids),
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )

    def runtime_shutdown_started(self, *, total_workers: int) -> LogMessage:
        return LogMessage(
            level=self.level,
            message="control-plane runtime shutdown started",
            fields={
                "event": "control_plane.lifecycle.runtime_shutdown_started",
                "process_name": "supervisor",
                "total_workers": total_workers,
            },
        )

    def runtime_shutdown_worker_finished(self, *, result: ControlPlaneRootShutdownResult) -> LogMessage:
        status = "stopped" if result.worker_stopped else "not_stopped"
        return LogMessage(
            level=self.level if result.worker_stopped else "warning",
            message=f"control-plane runtime shutdown worker {status}",
            fields={
                "event": "control_plane.lifecycle.runtime_shutdown_worker_finished",
                "process_name": "supervisor",
                "group_name": result.target_group,
                "worker_id": result.worker_id,
                "command_id": result.command_id,
                "fallback_used": result.fallback_used,
                "stop_command_timed_out": result.stop_command_timed_out,
                "worker_stopped": result.worker_stopped,
                "stop_ack_status": result.stop_ack.status if result.stop_ack is not None else None,
            },
        )

    def runtime_shutdown_worker_stopping(
        self,
        *,
        target_group: str,
        worker_id: str,
        command_id: str,
        stop_command_timeout_seconds: float,
        graceful_timeout_seconds: float,
        terminate_timeout_seconds: float,
        is_observability_group: bool,
    ) -> LogMessage:
        return LogMessage(
            level=self.level,
            message="control-plane runtime shutdown worker stopping",
            fields={
                "event": "control_plane.lifecycle.runtime_shutdown_worker_stopping",
                "process_name": "supervisor",
                "group_name": target_group,
                "worker_id": worker_id,
                "command_id": command_id,
                "stop_command_timeout_seconds": float(stop_command_timeout_seconds),
                "graceful_timeout_seconds": float(graceful_timeout_seconds),
                "terminate_timeout_seconds": float(terminate_timeout_seconds),
                "is_observability_group": bool(is_observability_group),
            },
        )

    def runtime_shutdown_worker_failed(
        self,
        *,
        target_group: str,
        worker_id: str,
        error: Exception,
    ) -> LogMessage:
        return LogMessage(
            level="error",
            message="control-plane runtime shutdown worker failed",
            fields={
                "event": "control_plane.lifecycle.runtime_shutdown_worker_failed",
                "process_name": "supervisor",
                "group_name": target_group,
                "worker_id": worker_id,
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )


__all__ = [
    "RootLifecycleLogFactory",
    "DefaultRootLifecycleLogFactory",
]
