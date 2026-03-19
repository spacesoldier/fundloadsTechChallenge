from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import os
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from stream_kernel.application_context import apply_injection
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.control_plane.leaf.system_nodes import (
    is_leaf_command_ingress_source_node_name,
    is_leaf_runtime_ingress_source_node_name,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.command.channel_services import (
    LeafCommandChannelIngressService,
    LeafRunnerControlService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.command.finalization_service import (
    LeafSessionFinalizationService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.session_state_service import (
    LeafRuntimeSessionStateService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.debug_logging import (
    LeafLifecycleDebugLoggingService,
    bind_leaf_debug_sink,
    configure_leaf_debug_logging,
    flush_leaf_debug_logging,
    leaf_debug_log,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneInitEvent,
)
from stream_kernel.platform.services.runtime.debug_buffer import RuntimeDebugBufferService
from stream_kernel.execution.runtime.runner import AsyncRunner, SyncRunner
from stream_kernel.execution.runtime.runner_ingress import (
    enqueue_runner_input_async,
    enqueue_runner_input_sync,
)
from stream_kernel.routing.envelope import Envelope
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    compose_execution_ipc_worker_target_id,
    ExecutionIpcTransportService,
)

if TYPE_CHECKING:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime import (
        LeafWorkerRuntimeSession,
    )


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
    execution_ipc: ExecutionIpcTransportService = inject.service(ExecutionIpcTransportService)
    command_channel_ingress: LeafCommandChannelIngressService = inject.service(LeafCommandChannelIngressService)
    runner_control: LeafRunnerControlService = inject.service(LeafRunnerControlService)
    session_state: LeafRuntimeSessionStateService = inject.service(LeafRuntimeSessionStateService)
    finalization_service: LeafSessionFinalizationService = inject.service(LeafSessionFinalizationService)
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
        debug_logging = resolve_leaf_debug_logging_service(session)
        _bind_leaf_debug_runtime_sink(session, debug_logging=debug_logging)
        configure_leaf_debug_logging(
            runtime=runtime,
            group_name=group_name,
            worker_id=worker_id,
            service=debug_logging,
        )
        leaf_debug_log(
            event="leaf.orchestrator.run.started",
            service=debug_logging,
            worker_id=worker_id,
            group_name=group_name,
            process_role=runtime.get("__process_role"),
        )
        ipc = self.execution_ipc
        session_state = self.session_state
        session_state.bind_session(session)
        if worker_id not in self._bound_control_workers:
            _bind_worker_control_endpoint(ipc=ipc, worker_id=worker_id, control_pipe=control_pipe)
            self._bound_control_workers.add(worker_id)
            leaf_debug_log(
                event="leaf.orchestrator.control_endpoint.bound",
                service=debug_logging,
                worker_id=worker_id,
            )
        ingress = self.command_channel_ingress
        ingress.configure_poll_timeout_seconds(max(0.0, float(boundary_control_poll_seconds)))
        runner = _build_leaf_runner(
            session=session,
            runtime=runtime,
        )
        apply_injection(runner, session.child.scenario_scope, True)
        runner_control = self.runner_control
        runner_control.bind_runner_stop(runner.request_stop)
        try:
            leaf_debug_log(
                event="leaf.orchestrator.runner_loop.start",
                service=debug_logging,
                worker_id=worker_id,
                poll_interval_seconds=max(0.0, float(boundary_control_poll_seconds)),
            )
            _enqueue_leaf_startup_init(
                runner=runner,
                session=session,
                runtime=runtime,
            )
            if isinstance(runner, AsyncRunner):
                runner.run_until_stopped(
                    poll_timeout_seconds=max(0.0, float(boundary_control_poll_seconds)),
                    idle_timeout_seconds=None,
                )
            else:
                runner.run_until_stopped(
                    poll_timeout_seconds=max(0.0, float(boundary_control_poll_seconds)),
                    idle_timeout_seconds=None,
                )
            leaf_debug_log(
                event="leaf.orchestrator.runner_loop.finished",
                service=debug_logging,
                worker_id=worker_id,
            )
        except Exception as exc:
            leaf_debug_log(
                event="leaf.orchestrator.runner_loop.error",
                service=debug_logging,
                worker_id=worker_id,
                error=exc.__class__.__name__,
                message=str(exc),
            )
            try:
                flush_leaf_debug_logging(service=debug_logging)
            except Exception:
                pass
            raise
        finally:
            try:
                runner.on_run_end()
            except Exception:
                pass
            runner_control.clear_runner_stop()
            session_state.clear_session()
            self.finalization_service.finalize(session=session)


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
    debug_logging = resolve_leaf_debug_logging_service(session)
    _bind_leaf_debug_runtime_sink(session, debug_logging=debug_logging)
    configure_leaf_debug_logging(
        runtime=leaf_runtime,
        group_name=group_name,
        worker_id=worker_id,
        service=debug_logging,
    )
    leaf_debug_log(
        event="leaf.process.entry.started",
        service=debug_logging,
        group_name=group_name,
        worker_id=worker_id,
        runner_profile_requested=runner_profile_requested,
        boundary_control_poll_seconds=boundary_control_poll_seconds,
        pipe_codec_mode=pipe_codec_mode,
    )
    leaf_debug_log(
        event="leaf.process.runtime_bootstrap.completed",
        service=debug_logging,
        process_role=leaf_runtime.get("__process_role"),
        process_group=leaf_runtime.get("__process_group"),
    )
    orchestrator = resolve_leaf_process_entry_orchestration_service(session)
    if orchestrator is None:
        leaf_debug_log(
            event="leaf.process.entry.orchestrator_missing",
            service=debug_logging,
        )
        raise RuntimeError("LeafProcessEntryOrchestrationService binding is required")
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
        service=debug_logging,
        worker_id=worker_id,
        group_name=group_name,
    )
    try:
        flush_leaf_debug_logging(service=debug_logging)
    except Exception:
        pass

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
            lane_target = _resolve_child_endpoint_target_id(
                worker_id=worker_id,
                endpoint_key=str(lane_name),
            )
            if not isinstance(lane_target, str) or not lane_target:
                continue
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


def _resolve_child_endpoint_target_id(*, worker_id: str, endpoint_key: str) -> str | None:
    if not isinstance(endpoint_key, str) or not endpoint_key:
        return None
    if endpoint_key.startswith("target::"):
        target_id = endpoint_key.removeprefix("target::")
        if isinstance(target_id, str) and target_id:
            return target_id
        return None
    return compose_execution_ipc_worker_target_id(worker_id, lane=endpoint_key)


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
    return None


def resolve_leaf_runtime_debug_buffer(
    session: "LeafWorkerRuntimeSession",
) -> RuntimeDebugBufferService | None:
    child = getattr(session, "child", None)
    scope = getattr(child, "scenario_scope", None)
    if scope is None:
        return None
    try:
        candidate = scope.resolve("service", RuntimeDebugBufferService)
    except Exception:
        return None
    if isinstance(candidate, RuntimeDebugBufferService):
        return candidate
    return None


def _bind_leaf_debug_runtime_sink(
    session: "LeafWorkerRuntimeSession",
    *,
    debug_logging: LeafLifecycleDebugLoggingService | None = None,
) -> None:
    bind_leaf_debug_sink(
        resolve_leaf_runtime_debug_buffer(session),
        service=debug_logging,
    )


def resolve_leaf_debug_logging_service(
    session: "LeafWorkerRuntimeSession",
) -> LeafLifecycleDebugLoggingService | None:
    child = getattr(session, "child", None)
    scope = getattr(child, "scenario_scope", None)
    if scope is None:
        return None
    try:
        candidate = scope.resolve("service", LeafLifecycleDebugLoggingService)
    except Exception:
        return None
    if isinstance(candidate, LeafLifecycleDebugLoggingService):
        return candidate
    return None


def _build_leaf_runner(
    *,
    session: "LeafWorkerRuntimeSession",
    runtime: dict[str, object],
) -> SyncRunner | AsyncRunner:
    child = getattr(session, "child", None)
    scenario_steps = getattr(child, "scenario_steps", None)
    if not isinstance(scenario_steps, dict):
        raise RuntimeError("leaf runtime requires scenario_steps mapping")
    nodes = {
        name: step
        for name, step in scenario_steps.items()
        if isinstance(name, str)
        and name
        and callable(step)
    }
    ingress_source_names = [
        name
        for name in nodes.keys()
        if is_leaf_command_ingress_source_node_name(name)
        or is_leaf_runtime_ingress_source_node_name(name)
    ]
    if not ingress_source_names:
        raise RuntimeError(
            "leaf runtime is missing IPC ingress source node"
        )
    scenario_id = getattr(child, "scenario_id", "scenario")
    if not isinstance(scenario_id, str) or not scenario_id:
        scenario_id = "scenario"
    run_id = runtime.get("__run_id")
    if not isinstance(run_id, str) or not run_id:
        run_id = "run"
    full_context_nodes = getattr(child, "full_context_nodes", set())
    if not isinstance(full_context_nodes, set):
        full_context_nodes = set()
    ordering = runtime.get("ordering", {})
    sink_mode = "completion"
    if isinstance(ordering, dict):
        configured = ordering.get("sink_mode")
        if isinstance(configured, str) and configured:
            sink_mode = configured
    profile = session.runner_profile_effective or session.runner_profile_requested
    debug_buffer = resolve_leaf_runtime_debug_buffer(session)
    if profile == "sync":
        return SyncRunner(
            nodes=nodes,
            run_id=run_id,
            scenario_id=scenario_id,
            full_context_nodes=set(full_context_nodes),
            ordered_sink_mode=sink_mode,
            runtime_debug_buffer=debug_buffer,
        )
    return AsyncRunner(
        nodes=nodes,
        run_id=run_id,
        scenario_id=scenario_id,
        full_context_nodes=set(full_context_nodes),
        ordered_sink_mode=sink_mode,
        runtime_debug_buffer=debug_buffer,
    )


def build_leaf_startup_init_input(
    *,
    runtime: dict[str, object],
) -> Envelope:
    return Envelope(
        payload=ControlPlaneInitEvent(runtime=dict(runtime)),
        target="system.cp.consumer_registry_bindings_bootstrap",
    )


def _enqueue_leaf_startup_init(
    *,
    runner: SyncRunner | AsyncRunner,
    session: "LeafWorkerRuntimeSession",
    runtime: dict[str, object],
) -> None:
    child = getattr(session, "child", None)
    scenario_id = getattr(child, "scenario_id", "scenario")
    if not isinstance(scenario_id, str) or not scenario_id:
        scenario_id = "scenario"
    run_id = getattr(runner, "run_id", None)
    if not isinstance(run_id, str) or not run_id:
        run_id = "run"
    payload = build_leaf_startup_init_input(runtime=runtime)
    if isinstance(runner, AsyncRunner):
        enqueue_runner_input_async(
            runner,
            payload,
            run_id=run_id,
            scenario_id=scenario_id,
            index=0,
        )
        return
    enqueue_runner_input_sync(
        runner,
        payload,
        run_id=run_id,
        scenario_id=scenario_id,
        index=0,
    )


__all__ = [
    "LeafProcessEntryOrchestrationService",
    "DefaultLeafProcessEntryOrchestrationService",
    "LeafWorkerControlPlaneService",
    "DefaultLeafWorkerControlPlaneService",
    "leaf_worker_process_entry",
    "build_leaf_startup_runtime",
    "build_leaf_startup_init_input",
    "resolve_leaf_process_entry_orchestration_service",
    "resolve_leaf_runtime_debug_buffer",
    "resolve_leaf_debug_logging_service",
]
