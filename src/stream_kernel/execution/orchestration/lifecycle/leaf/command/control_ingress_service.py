from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
    LeafWorkerCommandLoopService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.command.finalization_service import (
    DefaultLeafSessionFinalizationService,
    LeafSessionFinalizationService,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    ExecutionIpcControlSignal,
    ExecutionIpcMessage,
    ExecutionIpcTransportService,
)
from stream_kernel.integration.consumer_registry import ConsumerRegistry
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafStopAckEvent,
)

if TYPE_CHECKING:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime import (
        LeafWorkerRuntimeSession,
    )


@runtime_checkable
class LeafControlIngressService(Protocol):
    def run_until_stopped(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        stop_event: object | None,
        poll_interval_seconds: float,
    ) -> str:
        raise NotImplementedError


@service(name="leaf_control_ingress_service")
@dataclass(slots=True)
class DefaultLeafControlIngressService(LeafControlIngressService):
    command_loop_service: LeafWorkerCommandLoopService = inject.service(LeafWorkerCommandLoopService)
    execution_ipc: object | None = inject.service(ExecutionIpcTransportService)
    finalization_service: object | None = inject.service(LeafSessionFinalizationService)

    def run_until_stopped(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        stop_event: object | None,
        poll_interval_seconds: float,
    ) -> str:
        return _run_async_blocking(
            self.run_until_stopped_async(
                session=session,
                control_pipe=control_pipe,
                stop_event=stop_event,
                poll_interval_seconds=poll_interval_seconds,
            )
        )

    async def run_until_stopped_async(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        stop_event: object | None,
        poll_interval_seconds: float,
    ) -> str:
        _ = control_pipe
        poll_interval = max(0.0, float(poll_interval_seconds))
        while True:
            if _stop_requested(stop_event):
                self._finalize_session(session=session)
                return "stop_event"
            message = self._recv_control_message_nonblocking(session=session)
            if message is None:
                await asyncio.sleep(poll_interval)
                continue
            if _is_transport_ack_signal(message):
                continue
            dispatched, status = self._dispatch_via_leaf_nodes(
                session=session,
                message=message,
            )
            if not dispatched:
                status = self.command_loop_service.handle_control_message(
                    session=session,
                    control_pipe=control_pipe,
                    message=message,
                )
            if status == "stop_requested":
                if dispatched:
                    self._finalize_session(session=session)
                return "stop_requested"

    def _dispatch_via_leaf_nodes(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        message: object,
    ) -> tuple[bool, str | None]:
        steps = _scenario_steps_for_session(session)
        if not steps:
            return (False, None)
        registry = _consumer_registry_for_session(session)
        if registry is None:
            return (False, None)
        consumers = _consumer_names_for_message(registry, message)
        if not consumers:
            return (False, None)
        dispatched = False
        status: str | None = None
        for node_name in consumers:
            if not node_name.startswith("system.cp.leaf_"):
                continue
            step = steps.get(node_name)
            if not callable(step):
                continue
            dispatched = True
            produced = step(message, {"__leaf_session": session})
            for item in _coerce_step_outputs(produced):
                if _is_leaf_control_reply(item):
                    self._send_control_message(session=session, payload=item)
                if isinstance(item, ControlPlaneLeafConfigAckEvent):
                    status = "configured"
                elif isinstance(item, ControlPlaneLeafDiscoveryAckEvent):
                    status = "discovery_acknowledged"
                elif isinstance(item, ControlPlaneLeafBoundaryResultEvent):
                    status = "boundary_executed"
                elif isinstance(item, ControlPlaneLeafStopAckEvent):
                    status = "stop_requested"
        return (dispatched, status)

    def _recv_control_message_nonblocking(self, *, session: "LeafWorkerRuntimeSession") -> object | None:
        ipc = self._resolve_execution_ipc_service(session)
        if ipc is None:
            return None
        try:
            message = ipc.recv(session.worker_id, timeout=0.0)
        except Exception:
            return None
        if message is None:
            return None
        if isinstance(message, ExecutionIpcMessage):
            return message.payload
        return message

    def _send_control_message(self, *, session: "LeafWorkerRuntimeSession", payload: object) -> None:
        ipc = self._resolve_execution_ipc_service(session)
        if ipc is None:
            return
        try:
            ipc.send(session.worker_id, payload, no_reply=True)
        except Exception:
            return

    def _resolve_execution_ipc_service(
        self,
        session: "LeafWorkerRuntimeSession",
    ) -> ExecutionIpcTransportService | None:
        candidate = self.execution_ipc
        if isinstance(candidate, ExecutionIpcTransportService):
            return candidate
        if callable(getattr(candidate, "recv", None)) and callable(getattr(candidate, "send", None)):
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
        if callable(getattr(resolved, "recv", None)) and callable(getattr(resolved, "send", None)):
            return resolved  # type: ignore[return-value]
        return None

    def _finalize_session(self, *, session: "LeafWorkerRuntimeSession") -> None:
        finalizer = self._resolve_finalization_service(session)
        if finalizer is None:
            return
        try:
            finalizer.finalize(session=session)
        except Exception:
            return

    def _resolve_finalization_service(
        self,
        session: "LeafWorkerRuntimeSession",
    ) -> LeafSessionFinalizationService | None:
        candidate = self.finalization_service
        if isinstance(candidate, LeafSessionFinalizationService):
            return candidate
        if callable(getattr(candidate, "finalize", None)):
            return candidate  # type: ignore[return-value]
        child = getattr(session, "child", None)
        scope = getattr(child, "scenario_scope", None)
        if scope is None:
            return DefaultLeafSessionFinalizationService()
        resolve = getattr(scope, "resolve", None)
        if not callable(resolve):
            return DefaultLeafSessionFinalizationService()
        try:
            resolved = resolve("service", LeafSessionFinalizationService)
        except Exception:
            return DefaultLeafSessionFinalizationService()
        if isinstance(resolved, LeafSessionFinalizationService):
            return resolved
        if callable(getattr(resolved, "finalize", None)):
            return resolved  # type: ignore[return-value]
        return DefaultLeafSessionFinalizationService()


def _stop_requested(stop_event: object | None) -> bool:
    if stop_event is None:
        return False
    checker = getattr(stop_event, "is_set", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    return False


def _run_async_blocking(awaitable: object) -> str:
    if asyncio.iscoroutine(awaitable):
        try:
            return asyncio.run(awaitable)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(awaitable)
            finally:
                loop.close()
    raise ValueError("LeafControlIngressService expected coroutine awaitable")


def _scenario_steps_for_session(session: object) -> dict[str, object]:
    child = getattr(session, "child", None)
    steps = getattr(child, "scenario_steps", None)
    return dict(steps) if isinstance(steps, dict) else {}


def _consumer_registry_for_session(session: object) -> ConsumerRegistry | None:
    child = getattr(session, "child", None)
    scope = getattr(child, "scenario_scope", None)
    if scope is None:
        return None
    resolve = getattr(scope, "resolve", None)
    if not callable(resolve):
        return None
    try:
        resolved = resolve("service", ConsumerRegistry)
    except Exception:
        return None
    if isinstance(resolved, ConsumerRegistry):
        return resolved
    if callable(getattr(resolved, "get_consumers", None)):
        return resolved  # type: ignore[return-value]
    return None


def _consumer_names_for_message(registry: ConsumerRegistry, message: object) -> list[str]:
    get_consumers = getattr(registry, "get_consumers", None)
    if not callable(get_consumers):
        return []
    try:
        resolved = get_consumers(type(message))
    except Exception:
        return []
    if not isinstance(resolved, list):
        return []
    return [name for name in resolved if isinstance(name, str) and name]


def _coerce_step_outputs(produced: object) -> list[object]:
    if produced is None:
        return []
    if isinstance(produced, list):
        return list(produced)
    try:
        return list(produced)  # type: ignore[arg-type]
    except Exception:
        return []


def _is_leaf_control_reply(message: object) -> bool:
    return isinstance(
        message,
        (
            ControlPlaneLeafDiscoveryAckEvent,
            ControlPlaneLeafConfigAckEvent,
            ControlPlaneLeafBoundaryResultEvent,
            ControlPlaneLeafStopAckEvent,
        ),
    )


def _is_transport_ack_signal(message: object) -> bool:
    return isinstance(message, ExecutionIpcControlSignal) and message.kind == "ack"


__all__ = [
    "LeafControlIngressService",
    "DefaultLeafControlIngressService",
]
