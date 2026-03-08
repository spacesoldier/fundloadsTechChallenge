from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import os
import time
from threading import Event
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.lifecycle.leaf.command.control_ingress_service import (
    LeafControlIngressService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.debug_logging import (
    configure_leaf_debug_logging,
    flush_leaf_debug_logging,
    leaf_debug_log,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    compose_execution_ipc_worker_target_id,
    ExecutionIpcTransportService,
)

if TYPE_CHECKING:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime import (
        LeafWorkerRuntimeSession,
    )

_STARTUP_RETRY_WAIT = Event()


@runtime_checkable
class LeafWorkerControlPlaneService(Protocol):
    # Provides process target callable for lifecycle spawn service and owns leaf-side control-plane bootstrap steps.
    def worker_target(self) -> Callable[..., object]:
        raise NotImplementedError("LeafWorkerControlPlaneService.worker_target must be implemented")


@runtime_checkable
class LeafProcessEntryOrchestrationService(Protocol):
    # Leaf process-entry orchestration contract: startup events + ingress loop handoff.
    def run(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        stop_event: object | None,
        worker_id: str,
        group_name: str,
        runner_profile_requested: str,
        boundary_control_poll_seconds: float,
    ) -> None:
        raise NotImplementedError("LeafProcessEntryOrchestrationService.run must be implemented")


@service(name="leaf_worker_control_plane_service")
@dataclass(slots=True)
class DefaultLeafWorkerControlPlaneService(LeafWorkerControlPlaneService):
    def worker_target(self) -> Callable[..., object]:
        return leaf_worker_process_entry


@service(name="leaf_process_entry_orchestration_service")
@dataclass(slots=True)
class DefaultLeafProcessEntryOrchestrationService(LeafProcessEntryOrchestrationService):
    control_ingress_service: object | None = None
    execution_ipc: object | None = inject.service(ExecutionIpcTransportService)
    _bound_control_workers: set[str] = field(default_factory=set)

    def run(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        stop_event: object | None,
        worker_id: str,
        group_name: str,
        runner_profile_requested: str,
        boundary_control_poll_seconds: float,
    ) -> None:
        runtime = build_leaf_startup_runtime(
            session=session,
            worker_id=worker_id,
            group_name=group_name,
            runner_profile_requested=runner_profile_requested,
        )
        leaf_debug_log(
            event="leaf.orchestrator.run.started",
            worker_id=worker_id,
            group_name=group_name,
            process_role=runtime.get("__process_role"),
        )
        startup_send_timeout_seconds = _resolve_leaf_startup_send_timeout_seconds(runtime)
        ipc = _resolve_execution_ipc_service(session, explicit=self.execution_ipc)
        if ipc is None:
            leaf_debug_log(
                event="leaf.orchestrator.run.no_ipc",
                worker_id=worker_id,
                group_name=group_name,
            )
            _wait_for_stop_event(stop_event)
            return
        if worker_id not in self._bound_control_workers:
            _bind_worker_control_endpoint(ipc=ipc, worker_id=worker_id, control_pipe=control_pipe)
            self._bound_control_workers.add(worker_id)
            leaf_debug_log(
                event="leaf.orchestrator.control_endpoint.bound",
                worker_id=worker_id,
            )
        startup_events = build_leaf_startup_events(runtime=runtime)
        leaf_debug_log(
            event="leaf.orchestrator.startup_events.built",
            worker_id=worker_id,
            event_count=len(startup_events),
            event_types=[type(event).__name__ for event in startup_events],
        )
        for event in startup_events:
            if not _send_startup_event_with_retry(
                ipc=ipc,
                worker_id=worker_id,
                event=event,
                stop_event=stop_event,
                timeout_seconds=startup_send_timeout_seconds,
            ):
                leaf_debug_log(
                    event="leaf.orchestrator.startup_event.send_failed",
                    worker_id=worker_id,
                    startup_event=type(event).__name__,
                )
                return
            leaf_debug_log(
                event="leaf.orchestrator.startup_event.sent",
                worker_id=worker_id,
                startup_event=type(event).__name__,
            )
        ingress = _resolve_ingress_contract(self.control_ingress_service)
        if ingress is None:
            ingress = resolve_leaf_control_ingress_service(session)
        if ingress is None:
            leaf_debug_log(
                event="leaf.orchestrator.run.no_ingress",
                worker_id=worker_id,
                group_name=group_name,
            )
            _wait_for_stop_event(stop_event)
            return
        try:
            leaf_debug_log(
                event="leaf.orchestrator.ingress_loop.start",
                worker_id=worker_id,
                poll_interval_seconds=max(0.0, float(boundary_control_poll_seconds)),
            )
            ingress.run_until_stopped(
                session=session,
                control_pipe=control_pipe,
                stop_event=stop_event,
                poll_interval_seconds=max(0.0, float(boundary_control_poll_seconds)),
            )
            leaf_debug_log(
                event="leaf.orchestrator.ingress_loop.finished",
                worker_id=worker_id,
            )
        except Exception:
            leaf_debug_log(
                event="leaf.orchestrator.ingress_loop.error",
                worker_id=worker_id,
            )
            try:
                flush_leaf_debug_logging()
            except Exception:
                pass
            return


@dataclass(slots=True)
class _NoopLeafControlIngressService(LeafControlIngressService):
    def run_until_stopped(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        stop_event: object | None,
        poll_interval_seconds: float,
    ) -> str:
        _ = (session, control_pipe, poll_interval_seconds)
        _wait_for_stop_event(stop_event)
        return "stop_event"


def leaf_worker_process_entry(
    stop_event: object | None,
    control_pipe: object | None,
    bundle: object,
    worker_id: str,
    group_name: str,
    runner_profile_requested: str,
    boundary_control_poll_seconds: float,
    pipe_codec_mode: str,
) -> None:
    _ = (boundary_control_poll_seconds, pipe_codec_mode)
    os.environ["STREAM_KERNEL_PROCESS_GROUP"] = group_name
    os.environ["STREAM_KERNEL_WORKER_ID"] = worker_id
    initial_runtime = getattr(bundle, "runtime", None)
    configure_leaf_debug_logging(
        runtime=initial_runtime if isinstance(initial_runtime, dict) else None,
        group_name=group_name,
        worker_id=worker_id,
    )
    leaf_debug_log(
        event="leaf.process.entry.started",
        group_name=group_name,
        worker_id=worker_id,
        runner_profile_requested=runner_profile_requested,
        boundary_control_poll_seconds=boundary_control_poll_seconds,
        pipe_codec_mode=pipe_codec_mode,
    )
    session = bootstrap_leaf_worker_runtime_from_bundle(
        bundle=bundle,
        worker_id=worker_id,
        group_name=group_name,
        runner_profile_requested=runner_profile_requested,
    )
    leaf_runtime = build_leaf_startup_runtime(
        session=session,
        worker_id=worker_id,
        group_name=group_name,
        runner_profile_requested=runner_profile_requested,
    )
    configure_leaf_debug_logging(
        runtime=leaf_runtime,
        group_name=group_name,
        worker_id=worker_id,
    )
    leaf_debug_log(
        event="leaf.process.runtime_bootstrap.completed",
        process_role=leaf_runtime.get("__process_role"),
        process_group=leaf_runtime.get("__process_group"),
    )
    orchestrator = resolve_leaf_process_entry_orchestration_service(session)
    if orchestrator is None:
        ingress = resolve_leaf_control_ingress_service(session)
        orchestrator = DefaultLeafProcessEntryOrchestrationService(
            control_ingress_service=ingress or _NoopLeafControlIngressService()
        )
        leaf_debug_log(
            event="leaf.process.entry.orchestrator_fallback",
            ingress_fallback=ingress is None,
        )
    orchestrator.run(
        session=session,
        control_pipe=control_pipe,
        stop_event=stop_event,
        worker_id=worker_id,
        group_name=group_name,
        runner_profile_requested=runner_profile_requested,
        boundary_control_poll_seconds=boundary_control_poll_seconds,
    )
    leaf_debug_log(
        event="leaf.process.entry.finished",
        worker_id=worker_id,
        group_name=group_name,
    )
    try:
        flush_leaf_debug_logging()
    except Exception:
        pass


def _resolve_execution_ipc_service(
    session: "LeafWorkerRuntimeSession",
    *,
    explicit: object | None = None,
) -> ExecutionIpcTransportService | None:
    candidate = explicit
    if isinstance(candidate, ExecutionIpcTransportService):
        return candidate
    if callable(getattr(candidate, "send", None)) and callable(getattr(candidate, "recv", None)):
        return candidate  # type: ignore[return-value]
    child = getattr(session, "child", None)
    scope = getattr(child, "scenario_scope", None)
    if scope is None:
        return None
    resolve = getattr(scope, "resolve", None)
    if not callable(resolve):
        return None
    try:
        resolved = resolve("service", ExecutionIpcTransportService)
    except Exception:
        return None
    if isinstance(resolved, ExecutionIpcTransportService):
        return resolved
    if callable(getattr(resolved, "send", None)) and callable(getattr(resolved, "recv", None)):
        return resolved  # type: ignore[return-value]
    return None


def _bind_worker_control_endpoint(
    *,
    ipc: ExecutionIpcTransportService,
    worker_id: str,
    control_pipe: object | None,
) -> None:
    if control_pipe is None:
        return
    binder = getattr(ipc, "bind_local_endpoint", None)
    if not callable(binder):
        return
    if isinstance(control_pipe, dict):
        bound_any = False
        for lane_name, endpoint in control_pipe.items():
            if endpoint is None:
                continue
            lane_target = compose_execution_ipc_worker_target_id(
                worker_id,
                lane=str(lane_name),
            )
            try:
                binder(lane_target, endpoint)
                bound_any = True
            except Exception:
                continue
        if bound_any:
            return
        control_pipe = control_pipe.get(EXECUTION_IPC_LANE_CONTROL)
        if control_pipe is None:
            return
    try:
        binder(
            compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_CONTROL),
            control_pipe,
        )
    except Exception:
        return


def build_leaf_startup_runtime(
    *,
    session: "LeafWorkerRuntimeSession",
    worker_id: str,
    group_name: str,
    runner_profile_requested: str,
) -> dict[str, object]:
    child = getattr(session, "child", None)
    child_runtime = getattr(child, "runtime", None)
    runtime = dict(child_runtime) if isinstance(child_runtime, dict) else {}
    runtime["__process_group"] = group_name
    runtime["__worker_id"] = worker_id
    runtime["__runner_profile_requested"] = runner_profile_requested
    process_role = runtime.get("__process_role")
    if not isinstance(process_role, str) or not process_role:
        runtime["__process_role"] = "worker"
    return runtime


def build_leaf_startup_events(
    *,
    runtime: dict[str, object],
    pulse_payload: object | None = None,
) -> list[object]:
    from stream_kernel.execution.orchestration.control_plane.leaf.system_nodes import (
        ControlPlaneLeafBootstrapNode,
    )
    from stream_kernel.platform.services.runtime.control_plane_events import (
        ControlPlaneLeafPulse,
    )

    node = ControlPlaneLeafBootstrapNode()
    payload = (
        pulse_payload
        if pulse_payload is not None
        else ControlPlaneLeafPulse(runtime=runtime if isinstance(runtime, dict) else {})
    )
    produced = node(payload, None)
    if isinstance(produced, list):
        return list(produced)
    return list(produced or ())


def bootstrap_leaf_worker_runtime_from_bundle(*args: object, **kwargs: object):
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime import (
        bootstrap_leaf_worker_runtime_from_bundle as impl,
    )

    return impl(*args, **kwargs)


def build_leaf_hello_event(session: "LeafWorkerRuntimeSession"):
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime import (
        build_leaf_hello_event as impl,
    )

    return impl(session)


def resolve_leaf_process_entry_orchestration_service(session: "LeafWorkerRuntimeSession"):
    child = getattr(session, "child", None)
    scope = getattr(child, "scenario_scope", None)
    if scope is None:
        return None
    try:
        candidate = scope.resolve("service", LeafProcessEntryOrchestrationService)
    except Exception:
        return None
    if isinstance(candidate, LeafProcessEntryOrchestrationService):
        return candidate
    if callable(getattr(candidate, "run", None)):
        return candidate
    return None


def resolve_leaf_control_ingress_service(session: "LeafWorkerRuntimeSession"):
    child = getattr(session, "child", None)
    scope = getattr(child, "scenario_scope", None)
    if scope is None:
        return None
    try:
        from stream_kernel.execution.orchestration.lifecycle.leaf.command.control_ingress_service import (
            LeafControlIngressService,
        )
    except Exception:
        return None
    try:
        candidate = scope.resolve("service", LeafControlIngressService)
    except Exception:
        return None
    if isinstance(candidate, LeafControlIngressService):
        return candidate
    if callable(getattr(candidate, "run_until_stopped", None)):
        return candidate
    return None


def _resolve_ingress_contract(candidate: object) -> LeafControlIngressService | None:
    if isinstance(candidate, LeafControlIngressService):
        return candidate
    if callable(getattr(candidate, "run_until_stopped", None)):
        return candidate  # type: ignore[return-value]
    return None


def _wait_for_stop_event(stop_event: object | None) -> None:
    if stop_event is None:
        return
    waiter = getattr(stop_event, "wait", None)
    if callable(waiter):
        waiter(0.0)
        return
    checker = getattr(stop_event, "is_set", None)
    if callable(checker):
        checker()


def _send_startup_event_with_retry(
    *,
    ipc: object,
    worker_id: str,
    event: object,
    stop_event: object | None,
    timeout_seconds: float,
) -> bool:
    deadline = time.monotonic() + max(0.001, float(timeout_seconds))
    attempts = 0
    while True:
        attempts += 1
        try:
            sender = getattr(ipc, "send", None)
            if not callable(sender):
                leaf_debug_log(
                    event="leaf.startup_event.send.no_sender",
                    worker_id=worker_id,
                    startup_event=type(event).__name__,
                )
                return False
            sender(
                compose_execution_ipc_worker_target_id(
                    worker_id,
                    lane=EXECUTION_IPC_LANE_CONTROL,
                ),
                event,
                no_reply=True,
            )
            leaf_debug_log(
                event="leaf.startup_event.send.ok",
                worker_id=worker_id,
                startup_event=type(event).__name__,
                attempts=attempts,
            )
            return True
        except Exception as exc:
            if _is_stop_set(stop_event):
                leaf_debug_log(
                    event="leaf.startup_event.send.stopped",
                    worker_id=worker_id,
                    startup_event=type(event).__name__,
                    attempts=attempts,
                )
                return False
            if time.monotonic() >= deadline:
                leaf_debug_log(
                    event="leaf.startup_event.send.timeout",
                    worker_id=worker_id,
                    startup_event=type(event).__name__,
                    attempts=attempts,
                    error=exc.__class__.__name__,
                )
                return False
            waiter = getattr(stop_event, "wait", None)
            if callable(waiter):
                try:
                    waiter(0.01)
                except Exception:
                    _STARTUP_RETRY_WAIT.wait(0.01)
            else:
                _STARTUP_RETRY_WAIT.wait(0.01)


def _resolve_leaf_startup_send_timeout_seconds(runtime: dict[str, object]) -> float:
    timeout_seconds = 5.0
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return timeout_seconds
    readiness = platform.get("readiness", {})
    if not isinstance(readiness, dict):
        return timeout_seconds
    configured = readiness.get("readiness_timeout_seconds")
    if not isinstance(configured, (int, float)):
        return timeout_seconds
    value = float(configured)
    if value <= 0:
        return timeout_seconds
    return max(0.25, min(10.0, value))


def _is_stop_set(stop_event: object | None) -> bool:
    if stop_event is None:
        return False
    checker = getattr(stop_event, "is_set", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    return False


__all__ = [
    "LeafProcessEntryOrchestrationService",
    "DefaultLeafProcessEntryOrchestrationService",
    "LeafWorkerControlPlaneService",
    "DefaultLeafWorkerControlPlaneService",
    "leaf_worker_process_entry",
    "build_leaf_startup_runtime",
    "build_leaf_startup_events",
    "resolve_leaf_process_entry_orchestration_service",
    "resolve_leaf_control_ingress_service",
]
