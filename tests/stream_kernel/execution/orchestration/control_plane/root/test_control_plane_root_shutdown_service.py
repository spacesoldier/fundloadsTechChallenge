from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafStopAckEvent,
)


@dataclass(slots=True)
class _RootStop:
    result: object | None = None
    error: Exception | None = None
    calls: list[dict[str, object]] = field(default_factory=list)

    def stop_leaf(self, **kwargs: object):
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        return self.result


@dataclass(slots=True)
class _WorkerLifecycle:
    calls: list[dict[str, object]] = field(default_factory=list)
    result: bool = True

    def stop_worker(self, target_id: str, **kwargs: object) -> bool:
        payload = {"target_id": target_id}
        payload.update(kwargs)
        self.calls.append(payload)
        return self.result


def test_root_shutdown_service_uses_typed_stop_then_joins_process() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.shutdown_service import (
        DefaultControlPlaneRootShutdownService,
    )

    root_stop = _RootStop(
        result=ControlPlaneLeafStopAckEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            command_id="stop-1",
            status="accepted",
        )
    )
    worker_lifecycle = _WorkerLifecycle(result=True)
    service = DefaultControlPlaneRootShutdownService(
        root_stop=root_stop,
        worker_lifecycle=worker_lifecycle,
    )

    result = service.shutdown_leaf(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        command_id="stop-1",
        stop_command_timeout_seconds=0.1,
        graceful_timeout_seconds=0.2,
        terminate_timeout_seconds=0.3,
        reason="shutdown",
    )

    assert result.stop_ack is not None
    assert result.stop_ack.command_id == "stop-1"
    assert result.stop_command_timed_out is False
    assert result.fallback_used is False
    assert result.worker_stopped is True
    assert len(root_stop.calls) == 1
    assert len(worker_lifecycle.calls) == 1
    assert worker_lifecycle.calls[0]["target_id"] == "execution.alpha#1"


def test_root_shutdown_service_falls_back_to_worker_lifecycle_on_typed_stop_timeout() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.shutdown_service import (
        DefaultControlPlaneRootShutdownService,
    )
    from stream_kernel.execution.orchestration.control_plane.root.stop_execution_service import (
        ControlPlaneRootStopExecutionTimeoutError,
    )

    root_stop = _RootStop(
        error=ControlPlaneRootStopExecutionTimeoutError("timed out"),
    )
    worker_lifecycle = _WorkerLifecycle(result=True)
    service = DefaultControlPlaneRootShutdownService(
        root_stop=root_stop,
        worker_lifecycle=worker_lifecycle,
    )

    result = service.shutdown_leaf(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        command_id="stop-timeout",
        stop_command_timeout_seconds=0.01,
        graceful_timeout_seconds=0.02,
        terminate_timeout_seconds=0.03,
    )

    assert result.stop_ack is None
    assert result.stop_command_timed_out is True
    assert result.fallback_used is True
    assert result.worker_stopped is True
    assert len(worker_lifecycle.calls) == 1
    assert worker_lifecycle.calls[0]["graceful_timeout_seconds"] == 0.02
    assert worker_lifecycle.calls[0]["terminate_timeout_seconds"] == 0.03


def test_root_shutdown_service_caps_graceful_join_on_fallback() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.shutdown_service import (
        DefaultControlPlaneRootShutdownService,
    )
    from stream_kernel.execution.orchestration.control_plane.root.stop_execution_service import (
        ControlPlaneRootStopExecutionTimeoutError,
    )

    root_stop = _RootStop(error=ControlPlaneRootStopExecutionTimeoutError("timed out"))
    worker_lifecycle = _WorkerLifecycle(result=True)
    service = DefaultControlPlaneRootShutdownService(
        root_stop=root_stop,
        worker_lifecycle=worker_lifecycle,
        fallback_graceful_timeout_seconds=1.0,
    )

    _ = service.shutdown_leaf(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        command_id="stop-timeout-long-graceful",
        stop_command_timeout_seconds=1.0,
        graceful_timeout_seconds=120.0,
        terminate_timeout_seconds=0.5,
    )

    assert len(worker_lifecycle.calls) == 1
    assert worker_lifecycle.calls[0]["graceful_timeout_seconds"] == 1.0


def test_root_shutdown_service_keeps_graceful_timeout_on_fallback_by_default() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.shutdown_service import (
        DefaultControlPlaneRootShutdownService,
    )
    from stream_kernel.execution.orchestration.control_plane.root.stop_execution_service import (
        ControlPlaneRootStopExecutionTimeoutError,
    )

    root_stop = _RootStop(error=ControlPlaneRootStopExecutionTimeoutError("timed out"))
    worker_lifecycle = _WorkerLifecycle(result=True)
    service = DefaultControlPlaneRootShutdownService(
        root_stop=root_stop,
        worker_lifecycle=worker_lifecycle,
    )

    _ = service.shutdown_leaf(
        target_group="system.observability",
        worker_id="system.observability#1",
        command_id="stop-timeout-no-cap",
        stop_command_timeout_seconds=1.0,
        graceful_timeout_seconds=120.0,
        terminate_timeout_seconds=0.5,
    )

    assert len(worker_lifecycle.calls) == 1
    assert worker_lifecycle.calls[0]["graceful_timeout_seconds"] == 120.0
