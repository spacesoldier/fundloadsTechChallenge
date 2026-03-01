from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
    ControlPlaneRootReplyIngressService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneRootLeafBoundaryExecuteRequestEvent,
    ControlPlaneRootLeafStopRequestEvent,
)
from stream_kernel.platform.services.runtime.control_plane_reply_waiter import (
    ControlPlaneReplyWaiterService,
)


@runtime_checkable
class ControlPlaneRootLeafCommandService(Protocol):
    def make_stop_request(
        self,
        *,
        target_group: str,
        worker_id: str,
        command_id: str,
        reason: str | None = None,
    ) -> ControlPlaneRootLeafStopRequestEvent:
        raise NotImplementedError

    def make_boundary_execute_request(
        self,
        *,
        target_group: str,
        worker_id: str,
        request_id: str,
        inputs: tuple[object, ...],
        finalize: bool = True,
    ) -> ControlPlaneRootLeafBoundaryExecuteRequestEvent:
        raise NotImplementedError

    def wait_stop_ack(
        self,
        *,
        target_group: str,
        worker_id: str,
        command_id: str,
        timeout_seconds: float,
    ) -> ControlPlaneLeafStopAckEvent | None:
        raise NotImplementedError

    def wait_boundary_result(
        self,
        *,
        target_group: str,
        worker_id: str,
        request_id: str,
        timeout_seconds: float,
    ) -> ControlPlaneLeafBoundaryResultEvent | None:
        raise NotImplementedError


@service(name="control_plane_root_leaf_command_service")
@dataclass(slots=True)
class DefaultControlPlaneRootLeafCommandService(ControlPlaneRootLeafCommandService):
    reply_waiter: ControlPlaneReplyWaiterService = inject.service(ControlPlaneReplyWaiterService)
    reply_ingress: ControlPlaneRootReplyIngressService = inject.service(ControlPlaneRootReplyIngressService)
    poll_interval_seconds: float = 0.005

    def configure_poll_interval_seconds(self, poll_interval_seconds: float) -> None:
        if isinstance(poll_interval_seconds, (int, float)) and float(poll_interval_seconds) > 0:
            self.poll_interval_seconds = float(poll_interval_seconds)

    def make_stop_request(
        self,
        *,
        target_group: str,
        worker_id: str,
        command_id: str,
        reason: str | None = None,
    ) -> ControlPlaneRootLeafStopRequestEvent:
        return ControlPlaneRootLeafStopRequestEvent(
            target_group=target_group,
            worker_id=worker_id,
            command_id=command_id,
            reason=reason,
        )

    def make_boundary_execute_request(
        self,
        *,
        target_group: str,
        worker_id: str,
        request_id: str,
        inputs: tuple[object, ...],
        finalize: bool = True,
    ) -> ControlPlaneRootLeafBoundaryExecuteRequestEvent:
        return ControlPlaneRootLeafBoundaryExecuteRequestEvent(
            target_group=target_group,
            worker_id=worker_id,
            request_id=request_id,
            inputs=tuple(inputs),
            finalize=finalize,
        )

    def wait_stop_ack(
        self,
        *,
        target_group: str,
        worker_id: str,
        command_id: str,
        timeout_seconds: float,
    ) -> ControlPlaneLeafStopAckEvent | None:
        return self._wait_with_ingress_pump(
            worker_id=worker_id,
            timeout_seconds=timeout_seconds,
            getter=lambda: self._reply_waiter().wait_for_leaf_stop_ack(
                target_group=target_group,
                worker_id=worker_id,
                command_id=command_id,
                timeout_seconds=0.0,
            ),
        )

    def wait_boundary_result(
        self,
        *,
        target_group: str,
        worker_id: str,
        request_id: str,
        timeout_seconds: float,
    ) -> ControlPlaneLeafBoundaryResultEvent | None:
        return self._wait_with_ingress_pump(
            worker_id=worker_id,
            timeout_seconds=timeout_seconds,
            getter=lambda: self._reply_waiter().wait_for_leaf_boundary_result(
                target_group=target_group,
                worker_id=worker_id,
                request_id=request_id,
                timeout_seconds=0.0,
            ),
        )

    def _reply_waiter(self) -> ControlPlaneReplyWaiterService:
        candidate = self.reply_waiter
        if isinstance(candidate, ControlPlaneReplyWaiterService):
            return candidate
        if callable(getattr(candidate, "wait_for_leaf_stop_ack", None)) and callable(
            getattr(candidate, "wait_for_leaf_boundary_result", None)
        ):
            return candidate  # type: ignore[return-value]
        raise ValueError("ControlPlaneReplyWaiterService binding is required")

    def _reply_ingress(self) -> ControlPlaneRootReplyIngressService | None:
        candidate = self.reply_ingress
        if isinstance(candidate, ControlPlaneRootReplyIngressService):
            return candidate
        if callable(getattr(candidate, "drain_worker_replies", None)):
            return candidate  # type: ignore[return-value]
        return None

    def _wait_with_ingress_pump(self, *, worker_id: str, timeout_seconds: float, getter):
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        ingress = self._reply_ingress()
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                return None
            if ingress is not None:
                ingress.drain_worker_replies(
                    worker_id=worker_id,
                    timeout_seconds=min(max(0.0, float(self.poll_interval_seconds)), remaining),
                    max_items=64,
                )
            result = getter()
            if result is not None:
                return result
            if ingress is None:
                return None


__all__ = [
    "ControlPlaneRootLeafCommandService",
    "DefaultControlPlaneRootLeafCommandService",
]
