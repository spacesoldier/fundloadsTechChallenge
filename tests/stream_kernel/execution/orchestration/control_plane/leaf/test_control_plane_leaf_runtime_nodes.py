from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.control_plane.leaf import (
    ControlPlaneLeafBoundaryExecuteNode,
    ControlPlaneLeafConfigApplyRuntimeNode,
    ControlPlaneLeafStopNode,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime import (
    LeafWorkerRuntimeSession,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
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
    seen: list[tuple[LeafWorkerRuntimeSession, list[object]]] = field(default_factory=list)
    fail_with: Exception | None = None

    def execute(self, *, session: LeafWorkerRuntimeSession, inputs: list[object]) -> list[object]:
        self.seen.append((session, list(inputs)))
        if self.fail_with is not None:
            raise self.fail_with
        return ["ok"]



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

    assert boundary.seen == [(session, [{"payload": 1}])]
    assert len(produced) == 1
    event = produced[0]
    assert isinstance(event, ControlPlaneLeafBoundaryResultEvent)
    assert event.request_id == "req-1"
    assert event.status == "completed"
    assert event.outputs == ("ok",)


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

    assert boundary.seen == [(session, [{"payload": 2}])]
    assert produced == []


def test_leaf_stop_node_emits_ack() -> None:
    node = ControlPlaneLeafStopNode()
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
