from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.lifecycle.root.startup.system_nodes import (
    ControlPlaneGroupStartupWaitNode,
    ControlPlaneLifecycleLogDispatchNode,
    ControlPlaneSpawnDispatchNode,
)
from stream_kernel.observability.events import LogDispatchEvent
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneSpawnRequestedEvent,
)


@dataclass(slots=True)
class _LifecycleService:
    seen: list[ControlPlaneSpawnRequestedEvent] = field(default_factory=list)

    def on_spawn_requested(self, event: ControlPlaneSpawnRequestedEvent) -> None:
        self.seen.append(event)


@dataclass(slots=True)
class _StateService:
    recorded: list[object] = field(default_factory=list)

    def append_event(self, event: object) -> None:
        self.recorded.append(event)

    def events(self) -> list[object]:
        return list(self.recorded)


@dataclass(slots=True)
class _LogFactory:
    seen: list[tuple[str, object]] = field(default_factory=list)

    def spawn_requested(self, event: ControlPlaneSpawnRequestedEvent) -> LogMessage:
        self.seen.append(("spawn_requested", event))
        return LogMessage(level="info", message="spawn requested")

    def group_startup_ready(
        self,
        *,
        event: ControlPlaneSpawnRequestedEvent,
        worker_ids: tuple[str, ...],
    ) -> LogMessage:
        self.seen.append(("group_startup_ready", event, worker_ids))
        return LogMessage(level="info", message="group startup ready")

    def group_startup_failed(
        self,
        *,
        event: ControlPlaneSpawnRequestedEvent,
        worker_ids: tuple[str, ...],
        error: Exception,
    ) -> LogMessage:
        self.seen.append(("group_startup_failed", event, worker_ids, error))
        return LogMessage(level="error", message=f"group startup failed: {error}")


def test_lifecycle_spawn_dispatch_node_delegates_spawn_request_to_service() -> None:
    service = _LifecycleService()
    logs = _LogFactory()
    node = ControlPlaneSpawnDispatchNode(lifecycle=service, log_factory=logs)
    event = ControlPlaneSpawnRequestedEvent(
        group_name="execution.alpha",
        workers=2,
        nodes=("node.a", "node.b"),
    )

    produced = node(event, None)

    assert len(produced) == 1
    assert isinstance(produced[0], LogMessage)
    assert service.seen == [event]
    assert logs.seen and logs.seen[0][0] == "spawn_requested"


def test_lifecycle_group_startup_wait_node_waits_for_config_acks_for_spawned_workers() -> None:
    state = _StateService()
    logs = _LogFactory()
    node = ControlPlaneGroupStartupWaitNode(
        state=state,
        log_factory=logs,
    )
    spawn = ControlPlaneSpawnRequestedEvent(
        group_name="execution.alpha",
        workers=2,
        nodes=("node.a",),
    )
    first_ack = ControlPlaneLeafConfigAckEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        config_id="cfg-1",
        status="applied",
        resolved_nodes=("node.a",),
    )
    second_ack = ControlPlaneLeafConfigAckEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#2",
        config_id="cfg-1",
        status="applied",
        resolved_nodes=("node.a",),
    )

    first = node(spawn, None)
    middle = node(first_ack, None)
    final = node(second_ack, None)

    assert first == []
    assert middle == []
    assert len(final) == 1
    assert isinstance(final[0], LogMessage)
    assert logs.seen and logs.seen[-1][0] == "group_startup_ready"
    assert any(isinstance(item, ControlPlaneSpawnRequestedEvent) for item in state.recorded)
    assert any(isinstance(item, ControlPlaneLeafConfigAckEvent) for item in state.recorded)


def test_lifecycle_group_startup_wait_node_emits_failed_log_on_rejected_ack() -> None:
    state = _StateService()
    logs = _LogFactory()
    node = ControlPlaneGroupStartupWaitNode(
        state=state,
        log_factory=logs,
    )
    spawn = ControlPlaneSpawnRequestedEvent(group_name="execution.alpha", workers=1, nodes=("node.a",))
    rejected_ack = ControlPlaneLeafConfigAckEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        config_id="cfg-1",
        status="rejected",
        resolved_nodes=(),
        error="activation failed",
    )

    first = node(spawn, None)
    second = node(rejected_ack, None)
    third = node(rejected_ack, None)

    assert first == []
    assert len(second) == 1
    assert isinstance(second[0], LogMessage)
    assert second[0].level == "error"
    assert third == []
    assert logs.seen and logs.seen[-1][0] == "group_startup_failed"


def test_lifecycle_log_dispatch_node_delegates_log_message_to_console_service() -> None:
    @dataclass(slots=True)
    class _ConsoleDispatch:
        seen: list[LogMessage] = field(default_factory=list)

        def publish(self, message: LogMessage) -> bool:
            self.seen.append(message)
            return True

    console = _ConsoleDispatch()
    node = ControlPlaneLifecycleLogDispatchNode(console_dispatch=console)
    msg = LogMessage(level="info", message="hello")

    produced = node(msg, None)

    assert len(produced) == 1
    assert isinstance(produced[0], LogDispatchEvent)
    assert produced[0].payload == msg
    assert console.seen == []
