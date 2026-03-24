from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

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
    leaf_runtime_ingress_drain_source_node_name,
    leaf_runtime_ingress_source_lanes,
)
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime import (
    LeafWorkerRuntimeSession,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafBoundaryOutputsEvent,
    ControlPlaneLeafSinkDispatchAckEvent,
    ControlPlaneLeafRunnerTombstoneEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafStartWorkEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
)
from stream_kernel.routing.envelope import Envelope
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_TRACE,
    EXECUTION_IPC_LANE_DATA,
    EXECUTION_IPC_LANE_LOG,
    EXECUTION_IPC_LANE_METRIC,
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
    seen_runner: list[ControlPlaneLeafRunnerTombstoneEvent] = field(default_factory=list)

    def observe_runner_tombstone(
        self,
        event: ControlPlaneLeafRunnerTombstoneEvent,
    ) -> ControlPlaneLeafDrainReadyEvent:
        self.seen_runner.append(event)
        return ControlPlaneLeafDrainReadyEvent(
            target_group=event.target_group,
            worker_id=event.worker_id,
            request_id=event.request_id,
            tombstone_output=event.tombstone_output,
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


@dataclass(slots=True)
class _HandoffDispatch:
    sent: list[tuple[Envelope, str | None]] = field(default_factory=list)
    accepted: bool = True

    def dispatch_envelope(self, envelope: Envelope, *, source_group: str | None = None) -> bool:
        self.sent.append((envelope, source_group))
        return bool(self.accepted)


@dataclass(slots=True)
class _WakeQueue:
    items: list[Envelope] = field(default_factory=list)

    def push(self, envelope: Envelope) -> None:
        self.items.append(envelope)


@dataclass(slots=True)
class _WakeIngress:
    seen: list[tuple[str, str, float]] = field(default_factory=list)
    callbacks: list[tuple[str, str, object]] = field(default_factory=list)
    payload: object | None = None

    def register_data_available_callback(
        self,
        *,
        worker_id: str,
        lane: str,
        callback: object,
        loop: object | None = None,
    ) -> bool:
        _ = loop
        self.callbacks.append((worker_id, lane, callback))
        return True

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
class _WakeIngressUnavailable:
    seen: list[tuple[str, str, float]] = field(default_factory=list)
    register_calls: int = 0

    def register_data_available_callback(
        self,
        *,
        worker_id: str,
        lane: str,
        callback: object,
        loop: object | None = None,
    ) -> bool:
        _ = (worker_id, lane, callback, loop)
        self.register_calls += 1
        return False

    def poll_next_message_for_lane(
        self,
        *,
        worker_id: str,
        lane: str,
        timeout_seconds: float = 0.0,
    ) -> object | None:
        self.seen.append((worker_id, lane, timeout_seconds))
        return None


def test_leaf_command_ingress_source_node_polls_single_lane_and_self_rearms_on_budget() -> None:
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
        max_messages_per_poll=1,
    )

    produced = asyncio.run(node(BootstrapControl(target=source_name), None))

    assert len(produced) == 2
    assert isinstance(produced[0], ControlPlaneLeafStopCommand)
    assert isinstance(produced[1], BootstrapControl)
    assert produced[1].target == source_name
    assert len(ingress.seen) == 1
    worker_id, lane, timeout = ingress.seen[0]
    assert isinstance(worker_id, str) and worker_id
    assert lane == EXECUTION_IPC_LANE_TRACE
    assert timeout == 0.0



def test_leaf_runtime_ingress_source_wraps_envelope_into_boundary_command() -> None:
    source_name = leaf_runtime_ingress_drain_source_node_name()
    ingress_payload = Envelope(
        payload={"id": 42},
        target="compute_features",
        trace_id="trace-42",
        reply_to="reply-42",
        span_id="span-42",
        tombstone=True,
    )
    ingress = _LaneIngress(seen=[], payload=ingress_payload)
    node = ControlPlaneLeafCommandIngressSourceNode(
        ingress=ingress,  # type: ignore[arg-type]
        runner_control=_RunnerControlStopFlag(),  # type: ignore[arg-type]
        lane=EXECUTION_IPC_LANE_DATA,
        source_name=source_name,
        max_messages_per_poll=1,
    )
    ctx = {
        "__leaf_session": SimpleNamespace(
            child=SimpleNamespace(
                runtime={
                    "__process_group": "execution.features",
                    "__worker_id": "execution.features#1",
                }
            )
        )
    }

    produced = asyncio.run(node(BootstrapControl(target=source_name), ctx))

    assert len(produced) == 2
    command = produced[0]
    assert isinstance(command, ControlPlaneLeafBoundaryExecuteCommand)
    assert command.target_group == "execution.features"
    assert command.worker_id == "execution.features#1"
    assert command.finalize is True
    assert len(command.inputs) == 1
    item = command.inputs[0]
    assert isinstance(item, dict)
    assert item["target"] == "compute_features"
    assert item["payload"] == {"id": 42}
    assert item["trace_id"] == "trace-42"
    assert item["reply_to"] == "reply-42"
    assert item["span_id"] == "span-42"
    assert item["tombstone"] is True
    assert isinstance(produced[1], BootstrapControl)
    assert produced[1].target == source_name


def test_leaf_runtime_ingress_source_uses_session_group_when_runtime_has_no_process_group() -> None:
    source_name = leaf_runtime_ingress_drain_source_node_name()
    ingress_payload = Envelope(
        payload={"id": 7},
        target="compute_features",
        trace_id="trace-7",
        reply_to=None,
        span_id=None,
        tombstone=False,
    )
    ingress = _LaneIngress(seen=[], payload=ingress_payload)
    node = ControlPlaneLeafCommandIngressSourceNode(
        ingress=ingress,  # type: ignore[arg-type]
        runner_control=_RunnerControlStopFlag(),  # type: ignore[arg-type]
        lane=EXECUTION_IPC_LANE_DATA,
        source_name=source_name,
        max_messages_per_poll=1,
    )
    ctx = {
        "__leaf_session": SimpleNamespace(
            worker_id="execution.features#1",
            group_name="execution.features",
            child=SimpleNamespace(
                process_group="execution.features",
                runtime={"platform": {}},
            ),
        )
    }

    produced = asyncio.run(node(BootstrapControl(target=source_name), ctx))

    assert len(produced) == 2
    command = produced[0]
    assert isinstance(command, ControlPlaneLeafBoundaryExecuteCommand)
    assert command.target_group == "execution.features"
    assert command.worker_id == "execution.features#1"
    assert command.inputs[0]["dispatch_group"] == "execution.features"


def test_leaf_command_ingress_source_lanes_include_control_only() -> None:
    assert leaf_command_ingress_source_lanes() == (EXECUTION_IPC_LANE_CONTROL,)


def test_leaf_runtime_ingress_source_lanes_include_non_control_lanes() -> None:
    assert leaf_runtime_ingress_source_lanes() == (
        EXECUTION_IPC_LANE_DATA,
        EXECUTION_IPC_LANE_TRACE,
        EXECUTION_IPC_LANE_LOG,
        EXECUTION_IPC_LANE_METRIC,
    )


def test_leaf_command_ingress_source_initialize_emits_bootstrap_pulse() -> None:
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
    assert isinstance(command, BootstrapControl)
    assert command.target == source_name


def test_leaf_command_ingress_source_initialize_registers_wakeup_callback_and_coalesces() -> None:
    source_name = leaf_command_ingress_source_node_name(lane=EXECUTION_IPC_LANE_TRACE)
    ingress = _WakeIngress()
    queue = _WakeQueue()
    node = ControlPlaneLeafCommandIngressSourceNode(
        ingress=ingress,  # type: ignore[arg-type]
        runner_control=_RunnerControlStopFlag(),  # type: ignore[arg-type]
        lane=EXECUTION_IPC_LANE_TRACE,
        poll_worker_ids=("execution.alpha#1",),
        source_name=source_name,
        work_queue=queue,  # type: ignore[arg-type]
    )

    _ = node.initialize()
    assert len(ingress.callbacks) == 1
    _worker_id, _lane, callback = ingress.callbacks[0]
    assert _worker_id == "execution.alpha#1"
    assert _lane == EXECUTION_IPC_LANE_TRACE
    assert callable(callback)

    callback()
    callback()
    assert len(queue.items) == 1
    assert isinstance(queue.items[0].payload, BootstrapControl)
    assert queue.items[0].target == source_name

    asyncio.run(node(queue.items[0], None))
    callback()
    assert len(queue.items) == 2


def test_leaf_command_ingress_source_node_self_rearms_when_wakeup_unavailable() -> None:
    source_name = leaf_command_ingress_source_node_name(lane=EXECUTION_IPC_LANE_CONTROL)
    ingress = _WakeIngressUnavailable()
    node = ControlPlaneLeafCommandIngressSourceNode(
        ingress=ingress,  # type: ignore[arg-type]
        runner_control=_RunnerControlStopFlag(),  # type: ignore[arg-type]
        lane=EXECUTION_IPC_LANE_CONTROL,
        poll_worker_ids=("execution.alpha#1",),
        source_name=source_name,
        max_messages_per_poll=8,
    )

    bootstrap = node.initialize()[0]
    assert isinstance(bootstrap, BootstrapControl)
    assert ingress.register_calls >= 1

    produced = asyncio.run(node(bootstrap, None))

    assert len(produced) == 1
    assert isinstance(produced[0], BootstrapControl)
    assert produced[0].target == source_name
    assert ingress.seen == [("execution.alpha#1", EXECUTION_IPC_LANE_CONTROL, 0.0)]


def test_leaf_command_ingress_source_node_polls_configured_worker_ids_round_robin() -> None:
    source_name = leaf_command_ingress_source_node_name(lane=EXECUTION_IPC_LANE_TRACE)
    event = ControlPlaneLeafStopCommand(
        target_group="system.observability",
        worker_id="execution.features#1",
        command_id="stop-obs",
    )

    @dataclass(slots=True)
    class _MultiWorkerIngress:
        calls: list[tuple[str, str, float]] = field(default_factory=list)

        def poll_next_message_for_lane(
            self,
            *,
            worker_id: str,
            lane: str,
            timeout_seconds: float = 0.0,
        ) -> object | None:
            self.calls.append((worker_id, lane, timeout_seconds))
            if worker_id == "execution.features#1":
                return event
            return None

    ingress = _MultiWorkerIngress()
    node = ControlPlaneLeafCommandIngressSourceNode(
        ingress=ingress,  # type: ignore[arg-type]
        runner_control=_RunnerControlStopFlag(),  # type: ignore[arg-type]
        lane=EXECUTION_IPC_LANE_TRACE,
        poll_worker_ids=("execution.ingress#1", "execution.features#1"),
        source_name=source_name,
        max_messages_per_poll=1,
    )

    produced = asyncio.run(node(BootstrapControl(target=source_name), None))

    assert len(produced) == 2
    assert produced[0] == event
    assert isinstance(produced[1], BootstrapControl)
    assert produced[1].target == source_name
    assert ingress.calls == [
        ("execution.ingress#1", EXECUTION_IPC_LANE_TRACE, 0.0),
        ("execution.features#1", EXECUTION_IPC_LANE_TRACE, 0.0),
    ]


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

    assert len(produced) == 2
    final_event = produced[0]
    assert isinstance(final_event, ControlPlaneLeafBoundaryOutputsEvent)
    assert len(final_event.outputs) == 2
    assert isinstance(final_event.outputs[0], Envelope)
    assert isinstance(final_event.outputs[1], Envelope)
    assert final_event.outputs[0].tombstone is False
    assert final_event.outputs[1].tombstone is True
    runner_event = produced[1]
    assert isinstance(runner_event, ControlPlaneLeafRunnerTombstoneEvent)
    assert runner_event.observed_node == "system.cp.leaf_boundary_execute"


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

    assert len(produced) == 2
    boundary_event = produced[0]
    assert isinstance(boundary_event, ControlPlaneLeafBoundaryOutputsEvent)
    assert boundary_event.tombstone_input is True
    assert boundary_event.tombstone_output is True
    assert len(boundary_event.outputs) == 1
    assert isinstance(boundary_event.outputs[0], Envelope)
    assert boundary_event.outputs[0].tombstone is True
    runner_event = produced[1]
    assert isinstance(runner_event, ControlPlaneLeafRunnerTombstoneEvent)
    assert runner_event.observed_node == "system.cp.leaf_boundary_execute"
    assert runner_event.expected_nodes == ("system.cp.leaf_boundary_execute",)
    assert runner_event.tombstone_output is True


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

    assert len(produced) == 2
    completed = produced[0]
    assert isinstance(completed, ControlPlaneLeafBoundaryOutputsEvent)
    assert completed.tombstone_input is True
    assert completed.tombstone_output is True
    assert completed.outputs == ()
    runner_event = produced[1]
    assert isinstance(runner_event, ControlPlaneLeafRunnerTombstoneEvent)
    assert runner_event.observed_node == "system.cp.leaf_boundary_execute"
    assert runner_event.expected_nodes == ("system.cp.leaf_boundary_execute",)
    assert runner_event.tombstone_output is True


def test_leaf_tombstone_finalize_node_ignores_boundary_outputs_event() -> None:
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

    assert produced == []
    assert len(readiness.seen_runner) == 0


def test_leaf_tombstone_finalize_node_ignores_sink_dispatch_ack_event() -> None:
    readiness = _Readiness()
    node = ControlPlaneLeafTombstoneFinalizeNode(readiness=readiness)  # type: ignore[arg-type]
    ack = ControlPlaneLeafSinkDispatchAckEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-terminal-tombstone",
        source_target="source:source",
        payload_class="Envelope",
        tombstone_output=True,
    )

    produced = node(ack, {"__leaf_session": _session()})

    assert produced == []
    assert len(readiness.seen_runner) == 0


def test_leaf_tombstone_finalize_node_accepts_runner_tombstone_event() -> None:
    readiness = _Readiness()
    node = ControlPlaneLeafTombstoneFinalizeNode(readiness=readiness)  # type: ignore[arg-type]
    runner_event = ControlPlaneLeafRunnerTombstoneEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="runner-tombstone:req-1",
        observed_node="sink:egress",
        expected_nodes=("source:ingress", "transform.features", "sink:egress"),
        tombstone_output=True,
    )

    produced = node(runner_event, {"__leaf_session": _session()})

    assert len(produced) == 1
    assert isinstance(produced[0], ControlPlaneLeafDrainReadyEvent)
    assert len(readiness.seen_runner) == 1


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
    assert len(acks) == 1
    ack = acks[0]
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
    assert command.inputs[0]["payload"] == BootstrapControl(target="source:source", single_shot=True)


def test_leaf_reply_dispatch_node_emits_sink_ack_after_successful_boundary_dispatch() -> None:
    reply_dispatch = _ReplyDispatch(accepted=True)
    handoff_dispatch = _HandoffDispatch(accepted=True)
    node = ControlPlaneLeafReplyDispatchNode(
        reply_dispatch=reply_dispatch,  # type: ignore[arg-type]
        handoff_dispatch=handoff_dispatch,  # type: ignore[arg-type]
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

    assert len(reply_dispatch.sent) == 0
    assert len(handoff_dispatch.sent) == 1
    assert handoff_dispatch.sent[0][0] == event.outputs[0]
    assert handoff_dispatch.sent[0][1] == "execution.ingress"
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
    handoff_dispatch = _HandoffDispatch(accepted=True)
    node = ControlPlaneLeafReplyDispatchNode(
        reply_dispatch=reply_dispatch,  # type: ignore[arg-type]
        handoff_dispatch=handoff_dispatch,  # type: ignore[arg-type]
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

    assert len(reply_dispatch.sent) == 0
    assert len(handoff_dispatch.sent) == 1
    assert len(produced) == 1
    ack = produced[0]
    assert isinstance(ack, ControlPlaneLeafSinkDispatchAckEvent)
    assert ack.tombstone_output is True


def test_leaf_reply_dispatch_node_keeps_sink_ack_when_only_observability_dispatch_fails() -> None:
    reply_dispatch = _ReplyDispatch(accepted=True)
    handoff_dispatch = _HandoffDispatch(accepted=False)
    node = ControlPlaneLeafReplyDispatchNode(
        reply_dispatch=reply_dispatch,  # type: ignore[arg-type]
        handoff_dispatch=handoff_dispatch,  # type: ignore[arg-type]
    )
    event = ControlPlaneLeafBoundaryOutputsEvent(
        target_group="execution.features",
        worker_id="execution.features#1",
        request_id="req-obs-fail",
        outputs=(Envelope(payload={"trace": 1}, target="system.obs.trace_dispatch"),),
        source_target="source:source",
        tombstone_input=False,
        tombstone_output=False,
    )

    produced = node(event, None)

    assert len(handoff_dispatch.sent) == 1
    assert len(produced) == 1
    assert isinstance(produced[0], ControlPlaneLeafSinkDispatchAckEvent)


def test_leaf_reply_dispatch_node_does_not_emit_sink_ack_when_business_dispatch_fails() -> None:
    reply_dispatch = _ReplyDispatch(accepted=True)
    handoff_dispatch = _HandoffDispatch(accepted=False)
    node = ControlPlaneLeafReplyDispatchNode(
        reply_dispatch=reply_dispatch,  # type: ignore[arg-type]
        handoff_dispatch=handoff_dispatch,  # type: ignore[arg-type]
    )
    event = ControlPlaneLeafBoundaryOutputsEvent(
        target_group="execution.features",
        worker_id="execution.features#1",
        request_id="req-biz-fail",
        outputs=(Envelope(payload={"attempt": 1}, target="compute_features"),),
        source_target="source:source",
        tombstone_input=False,
        tombstone_output=False,
    )

    produced = node(event, None)

    assert len(handoff_dispatch.sent) == 1
    assert produced == []


def test_leaf_reply_dispatch_node_routes_control_events_via_reply_channel() -> None:
    reply_dispatch = _ReplyDispatch(accepted=True)
    handoff_dispatch = _HandoffDispatch(accepted=True)
    node = ControlPlaneLeafReplyDispatchNode(
        reply_dispatch=reply_dispatch,  # type: ignore[arg-type]
        handoff_dispatch=handoff_dispatch,  # type: ignore[arg-type]
    )
    event = ControlPlaneLeafConfigAckEvent(
        target_group="execution.ingress",
        worker_id="execution.ingress#1",
        config_id="cfg-1",
        status="applied",
    )

    produced = node(event, None)

    assert produced == []
    assert len(reply_dispatch.sent) == 1
    assert reply_dispatch.sent[0][0] == "execution.ingress#1"
    assert reply_dispatch.sent[0][1] == event
    assert handoff_dispatch.sent == []


def test_leaf_reply_dispatch_node_logs_when_drain_ready_dispatch_fails(monkeypatch) -> None:
    seen_debug: list[dict[str, object]] = []

    def _capture_leaf_debug(*, event: str, service: object | None = None, **fields: object) -> None:
        _ = service
        seen_debug.append({"event": event, **fields})

    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.control_plane.leaf.system_nodes.leaf_debug_log",
        _capture_leaf_debug,
    )
    reply_dispatch = _ReplyDispatch(accepted=False)
    handoff_dispatch = _HandoffDispatch(accepted=True)
    node = ControlPlaneLeafReplyDispatchNode(
        reply_dispatch=reply_dispatch,  # type: ignore[arg-type]
        handoff_dispatch=handoff_dispatch,  # type: ignore[arg-type]
    )
    event = ControlPlaneLeafDrainReadyEvent(
        target_group="execution.egress",
        worker_id="execution.egress#1",
        request_id="drain:req-1",
        tombstone_output=True,
    )

    produced = node(event, None)

    assert produced == []
    assert any(item.get("event") == "leaf.node.reply_dispatch.drain_ready_failed" for item in seen_debug)


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
