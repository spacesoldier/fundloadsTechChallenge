from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime import (
    LeafWorkerRuntimeSession,
)
from stream_kernel.execution.transport.ipc.ipc_transport import ExecutionIpcMessage
from stream_kernel.observability.events import TraceDispatchEvent
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryItemEvent,
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafShutdownPrepareCommand,
    ControlPlaneLeafStartWorkEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
)
from stream_kernel.platform.services.runtime.control_plane_shutdown_readiness import (
    InMemoryControlPlaneLeafShutdownReadinessService,
)
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class _ExecutionIpc:
    incoming: list[object] = field(default_factory=list)
    sent: list[tuple[str, object, bool]] = field(default_factory=list)
    recv_timeouts: list[float | None] = field(default_factory=list)

    def recv(self, target_id: str, *, timeout: float | None = None):
        self.recv_timeouts.append(timeout)
        if not self.incoming:
            return None
        return ExecutionIpcMessage(
            target_id=target_id,
            payload=self.incoming.pop(0),
            ts_epoch_ms=0,
        )

    def send(self, target_id: str, payload: object, *, no_reply: bool = False):
        self.sent.append((target_id, payload, bool(no_reply)))
        return None


@dataclass(slots=True)
class _StopEvent:
    is_set_now: bool = False

    def is_set(self) -> bool:
        return self.is_set_now


def _session() -> LeafWorkerRuntimeSession:
    return LeafWorkerRuntimeSession(
        child=object(),
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="auto",
        runner_profile_effective="sync",
    )


@dataclass(slots=True)
class _Activation:
    mode: str = "applied"
    seen: list[ControlPlaneLeafConfigCardEvent] = field(default_factory=list)

    def apply_config(
        self,
        *,
        session: LeafWorkerRuntimeSession,
        card: ControlPlaneLeafConfigCardEvent,
    ) -> ControlPlaneLeafConfigAckEvent:
        self.seen.append(card)
        if self.mode == "rejected":
            return ControlPlaneLeafConfigAckEvent(
                target_group=session.group_name,
                worker_id=session.worker_id,
                config_id=card.config_id,
                status="rejected",
                error="activation-rejected",
            )
        return ControlPlaneLeafConfigAckEvent(
            target_group=session.group_name,
            worker_id=session.worker_id,
            config_id=card.config_id,
            status="applied",
            resolved_nodes=tuple(card.nodes),
        )


@dataclass(slots=True)
class _BoundaryExecution:
    outputs: tuple[object, ...] = ("out-1",)
    seen: list[tuple[LeafWorkerRuntimeSession, list[object], bool]] = field(default_factory=list)

    def execute(
        self,
        *,
        session: LeafWorkerRuntimeSession,
        inputs: list[object],
        finalize_runtime: bool = False,
    ) -> list[object]:
        self.seen.append((session, list(inputs), finalize_runtime))
        return list(self.outputs)


@dataclass(slots=True)
class _Finalizer:
    seen: list[LeafWorkerRuntimeSession] = field(default_factory=list)

    def finalize(self, *, session: LeafWorkerRuntimeSession) -> None:
        self.seen.append(session)


@dataclass(slots=True)
class _Bootstrapper:
    items: list[object] = field(default_factory=list)
    fail_with: Exception | None = None
    calls: list[dict[str, object]] = field(default_factory=list)

    def discover_all(self, runtime: dict[str, object]) -> list[object]:
        self.calls.append(dict(runtime))
        if self.fail_with is not None:
            raise self.fail_with
        return list(self.items)


@dataclass(slots=True)
class _Discovery:
    seen: list[object] = field(default_factory=list)

    def append_item(self, item: object) -> None:
        self.seen.append(item)


def test_leaf_worker_command_loop_service_applies_config_and_sends_ack() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    ipc = _ExecutionIpc(
        incoming=[
            ControlPlaneLeafConfigCardEvent(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                config_id="cfg-1",
                run_id="run",
                scenario_id="scenario",
                group_name="execution.alpha",
                nodes=("node.a", "node.b"),
                runner_profile="auto",
            )
        ]
    )
    activation = _Activation(mode="applied")
    finalizer = _Finalizer()
    boundary = _BoundaryExecution()
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=activation,
        boundary_execution_service=boundary,
        finalization_service=finalizer,
        execution_ipc=ipc,
    )

    handled = service.run_startup_handshake(
        session=_session(),
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert handled is True
    assert len(activation.seen) == 1
    assert len(ipc.sent) == 1
    ack = ipc.sent[0][1]
    assert isinstance(ack, ControlPlaneLeafConfigAckEvent)
    assert ack.status == "applied"
    assert ack.worker_id == "execution.alpha#1"
    assert ack.target_group == "execution.alpha"


def test_leaf_worker_command_loop_service_sends_rejected_ack_on_error() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    ipc = _ExecutionIpc(
        incoming=[
            ControlPlaneLeafConfigCardEvent(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                config_id="cfg-1",
                run_id="run",
                scenario_id="scenario",
                group_name="execution.alpha",
                nodes=("node.a",),
                runner_profile="auto",
            )
        ]
    )
    activation = _Activation(mode="rejected")
    finalizer = _Finalizer()
    boundary = _BoundaryExecution()
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=activation,
        boundary_execution_service=boundary,
        finalization_service=finalizer,
        execution_ipc=ipc,
    )

    handled = service.run_startup_handshake(
        session=_session(),
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert handled is True
    assert len(ipc.sent) == 1
    ack = ipc.sent[0][1]
    assert isinstance(ack, ControlPlaneLeafConfigAckEvent)
    assert ack.status == "rejected"
    assert ack.error is not None


def test_leaf_worker_command_loop_service_returns_false_when_no_pipe() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    activation = _Activation(mode="applied")
    finalizer = _Finalizer()
    boundary = _BoundaryExecution()
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=activation,
        boundary_execution_service=boundary,
        finalization_service=finalizer,
        execution_ipc=_ExecutionIpc(incoming=[]),
    )

    handled = service.run_startup_handshake(
        session=_session(),
        control_pipe=None,
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert handled is False


def test_leaf_worker_command_loop_service_handles_stop_command_and_sends_ack() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    ipc = _ExecutionIpc(
        incoming=[
            ControlPlaneLeafStopCommand(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                command_id="stop-1",
                reason="shutdown",
            )
        ]
    )
    activation = _Activation(mode="applied")
    finalizer = _Finalizer()
    boundary = _BoundaryExecution()
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=activation,
        boundary_execution_service=boundary,
        finalization_service=finalizer,
        execution_ipc=ipc,
    )
    session = _session()

    result = service.run_control_iteration(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert result == "stop_requested"
    assert finalizer.seen == [session]
    assert len(ipc.sent) == 1
    ack = ipc.sent[0][1]
    assert isinstance(ack, ControlPlaneLeafStopAckEvent)
    assert ack.command_id == "stop-1"


def test_leaf_worker_command_loop_service_sends_stop_ack_before_finalization() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    @dataclass(slots=True)
    class _OrderSensitiveIpc:
        incoming: list[object] = field(default_factory=list)
        sent: list[tuple[str, object, bool]] = field(default_factory=list)
        finalizer_called: bool = False

        def recv(self, target_id: str, *, timeout: float | None = None):
            _ = timeout
            if not self.incoming:
                return None
            return ExecutionIpcMessage(
                target_id=target_id,
                payload=self.incoming.pop(0),
                ts_epoch_ms=0,
            )

        def send(self, target_id: str, payload: object, *, no_reply: bool = False):
            if self.finalizer_called:
                raise RuntimeError("send after finalization is not allowed in this test")
            self.sent.append((target_id, payload, bool(no_reply)))
            return None

    @dataclass(slots=True)
    class _FinalizerAfterAck:
        ipc: _OrderSensitiveIpc
        seen: list[LeafWorkerRuntimeSession] = field(default_factory=list)

        def finalize(self, *, session: LeafWorkerRuntimeSession) -> None:
            self.seen.append(session)
            self.ipc.finalizer_called = True

    ipc = _OrderSensitiveIpc(
        incoming=[
            ControlPlaneLeafStopCommand(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                command_id="stop-2",
                reason="shutdown",
            )
        ]
    )
    finalizer = _FinalizerAfterAck(ipc=ipc)
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=_Activation(mode="applied"),
        boundary_execution_service=_BoundaryExecution(),
        finalization_service=finalizer,
        execution_ipc=ipc,
    )
    session = _session()

    result = service.run_control_iteration(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert result == "stop_requested"
    assert finalizer.seen == [session]
    assert len(ipc.sent) == 1
    payload = ipc.sent[0][1]
    assert isinstance(payload, ControlPlaneLeafStopAckEvent)
    assert payload.command_id == "stop-2"


def test_leaf_worker_command_loop_service_observability_sends_stop_ack_and_finalizes() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    @dataclass(slots=True)
    class _OrderSensitiveIpc:
        incoming: list[object] = field(default_factory=list)
        sent: list[tuple[str, object, bool]] = field(default_factory=list)
        finalizer_called: bool = False

        def recv(self, target_id: str, *, timeout: float | None = None):
            _ = timeout
            if not self.incoming:
                return None
            return ExecutionIpcMessage(
                target_id=target_id,
                payload=self.incoming.pop(0),
                ts_epoch_ms=0,
            )

        def send(self, target_id: str, payload: object, *, no_reply: bool = False):
            self.sent.append((target_id, payload, bool(no_reply)))
            return None

    @dataclass(slots=True)
    class _FinalizerBeforeAck:
        ipc: _OrderSensitiveIpc
        seen: list[LeafWorkerRuntimeSession] = field(default_factory=list)

        def finalize(self, *, session: LeafWorkerRuntimeSession) -> None:
            self.seen.append(session)
            self.ipc.finalizer_called = True

    ipc = _OrderSensitiveIpc(
        incoming=[
            ControlPlaneLeafStopCommand(
                target_group="system.observability",
                worker_id="system.observability#1",
                command_id="stop-obs-1",
                reason="shutdown",
            )
        ]
    )
    finalizer = _FinalizerBeforeAck(ipc=ipc)
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=_Activation(mode="applied"),
        boundary_execution_service=_BoundaryExecution(),
        finalization_service=finalizer,
        execution_ipc=ipc,
    )
    session = LeafWorkerRuntimeSession(
        child=object(),
        worker_id="system.observability#1",
        group_name="system.observability",
        runner_profile_requested="async",
        runner_profile_effective="async",
    )

    result = service.run_control_iteration(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert result == "stop_requested"
    assert finalizer.seen == [session]
    assert len(ipc.sent) == 1
    payload = ipc.sent[0][1]
    assert isinstance(payload, ControlPlaneLeafStopAckEvent)
    assert payload.command_id == "stop-obs-1"
    assert payload.status == "accepted"


def test_leaf_worker_command_loop_service_handles_boundary_execute_and_sends_result() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    ipc = _ExecutionIpc(
        incoming=[
            ControlPlaneLeafBoundaryExecuteCommand(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                request_id="req-1",
                inputs=({"payload": 1},),
                finalize=True,
            )
        ]
    )
    activation = _Activation(mode="applied")
    finalizer = _Finalizer()
    boundary = _BoundaryExecution(outputs=("out-1",))
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=activation,
        boundary_execution_service=boundary,
        finalization_service=finalizer,
        execution_ipc=ipc,
    )
    session = _session()

    result = service.run_control_iteration(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert result == "boundary_executed"
    assert boundary.seen == [(session, [{"payload": 1}], False)]
    assert len(ipc.sent) == 1
    event = ipc.sent[0][1]
    assert isinstance(event, ControlPlaneLeafBoundaryResultEvent)
    assert event.request_id == "req-1"
    assert event.status == "completed"
    assert event.outputs == ("out-1",)


def test_leaf_worker_command_loop_service_streams_large_boundary_results_in_chunks() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    ipc = _ExecutionIpc(
        incoming=[
            ControlPlaneLeafBoundaryExecuteCommand(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                request_id="req-chunk-1",
                inputs=({"payload": 1},),
                finalize=True,
            )
        ]
    )
    boundary = _BoundaryExecution(outputs=tuple(f"out-{index}" for index in range(5)))
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=_Activation(mode="applied"),
        boundary_execution_service=boundary,
        finalization_service=_Finalizer(),
        execution_ipc=ipc,
        boundary_result_chunk_items=2,
    )

    result = service.run_control_iteration(
        session=_session(),
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert result == "boundary_executed"
    sent_events = [payload for _target, payload, _no_reply in ipc.sent]
    assert len(sent_events) == 3
    assert [event.status for event in sent_events] == ["stream", "stream", "completed"]
    assert sent_events[0].outputs == ("out-0", "out-1")
    assert sent_events[1].outputs == ("out-2", "out-3")
    assert sent_events[2].outputs == ("out-4",)


def test_leaf_worker_command_loop_service_executes_observability_boundary_without_result_reply() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    ipc = _ExecutionIpc(
        incoming=[
            ControlPlaneLeafBoundaryExecuteCommand(
                target_group="system.observability",
                worker_id="system.observability#1",
                request_id="req-obs-1",
                inputs=({"trace": 1},),
                finalize=False,
            )
        ]
    )
    activation = _Activation(mode="applied")
    finalizer = _Finalizer()
    boundary = _BoundaryExecution(outputs=("out-obs",))
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=activation,
        boundary_execution_service=boundary,
        finalization_service=finalizer,
        execution_ipc=ipc,
    )
    session = _session()

    result = service.run_control_iteration(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert result == "boundary_executed"
    assert boundary.seen == [(session, [{"trace": 1}], False)]
    assert ipc.sent == []


def test_leaf_worker_command_loop_service_executes_inline_envelope_via_boundary_and_sends_result() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    ipc = _ExecutionIpc(
        incoming=[
            Envelope(
                payload=BootstrapControl(target="source:source"),
                target="source:source",
                trace_id="trace-inline-1",
            )
        ]
    )
    boundary = _BoundaryExecution(outputs=("out-inline",))
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=_Activation(mode="applied"),
        boundary_execution_service=boundary,
        finalization_service=_Finalizer(),
        execution_ipc=ipc,
    )
    session = _session()

    result = service.run_control_iteration(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert result == "boundary_executed"
    assert len(boundary.seen) == 1
    _seen_session, seen_inputs, seen_finalize = boundary.seen[0]
    assert seen_finalize is False
    assert isinstance(seen_inputs, list) and len(seen_inputs) == 1
    inline = seen_inputs[0]
    assert isinstance(inline, dict)
    assert inline.get("dispatch_group") == "execution.alpha"
    assert inline.get("target") == "source:source"
    assert isinstance(inline.get("payload"), BootstrapControl)
    assert inline.get("trace_id") == "trace-inline-1"
    assert len(ipc.sent) == 1
    event = ipc.sent[0][1]
    assert isinstance(event, ControlPlaneLeafBoundaryResultEvent)
    assert event.status == "completed"
    assert event.outputs == ("out-inline",)


def test_leaf_worker_command_loop_service_handles_leaf_start_work_event_without_consumer_registry() -> None:
    from types import SimpleNamespace

    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    ipc = _ExecutionIpc(
        incoming=[
            ControlPlaneLeafStartWorkEvent(source_targets=("source:source",)),
        ]
    )
    boundary = _BoundaryExecution(outputs=("out-start",))
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=_Activation(mode="applied"),
        boundary_execution_service=boundary,
        finalization_service=_Finalizer(),
        execution_ipc=ipc,
    )
    session = LeafWorkerRuntimeSession(
        child=SimpleNamespace(scenario_steps={"source:source": object(), "parse_load_attempt": object()}),
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="async",
        runner_profile_effective="async",
    )

    result = service.run_control_iteration(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert result == "boundary_executed"
    assert len(boundary.seen) == 1
    _seen_session, seen_inputs, seen_finalize = boundary.seen[0]
    assert seen_finalize is False
    assert isinstance(seen_inputs, list) and len(seen_inputs) == 1
    payload = seen_inputs[0]
    assert isinstance(payload, dict)
    assert payload.get("target") == "source:source"
    assert isinstance(payload.get("payload"), BootstrapControl)
    assert len(ipc.sent) == 1
    ack = ipc.sent[0][1]
    assert isinstance(ack, ControlPlaneLeafBoundaryResultEvent)
    assert ack.status == "completed"


def test_leaf_worker_command_loop_service_emits_drain_ready_only_after_shutdown_prepare() -> None:
    from stream_kernel.integration.kv_store import InMemoryKvStore
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    ipc = _ExecutionIpc(
        incoming=[
            ControlPlaneLeafBoundaryExecuteCommand(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                request_id="req-boundary-tomb",
                inputs=({"payload": 1, "tombstone": True},),
                finalize=True,
            ),
            ControlPlaneLeafShutdownPrepareCommand(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                command_id="prepare-1",
            ),
        ]
    )
    boundary = _BoundaryExecution(outputs=("out-after-tomb",))
    leaf_readiness = InMemoryControlPlaneLeafShutdownReadinessService(store=InMemoryKvStore())
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=_Activation(mode="applied"),
        boundary_execution_service=boundary,
        finalization_service=_Finalizer(),
        execution_ipc=ipc,
        leaf_shutdown_readiness_service=leaf_readiness,
    )
    session = _session()

    first = service.run_control_iteration(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )
    assert first == "boundary_executed"
    sent_results_1 = [payload for _target, payload, _nr in ipc.sent if isinstance(payload, ControlPlaneLeafBoundaryResultEvent)]
    sent_drain_ready_1 = [payload for _target, payload, _nr in ipc.sent if isinstance(payload, ControlPlaneLeafDrainReadyEvent)]
    assert len(sent_results_1) == 1
    assert sent_results_1[0].tombstone_input is True
    assert sent_drain_ready_1 == []

    second = service.run_control_iteration(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )
    assert second == "shutdown_prepare_observed"
    sent_drain_ready_2 = [payload for _target, payload, _nr in ipc.sent if isinstance(payload, ControlPlaneLeafDrainReadyEvent)]
    assert len(sent_drain_ready_2) == 1
    assert sent_drain_ready_2[0].target_group == "execution.alpha"
    assert sent_drain_ready_2[0].worker_id == "execution.alpha#1"


def test_leaf_worker_command_loop_service_ignores_leaf_start_work_when_no_local_source_nodes() -> None:
    from types import SimpleNamespace

    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    ipc = _ExecutionIpc(
        incoming=[
            ControlPlaneLeafStartWorkEvent(source_targets=("source:source",)),
        ]
    )
    boundary = _BoundaryExecution(outputs=("out-start",))
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=_Activation(mode="applied"),
        boundary_execution_service=boundary,
        finalization_service=_Finalizer(),
        execution_ipc=ipc,
    )
    session = LeafWorkerRuntimeSession(
        child=SimpleNamespace(scenario_steps={"compute_features": object(), "parse_load_attempt": object()}),
        worker_id="execution.features#1",
        group_name="execution.features",
        runner_profile_requested="async",
        runner_profile_effective="async",
    )

    result = service.run_control_iteration(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert result is None
    assert boundary.seen == []
    assert ipc.sent == []


def test_leaf_worker_command_loop_service_handles_trace_dispatch_event_via_boundary_execution() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    ipc = _ExecutionIpc(
        incoming=[
            TraceDispatchEvent(
                payload={"span": "s1"},
                trace_id="trace-1",
                attributes={"source_node": "compute_features"},
            )
        ]
    )
    boundary = _BoundaryExecution(outputs=())
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=_Activation(mode="applied"),
        boundary_execution_service=boundary,
        finalization_service=_Finalizer(),
        execution_ipc=ipc,
    )
    session = LeafWorkerRuntimeSession(
        child=object(),
        worker_id="system.observability#1",
        group_name="system.observability",
        runner_profile_requested="async",
        runner_profile_effective="async",
    )

    result = service.run_control_iteration(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert result == "boundary_executed"
    assert len(boundary.seen) == 1
    _seen_session, seen_inputs, seen_finalize = boundary.seen[0]
    assert seen_finalize is False
    assert isinstance(seen_inputs, list) and len(seen_inputs) == 1
    payload = seen_inputs[0]
    assert isinstance(payload, dict)
    assert payload.get("dispatch_group") == "system.observability"
    assert payload.get("target") == "system.obs.trace_dispatch"
    assert payload.get("trace_id") == "trace-1"
    assert ipc.sent == []


def test_leaf_worker_command_loop_service_sends_discovery_ack_accepted() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    request = ControlPlaneLeafDiscoveryRequestEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-discovery-1",
        required_nodes=("node.a", "node.b"),
        protocol_revision=2,
    )
    ipc = _ExecutionIpc(incoming=[request])
    bootstrapper = _Bootstrapper(
        items=[
            ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.a"}),
            ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.b"}),
            ControlPlaneDiscoveryItemEvent(item_kind="service", payload={"name": "svc.x"}),
        ]
    )
    discovery = _Discovery()
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=_Activation(mode="applied"),
        boundary_execution_service=_BoundaryExecution(),
        finalization_service=_Finalizer(),
        execution_ipc=ipc,
        bootstrapper=bootstrapper,
        discovery=discovery,
    )

    result = service.run_control_iteration(
        session=_session(),
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert result == "discovery_acknowledged"
    assert len(ipc.sent) == 1
    ack = ipc.sent[0][1]
    assert isinstance(ack, ControlPlaneLeafDiscoveryAckEvent)
    assert ack.status == "accepted"
    assert ack.missing_nodes == ()
    assert ack.discovered_nodes == ("node.a", "node.b")
    assert len(bootstrapper.calls) == 1
    assert len(discovery.seen) == 3


def test_leaf_worker_command_loop_service_sends_discovery_ack_rejected_on_missing_nodes() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    request = ControlPlaneLeafDiscoveryRequestEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-discovery-2",
        required_nodes=("node.a", "node.missing"),
        protocol_revision=2,
    )
    ipc = _ExecutionIpc(incoming=[request])
    bootstrapper = _Bootstrapper(
        items=[ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.a"})]
    )
    discovery = _Discovery()
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=_Activation(mode="applied"),
        boundary_execution_service=_BoundaryExecution(),
        finalization_service=_Finalizer(),
        execution_ipc=ipc,
        bootstrapper=bootstrapper,
        discovery=discovery,
    )

    result = service.run_control_iteration(
        session=_session(),
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert result == "discovery_acknowledged"
    assert len(ipc.sent) == 1
    ack = ipc.sent[0][1]
    assert isinstance(ack, ControlPlaneLeafDiscoveryAckEvent)
    assert ack.status == "rejected"
    assert ack.missing_nodes == ("node.missing",)
    assert ack.error is not None


def test_leaf_worker_command_loop_service_accepts_discovery_transport_aliases_without_metadata() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    request = ControlPlaneLeafDiscoveryRequestEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-discovery-transport-alias",
        required_nodes=("node.a", "sink:sink"),
        protocol_revision=2,
    )
    ipc = _ExecutionIpc(incoming=[request])
    bootstrapper = _Bootstrapper(
        items=[ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.a"})]
    )
    discovery = _Discovery()
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=_Activation(mode="applied"),
        boundary_execution_service=_BoundaryExecution(),
        finalization_service=_Finalizer(),
        execution_ipc=ipc,
        bootstrapper=bootstrapper,
        discovery=discovery,
    )

    result = service.run_control_iteration(
        session=_session(),
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert result == "discovery_acknowledged"
    assert len(ipc.sent) == 1
    ack = ipc.sent[0][1]
    assert isinstance(ack, ControlPlaneLeafDiscoveryAckEvent)
    assert ack.status == "accepted"
    assert ack.missing_nodes == ()
    assert ack.discovered_nodes == ("node.a", "sink:sink")


def test_leaf_worker_command_loop_service_accepts_discovery_handoff_aliases_without_metadata() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    request = ControlPlaneLeafDiscoveryRequestEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-discovery-handoff-alias",
        required_nodes=("node.a", "system.transport.handoff.observability_dispatch"),
        protocol_revision=2,
    )
    ipc = _ExecutionIpc(incoming=[request])
    bootstrapper = _Bootstrapper(
        items=[ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.a"})]
    )
    discovery = _Discovery()
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=_Activation(mode="applied"),
        boundary_execution_service=_BoundaryExecution(),
        finalization_service=_Finalizer(),
        execution_ipc=ipc,
        bootstrapper=bootstrapper,
        discovery=discovery,
    )

    result = service.run_control_iteration(
        session=_session(),
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert result == "discovery_acknowledged"
    assert len(ipc.sent) == 1
    ack = ipc.sent[0][1]
    assert isinstance(ack, ControlPlaneLeafDiscoveryAckEvent)
    assert ack.status == "accepted"
    assert ack.missing_nodes == ()
    assert ack.discovered_nodes == ("node.a", "system.transport.handoff.observability_dispatch")
