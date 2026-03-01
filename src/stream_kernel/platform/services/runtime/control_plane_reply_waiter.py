from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafStopAckEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneStateService,
)


@runtime_checkable
class ControlPlaneReplyWaiterService(Protocol):
    def wait_for_leaf_stop_ack(
        self,
        *,
        target_group: str,
        worker_id: str,
        command_id: str,
        timeout_seconds: float,
    ) -> ControlPlaneLeafStopAckEvent | None:
        raise NotImplementedError

    def wait_for_leaf_boundary_result(
        self,
        *,
        target_group: str,
        worker_id: str,
        request_id: str,
        timeout_seconds: float,
    ) -> ControlPlaneLeafBoundaryResultEvent | None:
        raise NotImplementedError


@service(name="control_plane_reply_waiter_service")
@dataclass(slots=True)
class DefaultControlPlaneReplyWaiterService(ControlPlaneReplyWaiterService):
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)

    def wait_for_leaf_stop_ack(
        self,
        *,
        target_group: str,
        worker_id: str,
        command_id: str,
        timeout_seconds: float,
    ) -> ControlPlaneLeafStopAckEvent | None:
        _ = timeout_seconds
        return self._find_stop_ack(
            target_group=target_group,
            worker_id=worker_id,
            command_id=command_id,
        )

    def wait_for_leaf_boundary_result(
        self,
        *,
        target_group: str,
        worker_id: str,
        request_id: str,
        timeout_seconds: float,
    ) -> ControlPlaneLeafBoundaryResultEvent | None:
        _ = timeout_seconds
        return self._find_boundary_result(
            target_group=target_group,
            worker_id=worker_id,
            request_id=request_id,
        )

    def _find_stop_ack(
        self,
        *,
        target_group: str,
        worker_id: str,
        command_id: str,
    ) -> ControlPlaneLeafStopAckEvent | None:
        for event in reversed(self.state.events()):
            if not isinstance(event, ControlPlaneLeafStopAckEvent):
                continue
            if event.target_group != target_group or event.worker_id != worker_id:
                continue
            if event.command_id != command_id:
                continue
            return event
        return None

    def _find_boundary_result(
        self,
        *,
        target_group: str,
        worker_id: str,
        request_id: str,
    ) -> ControlPlaneLeafBoundaryResultEvent | None:
        for event in reversed(self.state.events()):
            if not isinstance(event, ControlPlaneLeafBoundaryResultEvent):
                continue
            if event.target_group != target_group or event.worker_id != worker_id:
                continue
            if event.request_id != request_id:
                continue
            return event
        return None


__all__ = [
    "ControlPlaneReplyWaiterService",
    "DefaultControlPlaneReplyWaiterService",
]
