from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.control_plane import (
    ControlPlaneRootLeafBoundaryDispatchNode,
    ControlPlaneRootLeafBoundaryResultNode,
)
from stream_kernel.execution.orchestration.control_plane.root.leaf_command_service import (
    DefaultControlPlaneRootLeafCommandService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
    DefaultLeafRuntimeActivationService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
    DefaultLeafWorkerCommandLoopService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime import (
    LeafWorkerRuntimeSession,
)
from stream_kernel.execution.transport.ipc.ipc_transport import ExecutionIpcMessage
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryItemEvent,
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneRootLeafBoundaryExecuteRequestEvent,
)
from stream_kernel.platform.services.runtime.control_plane_reply_waiter import (
    DefaultControlPlaneReplyWaiterService,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)


@dataclass(slots=True)
class _Bootstrapper:
    items: list[ControlPlaneDiscoveryItemEvent] = field(default_factory=list)

    def discover_subset(self, *, runtime: dict[str, object], node_names: list[str]) -> list[ControlPlaneDiscoveryItemEvent]:
        _ = runtime
        allowed = set(node_names)
        return [
            item
            for item in self.items
            if item.item_kind == "node"
            and isinstance(item.payload.get("name"), str)
            and item.payload["name"] in allowed
        ]


@dataclass(slots=True)
class _Discovery:
    seen: list[object] = field(default_factory=list)

    def append_item(self, item: object) -> None:
        self.seen.append(item)


@dataclass(slots=True)
class _ExecutionIpc:
    incoming: list[object] = field(default_factory=list)
    sent: list[tuple[str, object, bool]] = field(default_factory=list)

    def recv(self, target_id: str, *, timeout: float | None = None):
        _ = timeout
        if not self.incoming:
            return None
        return ExecutionIpcMessage(target_id=target_id, payload=self.incoming.pop(0), ts_epoch_ms=0)

    def send(self, target_id: str, payload: object, *, no_reply: bool = False):
        self.sent.append((target_id, payload, bool(no_reply)))
        return None


@dataclass(slots=True)
class _StopEvent:
    flag: bool = False

    def is_set(self) -> bool:
        return self.flag


def test_root_leaf_boundary_roundtrip_typed_command_result_updates_state_and_waiter() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    root_dispatch = ControlPlaneRootLeafBoundaryDispatchNode(state=state)
    root_result = ControlPlaneRootLeafBoundaryResultNode(state=state)
    waiter = DefaultControlPlaneReplyWaiterService(state=state)
    root_commands = DefaultControlPlaneRootLeafCommandService(reply_waiter=waiter)
    ipc = _ExecutionIpc()
    leaf_loop = DefaultLeafWorkerCommandLoopService(
        activation_service=DefaultLeafRuntimeActivationService(
            bootstrapper=_Bootstrapper(
                items=[ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.a"})]
            ),
            discovery=_Discovery(),
        ),
        execution_ipc=ipc,
    )
    session = LeafWorkerRuntimeSession(
        child=object(),
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="auto",
        runner_profile_effective="sync",
    )
    class _BoundaryExec:
        def execute(self, *, session, inputs):
            return [{"handled": len(inputs), "worker_id": session.worker_id}]

    leaf_loop.boundary_execution_service = _BoundaryExec()  # type: ignore[assignment]
    req = root_commands.make_boundary_execute_request(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-1",
        inputs=({"payload": 1, "target": "node.a"},),
        finalize=True,
    )
    cmd_out = root_dispatch(req, None)
    assert len(cmd_out) == 1
    ipc.incoming.extend(cmd_out)

    result_kind = leaf_loop.run_control_iteration(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )
    assert result_kind == "boundary_executed"
    assert len(ipc.sent) == 1
    leaf_result = ipc.sent[0][1]
    assert isinstance(leaf_result, ControlPlaneLeafBoundaryResultEvent)

    assert root_result(leaf_result, None) == []
    waited = root_commands.wait_boundary_result(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-1",
        timeout_seconds=0.01,
    )
    assert isinstance(waited, ControlPlaneLeafBoundaryResultEvent)
    assert waited.request_id == "req-1"
