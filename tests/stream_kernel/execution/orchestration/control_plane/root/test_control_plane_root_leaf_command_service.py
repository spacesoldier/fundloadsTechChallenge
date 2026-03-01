from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneRootLeafBoundaryExecuteRequestEvent,
    ControlPlaneRootLeafStopRequestEvent,
)


@dataclass(slots=True)
class _ReplyWaiter:
    stop_acks: list[object] = field(default_factory=list)
    boundary_results: list[object] = field(default_factory=list)
    stop_calls: list[dict[str, object]] = field(default_factory=list)
    boundary_calls: list[dict[str, object]] = field(default_factory=list)

    def wait_for_leaf_stop_ack(self, **kwargs: object):
        self.stop_calls.append(dict(kwargs))
        return self.stop_acks[-1] if self.stop_acks else None

    def wait_for_leaf_boundary_result(self, **kwargs: object):
        self.boundary_calls.append(dict(kwargs))
        return self.boundary_results[-1] if self.boundary_results else None


@dataclass(slots=True)
class _ReplyIngress:
    calls: list[dict[str, object]] = field(default_factory=list)
    on_drain: object | None = None

    def drain_worker_replies(self, **kwargs: object) -> int:
        self.calls.append(dict(kwargs))
        callback = self.on_drain
        if callable(callback):
            callback()
        return 1


def test_root_leaf_command_service_builds_typed_stop_request() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.leaf_command_service import (
        DefaultControlPlaneRootLeafCommandService,
    )

    service = DefaultControlPlaneRootLeafCommandService(reply_waiter=_ReplyWaiter())

    req = service.make_stop_request(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        command_id="stop-1",
        reason="shutdown",
    )

    assert isinstance(req, ControlPlaneRootLeafStopRequestEvent)
    assert req.target_group == "execution.alpha"
    assert req.worker_id == "execution.alpha#1"
    assert req.command_id == "stop-1"


def test_root_leaf_command_service_delegates_wait_stop_ack_to_reply_waiter() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.leaf_command_service import (
        DefaultControlPlaneRootLeafCommandService,
    )

    waiter = _ReplyWaiter(
        stop_acks=[
            ControlPlaneLeafStopAckEvent(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                command_id="stop-1",
            )
        ]
    )
    service = DefaultControlPlaneRootLeafCommandService(reply_waiter=waiter)

    ack = service.wait_stop_ack(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        command_id="stop-1",
        timeout_seconds=0.1,
    )

    assert isinstance(ack, ControlPlaneLeafStopAckEvent)
    assert waiter.stop_calls and waiter.stop_calls[-1]["command_id"] == "stop-1"


def test_root_leaf_command_service_builds_boundary_request_and_waits_result() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.leaf_command_service import (
        DefaultControlPlaneRootLeafCommandService,
    )

    waiter = _ReplyWaiter(
        boundary_results=[
            ControlPlaneLeafBoundaryResultEvent(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                request_id="req-1",
                status="completed",
                outputs=("out-1",),
            )
        ]
    )
    service = DefaultControlPlaneRootLeafCommandService(reply_waiter=waiter)

    req = service.make_boundary_execute_request(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-1",
        inputs=({"payload": 1},),
        finalize=True,
    )
    result = service.wait_boundary_result(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-1",
        timeout_seconds=0.1,
    )

    assert isinstance(req, ControlPlaneRootLeafBoundaryExecuteRequestEvent)
    assert req.request_id == "req-1"
    assert isinstance(result, ControlPlaneLeafBoundaryResultEvent)
    assert waiter.boundary_calls and waiter.boundary_calls[-1]["request_id"] == "req-1"


def test_root_leaf_command_service_pumps_reply_ingress_while_waiting() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.leaf_command_service import (
        DefaultControlPlaneRootLeafCommandService,
    )

    waiter = _ReplyWaiter()
    ingress = _ReplyIngress()

    def _release_result() -> None:
        if waiter.boundary_results:
            return
        waiter.boundary_results.append(
            ControlPlaneLeafBoundaryResultEvent(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                request_id="req-2",
                status="completed",
                outputs=("done",),
            )
        )

    ingress.on_drain = _release_result
    service = DefaultControlPlaneRootLeafCommandService(
        reply_waiter=waiter,
        reply_ingress=ingress,
        poll_interval_seconds=0.001,
    )

    result = service.wait_boundary_result(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-2",
        timeout_seconds=0.05,
    )

    assert isinstance(result, ControlPlaneLeafBoundaryResultEvent)
    assert ingress.calls
    assert ingress.calls[0]["worker_id"] == "execution.alpha#1"
