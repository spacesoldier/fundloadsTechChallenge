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
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.observability.events import TraceDispatchEvent
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
class _HandoffDispatchStub:
    calls: list[tuple[Envelope, str | None]] = field(default_factory=list)

    def dispatch_envelope(
        self,
        envelope: Envelope,
        *,
        source_group: str | None = None,
    ) -> bool:
        self.calls.append((envelope, source_group))
        return True


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


@dataclass(slots=True)
class _WakeQueue:
    items: list[Envelope] = field(default_factory=list)

    def push(self, envelope: Envelope) -> None:
        self.items.append(envelope)


@dataclass(slots=True)
class _WakeIngressStub:
    callbacks: list[tuple[str, str, object]] = field(default_factory=list)

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

    def poll_next_leaf_ingress_for_worker_lane(
        self,
        *,
        worker_id: str,
        lane: str,
        timeout_seconds: float = 0.0,
    ) -> object | None:
        _ = (worker_id, lane, timeout_seconds)
        return None


@dataclass(slots=True)
class _DynamicSpecsIngressStub(_WakeIngressStub):
    dynamic_specs: tuple[tuple[str, str], ...] = (("execution.features#1", "control"),)

    def worker_lane_specs_for_polling(self) -> tuple[tuple[str, str], ...]:
        return self.dynamic_specs


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


def test_root_leaf_ingress_source_initialize_emits_bootstrap_pulse() -> None:
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
    assert isinstance(command, BootstrapControl)
    assert command.target == source_name


def test_root_leaf_ingress_source_initialize_registers_wakeup_callback_and_coalesces() -> None:
    source_name = root_leaf_ingress_source_node_name(
        worker_id="execution.ingress#1",
        lane=EXECUTION_IPC_LANE_DATA,
    )
    ingress = _WakeIngressStub()
    queue = _WakeQueue()
    node = ControlPlaneRootLeafIngressSourceNode(
        ingress=ingress,  # type: ignore[arg-type]
        runner_control=_RunnerControlStub(stopped=False),
        worker_id="execution.ingress#1",
        lane=EXECUTION_IPC_LANE_DATA,
        source_name=source_name,
        work_queue=queue,  # type: ignore[arg-type]
    )

    _ = node.initialize()
    assert len(ingress.callbacks) == 1
    worker_id, lane, callback = ingress.callbacks[0]
    assert worker_id == "execution.ingress#1"
    assert lane == EXECUTION_IPC_LANE_DATA
    assert callable(callback)

    callback()
    callback()
    assert len(queue.items) == 1
    assert isinstance(queue.items[0].payload, BootstrapControl)
    assert queue.items[0].target == source_name

    asyncio.run(node(queue.items[0], None))
    callback()
    assert len(queue.items) == 2


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


def test_root_leaf_ingress_source_node_self_rearms_when_specs_are_not_ready() -> None:
    source_name = "source:system.cp.root_leaf_ingress"
    ingress = _IngressStub(payload=None)
    node = ControlPlaneRootLeafIngressSourceNode(
        ingress=ingress,
        runner_control=_RunnerControlStub(stopped=False),
        worker_id=None,
        lane="control",
        ingress_specs=(),
        source_name=source_name,
    )

    outputs = asyncio.run(
        node(
            Envelope(payload=BootstrapControl(target=source_name), target=source_name),
            None,
        )
    )

    assert len(outputs) == 1
    assert isinstance(outputs[0], BootstrapControl)
    assert outputs[0].target == source_name


def test_root_leaf_ingress_source_initialize_registers_dynamic_wakeup_specs() -> None:
    source_name = "source:system.cp.root_leaf_ingress"
    ingress = _DynamicSpecsIngressStub()
    node = ControlPlaneRootLeafIngressSourceNode(
        ingress=ingress,  # type: ignore[arg-type]
        runner_control=_RunnerControlStub(stopped=False),
        worker_id=None,
        lane="control",
        ingress_specs=(),
        source_name=source_name,
        work_queue=_WakeQueue(),  # type: ignore[arg-type]
    )

    _ = node.initialize()

    assert len(ingress.callbacks) == 1
    worker_id, lane, _ = ingress.callbacks[0]
    assert worker_id == "execution.features#1"
    assert lane == "control"


def test_root_leaf_ingress_source_node_emits_log_message_payload() -> None:
    source_name = root_leaf_ingress_source_node_name(
        worker_id="system.observability#1",
        lane=EXECUTION_IPC_LANE_DATA,
    )
    log_message = LogMessage(level="info", message="observability-relay")
    ingress = _IngressStub(payload=log_message)
    node = ControlPlaneRootLeafIngressSourceNode(
        ingress=ingress,
        runner_control=_RunnerControlStub(stopped=False),
        worker_id="system.observability#1",
        lane=EXECUTION_IPC_LANE_DATA,
        source_name=source_name,
    )

    outputs = asyncio.run(
        node(
            Envelope(payload=BootstrapControl(target=source_name), target=source_name),
            None,
        )
    )

    assert outputs == [log_message]


def test_root_boundary_handoff_sink_node_normalizes_observability_relay_target() -> None:
    handoff = _HandoffDispatchStub()
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
    envelope, source_group = handoff.calls[0]
    assert source_group == "execution.egress"
    assert envelope.target == "system.obs.trace_dispatch"
    assert ROOT_BOUNDARY_HANDOFF_SINK_NODE_NAME == "system.cp.root_boundary_handoff_sink"


def test_root_boundary_handoff_sink_node_passes_envelope_to_handoff() -> None:
    handoff = _HandoffDispatchStub()
    node = ControlPlaneRootBoundaryHandoffSinkNode(handoff=handoff)
    event = ControlPlaneRootLeafIngressEnvelopeEvent(
        worker_id="execution.features#1",
        lane=EXECUTION_IPC_LANE_DATA,
        envelope=Envelope(payload={"k": 1}, target="evaluate_policies"),
    )

    produced = node(event, None)

    assert produced == []
    assert len(handoff.calls) == 1
    envelope, source_group = handoff.calls[0]
    assert source_group == "execution.features"
    assert envelope.target == "evaluate_policies"
