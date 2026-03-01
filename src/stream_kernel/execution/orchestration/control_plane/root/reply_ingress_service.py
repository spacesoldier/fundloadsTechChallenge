from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.control_plane.root.discovery_snapshot_service import (
    ControlPlaneRootDiscoverySnapshotService,
)
from stream_kernel.execution.orchestration.control_plane.root.system_nodes import (
    ControlPlaneRootLeafBoundaryResultNode,
    ControlPlaneRootLeafConfigAckNode,
    ControlPlaneRootLeafConfigAssignNode,
    ControlPlaneRootLeafStopAckNode,
    find_group_spec_from_state,
    next_config_revision,
    worker_slot_from_worker_id,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    ExecutionIpcMessage,
    ExecutionIpcTransportService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafDiscoverySnapshotEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafStopAckEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneStateService,
)


@runtime_checkable
class ControlPlaneRootReplyIngressService(Protocol):
    def drain_worker_replies(
        self,
        *,
        worker_id: str,
        timeout_seconds: float = 0.0,
        max_items: int = 64,
    ) -> int:
        raise NotImplementedError

    def configure_startup_protocol_revision(self, revision: int) -> None:
        raise NotImplementedError

    def configure_discovery_request_fallback(self, enabled: bool) -> None:
        raise NotImplementedError


@service(name="control_plane_root_reply_ingress_service")
@dataclass(slots=True)
class DefaultControlPlaneRootReplyIngressService(ControlPlaneRootReplyIngressService):
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)
    execution_ipc: ExecutionIpcTransportService = inject.service(ExecutionIpcTransportService)
    snapshot_builder: object | None = inject.service(ControlPlaneRootDiscoverySnapshotService)
    startup_protocol_revision: int = 1
    discovery_request_fallback_enabled: bool = False

    def configure_startup_protocol_revision(self, revision: int) -> None:
        if not isinstance(revision, int):
            return
        self.startup_protocol_revision = max(1, int(revision))

    def configure_discovery_request_fallback(self, enabled: bool) -> None:
        self.discovery_request_fallback_enabled = bool(enabled)

    def drain_worker_replies(
        self,
        *,
        worker_id: str,
        timeout_seconds: float = 0.0,
        max_items: int = 64,
    ) -> int:
        if not isinstance(worker_id, str) or not worker_id:
            return 0
        drained = 0
        for index in range(max(1, int(max_items))):
            timeout = max(0.0, float(timeout_seconds)) if index == 0 else 0.0
            message = self._ipc().recv(worker_id, timeout=timeout)
            if message is None:
                break
            payload = message.payload if isinstance(message, ExecutionIpcMessage) else message
            if self._dispatch_reply(worker_id=worker_id, payload=payload):
                drained += 1
        return drained

    def _dispatch_reply(self, *, worker_id: str, payload: object) -> bool:
        if isinstance(payload, ControlPlaneLeafHelloEvent):
            if self.startup_protocol_revision >= 2:
                self._state().append_event(payload)
                if self.startup_protocol_revision >= 3:
                    snapshot = self._build_discovery_snapshot(payload)
                    if snapshot is not None:
                        self._state().append_event(snapshot)
                        self._ipc().send(worker_id, snapshot, no_reply=True)
                        return True
                    if not self.discovery_request_fallback_enabled:
                        return True
                request = self._build_discovery_request(payload)
                if request is not None:
                    self._state().append_event(request)
                    self._ipc().send(worker_id, request, no_reply=True)
                return True
            emitted = self._root_assign_node()(payload, None)
            for event in emitted:
                if isinstance(event, ControlPlaneLeafConfigCardEvent):
                    self._ipc().send(worker_id, event, no_reply=True)
            return True
        if isinstance(payload, ControlPlaneLeafDiscoveryAckEvent):
            if self.startup_protocol_revision < 2:
                return False
            self._state().append_event(payload)
            if payload.status != "accepted":
                return True
            card = self._build_config_card_from_ack(payload)
            if card is None:
                return True
            self._state().append_event(card)
            self._ipc().send(worker_id, card, no_reply=True)
            return True
        if isinstance(payload, ControlPlaneLeafConfigAckEvent):
            self._root_ack_node()(payload, None)
            return True
        if isinstance(payload, ControlPlaneLeafStopAckEvent):
            self._root_stop_ack_node()(payload, None)
            return True
        if isinstance(payload, ControlPlaneLeafBoundaryResultEvent):
            self._root_boundary_result_node()(payload, None)
            return True
        return False

    def _root_assign_node(self) -> ControlPlaneRootLeafConfigAssignNode:
        return ControlPlaneRootLeafConfigAssignNode(state=self._state())

    def _root_ack_node(self) -> ControlPlaneRootLeafConfigAckNode:
        return ControlPlaneRootLeafConfigAckNode(state=self._state())

    def _root_stop_ack_node(self) -> ControlPlaneRootLeafStopAckNode:
        return ControlPlaneRootLeafStopAckNode(state=self._state())

    def _root_boundary_result_node(self) -> ControlPlaneRootLeafBoundaryResultNode:
        return ControlPlaneRootLeafBoundaryResultNode(state=self._state())

    def _build_discovery_request(
        self,
        hello: ControlPlaneLeafHelloEvent,
    ) -> ControlPlaneLeafDiscoveryRequestEvent | None:
        state_events = self._state().events()
        group = find_group_spec_from_state(state_events, hello.target_group)
        if group is None:
            return None
        request_id = f"{hello.worker_id}:discover:{int(time.time() * 1000)}"
        return ControlPlaneLeafDiscoveryRequestEvent(
            target_group=hello.target_group,
            worker_id=hello.worker_id,
            request_id=request_id,
            required_nodes=tuple(group.nodes),
            include_relationships=False,
            protocol_revision=max(1, int(self.startup_protocol_revision)),
        )

    def _build_discovery_snapshot(
        self,
        hello: ControlPlaneLeafHelloEvent,
    ) -> ControlPlaneLeafDiscoverySnapshotEvent | None:
        snapshot_builder = self.snapshot_builder
        if isinstance(snapshot_builder, ControlPlaneRootDiscoverySnapshotService):
            return snapshot_builder.build_snapshot(
                hello=hello,
                protocol_revision=max(1, int(self.startup_protocol_revision)),
            )
        build_snapshot = getattr(snapshot_builder, "build_snapshot", None)
        if not callable(build_snapshot):
            return None
        try:
            built = build_snapshot(
                hello=hello,
                protocol_revision=max(1, int(self.startup_protocol_revision)),
            )
        except Exception:
            return None
        if isinstance(built, ControlPlaneLeafDiscoverySnapshotEvent):
            return built
        return None

    def _build_config_card_from_ack(
        self,
        ack: ControlPlaneLeafDiscoveryAckEvent,
    ) -> ControlPlaneLeafConfigCardEvent | None:
        state_events = self._state().events()
        group = find_group_spec_from_state(state_events, ack.target_group)
        if group is None:
            return None
        revision = next_config_revision(state_events, ack.worker_id)
        runner_profile: str | None = None
        for event in reversed(state_events):
            if not isinstance(event, ControlPlaneLeafHelloEvent):
                continue
            if event.worker_id != ack.worker_id:
                continue
            runner_profile = event.runner_profile
            break
        return ControlPlaneLeafConfigCardEvent(
            target_group=group.group_name,
            worker_id=ack.worker_id,
            config_id=f"{ack.worker_id}:cfg:{revision}",
            run_id="run",
            scenario_id="scenario",
            group_name=group.group_name,
            nodes=tuple(group.nodes),
            runner_profile=runner_profile,
            worker_slot=worker_slot_from_worker_id(ack.worker_id),
            config_revision=revision,
        )

    def _state(self) -> ControlPlaneStateService:
        candidate = self.state
        if isinstance(candidate, ControlPlaneStateService):
            return candidate
        if callable(getattr(candidate, "append_event", None)) and callable(getattr(candidate, "events", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ControlPlaneStateService binding is required")

    def _ipc(self) -> ExecutionIpcTransportService:
        candidate = self.execution_ipc
        if isinstance(candidate, ExecutionIpcTransportService):
            return candidate
        if callable(getattr(candidate, "recv", None)) and callable(getattr(candidate, "send", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ExecutionIpcTransportService binding is required")


__all__ = [
    "ControlPlaneRootReplyIngressService",
    "DefaultControlPlaneRootReplyIngressService",
]
