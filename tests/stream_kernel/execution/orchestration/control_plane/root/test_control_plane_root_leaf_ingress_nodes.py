from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_nodes import (
    ControlPlaneRootLeafIngressEnvelopeEvent,
    ControlPlaneRootBoundaryHandoffSinkNode,
    ControlPlaneRootLeafIngressSourceNode,
    ROOT_BOUNDARY_HANDOFF_SINK_NODE_NAME,
    root_leaf_ingress_source_node_name,
)
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.execution.transport.handoff.system_nodes import (
    OBSERVABILITY_HANDOFF_NODE_NAME,
)
from stream_kernel.execution.transport.ipc.ipc_transport import EXECUTION_IPC_LANE_DATA
from stream_kernel.observability.events import TraceDispatchEvent
from stream_kernel.platform.services.runtime.platform_scheduler import (
    PlatformSchedulerUpsertCommand,
)
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class _IngressStub:
    payload: object | None
    dispatch_calls: int = 0
    last_timeout_seconds: float | None = None

    def poll_next_leaf_ingress_for_worker_lane(
        self,
        *,
        worker_id: str,
        lane: str,
        timeout_seconds: float = 0.0,
    ) -> object | None:
        _ = (worker_id, lane)
        self.last_timeout_seconds = timeout_seconds
        value = self.payload
        self.payload = None
        return value

    def dispatch_polled_leaf_ingress(
        self,
        *,
        worker_id: str,
        payload: object,
        lane: str | None = None,
    ) -> bool:
        _ = (worker_id, payload, lane)
        self.dispatch_calls += 1
        return True


@dataclass(slots=True)
class _BoundaryHandoffStub:
    calls: list[tuple[list[Envelope], str | None, bool]] = field(default_factory=list)

    def drain_external_deliveries(
        self,
        *,
        envelopes: list[Envelope],
        source_group: str | None = None,
        pump_replies: bool = True,
    ) -> list[object]:
        self.calls.append((list(envelopes), source_group, pump_replies))
        return []


@dataclass(slots=True)
class _RunnerControlStub:
    stopped: bool = False

    def stop_requested(self) -> bool:
        return self.stopped


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


def test_root_leaf_ingress_source_node_emits_envelope_without_dispatch_call() -> None:
    source_name = root_leaf_ingress_source_node_name(
        worker_id="execution.ingress#1",
        lane=EXECUTION_IPC_LANE_DATA,
    )
    payload = Envelope(payload={"v": 1}, target="compute_time_keys")
    ingress = _IngressStub(payload=payload)
    node = ControlPlaneRootLeafIngressSourceNode(
        ingress=ingress,
        runner_control=_RunnerControlStub(stopped=False),
        worker_id="execution.ingress#1",
        lane=EXECUTION_IPC_LANE_DATA,
        source_name=source_name,
    )
    outputs = node(
        Envelope(payload=BootstrapControl(target=source_name), target=source_name), None
    )
    outputs = asyncio.run(outputs)

    assert any(isinstance(item, ControlPlaneRootLeafIngressEnvelopeEvent) for item in outputs)
    event = next(item for item in outputs if isinstance(item, ControlPlaneRootLeafIngressEnvelopeEvent))
    assert event.worker_id == "execution.ingress#1"
    assert event.lane == EXECUTION_IPC_LANE_DATA
    assert isinstance(event.envelope, Envelope)
    assert ingress.dispatch_calls == 0
    assert isinstance(ingress.last_timeout_seconds, float)
    assert ingress.last_timeout_seconds == 0.0


def test_root_leaf_ingress_source_initialize_registers_scheduler_job() -> None:
    source_name = root_leaf_ingress_source_node_name(
        worker_id="execution.ingress#1",
        lane=EXECUTION_IPC_LANE_DATA,
    )
    node = ControlPlaneRootLeafIngressSourceNode(
        ingress=_IngressStub(payload=None),
        runner_control=_RunnerControlStub(stopped=False),
        worker_id="execution.ingress#1",
        lane=EXECUTION_IPC_LANE_DATA,
        source_name=source_name,
    )

    produced = node.initialize()
    assert len(produced) == 1
    command = produced[0]
    assert isinstance(command, PlatformSchedulerUpsertCommand)
    assert command.target == source_name


def test_root_leaf_ingress_source_node_stops_rearm_when_runner_stop_requested() -> None:
    source_name = root_leaf_ingress_source_node_name(
        worker_id="execution.ingress#1",
        lane=EXECUTION_IPC_LANE_DATA,
    )
    ingress = _IngressStub(payload={"ignored": True})
    node = ControlPlaneRootLeafIngressSourceNode(
        ingress=ingress,
        runner_control=_RunnerControlStub(stopped=True),
        worker_id="execution.ingress#1",
        lane=EXECUTION_IPC_LANE_DATA,
        source_name=source_name,
    )

    outputs = asyncio.run(
        node(
            Envelope(payload=BootstrapControl(target=source_name), target=source_name),
            None,
        )
    )

    assert outputs == []


def test_root_leaf_ingress_source_node_drops_unknown_payload_without_rearm() -> None:
    source_name = root_leaf_ingress_source_node_name(
        worker_id="execution.ingress#1",
        lane=EXECUTION_IPC_LANE_DATA,
    )
    ingress = _IngressStub(payload={"unexpected": True})
    node = ControlPlaneRootLeafIngressSourceNode(
        ingress=ingress,
        runner_control=_RunnerControlStub(stopped=False),
        worker_id="execution.ingress#1",
        lane=EXECUTION_IPC_LANE_DATA,
        source_name=source_name,
    )

    outputs = asyncio.run(
        node(
            Envelope(payload=BootstrapControl(target=source_name), target=source_name),
            None,
        )
    )

    assert outputs == []


def test_root_boundary_handoff_sink_node_normalizes_observability_relay_target() -> None:
    handoff = _BoundaryHandoffStub()
    node = ControlPlaneRootBoundaryHandoffSinkNode(handoff=handoff)
    payload = ControlPlaneRootLeafIngressEnvelopeEvent(
        worker_id="execution.egress#1",
        lane=EXECUTION_IPC_LANE_DATA,
        envelope=Envelope(
            payload=TraceDispatchEvent(payload={"span": 1}, trace_id="t-1"),
            target=OBSERVABILITY_HANDOFF_NODE_NAME,
        ),
    )

    produced = node(payload, None)

    assert produced == []
    assert len(handoff.calls) == 1
    envelopes, source_group, pump_replies = handoff.calls[0]
    assert source_group == "execution.egress"
    assert pump_replies is False
    assert len(envelopes) == 1
    assert envelopes[0].target == "system.obs.trace_dispatch"
    assert ROOT_BOUNDARY_HANDOFF_SINK_NODE_NAME == "system.cp.root_boundary_handoff_sink"


def test_root_boundary_handoff_sink_node_passes_envelope_to_handoff() -> None:
    handoff = _BoundaryHandoffStub()
    node = ControlPlaneRootBoundaryHandoffSinkNode(handoff=handoff)
    event = ControlPlaneRootLeafIngressEnvelopeEvent(
        worker_id="execution.features#1",
        lane=EXECUTION_IPC_LANE_DATA,
        envelope=Envelope(payload={"k": 1}, target="evaluate_policies"),
    )

    produced = node(event, None)

    assert produced == []
    assert len(handoff.calls) == 1
    envelopes, source_group, pump_replies = handoff.calls[0]
    assert source_group == "execution.features"
    assert pump_replies is False
    assert len(envelopes) == 1
    assert envelopes[0].target == "evaluate_policies"
