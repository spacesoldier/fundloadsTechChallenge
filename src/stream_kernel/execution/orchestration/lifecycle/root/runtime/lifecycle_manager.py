from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from collections import defaultdict
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.control_plane.root.shutdown_service import (
    ControlPlaneRootShutdownService,
)
from stream_kernel.execution.transport.handoff.ipc_handoff_dispatch_service import (
    ExecutionIpcHandoffDispatchService,
)
from stream_kernel.execution.orchestration.lifecycle.root.startup.console_log_dispatch_service import (
    RootConsoleLogDispatchService,
)
from stream_kernel.execution.orchestration.lifecycle.root.startup.log_factory_service import (
    RootLifecycleLogFactory,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafStopCommand,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneStateService,
)
from stream_kernel.routing.envelope import Envelope
from stream_kernel.platform.services.runtime.lifecycle import RuntimeLifecycleManager


@runtime_checkable
class RootRuntimeLifecycleSpawnIndex(Protocol):
    def spawned_workers(self) -> list[tuple[str, str]]:
        raise NotImplementedError


@service(name="runtime_lifecycle_control_plane_root")
@dataclass(slots=True)
class ControlPlaneRootRuntimeLifecycleManager(RuntimeLifecycleManager):
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)
    root_shutdown: ControlPlaneRootShutdownService = inject.service(ControlPlaneRootShutdownService)
    log_factory: RootLifecycleLogFactory = inject.service(RootLifecycleLogFactory)
    console_dispatch: RootConsoleLogDispatchService = inject.service(RootConsoleLogDispatchService)
    handoff_dispatch: ExecutionIpcHandoffDispatchService = inject.service(ExecutionIpcHandoffDispatchService)
    stop_command_timeout_seconds: float = 0.2
    observability_group_name: str = "system.observability"
    observability_stop_command_timeout_seconds: float = 5.0
    fallback_terminate_timeout_seconds: float = 1.0
    parallel_shutdown_workers: bool = True

    def configure_shutdown_policy(
        self,
        *,
        observability_group_name: str | None = None,
        observability_stop_command_timeout_seconds: float | None = None,
        stop_command_timeout_seconds: float | None = None,
        fallback_graceful_timeout_seconds: float | None = None,
    ) -> None:
        if isinstance(observability_group_name, str) and observability_group_name:
            self.observability_group_name = observability_group_name
        if (
            isinstance(observability_stop_command_timeout_seconds, (int, float))
            and float(observability_stop_command_timeout_seconds) > 0
        ):
            self.observability_stop_command_timeout_seconds = float(observability_stop_command_timeout_seconds)
        if isinstance(stop_command_timeout_seconds, (int, float)) and float(stop_command_timeout_seconds) > 0:
            self.stop_command_timeout_seconds = float(stop_command_timeout_seconds)
        if (
            isinstance(fallback_graceful_timeout_seconds, (int, float))
            and float(fallback_graceful_timeout_seconds) > 0
        ):
            root_shutdown = self._root_shutdown_optional()
            if root_shutdown is not None:
                try:
                    setattr(
                        root_shutdown,
                        "fallback_graceful_timeout_seconds",
                        float(fallback_graceful_timeout_seconds),
                    )
                except Exception:
                    pass

    def start(self) -> None:
        return None

    def ready(self, timeout_seconds: int) -> bool:
        _ = timeout_seconds
        return True

    def stop(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
        _ = drain_inflight
        graceful = max(0.0, float(graceful_timeout_seconds))
        stop_timeout_default = max(0.001, float(self.stop_command_timeout_seconds))
        stop_timeout = min(graceful, stop_timeout_default) if graceful > 0 else stop_timeout_default
        terminate_timeout = max(0.0, float(self.fallback_terminate_timeout_seconds))
        spawned = list(reversed(self._spawned_workers()))
        regular_workers, observability_workers = self._partition_shutdown_workers(spawned)
        factory = self._log_factory_optional()
        if factory is not None:
            self._publish_log_safely(factory.runtime_shutdown_started(total_workers=len(spawned)))
        if self.parallel_shutdown_workers and len(regular_workers) > 1:
            pre_dispatched_workers = self._dispatch_stop_commands(
                spawned=regular_workers,
                include_observability=False,
            )
            self._stop_workers_parallel(
                spawned=regular_workers,
                stop_timeout=stop_timeout,
                graceful=graceful,
                terminate_timeout=terminate_timeout,
                factory=factory,
                pre_dispatched_workers=pre_dispatched_workers,
            )
        else:
            pre_dispatched_workers = self._dispatch_stop_commands(
                spawned=regular_workers,
                include_observability=False,
            )
            self._stop_workers_sequential(
                spawned=regular_workers,
                stop_timeout=stop_timeout,
                graceful=graceful,
                terminate_timeout=terminate_timeout,
                factory=factory,
                pre_dispatched_workers=pre_dispatched_workers,
            )
        if observability_workers:
            pre_dispatched_obs_workers = self._dispatch_stop_commands(
                spawned=observability_workers,
                include_observability=True,
            )
            self._stop_workers_sequential(
                spawned=observability_workers,
                stop_timeout=stop_timeout,
                graceful=graceful,
                terminate_timeout=terminate_timeout,
                factory=factory,
                pre_dispatched_workers=pre_dispatched_obs_workers,
            )
        self._flush_console_logs()

    def _stop_workers_sequential(
        self,
        *,
        spawned: list[tuple[str, str]],
        stop_timeout: float,
        graceful: float,
        terminate_timeout: float,
        factory: RootLifecycleLogFactory | None,
        pre_dispatched_workers: set[str] | None = None,
    ) -> None:
        for group_name, worker_id in spawned:
            worker_graceful = self._graceful_timeout_for_worker(
                group_name=group_name,
                worker_id=worker_id,
                default_graceful_timeout_seconds=graceful,
            )
            command_id = f"runtime-stop:{worker_id}"
            worker_stop_timeout = self._stop_timeout_for_group(
                group_name=group_name,
                worker_id=worker_id,
                default_stop_timeout_seconds=stop_timeout,
                graceful_timeout_seconds=worker_graceful,
            )
            self._publish_shutdown_worker_stopping(
                factory=factory,
                target_group=group_name,
                worker_id=worker_id,
                command_id=command_id,
                stop_command_timeout_seconds=worker_stop_timeout,
                graceful_timeout_seconds=worker_graceful,
                terminate_timeout_seconds=terminate_timeout,
            )
            try:
                result = self._root_shutdown().shutdown_leaf(
                    target_group=group_name,
                    worker_id=worker_id,
                    command_id=command_id,
                    stop_command_timeout_seconds=worker_stop_timeout,
                    graceful_timeout_seconds=worker_graceful,
                    terminate_timeout_seconds=terminate_timeout,
                    reason="runtime_lifecycle.stop",
                    # Broadcast is best-effort warm-up. Always send a direct stop command per worker
                    # so ack waiting cannot depend on optimistic pre-dispatch delivery.
                    dispatch_command=True,
                )
                if factory is not None:
                    self._publish_log_safely(factory.runtime_shutdown_worker_finished(result=result))
            except Exception as exc:
                if factory is not None:
                    self._publish_log_safely(
                        factory.runtime_shutdown_worker_failed(
                            target_group=group_name,
                            worker_id=worker_id,
                            error=exc,
                        )
                    )

    def _stop_workers_parallel(
        self,
        *,
        spawned: list[tuple[str, str]],
        stop_timeout: float,
        graceful: float,
        terminate_timeout: float,
        factory: RootLifecycleLogFactory | None,
        pre_dispatched_workers: set[str] | None = None,
    ) -> None:
        futures: list[tuple[str, str, Future[object]]] = []
        max_workers = max(1, len(spawned))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="sk-root-stop") as pool:
            for group_name, worker_id in spawned:
                worker_graceful = self._graceful_timeout_for_worker(
                    group_name=group_name,
                    worker_id=worker_id,
                    default_graceful_timeout_seconds=graceful,
                )
                command_id = f"runtime-stop:{worker_id}"
                worker_stop_timeout = self._stop_timeout_for_group(
                    group_name=group_name,
                    worker_id=worker_id,
                    default_stop_timeout_seconds=stop_timeout,
                    graceful_timeout_seconds=worker_graceful,
                )
                self._publish_shutdown_worker_stopping(
                    factory=factory,
                    target_group=group_name,
                    worker_id=worker_id,
                    command_id=command_id,
                    stop_command_timeout_seconds=worker_stop_timeout,
                    graceful_timeout_seconds=worker_graceful,
                    terminate_timeout_seconds=terminate_timeout,
                )
                future = pool.submit(
                    self._root_shutdown().shutdown_leaf,
                    target_group=group_name,
                    worker_id=worker_id,
                    command_id=command_id,
                    stop_command_timeout_seconds=worker_stop_timeout,
                    graceful_timeout_seconds=worker_graceful,
                    terminate_timeout_seconds=terminate_timeout,
                    reason="runtime_lifecycle.stop",
                    # Broadcast is best-effort warm-up. Always send a direct stop command per worker
                    # so ack waiting cannot depend on optimistic pre-dispatch delivery.
                    dispatch_command=True,
                )
                futures.append((group_name, worker_id, future))
            for group_name, worker_id, future in futures:
                try:
                    result = future.result()
                    if factory is not None:
                        self._publish_log_safely(factory.runtime_shutdown_worker_finished(result=result))
                except Exception as exc:
                    if factory is not None:
                        self._publish_log_safely(
                            factory.runtime_shutdown_worker_failed(
                                target_group=group_name,
                                worker_id=worker_id,
                                error=exc,
                            )
                        )

    def _spawned_workers(self) -> list[tuple[str, str]]:
        seen: set[str] = set()
        pairs: list[tuple[str, str]] = []
        for event in self._state().events():
            if not isinstance(event, dict):
                continue
            if event.get("kind") != "control_plane.lifecycle.worker_spawned":
                continue
            group_name = event.get("group_name")
            worker_id = event.get("worker_id")
            if not isinstance(group_name, str) or not group_name:
                continue
            if not isinstance(worker_id, str) or not worker_id:
                continue
            if worker_id in seen:
                continue
            seen.add(worker_id)
            pairs.append((group_name, worker_id))
        return pairs

    def _partition_shutdown_workers(
        self,
        spawned: list[tuple[str, str]],
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        regular: list[tuple[str, str]] = []
        observability: list[tuple[str, str]] = []
        obs_group_name = self.observability_group_name if isinstance(self.observability_group_name, str) else ""
        for group_name, worker_id in spawned:
            if obs_group_name and group_name == obs_group_name:
                observability.append((group_name, worker_id))
                continue
            regular.append((group_name, worker_id))
        return (regular, observability)

    def _state(self) -> ControlPlaneStateService:
        candidate = self.state
        if isinstance(candidate, ControlPlaneStateService):
            return candidate
        if callable(getattr(candidate, "events", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ControlPlaneStateService binding is required")

    def _handoff_dispatch_optional(self) -> ExecutionIpcHandoffDispatchService | None:
        candidate = self.handoff_dispatch
        if isinstance(candidate, ExecutionIpcHandoffDispatchService):
            return candidate
        if callable(getattr(candidate, "dispatch_broadcast", None)):
            return candidate  # type: ignore[return-value]
        return None

    def _dispatch_stop_commands(
        self,
        *,
        spawned: list[tuple[str, str]],
        include_observability: bool,
    ) -> set[str] | None:
        dispatch = self._handoff_dispatch_optional()
        if dispatch is None:
            return None
        workers_by_group: dict[str, set[str]] = defaultdict(set)
        for group_name, worker_id in spawned:
            workers_by_group[group_name].add(worker_id)
        if not workers_by_group:
            return set()
        accepted: set[str] = set()
        for group_name in sorted(workers_by_group.keys()):
            group_workers = workers_by_group[group_name]
            command = ControlPlaneLeafStopCommand(
                target_group=group_name,
                worker_id="__broadcast__",
                command_id="runtime-stop:{worker_id}",
                reason="runtime_lifecycle.stop",
            )
            envelope = Envelope(payload=command, target="system.cp.leaf_stop")
            try:
                result = dispatch.dispatch_broadcast(
                    envelope,
                    target_group=group_name,
                    include_observability=include_observability,
                    policy="best_effort",
                )
            except Exception:
                continue
            failed = set(result.failed_workers)
            accepted.update(worker_id for worker_id in group_workers if worker_id not in failed)
        return accepted

    def _root_shutdown(self) -> ControlPlaneRootShutdownService:
        candidate = self.root_shutdown
        if isinstance(candidate, ControlPlaneRootShutdownService):
            return candidate
        if callable(getattr(candidate, "shutdown_leaf", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ControlPlaneRootShutdownService binding is required")

    def _root_shutdown_optional(self) -> object | None:
        candidate = self.root_shutdown
        if isinstance(candidate, ControlPlaneRootShutdownService):
            return candidate
        if callable(getattr(candidate, "shutdown_leaf", None)):
            return candidate
        return None

    def _log_factory_optional(self) -> RootLifecycleLogFactory | None:
        candidate = self.log_factory
        if isinstance(candidate, RootLifecycleLogFactory):
            return candidate
        if callable(getattr(candidate, "runtime_shutdown_started", None)):
            return candidate  # type: ignore[return-value]
        return None

    def _console_dispatch(self) -> RootConsoleLogDispatchService | None:
        candidate = self.console_dispatch
        if isinstance(candidate, RootConsoleLogDispatchService):
            return candidate
        if callable(getattr(candidate, "publish", None)):
            return candidate  # type: ignore[return-value]
        return None

    def _publish_log_safely(self, message: object) -> None:
        dispatch = self._console_dispatch()
        if dispatch is None:
            return
        publish = getattr(dispatch, "publish", None)
        if not callable(publish):
            return
        try:
            publish(message)
        except Exception:
            return

    def _publish_shutdown_worker_stopping(
        self,
        *,
        factory: RootLifecycleLogFactory | None,
        target_group: str,
        worker_id: str,
        command_id: str,
        stop_command_timeout_seconds: float,
        graceful_timeout_seconds: float,
        terminate_timeout_seconds: float,
    ) -> None:
        if factory is None:
            return
        maker = getattr(factory, "runtime_shutdown_worker_stopping", None)
        if not callable(maker):
            return
        try:
            self._publish_log_safely(
                maker(
                    target_group=target_group,
                    worker_id=worker_id,
                    command_id=command_id,
                    stop_command_timeout_seconds=stop_command_timeout_seconds,
                    graceful_timeout_seconds=graceful_timeout_seconds,
                    terminate_timeout_seconds=terminate_timeout_seconds,
                    is_observability_group=(
                        isinstance(self.observability_group_name, str)
                        and bool(self.observability_group_name)
                        and target_group == self.observability_group_name
                    ),
                )
            )
        except Exception:
            return

    def _stop_timeout_for_group(
        self,
        *,
        group_name: str,
        worker_id: str,
        default_stop_timeout_seconds: float,
        graceful_timeout_seconds: float,
    ) -> float:
        timeout = max(0.001, float(default_stop_timeout_seconds))
        if (
            isinstance(group_name, str)
            and group_name
            and isinstance(self.observability_group_name, str)
            and group_name == self.observability_group_name
            and self._worker_applied_config(worker_id)
        ):
            timeout = max(timeout, max(0.001, float(self.observability_stop_command_timeout_seconds)))
        if graceful_timeout_seconds > 0:
            timeout = min(timeout, float(graceful_timeout_seconds))
        return timeout

    def _worker_applied_config(self, worker_id: str) -> bool:
        if not isinstance(worker_id, str) or not worker_id:
            return False
        for event in reversed(self._state().events()):
            if isinstance(event, ControlPlaneLeafConfigAckEvent):
                if event.worker_id != worker_id:
                    continue
                return event.status == "applied"
            if isinstance(event, dict):
                if event.get("kind") != "control_plane.lifecycle.worker_config_ack":
                    continue
                event_worker = event.get("worker_id")
                status = event.get("status")
                if event_worker != worker_id:
                    continue
                return status == "applied"
        return False

    def _graceful_timeout_for_worker(
        self,
        *,
        group_name: str,
        worker_id: str,
        default_graceful_timeout_seconds: float,
    ) -> float:
        graceful = max(0.0, float(default_graceful_timeout_seconds))
        if graceful <= 0:
            return graceful
        if (
            isinstance(group_name, str)
            and group_name
            and isinstance(self.observability_group_name, str)
            and group_name == self.observability_group_name
            and not self._worker_applied_config(worker_id)
        ):
            return min(graceful, 1.0)
        return graceful

    def _flush_console_logs(self) -> None:
        dispatch = self._console_dispatch()
        if dispatch is None:
            return
        try:
            drain = getattr(dispatch, "drain", None)
            if callable(drain):
                drain(timeout_seconds=0.1)
        except Exception:
            pass
        try:
            stop = getattr(dispatch, "stop", None)
            if callable(stop):
                stop(drain=True, timeout_seconds=0.1)
        except Exception:
            pass


__all__ = [
    "ControlPlaneRootRuntimeLifecycleManager",
]
