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
    ControlPlaneLeafReplyDispatchNode,
    ControlPlaneLeafSourcePollFromSinkAckNode,
    leaf_command_ingress_source_lanes,
    leaf_command_ingress_source_node_name,
)
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime import (
    LeafWorkerRuntimeSession,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafBoundaryOutputsEvent,
    ControlPlaneLeafSinkDispatchAckEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafStartWorkEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
)
from stream_kernel.platform.services.runtime.platform_scheduler import (
    PlatformSchedulerCancelCommand,
    PlatformSchedulerUpsertCommand,
)
from stream_kernel.routing.envelope import Envelope
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_TRACE,
    EXECUTION_IPC_LANE_DATA,
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
    seen: list[ControlPlaneLeafBoundaryOutputsEvent] = field(default_factory=list)

    def observe_boundary_outputs(
        self,
        boundary_result: ControlPlaneLeafBoundaryOutputsEvent,
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


@dataclass(slots=True)
class _ReplyDispatch:
    sent: list[tuple[str, object]] = field(default_factory=list)
    accepted: bool = True

    def dispatch_reply(self, *, worker_id: str, payload: object) -> bool:
        self.sent.append((worker_id, payload))
        return bool(self.accepted)


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
    node = ControlPlaneLeafCommandIngressSourceNode(
        ingress=_LaneIngress(seen=[], payload=None),  # type: ignore[arg-type]
        runner_control=_RunnerControlStopFlag(),  # type: ignore[arg-type]
        lane=EXECUTION_IPC_LANE_TRACE,
        source_name=source_name,
    )

    produced = node.initialize()
    assert len(produced) == 1
    command = produced[0]
    assert isinstance(command, PlatformSchedulerUpsertCommand)
    assert command.target == source_name


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
    assert isinstance(event, ControlPlaneLeafBoundaryOutputsEvent)
    assert event.request_id == "req-1"
    assert event.outputs == ("ok",)


def test_leaf_boundary_execute_node_detects_source_target_for_batched_source_inputs() -> None:
    boundary = _BoundaryExecution()
    node = ControlPlaneLeafBoundaryExecuteNode(boundary_execution=boundary)
    session = _session()
    command = ControlPlaneLeafBoundaryExecuteCommand(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-batch-source",
        inputs=(
            {
                "dispatch_group": "execution.alpha",
                "target": "source:source",
                "payload": BootstrapControl(target="source:source", single_shot=True),
                "trace_id": None,
                "reply_to": None,
                "span_id": None,
                "tombstone": False,
            },
            {
                "dispatch_group": "execution.alpha",
                "target": "source:source",
                "payload": BootstrapControl(target="source:source", single_shot=True),
                "trace_id": None,
                "reply_to": None,
                "span_id": None,
                "tombstone": False,
            },
        ),
        finalize=True,
    )

    produced = node(command, {"__leaf_session": session})

    assert len(produced) == 1
    event = produced[0]
    assert isinstance(event, ControlPlaneLeafBoundaryOutputsEvent)
    assert event.source_target == "source:source"


def test_leaf_boundary_execute_node_does_not_emit_partial_outputs_to_root() -> None:
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
            assert stream_callback is None
            return [
                Envelope(payload={"v": 1}, target="compute_time_keys"),
                Envelope(payload={"v": 2}, target="compute_time_keys", tombstone=True),
            ]

    node = ControlPlaneLeafBoundaryExecuteNode(
        boundary_execution=_BoundaryWithStreaming(),
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

    assert len(produced) == 1
    final_event = produced[0]
    assert isinstance(final_event, ControlPlaneLeafBoundaryOutputsEvent)
    assert len(final_event.outputs) == 2
    assert isinstance(final_event.outputs[0], Envelope)
    assert isinstance(final_event.outputs[1], Envelope)
    assert final_event.outputs[0].tombstone is False
    assert final_event.outputs[1].tombstone is True


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
    assert isinstance(event, ControlPlaneLeafBoundaryOutputsEvent)
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
    assert isinstance(completed, ControlPlaneLeafBoundaryOutputsEvent)
    assert completed.tombstone_input is True
    assert completed.tombstone_output is True
    assert completed.outputs == ()


def test_leaf_tombstone_finalize_node_emits_drain_ready_event() -> None:
    readiness = _Readiness()
    node = ControlPlaneLeafTombstoneFinalizeNode(readiness=readiness)  # type: ignore[arg-type]
    boundary_result = ControlPlaneLeafBoundaryOutputsEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-terminal-tombstone",
        outputs=(),
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

    acks = [item for item in produced if isinstance(item, ControlPlaneLeafStopAckEvent)]
    cancel_commands = [item for item in produced if isinstance(item, PlatformSchedulerCancelCommand)]
    assert len(acks) == 1
    ack = acks[0]
    assert isinstance(ack, ControlPlaneLeafStopAckEvent)
    assert ack.command_id == "stop-1"
    assert ack.status == "accepted"
    assert len(cancel_commands) == 5
    assert all(
        command.job_id.startswith("cp.leaf.command_ingress:source:system.cp.command_ingress:")
        for command in cancel_commands
    )
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
    assert command.inputs[0]["payload"] == BootstrapControl(target="source:source", single_shot=True)


def test_leaf_reply_dispatch_node_emits_sink_ack_after_successful_boundary_dispatch() -> None:
    reply_dispatch = _ReplyDispatch(accepted=True)
    node = ControlPlaneLeafReplyDispatchNode(
        reply_dispatch=reply_dispatch,  # type: ignore[arg-type]
    )
    event = ControlPlaneLeafBoundaryOutputsEvent(
        target_group="execution.ingress",
        worker_id="execution.ingress#1",
        request_id="req-1",
        outputs=(Envelope(payload={"x": 1}, target="compute_features"),),
        source_target="source:source",
        tombstone_input=False,
        tombstone_output=False,
    )

    produced = node(event, None)

    assert len(reply_dispatch.sent) == 1
    assert len(produced) == 1
    ack = produced[0]
    assert isinstance(ack, ControlPlaneLeafSinkDispatchAckEvent)
    assert ack.target_group == "execution.ingress"
    assert ack.worker_id == "execution.ingress#1"
    assert ack.request_id == "req-1"
    assert ack.source_target == "source:source"
    assert ack.payload_class == "dict"
    assert ack.tombstone_output is False


def test_leaf_reply_dispatch_node_emits_sink_ack_for_tombstone_output() -> None:
    reply_dispatch = _ReplyDispatch(accepted=True)
    node = ControlPlaneLeafReplyDispatchNode(
        reply_dispatch=reply_dispatch,  # type: ignore[arg-type]
    )
    event = ControlPlaneLeafBoundaryOutputsEvent(
        target_group="execution.ingress",
        worker_id="execution.ingress#1",
        request_id="req-2",
        outputs=(Envelope(payload={"x": 1}, target="compute_features", tombstone=True),),
        source_target="source:source",
        tombstone_input=True,
        tombstone_output=True,
    )

    produced = node(event, None)

    assert len(reply_dispatch.sent) == 1
    assert len(produced) == 1
    ack = produced[0]
    assert isinstance(ack, ControlPlaneLeafSinkDispatchAckEvent)
    assert ack.tombstone_output is True


def test_leaf_source_poll_from_sink_ack_node_emits_next_source_poll_command_for_non_tombstone_ack() -> None:
    node = ControlPlaneLeafSourcePollFromSinkAckNode()
    ack = ControlPlaneLeafSinkDispatchAckEvent(
        target_group="execution.ingress",
        worker_id="execution.ingress#1",
        request_id="req-1",
        source_target="source:source",
        payload_class="dict",
        tombstone_output=False,
    )

    produced = node(ack, None)

    assert len(produced) == 1
    command = produced[0]
    assert isinstance(command, ControlPlaneLeafBoundaryExecuteCommand)
    assert command.target_group == "execution.ingress"
    assert command.worker_id == "execution.ingress#1"
    assert len(command.inputs) == 1
    assert command.inputs[0]["target"] == "source:source"
    assert command.inputs[0]["payload"] == BootstrapControl(target="source:source", single_shot=True)


def test_leaf_source_poll_from_sink_ack_node_does_not_emit_poll_for_tombstone_ack() -> None:
    node = ControlPlaneLeafSourcePollFromSinkAckNode()
    ack = ControlPlaneLeafSinkDispatchAckEvent(
        target_group="execution.ingress",
        worker_id="execution.ingress#1",
        request_id="req-2",
        source_target="source:source",
        payload_class="dict",
        tombstone_output=True,
    )

    produced = node(ack, None)

    assert produced == []


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


def test_leaf_start_work_node_honors_batch_pacing_policy() -> None:
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
                {
                    "scenario_steps": {"source:source": object()},
                    "runtime": {
                        "platform": {
                            "source_ingress": {
                                "pacing_mode": "batch",
                                "batch_size": 3,
                            }
                        }
                    },
                },
            )(),
        },
    )()

    produced = node(
        ControlPlaneLeafStartWorkEvent(source_targets=("source:source",)),
        {"__leaf_session": session},
    )

    assert len(produced) == 1
    command = produced[0]
    assert isinstance(command, ControlPlaneLeafBoundaryExecuteCommand)
    assert len(command.inputs) == 3
    assert all(item["target"] == "source:source" for item in command.inputs)
    assert all(item["payload"] == BootstrapControl(target="source:source", single_shot=True) for item in command.inputs)


def test_leaf_start_work_node_honors_all_pacing_policy() -> None:
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
                {
                    "scenario_steps": {"source:source": object()},
                    "runtime": {
                        "platform": {
                            "source_ingress": {
                                "pacing_mode": "all",
                            }
                        }
                    },
                },
            )(),
        },
    )()

    produced = node(
        ControlPlaneLeafStartWorkEvent(source_targets=("source:source",)),
        {"__leaf_session": session},
    )

    assert len(produced) == 1
    command = produced[0]
    assert isinstance(command, ControlPlaneLeafBoundaryExecuteCommand)
    assert len(command.inputs) == 1
    assert command.inputs[0]["target"] == "source:source"
    assert command.inputs[0]["payload"] == BootstrapControl(target="source:source", single_shot=False)


def test_leaf_source_poll_from_sink_ack_node_honors_batch_size_policy() -> None:
    node = ControlPlaneLeafSourcePollFromSinkAckNode()
    ack = ControlPlaneLeafSinkDispatchAckEvent(
        target_group="execution.ingress",
        worker_id="execution.ingress#1",
        request_id="req-batch",
        source_target="source:source",
        payload_class="dict",
        tombstone_output=False,
    )
    ctx = {
        "__leaf_session": type(
            "_Session",
            (),
            {
                "child": type(
                    "_Child",
                    (),
                    {
                        "runtime": {
                            "platform": {
                                "source_ingress": {
                                    "pacing_mode": "batch",
                                    "batch_size": 4,
                                }
                            }
                        }
                    },
                )()
            },
        )()
    }

    produced = node(ack, ctx)

    assert len(produced) == 1
    command = produced[0]
    assert isinstance(command, ControlPlaneLeafBoundaryExecuteCommand)
    assert len(command.inputs) == 4
    assert all(item["payload"] == BootstrapControl(target="source:source", single_shot=True) for item in command.inputs)


def test_leaf_source_poll_from_sink_ack_node_is_noop_for_all_pacing_policy() -> None:
    node = ControlPlaneLeafSourcePollFromSinkAckNode()
    ack = ControlPlaneLeafSinkDispatchAckEvent(
        target_group="execution.ingress",
        worker_id="execution.ingress#1",
        request_id="req-all",
        source_target="source:source",
        payload_class="dict",
        tombstone_output=False,
    )
    ctx = {
        "__leaf_session": type(
            "_Session",
            (),
            {
                "child": type(
                    "_Child",
                    (),
                    {
                        "runtime": {
                            "platform": {
                                "source_ingress": {
                                    "pacing_mode": "all",
                                }
                            }
                        }
                    },
                )()
            },
        )()
    }

    produced = node(ack, ctx)

    assert produced == []
