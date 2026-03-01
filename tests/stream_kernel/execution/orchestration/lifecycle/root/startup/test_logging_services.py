from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event

from stream_kernel.execution.orchestration.lifecycle.root.startup.console_log_dispatch_service import (
    DefaultRootConsoleLogDispatchService,
)
from stream_kernel.execution.orchestration.lifecycle.root.startup.log_factory_service import (
    DefaultRootLifecycleLogFactory,
)
from stream_kernel.execution.orchestration.control_plane.root.shutdown_service import (
    ControlPlaneRootShutdownResult,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafStopAckEvent,
)
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneSpawnRequestedEvent,
)


@dataclass(slots=True)
class _Sink:
    seen: list[LogMessage] = field(default_factory=list)
    ready: Event = field(default_factory=Event)

    def emit(self, message: LogMessage) -> None:
        self.seen.append(message)
        self.ready.set()

    async def emit_async(self, message: LogMessage) -> None:
        self.emit(message)


def test_root_lifecycle_log_factory_builds_spawn_requested_log_message() -> None:
    factory = DefaultRootLifecycleLogFactory()
    event = ControlPlaneSpawnRequestedEvent(group_name="execution.alpha", workers=2, nodes=("node.a",))

    msg = factory.spawn_requested(event)

    assert msg.level == "info"
    assert "spawn" in msg.message.lower()
    assert msg.fields["group_name"] == "execution.alpha"
    assert msg.fields["workers"] == 2
    assert msg.fields["event"] == "control_plane.lifecycle.spawn_requested"


def test_root_console_log_dispatch_service_uses_async_dispatch_loop_and_drains() -> None:
    sink = _Sink()
    service = DefaultRootConsoleLogDispatchService(
        sink=sink,
        queue_max_items=16,
        drop_policy="block_with_timeout",
    )
    msg = LogMessage(level="info", message="hello")

    accepted = service.publish(msg)
    assert accepted is True
    assert sink.ready.wait(timeout=0.5)
    assert sink.seen == [msg]
    assert service.drain(timeout_seconds=0.5) is True
    service.stop(drain=True, timeout_seconds=0.5)


def test_root_lifecycle_log_factory_builds_shutdown_progress_messages() -> None:
    factory = DefaultRootLifecycleLogFactory()
    result = ControlPlaneRootShutdownResult(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        command_id="stop-1",
        stop_ack=ControlPlaneLeafStopAckEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            command_id="stop-1",
            status="accepted",
        ),
        stop_command_timed_out=False,
        fallback_used=False,
        worker_stopped=True,
    )

    started = factory.runtime_shutdown_started(total_workers=2)
    finished = factory.runtime_shutdown_worker_finished(result=result)
    failed = factory.runtime_shutdown_worker_failed(
        target_group="execution.alpha",
        worker_id="execution.alpha#2",
        error=RuntimeError("oops"),
    )

    assert started.fields["event"] == "control_plane.lifecycle.runtime_shutdown_started"
    assert finished.fields["event"] == "control_plane.lifecycle.runtime_shutdown_worker_finished"
    assert finished.fields["worker_id"] == "execution.alpha#1"
    assert failed.level == "error"
    assert failed.fields["event"] == "control_plane.lifecycle.runtime_shutdown_worker_failed"
