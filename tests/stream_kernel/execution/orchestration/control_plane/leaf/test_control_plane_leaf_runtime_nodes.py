from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.control_plane.leaf import (
    ControlPlaneLeafBoundaryExecuteNode,
    ControlPlaneLeafCommandIngressSourceNode,
    ControlPlaneLeafConfigApplyRuntimeNode,
    ControlPlaneLeafStartWorkNode,
    ControlPlaneLeafStopNode,
    ControlPlaneLeafTombstoneFinalizeNode,
)
from stream_kernel.execution.orchestration.control_plane.leaf.system_nodes import (
    leaf_command_ingress_source_lanes,
    leaf_command_ingress_source_node_name,
)
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime import (
    LeafWorkerRuntimeSession,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafStartWorkEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
)
from stream_kernel.platform.services.runtime.platform_scheduler import (
    PlatformSchedulerUpsertCommand,
)
from stream_kernel.routing.envelope import Envelope
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_TRACE,
    EXECUTION_IPC_LANE_DATA,
    compose_execution_ipc_worker_target_id,
)


def _session() -> LeafWorkerRuntimeSession:
    return LeafWorkerRuntimeSession(
        child=object(),
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="async",
        runner_profile_effective="async",
    )


@dataclass(slots=True)
class _Activation:
    seen: list[tuple[LeafWorkerRuntimeSession, ControlPlaneLeafConfigCardEvent]] = field(default_factory=list)

    def apply_config(
        self,
        *,
        session: LeafWorkerRuntimeSession,
        card: ControlPlaneLeafConfigCardEvent,
    ) -> ControlPlaneLeafConfigAckEvent:
        self.seen.append((session, card))
        return ControlPlaneLeafConfigAckEvent(
            target_group=session.group_name,
            worker_id=session.worker_id,
            config_id=card.config_id,
            status="applied",
            resolved_nodes=tuple(card.nodes),
        )


@dataclass(slots=True)
class _BoundaryExecution:
    seen: list[tuple[LeafWorkerRuntimeSession, list[object], bool]] = field(default_factory=list)
    fail_with: Exception | None = None

    def execute(
        self,
        *,
        session: LeafWorkerRuntimeSession,
        inputs: list[object],
        finalize_runtime: bool = False,
    ) -> list[object]:
        self.seen.append((session, list(inputs), bool(finalize_runtime)))
        if self.fail_with is not None:
            raise self.fail_with
        return ["ok"]


@dataclass(slots=True)
class _RunnerControl:
    stop_calls: int = 0

    def request_stop(self) -> None:
        self.stop_calls += 1


@dataclass(slots=True)
class _Readiness:
    seen: list[ControlPlaneLeafBoundaryResultEvent] = field(default_factory=list)

    def observe_boundary_result(
        self,
        boundary_result: ControlPlaneLeafBoundaryResultEvent,
    ) -> ControlPlaneLeafDrainReadyEvent:
        self.seen.append(boundary_result)
        return ControlPlaneLeafDrainReadyEvent(
            target_group=boundary_result.target_group,
            worker_id=boundary_result.worker_id,
            request_id=boundary_result.request_id,
            tombstone_output=boundary_result.tombstone_output,
        )


@dataclass(slots=True)
class _LaneIngress:
    seen: list[tuple[str, str, float]]
    payload: object | None = None

    def poll_next_message_for_lane(
        self,
        *,
        worker_id: str,
        lane: str,
        timeout_seconds: float = 0.0,
    ) -> object | None:
        self.seen.append((worker_id, lane, timeout_seconds))
        return self.payload


@dataclass(slots=True)
class _RunnerControlStopFlag:
    stop_requested_value: bool = False

    def stop_requested(self) -> bool:
        return bool(self.stop_requested_value)


@dataclass(slots=True)
class _SchedulerStub:
    commands: list[object] = field(default_factory=list)

    def apply_command(self, command: object) -> None:
        self.commands.append(command)


@dataclass(slots=True)
class _TimerStub:
    commands: list[object] = field(default_factory=list)

    def apply_command(self, command: object) -> None:
        self.commands.append(command)


def test_leaf_command_ingress_source_node_polls_single_lane_without_rearm() -> None:
    source_name = leaf_command_ingress_source_node_name(lane=EXECUTION_IPC_LANE_TRACE)
    event = ControlPlaneLeafStopCommand(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        command_id="stop-1",
    )
    ingress = _LaneIngress(seen=[], payload=event)
    node = ControlPlaneLeafCommandIngressSourceNode(
        ingress=ingress,  # type: ignore[arg-type]
        runner_control=_RunnerControlStopFlag(),  # type: ignore[arg-type]
        lane=EXECUTION_IPC_LANE_TRACE,
        source_name=source_name,
    )

    produced = asyncio.run(node(BootstrapControl(target=source_name), None))

    assert len(produced) == 1
    assert isinstance(produced[0], ControlPlaneLeafStopCommand)
    assert len(ingress.seen) == 1
    worker_id, lane, timeout = ingress.seen[0]
    assert isinstance(worker_id, str) and worker_id
    assert lane == EXECUTION_IPC_LANE_TRACE
    assert timeout == 0.0



def test_leaf_command_ingress_source_lanes_include_control_and_data_only() -> None:
    assert leaf_command_ingress_source_lanes() == (
        EXECUTION_IPC_LANE_CONTROL,
        EXECUTION_IPC_LANE_DATA,
    )


def test_leaf_command_ingress_source_initialize_registers_scheduler_job() -> None:
    source_name = leaf_command_ingress_source_node_name(lane=EXECUTION_IPC_LANE_TRACE)
    scheduler = _SchedulerStub()
    timer = _TimerStub()
    node = ControlPlaneLeafCommandIngressSourceNode(
        ingress=_LaneIngress(seen=[], payload=None),  # type: ignore[arg-type]
        runner_control=_RunnerControlStopFlag(),  # type: ignore[arg-type]
        scheduler=scheduler,  # type: ignore[arg-type]
        timer=timer,  # type: ignore[arg-type]
        lane=EXECUTION_IPC_LANE_TRACE,
        source_name=source_name,
    )

    node.initialize()

    assert len(scheduler.commands) == 1
    command = scheduler.commands[0]
    assert isinstance(command, PlatformSchedulerUpsertCommand)
    assert command.target == source_name
    assert len(timer.commands) == 1
    assert isinstance(timer.commands[0], PlatformSchedulerUpsertCommand)
    assert timer.commands[0].target == source_name


def test_leaf_config_apply_runtime_node_uses_activation_service() -> None:
    activation = _Activation()
    node = ControlPlaneLeafConfigApplyRuntimeNode(activation=activation)
    session = _session()
    card = ControlPlaneLeafConfigCardEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        config_id="cfg-1",
        run_id="run",
        scenario_id="scenario",
        group_name="execution.alpha",
        nodes=("node.a", "node.b"),
        runner_profile="async",
    )

    produced = node(card, {"__leaf_session": session})

    assert len(produced) == 1
    ack = produced[0]
    assert isinstance(ack, ControlPlaneLeafConfigAckEvent)
    assert ack.status == "applied"
    assert ack.resolved_nodes == ("node.a", "node.b")
    assert activation.seen == [(session, card)]


def test_leaf_boundary_execute_node_emits_result_when_finalize_true() -> None:
    boundary = _BoundaryExecution()
    node = ControlPlaneLeafBoundaryExecuteNode(boundary_execution=boundary)
    session = _session()
    command = ControlPlaneLeafBoundaryExecuteCommand(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-1",
        inputs=({"payload": 1},),
        finalize=True,
    )

    produced = node(command, {"__leaf_session": session})

    assert boundary.seen == [(session, [{"payload": 1}], True)]
    assert len(produced) == 1
    event = produced[0]
    assert isinstance(event, ControlPlaneLeafBoundaryResultEvent)
    assert event.request_id == "req-1"
    assert event.status == "completed"
    assert event.outputs == ("ok",)


def test_leaf_boundary_execute_node_streams_partial_outputs_to_root() -> None:
    class _BoundaryWithStreaming:
        def execute(
            self,
            *,
            session: LeafWorkerRuntimeSession,
            inputs: list[object],
            finalize_runtime: bool = False,
            stream_callback: object | None = None,
            stream_batch_max_items: int = 1,
        ) -> list[object]:
            _ = session
            _ = inputs
            _ = finalize_runtime
            _ = stream_batch_max_items
            assert callable(stream_callback)
            sent = stream_callback([Envelope(payload={"v": 1}, target="compute_time_keys")])  # type: ignore[misc]
            assert sent is True
            return [Envelope(payload={"v": 2}, target="compute_time_keys", tombstone=True)]

    class _Ipc:
        def __init__(self) -> None:
            self.sent: list[tuple[str, object, bool]] = []

        def send(self, target_id: str, payload: object, *, no_reply: bool = False) -> None:
            self.sent.append((target_id, payload, no_reply))

        def recv(self, target_id: str, *, timeout: float | None = None) -> object | None:
            _ = target_id
            _ = timeout
            return None

    ipc = _Ipc()
    node = ControlPlaneLeafBoundaryExecuteNode(
        boundary_execution=_BoundaryWithStreaming(),
        execution_ipc=ipc,  # type: ignore[arg-type]
    )
    session = _session()
    command = ControlPlaneLeafBoundaryExecuteCommand(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-stream",
        inputs=({"payload": 1},),
        finalize=True,
    )

    produced = node(command, {"__leaf_session": session})

    assert len(ipc.sent) == 1
    target_id, payload, no_reply = ipc.sent[0]
    assert target_id == compose_execution_ipc_worker_target_id("execution.alpha#1", lane=EXECUTION_IPC_LANE_DATA)
    assert no_reply is True
    assert isinstance(payload, ControlPlaneLeafBoundaryResultEvent)
    assert payload.status == "partial"
    assert len(payload.outputs) == 1
    assert isinstance(payload.outputs[0], Envelope)
    assert payload.outputs[0].tombstone is False

    assert len(produced) >= 1
    final_event = produced[0]
    assert isinstance(final_event, ControlPlaneLeafBoundaryResultEvent)
    assert final_event.status == "completed"
    assert len(final_event.outputs) == 1
    assert isinstance(final_event.outputs[0], Envelope)
    assert final_event.outputs[0].tombstone is True


def test_leaf_boundary_execute_node_marks_tombstone_flags() -> None:
    class _BoundaryWithTombstone:
        def execute(
            self,
            *,
            session: LeafWorkerRuntimeSession,
            inputs: list[object],
            finalize_runtime: bool = False,
        ) -> list[object]:
            _ = session
            _ = inputs
            _ = finalize_runtime
            return [Envelope(payload={"kind": "x"}, target="system.obs.trace_dispatch", tombstone=True)]

    boundary = _BoundaryWithTombstone()
    node = ControlPlaneLeafBoundaryExecuteNode(boundary_execution=boundary)
    session = _session()
    command = ControlPlaneLeafBoundaryExecuteCommand(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-tombstone",
        inputs=({"payload": 1, "tombstone": True},),
        finalize=True,
    )

    produced = node(command, {"__leaf_session": session})

    assert len(produced) == 1
    event = produced[0]
    assert isinstance(event, ControlPlaneLeafBoundaryResultEvent)
    assert event.tombstone_input is True
    assert event.tombstone_output is True
    assert len(event.outputs) == 1
    assert isinstance(event.outputs[0], Envelope)
    assert event.outputs[0].tombstone is True


def test_leaf_boundary_execute_node_marks_tombstone_output_when_sink_consumes_terminal_message() -> None:
    class _BoundarySinkTerminal:
        def execute(
            self,
            *,
            session: LeafWorkerRuntimeSession,
            inputs: list[object],
            finalize_runtime: bool = False,
        ) -> list[object]:
            _ = session
            _ = inputs
            _ = finalize_runtime
            return []

    node = ControlPlaneLeafBoundaryExecuteNode(boundary_execution=_BoundarySinkTerminal())
    session = _session()
    command = ControlPlaneLeafBoundaryExecuteCommand(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-terminal-tombstone",
        inputs=({"payload": 1, "tombstone": True},),
        finalize=True,
    )

    produced = node(command, {"__leaf_session": session})

    assert len(produced) == 1
    completed = produced[0]
    assert isinstance(completed, ControlPlaneLeafBoundaryResultEvent)
    assert completed.tombstone_input is True
    assert completed.tombstone_output is True
    assert completed.outputs == ()


def test_leaf_tombstone_finalize_node_emits_drain_ready_event() -> None:
    readiness = _Readiness()
    node = ControlPlaneLeafTombstoneFinalizeNode(readiness=readiness)  # type: ignore[arg-type]
    boundary_result = ControlPlaneLeafBoundaryResultEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-terminal-tombstone",
        status="completed",
        outputs=(),
        tombstone_input=True,
        tombstone_output=True,
    )

    produced = node(boundary_result, {"__leaf_session": _session()})

    assert len(produced) == 1
    drain_ready = produced[0]
    assert isinstance(drain_ready, ControlPlaneLeafDrainReadyEvent)
    assert drain_ready.tombstone_output is True
    assert len(readiness.seen) == 1


def test_leaf_boundary_execute_node_drops_reply_when_finalize_false() -> None:
    boundary = _BoundaryExecution()
    node = ControlPlaneLeafBoundaryExecuteNode(boundary_execution=boundary)
    session = _session()
    command = ControlPlaneLeafBoundaryExecuteCommand(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-2",
        inputs=({"payload": 2},),
        finalize=False,
    )

    produced = node(command, {"__leaf_session": session})

    assert boundary.seen == [(session, [{"payload": 2}], False)]
    assert produced == []


def test_leaf_stop_node_emits_ack() -> None:
    runner_control = _RunnerControl()
    node = ControlPlaneLeafStopNode(runner_control=runner_control)
    session = _session()
    command = ControlPlaneLeafStopCommand(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        command_id="stop-1",
        reason="shutdown",
    )

    produced = node(command, {"__leaf_session": session})

    assert len(produced) == 1
    ack = produced[0]
    assert isinstance(ack, ControlPlaneLeafStopAckEvent)
    assert ack.command_id == "stop-1"
    assert ack.status == "accepted"
    assert runner_control.stop_calls == 1


def test_leaf_start_work_node_emits_bootstrap_controls_for_local_sources() -> None:
    node = ControlPlaneLeafStartWorkNode()
    session = type(
        "_Session",
        (),
        {
            "group_name": "execution.ingress",
            "worker_id": "execution.ingress#1",
            "child": type(
                "_Child",
                (),
                {"scenario_steps": {"source:source": object(), "compute_features": object()}},
            )(),
        },
    )()
    produced = node(
        ControlPlaneLeafStartWorkEvent(source_targets=("source:source", "source:source")),
        {"__leaf_session": session},
    )

    assert len(produced) == 1
    command = produced[0]
    assert isinstance(command, ControlPlaneLeafBoundaryExecuteCommand)
    assert command.target_group == "execution.ingress"
    assert command.worker_id == "execution.ingress#1"
    assert len(command.inputs) == 1
    assert command.inputs[0]["target"] == "source:source"
    assert command.inputs[0]["payload"] == BootstrapControl(target="source:source")


def test_leaf_start_work_node_filters_non_local_sources() -> None:
    node = ControlPlaneLeafStartWorkNode()
    session = type(
        "_Session",
        (),
        {
            "group_name": "execution.ingress",
            "worker_id": "execution.ingress#1",
            "child": type(
                "_Child",
                (),
                {"scenario_steps": {"source:source": object()}},
            )(),
        },
    )()
    produced = node(
        ControlPlaneLeafStartWorkEvent(source_targets=("source:remote",)),
        {"__leaf_session": session},
    )

    assert produced == []


def test_leaf_start_work_node_is_noop_when_group_has_no_local_sources() -> None:
    node = ControlPlaneLeafStartWorkNode()
    session = type(
        "_Session",
        (),
        {
            "group_name": "execution.ingress",
            "worker_id": "execution.ingress#1",
            "child": type(
                "_Child",
                (),
                {"scenario_steps": {"compute_features": object(), "parse_load_attempt": object()}},
            )(),
        },
    )()
    produced = node(
        ControlPlaneLeafStartWorkEvent(source_targets=("source:source",)),
        {"__leaf_session": session},
    )

    assert produced == []
