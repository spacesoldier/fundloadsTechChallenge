from __future__ import annotations

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafStopAckEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)
from stream_kernel.platform.services.runtime.control_plane_reply_waiter import (
    DefaultControlPlaneReplyWaiterService,
)


def test_reply_waiter_returns_matching_stop_ack_from_state() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLeafStopAckEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            command_id="stop-1",
        )
    )
    waiter = DefaultControlPlaneReplyWaiterService(state=state)

    ack = waiter.wait_for_leaf_stop_ack(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        command_id="stop-1",
        timeout_seconds=0.01,
    )

    assert isinstance(ack, ControlPlaneLeafStopAckEvent)
    assert ack.command_id == "stop-1"


def test_reply_waiter_returns_matching_boundary_result_from_state() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLeafBoundaryResultEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            request_id="req-1",
            status="completed",
            outputs=("out-1",),
        )
    )
    waiter = DefaultControlPlaneReplyWaiterService(state=state)

    result = waiter.wait_for_leaf_boundary_result(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-1",
        timeout_seconds=0.01,
    )

    assert isinstance(result, ControlPlaneLeafBoundaryResultEvent)
    assert result.outputs == ("out-1",)


def test_reply_waiter_returns_none_when_timeout_without_match() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    waiter = DefaultControlPlaneReplyWaiterService(state=state)

    result = waiter.wait_for_leaf_boundary_result(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-missing",
        timeout_seconds=0.002,
    )

    assert result is None
