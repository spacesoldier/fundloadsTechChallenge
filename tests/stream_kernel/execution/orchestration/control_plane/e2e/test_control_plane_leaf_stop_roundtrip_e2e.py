from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.control_plane import (
    ControlPlaneRootLeafStopAckNode,
    ControlPlaneRootLeafStopDispatchNode,
)
from stream_kernel.execution.orchestration.control_plane.root.leaf_command_service import (
    DefaultControlPlaneRootLeafCommandService,
)
from stream_kernel.execution.orchestration.control_plane.root.stop_execution_service import (
    DefaultControlPlaneRootStopExecutionService,
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
    ControlPlaneLeafStopAckEvent,
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
        _ = (runtime, node_names)
        return list(self.items)


@dataclass(slots=True)
class _Discovery:
    seen: list[object] = field(default_factory=list)

    def append_item(self, item: object) -> None:
        self.seen.append(item)


@dataclass(slots=True)
class _Pipe:
    incoming: list[object] = field(default_factory=list)
    sent: list[object] = field(default_factory=list)

    def poll(self, timeout: float) -> bool:
        _ = timeout
        return bool(self.incoming)

    def recv(self) -> object:
        return self.incoming.pop(0)

    def send(self, item: object) -> None:
        self.sent.append(item)


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


@dataclass(slots=True)
class _IpcPort:
    sends: list[dict[str, object]]
    pipe: _Pipe
    root_ack_node: object

    def send(self, target_id: str, payload: object, *, no_reply: bool = False):
        _ = (target_id, no_reply)
        self.sends.append({"payload": payload})
        self.pipe.incoming.append(payload)
        return None


def test_root_leaf_stop_roundtrip_typed_command_ack_updates_state_and_waiter() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    root_dispatch = ControlPlaneRootLeafStopDispatchNode(state=state)
    root_ack = ControlPlaneRootLeafStopAckNode(state=state)
    waiter = DefaultControlPlaneReplyWaiterService(state=state)
    root_commands = DefaultControlPlaneRootLeafCommandService(reply_waiter=waiter)
    pipe = _Pipe()
    leaf_ipc = _ExecutionIpc()
    ipc = _IpcPort(sends=[], pipe=pipe, root_ack_node=root_ack)
    stop_exec = DefaultControlPlaneRootStopExecutionService(
        root_leaf_commands=root_commands,
        execution_ipc=ipc,
    )
    leaf_loop = DefaultLeafWorkerCommandLoopService(
        activation_service=DefaultLeafRuntimeActivationService(
            bootstrapper=_Bootstrapper(),
            discovery=_Discovery(),
        ),
        execution_ipc=leaf_ipc,
    )
    session = LeafWorkerRuntimeSession(
        child=object(),
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="auto",
        runner_profile_effective="sync",
    )

    req = root_commands.make_stop_request(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        command_id="stop-1",
        reason="shutdown",
    )
    cmd_out = root_dispatch(req, None)
    assert len(cmd_out) == 1
    leaf_ipc.incoming.extend(cmd_out)

    result_kind = leaf_loop.run_control_iteration(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )
    assert result_kind == "stop_requested"
    assert len(leaf_ipc.sent) == 1
    leaf_ack = leaf_ipc.sent.pop(0)[1]
    assert isinstance(leaf_ack, ControlPlaneLeafStopAckEvent)
    assert root_ack(leaf_ack, None) == []

    ack = stop_exec.stop_leaf(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        command_id="stop-1",
        timeout_seconds=0.01,
        reason="shutdown",
    )
    assert isinstance(ack, ControlPlaneLeafStopAckEvent)
    assert ack.command_id == "stop-1"
