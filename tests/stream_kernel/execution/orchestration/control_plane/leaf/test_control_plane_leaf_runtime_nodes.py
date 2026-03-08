from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.control_plane.leaf import (
    ControlPlaneLeafBoundaryExecuteNode,
    ControlPlaneLeafConfigApplyRuntimeNode,
    ControlPlaneLeafStartWorkNode,
    ControlPlaneLeafStopNode,
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
    ControlPlaneLeafStartWorkEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
)
from stream_kernel.routing.envelope import Envelope


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


def test_leaf_start_work_node_emits_bootstrap_controls_for_local_sources() -> None:
    node = ControlPlaneLeafStartWorkNode()
    produced = node(
        ControlPlaneLeafStartWorkEvent(source_targets=("source:source", "source:source")),
        {
            "__leaf_session": type(
                "_Session",
                (),
                {
                    "child": type(
                        "_Child",
                        (),
                        {"scenario_steps": {"source:source": object(), "compute_features": object()}},
                    )()
                },
            )()
        },
    )

    assert produced == [
        Envelope(payload=BootstrapControl(target="source:source"), target="source:source")
    ]


def test_leaf_start_work_node_filters_non_local_sources() -> None:
    node = ControlPlaneLeafStartWorkNode()
    produced = node(
        ControlPlaneLeafStartWorkEvent(source_targets=("source:remote",)),
        {
            "__leaf_session": type(
                "_Session",
                (),
                {
                    "child": type(
                        "_Child",
                        (),
                        {"scenario_steps": {"source:source": object()}},
                    )()
                },
            )()
        },
    )

    assert produced == []


def test_leaf_start_work_node_is_noop_when_group_has_no_local_sources() -> None:
    node = ControlPlaneLeafStartWorkNode()
    produced = node(
        ControlPlaneLeafStartWorkEvent(source_targets=("source:source",)),
        {
            "__leaf_session": type(
                "_Session",
                (),
                {
                    "child": type(
                        "_Child",
                        (),
                        {"scenario_steps": {"compute_features": object(), "parse_load_attempt": object()}},
                    )()
                },
            )()
        },
    )

    assert produced == []
