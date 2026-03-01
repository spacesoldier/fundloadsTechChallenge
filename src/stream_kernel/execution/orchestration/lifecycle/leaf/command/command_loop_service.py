from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.lifecycle.leaf.command.finalization_service import (
    DefaultLeafSessionFinalizationService,
    LeafSessionFinalizationService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_execution_service import (
    DefaultLeafBoundaryExecutionService,
    LeafBoundaryExecutionService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
    DefaultLeafRuntimeActivationService,
    LeafRuntimeActivationService,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    ExecutionIpcControlSignal,
    ExecutionIpcMessage,
    ExecutionIpcTransportService,
)
from stream_kernel.platform.services.runtime.control_plane_bootstrapper import (
    ControlPlaneBootstrapperService,
)
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    ControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneDiscoveryItemEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
)

if TYPE_CHECKING:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime import (
        LeafWorkerRuntimeSession,
    )


@runtime_checkable
class LeafWorkerCommandLoopService(Protocol):
    def handle_control_message(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        message: object,
    ) -> str | None:
        raise NotImplementedError

    def run_startup_handshake(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        stop_event: object | None,
        poll_seconds: float,
    ) -> bool:
        raise NotImplementedError

    def run_control_iteration(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        stop_event: object | None,
        poll_seconds: float,
    ) -> str | None:
        raise NotImplementedError


@service(name="leaf_worker_command_loop_service")
@dataclass(slots=True)
class DefaultLeafWorkerCommandLoopService(LeafWorkerCommandLoopService):
    activation_service: object | None = None
    boundary_execution_service: object | None = None
    finalization_service: object | None = None
    bootstrapper: object | None = inject.service(ControlPlaneBootstrapperService)
    discovery: object | None = inject.service(ControlPlaneDiscoveryService)
    execution_ipc: object | None = inject.service(ExecutionIpcTransportService)

    def run_startup_handshake(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        stop_event: object | None,
        poll_seconds: float,
    ) -> bool:
        return self.run_control_iteration(
            session=session,
            control_pipe=control_pipe,
            stop_event=stop_event,
            poll_seconds=poll_seconds,
        ) == "configured"

    def run_control_iteration(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        stop_event: object | None,
        poll_seconds: float,
    ) -> str | None:
        _ = control_pipe
        if _stop_requested(stop_event):
            return None
        msg = self._recv_control_message(session=session, timeout_seconds=poll_seconds)
        if msg is None:
            return None
        return self.handle_control_message(
            session=session,
            control_pipe=control_pipe,
            message=msg,
        )

    def handle_control_message(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        control_pipe: object | None,
        message: object,
    ) -> str | None:
        _ = control_pipe
        msg = message
        if isinstance(msg, ExecutionIpcControlSignal) and msg.kind == "ack":
            return None
        if isinstance(msg, ControlPlaneLeafDiscoveryRequestEvent):
            ack = self._build_discovery_ack(session=session, request=msg)
            self._send_control_message(session=session, payload=ack)
            return "discovery_acknowledged"
        if isinstance(msg, ControlPlaneLeafConfigCardEvent):
            activation = self._resolve_activation_service(session)
            if activation is None:
                ack = ControlPlaneLeafConfigAckEvent(
                    target_group=session.group_name,
                    worker_id=session.worker_id,
                    config_id=msg.config_id,
                    status="rejected",
                    error="leaf activation service is unavailable",
                )
            else:
                ack = activation.apply_config(session=session, card=msg)
            self._send_control_message(session=session, payload=ack)
            return "configured"
        if isinstance(msg, ControlPlaneLeafStopCommand):
            finalization = self._resolve_finalization_service(session)
            self._send_control_message(
                session=session,
                payload=ControlPlaneLeafStopAckEvent(
                    target_group=session.group_name,
                    worker_id=session.worker_id,
                    command_id=msg.command_id,
                    status="accepted",
                ),
            )
            if finalization is not None:
                try:
                    finalization.finalize(session=session)
                except Exception:
                    return "stop_requested"
            return "stop_requested"
        if isinstance(msg, ControlPlaneLeafBoundaryExecuteCommand):
            response = self._execute_boundary(session=session, command=msg)
            if response is not None:
                self._send_control_message(session=session, payload=response)
            return "boundary_executed"
        return None

    def _build_discovery_ack(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        request: ControlPlaneLeafDiscoveryRequestEvent,
    ) -> ControlPlaneLeafDiscoveryAckEvent:
        bootstrapper = self._resolve_bootstrapper_service(session)
        discovery = self._resolve_discovery_service(session)
        if bootstrapper is None or discovery is None:
            return ControlPlaneLeafDiscoveryAckEvent(
                target_group=session.group_name,
                worker_id=session.worker_id,
                request_id=request.request_id,
                status="rejected",
                discovered_nodes=(),
                missing_nodes=tuple(request.required_nodes),
                error="leaf discovery services are unavailable",
            )
        discover_all = getattr(bootstrapper, "discover_all", None)
        append_item = getattr(discovery, "append_item", None)
        if not callable(discover_all) or not callable(append_item):
            return ControlPlaneLeafDiscoveryAckEvent(
                target_group=session.group_name,
                worker_id=session.worker_id,
                request_id=request.request_id,
                status="rejected",
                discovered_nodes=(),
                missing_nodes=tuple(request.required_nodes),
                error="leaf discovery services do not implement required contract",
            )
        try:
            items = list(discover_all(runtime=self._runtime_from_session(session)))
            for item in items:
                append_item(item)
            discovered_nodes = self._discovered_node_names(items)
            missing_nodes = tuple(
                name
                for name in request.required_nodes
                if name not in discovered_nodes and not _is_transport_alias(name)
            )
            if missing_nodes:
                return ControlPlaneLeafDiscoveryAckEvent(
                    target_group=session.group_name,
                    worker_id=session.worker_id,
                    request_id=request.request_id,
                    status="rejected",
                    discovered_nodes=discovered_nodes,
                    missing_nodes=missing_nodes,
                    error="missing runtime metadata for nodes: " + ", ".join(sorted(set(missing_nodes))),
                )
            resolved = tuple(
                name
                for name in request.required_nodes
                if name in discovered_nodes or _is_transport_alias(name)
            )
            return ControlPlaneLeafDiscoveryAckEvent(
                target_group=session.group_name,
                worker_id=session.worker_id,
                request_id=request.request_id,
                status="accepted",
                discovered_nodes=resolved,
                missing_nodes=(),
            )
        except Exception as exc:  # noqa: BLE001 - convert to deterministic typed ack
            return ControlPlaneLeafDiscoveryAckEvent(
                target_group=session.group_name,
                worker_id=session.worker_id,
                request_id=request.request_id,
                status="rejected",
                discovered_nodes=(),
                missing_nodes=tuple(request.required_nodes),
                error=str(exc) or exc.__class__.__name__,
            )

    def _send_control_message(self, *, session: "LeafWorkerRuntimeSession", payload: object) -> None:
        ipc = self._resolve_execution_ipc_service(session)
        if ipc is None:
            return
        try:
            ipc.send(session.worker_id, payload, no_reply=True)
        except Exception:
            return

    def _recv_control_message(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        timeout_seconds: float,
    ) -> object | None:
        ipc = self._resolve_execution_ipc_service(session)
        if ipc is None:
            return None
        try:
            message = ipc.recv(session.worker_id, timeout=max(0.0, float(timeout_seconds)))
        except Exception:
            return None
        if message is None:
            return None
        if isinstance(message, ExecutionIpcMessage):
            return message.payload
        return message

    def _resolve_execution_ipc_service(
        self,
        session: "LeafWorkerRuntimeSession",
    ) -> ExecutionIpcTransportService | None:
        candidate = self.execution_ipc
        if isinstance(candidate, ExecutionIpcTransportService):
            return candidate
        if callable(getattr(candidate, "recv", None)) and callable(getattr(candidate, "send", None)):
            return candidate  # type: ignore[return-value]
        scope = _scenario_scope(session)
        if scope is None:
            return None
        try:
            resolved = scope.resolve("service", ExecutionIpcTransportService)
        except Exception:
            return None
        if isinstance(resolved, ExecutionIpcTransportService):
            return resolved
        if callable(getattr(resolved, "recv", None)) and callable(getattr(resolved, "send", None)):
            return resolved  # type: ignore[return-value]
        return None

    def _execute_boundary(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        command: ControlPlaneLeafBoundaryExecuteCommand,
    ) -> ControlPlaneLeafBoundaryResultEvent | None:
        try:
            boundary = self._resolve_boundary_execution_service(session)
            if boundary is None:
                raise RuntimeError("leaf boundary execution service is unavailable")
            if not command.finalize:
                # Background lanes (for example observability) do not require
                # boundary result replies. Execute and consume outputs without
                # materializing them into a tuple to reduce blocking overhead.
                for _ in boundary.execute(
                    session=session,
                    inputs=list(command.inputs),
                    finalize_runtime=False,
                ):
                    pass
                return None
            outputs = tuple(
                boundary.execute(
                    session=session,
                    inputs=list(command.inputs),
                    finalize_runtime=False,
                )
            )
            return ControlPlaneLeafBoundaryResultEvent(
                target_group=session.group_name,
                worker_id=session.worker_id,
                request_id=command.request_id,
                status="completed",
                outputs=outputs,
            )
        except Exception as exc:  # noqa: BLE001 - convert to typed result event
            if not command.finalize:
                return None
            return ControlPlaneLeafBoundaryResultEvent(
                target_group=session.group_name,
                worker_id=session.worker_id,
                request_id=command.request_id,
                status="failed",
                error=str(exc) or exc.__class__.__name__,
            )

    def _resolve_activation_service(
        self,
        session: "LeafWorkerRuntimeSession",
    ) -> LeafRuntimeActivationService | None:
        candidate = self.activation_service
        if isinstance(candidate, LeafRuntimeActivationService):
            return candidate
        if callable(getattr(candidate, "apply_config", None)):
            return candidate  # type: ignore[return-value]
        scope = _scenario_scope(session)
        if scope is None:
            return None
        try:
            resolved = scope.resolve("service", LeafRuntimeActivationService)
        except Exception:
            resolved = None
        if isinstance(resolved, LeafRuntimeActivationService):
            return resolved
        if callable(getattr(resolved, "apply_config", None)):
            return resolved  # type: ignore[return-value]
        return DefaultLeafRuntimeActivationService()

    def _resolve_boundary_execution_service(
        self,
        session: "LeafWorkerRuntimeSession",
    ) -> LeafBoundaryExecutionService | None:
        candidate = self.boundary_execution_service
        if isinstance(candidate, LeafBoundaryExecutionService):
            return candidate
        if callable(getattr(candidate, "execute", None)):
            return candidate  # type: ignore[return-value]
        scope = _scenario_scope(session)
        if scope is None:
            return None
        try:
            resolved = scope.resolve("service", LeafBoundaryExecutionService)
        except Exception:
            resolved = None
        if isinstance(resolved, LeafBoundaryExecutionService):
            return resolved
        if callable(getattr(resolved, "execute", None)):
            return resolved  # type: ignore[return-value]
        return DefaultLeafBoundaryExecutionService()

    def _resolve_finalization_service(
        self,
        session: "LeafWorkerRuntimeSession",
    ) -> LeafSessionFinalizationService | None:
        candidate = self.finalization_service
        if isinstance(candidate, LeafSessionFinalizationService):
            return candidate
        if callable(getattr(candidate, "finalize", None)):
            return candidate  # type: ignore[return-value]
        scope = _scenario_scope(session)
        if scope is None:
            return None
        try:
            resolved = scope.resolve("service", LeafSessionFinalizationService)
        except Exception:
            resolved = None
        if isinstance(resolved, LeafSessionFinalizationService):
            return resolved
        if callable(getattr(resolved, "finalize", None)):
            return resolved  # type: ignore[return-value]
        return DefaultLeafSessionFinalizationService()

    def _resolve_bootstrapper_service(
        self,
        session: "LeafWorkerRuntimeSession",
    ) -> ControlPlaneBootstrapperService | None:
        candidate = self.bootstrapper
        if isinstance(candidate, ControlPlaneBootstrapperService):
            return candidate
        if callable(getattr(candidate, "discover_all", None)):
            return candidate  # type: ignore[return-value]
        scope = _scenario_scope(session)
        if scope is None:
            return None
        try:
            resolved = scope.resolve("service", ControlPlaneBootstrapperService)
        except Exception:
            return None
        if isinstance(resolved, ControlPlaneBootstrapperService):
            return resolved
        if callable(getattr(resolved, "discover_all", None)):
            return resolved  # type: ignore[return-value]
        return None

    def _resolve_discovery_service(
        self,
        session: "LeafWorkerRuntimeSession",
    ) -> ControlPlaneDiscoveryService | None:
        candidate = self.discovery
        if isinstance(candidate, ControlPlaneDiscoveryService):
            return candidate
        if callable(getattr(candidate, "append_item", None)):
            return candidate  # type: ignore[return-value]
        scope = _scenario_scope(session)
        if scope is None:
            return None
        try:
            resolved = scope.resolve("service", ControlPlaneDiscoveryService)
        except Exception:
            return None
        if isinstance(resolved, ControlPlaneDiscoveryService):
            return resolved
        if callable(getattr(resolved, "append_item", None)):
            return resolved  # type: ignore[return-value]
        return None

    @staticmethod
    def _runtime_from_session(session: "LeafWorkerRuntimeSession") -> dict[str, object]:
        child = getattr(session, "child", None)
        runtime = getattr(child, "runtime", None)
        if isinstance(runtime, dict):
            return dict(runtime)
        return {}

    @staticmethod
    def _discovered_node_names(items: list[object]) -> tuple[str, ...]:
        resolved: list[str] = []
        for item in items:
            name: str | None = None
            if isinstance(item, ControlPlaneDiscoveryItemEvent):
                if item.item_kind != "node":
                    continue
                payload_name = item.payload.get("name")
                if isinstance(payload_name, str) and payload_name:
                    name = payload_name
            elif isinstance(item, ControlPlaneDiscoveryEntityRecord):
                if item.entity_kind != "node":
                    continue
                payload_name = item.meta.get("name")
                if isinstance(payload_name, str) and payload_name:
                    name = payload_name
            if not isinstance(name, str) or not name:
                continue
            resolved.append(name)
        return tuple(dict.fromkeys(resolved))

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


def _scenario_scope(session: object) -> object | None:
    child = getattr(session, "child", None)
    return getattr(child, "scenario_scope", None)


def _is_transport_alias(node_name: object) -> bool:
    if not isinstance(node_name, str) or not node_name:
        return False
    if node_name.startswith("system.obs."):
        return True
    if node_name.startswith(("source:", "sink:")):
        return True
    if node_name.endswith("_bridge"):
        return True
    if "_line_bridge" in node_name:
        return True
    return False


__all__ = [
    "LeafWorkerCommandLoopService",
    "DefaultLeafWorkerCommandLoopService",
]
