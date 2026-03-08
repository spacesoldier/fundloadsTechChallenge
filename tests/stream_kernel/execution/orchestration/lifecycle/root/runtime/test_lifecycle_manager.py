from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafStopCommand,
)


@dataclass(slots=True)
class _State:
    _events: list[object]

    def events(self) -> list[object]:
        return list(self._events)


@dataclass(slots=True)
class _RootShutdown:
    calls: list[dict[str, object]] = field(default_factory=list)
    result: object = None
    error_by_worker: dict[str, Exception] = field(default_factory=dict)
    fallback_graceful_timeout_seconds: float = 0.0

    def shutdown_leaf(self, **kwargs: object):
        self.calls.append(dict(kwargs))
        worker_id = kwargs.get("worker_id")
        if isinstance(worker_id, str) and worker_id in self.error_by_worker:
            raise self.error_by_worker[worker_id]
        return self.result


@dataclass(slots=True)
class _ConsoleDispatch:
    seen: list[LogMessage] = field(default_factory=list)
    drained: int = 0
    stopped: int = 0

    def publish(self, message: LogMessage) -> bool:
        self.seen.append(message)
        return True

    def drain(self, *, timeout_seconds: float = 1.0) -> bool:
        _ = timeout_seconds
        self.drained += 1
        return True

    def stop(self, *, drain: bool = True, timeout_seconds: float = 1.0) -> None:
        _ = (drain, timeout_seconds)
        self.stopped += 1


@dataclass(slots=True)
class _LogFactory:
    def runtime_shutdown_started(self, *, total_workers: int) -> LogMessage:
        return LogMessage(level="info", message="shutdown started", fields={"total_workers": total_workers})

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
            level="info",
            message="shutdown worker stopping",
            fields={
                "group_name": target_group,
                "worker_id": worker_id,
                "command_id": command_id,
                "stop_command_timeout_seconds": stop_command_timeout_seconds,
                "graceful_timeout_seconds": graceful_timeout_seconds,
                "terminate_timeout_seconds": terminate_timeout_seconds,
                "is_observability_group": is_observability_group,
            },
        )

    def runtime_shutdown_worker_finished(self, *, result: object) -> LogMessage:
        return LogMessage(level="info", message="shutdown worker finished", fields={"result": str(result)})

    def runtime_shutdown_worker_failed(self, *, target_group: str, worker_id: str, error: Exception) -> LogMessage:
        return LogMessage(
            level="error",
            message="shutdown worker failed",
            fields={"group_name": target_group, "worker_id": worker_id, "error": str(error)},
        )


@dataclass(slots=True)
class _HandoffDispatch:
    calls: list[dict[str, object]] = field(default_factory=list)
    failed_by_group: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def dispatch_broadcast(
        self,
        envelope,
        *,
        target_group: str | None = None,
        include_observability: bool = False,
        policy: str = "best_effort",
        broadcast_id: str | None = None,
    ):
        _ = (broadcast_id, include_observability, policy)
        payload = envelope.payload
        self.calls.append(
            {
                "target_group": target_group,
                "target": envelope.target,
                "payload": payload,
            }
        )

        class _Result:
            def __init__(self, failed_workers: tuple[str, ...]):
                self.failed_workers = failed_workers

        failed = self.failed_by_group.get(str(target_group), ())
        return _Result(failed_workers=failed)


def test_control_plane_root_runtime_lifecycle_manager_stops_spawned_workers_via_root_shutdown() -> None:
    from stream_kernel.execution.orchestration.lifecycle.root.runtime.lifecycle_manager import (
        ControlPlaneRootRuntimeLifecycleManager,
    )

    state = _State(
        _events=[
            {"kind": "unrelated"},
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.alpha",
                "worker_id": "execution.alpha#1",
            },
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.beta",
                "worker_id": "execution.beta#1",
            },
            # duplicate should not trigger second shutdown
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.alpha",
                "worker_id": "execution.alpha#1",
            },
        ]
    )
    shutdown = _RootShutdown()
    console = _ConsoleDispatch()
    manager = ControlPlaneRootRuntimeLifecycleManager(
        state=state,
        root_shutdown=shutdown,
        log_factory=_LogFactory(),
        console_dispatch=console,
        fallback_terminate_timeout_seconds=1.25,
    )

    manager.start()
    assert manager.ready(1) is True
    manager.stop(graceful_timeout_seconds=3, drain_inflight=True)

    # de-duplicated by worker_id (execution order may vary with parallel shutdown)
    assert sorted(call["worker_id"] for call in shutdown.calls) == ["execution.alpha#1", "execution.beta#1"]
    assert all(call["stop_command_timeout_seconds"] == 0.2 for call in shutdown.calls)
    assert all(call["graceful_timeout_seconds"] == 3 for call in shutdown.calls)
    assert all(call["terminate_timeout_seconds"] == 1.25 for call in shutdown.calls)
    assert len(console.seen) == 5
    assert console.seen[0].message == "shutdown started"
    assert {msg.message for msg in console.seen} >= {"shutdown worker stopping", "shutdown worker finished"}
    assert console.drained == 1
    assert console.stopped == 1


def test_control_plane_root_runtime_lifecycle_manager_logs_shutdown_errors_and_continues() -> None:
    from stream_kernel.execution.orchestration.lifecycle.root.runtime.lifecycle_manager import (
        ControlPlaneRootRuntimeLifecycleManager,
    )

    state = _State(
        _events=[
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.alpha",
                "worker_id": "execution.alpha#1",
            },
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.beta",
                "worker_id": "execution.beta#1",
            },
        ]
    )
    shutdown = _RootShutdown(error_by_worker={"execution.beta#1": RuntimeError("fail beta")})
    console = _ConsoleDispatch()
    manager = ControlPlaneRootRuntimeLifecycleManager(
        state=state,
        root_shutdown=shutdown,
        log_factory=_LogFactory(),
        console_dispatch=console,
    )

    manager.stop(graceful_timeout_seconds=1, drain_inflight=True)

    # both workers must be attempted even when one fails
    assert sorted(call["worker_id"] for call in shutdown.calls) == ["execution.alpha#1", "execution.beta#1"]
    assert [msg.level for msg in console.seen].count("error") == 1
    assert any(msg.message == "shutdown worker finished" for msg in console.seen)
    assert console.drained == 1
    assert console.stopped == 1


def test_control_plane_root_runtime_lifecycle_manager_caps_stop_timeout_by_graceful_window() -> None:
    from stream_kernel.execution.orchestration.lifecycle.root.runtime.lifecycle_manager import (
        ControlPlaneRootRuntimeLifecycleManager,
    )

    state = _State(
        _events=[
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.alpha",
                "worker_id": "execution.alpha#1",
            },
        ]
    )
    shutdown = _RootShutdown()
    manager = ControlPlaneRootRuntimeLifecycleManager(
        state=state,
        root_shutdown=shutdown,
        log_factory=_LogFactory(),
        console_dispatch=_ConsoleDispatch(),
        stop_command_timeout_seconds=5.0,
    )

    manager.stop(graceful_timeout_seconds=2, drain_inflight=True)

    assert shutdown.calls[0]["stop_command_timeout_seconds"] == 2


def test_control_plane_root_runtime_lifecycle_manager_uses_observability_stop_timeout() -> None:
    from stream_kernel.execution.orchestration.lifecycle.root.runtime.lifecycle_manager import (
        ControlPlaneRootRuntimeLifecycleManager,
    )

    state = _State(
        _events=[
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "system.observability",
                "worker_id": "system.observability#1",
            },
            ControlPlaneLeafConfigAckEvent(
                target_group="system.observability",
                worker_id="system.observability#1",
                config_id="cfg-obs-1",
                status="applied",
                resolved_nodes=("system.obs.trace_dispatch",),
            ),
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.alpha",
                "worker_id": "execution.alpha#1",
            },
        ]
    )
    shutdown = _RootShutdown()
    manager = ControlPlaneRootRuntimeLifecycleManager(
        state=state,
        root_shutdown=shutdown,
        log_factory=_LogFactory(),
        console_dispatch=_ConsoleDispatch(),
        stop_command_timeout_seconds=0.2,
        observability_group_name="system.observability",
        observability_stop_command_timeout_seconds=5.0,
    )

    manager.stop(graceful_timeout_seconds=10, drain_inflight=True)

    by_group = {call["target_group"]: call["stop_command_timeout_seconds"] for call in shutdown.calls}
    assert by_group["execution.alpha"] == 0.2
    assert by_group["system.observability"] == 5.0
    by_group_graceful = {call["target_group"]: call["graceful_timeout_seconds"] for call in shutdown.calls}
    assert by_group_graceful["execution.alpha"] == 10
    assert by_group_graceful["system.observability"] == 10


def test_control_plane_root_runtime_lifecycle_manager_uses_config_ack_status_not_latest_generic_status() -> None:
    from stream_kernel.execution.orchestration.lifecycle.root.runtime.lifecycle_manager import (
        ControlPlaneRootRuntimeLifecycleManager,
    )

    state = _State(
        _events=[
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "system.observability",
                "worker_id": "system.observability#1",
            },
            ControlPlaneLeafConfigAckEvent(
                target_group="system.observability",
                worker_id="system.observability#1",
                config_id="cfg-obs-1",
                status="applied",
                resolved_nodes=("system.obs.trace_dispatch",),
            ),
            # Generic runtime status event must not downgrade applied-config detection.
            {
                "worker_id": "system.observability#1",
                "status": "completed",
            },
        ]
    )
    shutdown = _RootShutdown()
    manager = ControlPlaneRootRuntimeLifecycleManager(
        state=state,
        root_shutdown=shutdown,
        log_factory=_LogFactory(),
        console_dispatch=_ConsoleDispatch(),
        stop_command_timeout_seconds=0.2,
        observability_group_name="system.observability",
        observability_stop_command_timeout_seconds=5.0,
    )

    manager.stop(graceful_timeout_seconds=10, drain_inflight=True)

    assert shutdown.calls[0]["target_group"] == "system.observability"
    assert shutdown.calls[0]["stop_command_timeout_seconds"] == 5.0
    assert shutdown.calls[0]["graceful_timeout_seconds"] == 10


def test_control_plane_root_runtime_lifecycle_manager_keeps_default_timeout_for_unready_observability_worker() -> None:
    from stream_kernel.execution.orchestration.lifecycle.root.runtime.lifecycle_manager import (
        ControlPlaneRootRuntimeLifecycleManager,
    )

    state = _State(
        _events=[
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "system.observability",
                "worker_id": "system.observability#1",
            },
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.alpha",
                "worker_id": "execution.alpha#1",
            },
        ]
    )
    shutdown = _RootShutdown()
    manager = ControlPlaneRootRuntimeLifecycleManager(
        state=state,
        root_shutdown=shutdown,
        log_factory=_LogFactory(),
        console_dispatch=_ConsoleDispatch(),
        stop_command_timeout_seconds=0.2,
        observability_group_name="system.observability",
        observability_stop_command_timeout_seconds=5.0,
    )

    manager.stop(graceful_timeout_seconds=10, drain_inflight=True)

    by_group = {call["target_group"]: call["stop_command_timeout_seconds"] for call in shutdown.calls}
    assert by_group["execution.alpha"] == 0.2
    assert by_group["system.observability"] == 0.2
    by_group_graceful = {call["target_group"]: call["graceful_timeout_seconds"] for call in shutdown.calls}
    assert by_group_graceful["execution.alpha"] == 10
    assert by_group_graceful["system.observability"] == 1.0


def test_control_plane_root_runtime_lifecycle_manager_configures_stop_and_fallback_timeouts() -> None:
    from stream_kernel.execution.orchestration.lifecycle.root.runtime.lifecycle_manager import (
        ControlPlaneRootRuntimeLifecycleManager,
    )

    manager = ControlPlaneRootRuntimeLifecycleManager(
        state=_State(_events=[]),
        root_shutdown=_RootShutdown(),
        log_factory=_LogFactory(),
        console_dispatch=_ConsoleDispatch(),
    )

    manager.configure_shutdown_policy(
        stop_command_timeout_seconds=1.5,
        fallback_graceful_timeout_seconds=2.0,
    )

    assert manager.stop_command_timeout_seconds == 1.5
    assert manager.root_shutdown.fallback_graceful_timeout_seconds == 2.0


def test_control_plane_root_runtime_lifecycle_manager_stops_observability_group_last() -> None:
    from stream_kernel.execution.orchestration.lifecycle.root.runtime.lifecycle_manager import (
        ControlPlaneRootRuntimeLifecycleManager,
    )

    state = _State(
        _events=[
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.ingress",
                "worker_id": "execution.ingress#1",
            },
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "system.observability",
                "worker_id": "system.observability#1",
            },
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.egress",
                "worker_id": "execution.egress#1",
            },
            ControlPlaneLeafConfigAckEvent(
                target_group="system.observability",
                worker_id="system.observability#1",
                config_id="cfg-obs-1",
                status="applied",
                resolved_nodes=("system.obs.trace_dispatch",),
            ),
        ]
    )
    shutdown = _RootShutdown()
    manager = ControlPlaneRootRuntimeLifecycleManager(
        state=state,
        root_shutdown=shutdown,
        log_factory=_LogFactory(),
        console_dispatch=_ConsoleDispatch(),
        parallel_shutdown_workers=True,
    )

    manager.stop(graceful_timeout_seconds=10, drain_inflight=True)

    assert shutdown.calls
    assert shutdown.calls[-1]["target_group"] == "system.observability"


def test_control_plane_root_runtime_lifecycle_manager_uses_broadcast_stop_dispatch_and_direct_stop_command() -> None:
    from stream_kernel.execution.orchestration.lifecycle.root.runtime.lifecycle_manager import (
        ControlPlaneRootRuntimeLifecycleManager,
    )

    state = _State(
        _events=[
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.alpha",
                "worker_id": "execution.alpha#1",
            },
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.beta",
                "worker_id": "execution.beta#1",
            },
        ]
    )
    shutdown = _RootShutdown()
    handoff = _HandoffDispatch()
    manager = ControlPlaneRootRuntimeLifecycleManager(
        state=state,
        root_shutdown=shutdown,
        handoff_dispatch=handoff,
        log_factory=_LogFactory(),
        console_dispatch=_ConsoleDispatch(),
    )

    manager.stop(graceful_timeout_seconds=1, drain_inflight=True)

    assert len(handoff.calls) == 2
    assert sorted(call["target_group"] for call in handoff.calls) == ["execution.alpha", "execution.beta"]
    assert all(call["target"] == "system.cp.leaf_stop" for call in handoff.calls)
    assert all(isinstance(call["payload"], ControlPlaneLeafStopCommand) for call in handoff.calls)
    assert all(call["payload"].command_id == "runtime-stop:{worker_id}" for call in handoff.calls)
    assert sorted(call["worker_id"] for call in shutdown.calls) == ["execution.alpha#1", "execution.beta#1"]
    assert all(call["dispatch_command"] is True for call in shutdown.calls)
