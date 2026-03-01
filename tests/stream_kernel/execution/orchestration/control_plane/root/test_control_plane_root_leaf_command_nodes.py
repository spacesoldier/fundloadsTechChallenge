from __future__ import annotations

from stream_kernel.execution.orchestration.control_plane import (
    ControlPlaneRootLeafBoundaryDispatchNode,
    ControlPlaneRootLeafBoundaryResultNode,
    ControlPlaneRootLeafStopAckNode,
    ControlPlaneRootLeafStopDispatchNode,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
    ControlPlaneRootLeafBoundaryExecuteRequestEvent,
    ControlPlaneRootLeafStopRequestEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)


def test_root_leaf_stop_dispatch_emits_typed_leaf_stop_command_and_appends_state() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    node = ControlPlaneRootLeafStopDispatchNode(state=state)
    req = ControlPlaneRootLeafStopRequestEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        command_id="stop-1",
        reason="shutdown",
    )

    out = node(req, None)

    assert len(out) == 1
    cmd = out[0]
    assert isinstance(cmd, ControlPlaneLeafStopCommand)
    assert cmd.target_group == "execution.alpha"
    assert cmd.worker_id == "execution.alpha#1"
    assert cmd.command_id == "stop-1"
    events = state.events()
    assert events[0] is req
    assert isinstance(events[1], ControlPlaneLeafStopCommand)


def test_root_leaf_boundary_dispatch_emits_typed_boundary_command_and_appends_state() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    node = ControlPlaneRootLeafBoundaryDispatchNode(state=state)
    req = ControlPlaneRootLeafBoundaryExecuteRequestEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-1",
        inputs=({"x": 1}, {"x": 2}),
        finalize=True,
    )

    out = node(req, None)

    assert len(out) == 1
    cmd = out[0]
    assert isinstance(cmd, ControlPlaneLeafBoundaryExecuteCommand)
    assert cmd.request_id == "req-1"
    assert cmd.inputs == ({"x": 1}, {"x": 2})
    events = state.events()
    assert events[0] is req
    assert isinstance(events[1], ControlPlaneLeafBoundaryExecuteCommand)


def test_root_leaf_stop_ack_node_appends_ack_to_state() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    node = ControlPlaneRootLeafStopAckNode(state=state)
    ack = ControlPlaneLeafStopAckEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        command_id="stop-1",
    )

    assert node(ack, None) == []
    assert state.events() == [ack]


def test_root_leaf_boundary_result_node_appends_result_to_state() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    node = ControlPlaneRootLeafBoundaryResultNode(state=state)
    result = ControlPlaneLeafBoundaryResultEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-1",
        status="completed",
        outputs=("out",),
    )

    assert node(result, None) == []
    assert state.events() == [result]
