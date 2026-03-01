from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from stream_kernel.execution.orchestration.lifecycle.leaf.command.control_ingress_service import (
    DefaultLeafControlIngressService,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    ExecutionIpcControlSignal,
    ExecutionIpcMessage,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
)


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
    stop_after_checks: int = 1_000_000
    checks: int = 0

    def is_set(self) -> bool:
        self.checks += 1
        return self.checks >= self.stop_after_checks


def test_leaf_control_ingress_service_drains_typed_commands_and_stops_on_stop_requested() -> None:
    seen: list[object] = []

    class _CommandLoop:
        def handle_control_message(self, *, session, control_pipe, message):
            _ = (session, control_pipe)
            seen.append(message)
            if isinstance(message, ControlPlaneLeafStopCommand):
                return "stop_requested"
            return None

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
                runner_profile="async",
            ),
            ControlPlaneLeafStopCommand(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                command_id="stop-1",
            ),
        ]
    )
    service = DefaultLeafControlIngressService(command_loop_service=_CommandLoop(), execution_ipc=ipc)
    session = SimpleNamespace(group_name="execution.alpha", worker_id="execution.alpha#1")

    status = service.run_until_stopped(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(stop_after_checks=1000),
        poll_interval_seconds=0.0001,
    )

    assert status == "stop_requested"
    assert len(seen) == 2
    assert isinstance(seen[0], ControlPlaneLeafConfigCardEvent)
    assert isinstance(seen[1], ControlPlaneLeafStopCommand)


def test_leaf_control_ingress_service_honors_stop_event_with_bounded_polling() -> None:
    class _CommandLoop:
        def handle_control_message(self, **_kwargs):
            raise AssertionError("no command should be handled when ingress receives no messages")

    @dataclass(slots=True)
    class _Finalizer:
        seen: list[object] = field(default_factory=list)

        def finalize(self, *, session: object) -> None:
            self.seen.append(session)

    finalizer = _Finalizer()
    service = DefaultLeafControlIngressService(
        command_loop_service=_CommandLoop(),
        execution_ipc=_ExecutionIpc(),
        finalization_service=finalizer,
    )
    session = SimpleNamespace(group_name="execution.alpha", worker_id="execution.alpha#1")
    stop_event = _StopEvent(stop_after_checks=3)

    status = service.run_until_stopped(
        session=session,
        control_pipe=object(),
        stop_event=stop_event,
        poll_interval_seconds=0.0001,
    )

    assert status == "stop_event"
    assert stop_event.checks >= 3
    assert finalizer.seen == [session]


def test_leaf_control_ingress_service_dispatches_via_leaf_nodes_for_config_and_stop() -> None:
    class _CommandLoop:
        def handle_control_message(self, **_kwargs):
            raise AssertionError("phase C: command-loop path must not handle control messages directly")

    class _ConsumerRegistry:
        def get_consumers(self, token: object) -> list[str]:
            if token is ControlPlaneLeafConfigCardEvent:
                return ["system.cp.leaf_apply_config"]
            if token is ControlPlaneLeafStopCommand:
                return ["system.cp.leaf_stop"]
            return []

    class _Scope:
        def resolve(self, port_type: str, data_type: object, *, qualifier: str | None = None):
            _ = qualifier
            if port_type == "service" and getattr(data_type, "__name__", "") == "ConsumerRegistry":
                return _ConsumerRegistry()
            raise LookupError("unexpected resolve request")

    class _ConfigNode:
        def __call__(self, msg: object, _ctx: object | None):
            assert isinstance(msg, ControlPlaneLeafConfigCardEvent)
            return [
                ControlPlaneLeafConfigAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    config_id=msg.config_id,
                    status="applied",
                    resolved_nodes=tuple(msg.nodes),
                )
            ]

    class _StopNode:
        def __call__(self, msg: object, _ctx: object | None):
            assert isinstance(msg, ControlPlaneLeafStopCommand)
            return [
                ControlPlaneLeafStopAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    command_id=msg.command_id,
                    status="accepted",
                )
            ]

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
                runner_profile="async",
            ),
            ControlPlaneLeafStopCommand(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                command_id="stop-1",
            ),
        ]
    )
    service = DefaultLeafControlIngressService(command_loop_service=_CommandLoop(), execution_ipc=ipc)
    session = SimpleNamespace(
        group_name="execution.alpha",
        worker_id="execution.alpha#1",
        child=SimpleNamespace(
            scenario_scope=_Scope(),
            scenario_steps={
                "system.cp.leaf_apply_config": _ConfigNode(),
                "system.cp.leaf_stop": _StopNode(),
            },
        ),
    )
    status = service.run_until_stopped(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(stop_after_checks=1000),
        poll_interval_seconds=0.0001,
    )

    assert status == "stop_requested"
    control_replies = [
        payload
        for _target_id, payload, _no_reply in ipc.sent
        if isinstance(payload, (ControlPlaneLeafConfigAckEvent, ControlPlaneLeafStopAckEvent))
    ]
    transport_acks = [
        payload for _target_id, payload, _no_reply in ipc.sent if isinstance(payload, ExecutionIpcControlSignal)
    ]
    assert len(control_replies) == 2
    assert isinstance(control_replies[0], ControlPlaneLeafConfigAckEvent)
    assert isinstance(control_replies[1], ControlPlaneLeafStopAckEvent)
    assert not transport_acks


def test_leaf_control_ingress_service_dispatches_leaf_discovery_ack_to_root() -> None:
    class _CommandLoop:
        def handle_control_message(self, **_kwargs):
            raise AssertionError("leaf discovery must be handled by system.cp.leaf_discovery node")

    class _ConsumerRegistry:
        def get_consumers(self, token: object) -> list[str]:
            if token is ControlPlaneLeafDiscoveryRequestEvent:
                return ["system.cp.leaf_discovery"]
            if token is ControlPlaneLeafStopCommand:
                return ["system.cp.leaf_stop"]
            return []

    class _Scope:
        def resolve(self, port_type: str, data_type: object, *, qualifier: str | None = None):
            _ = qualifier
            if port_type == "service" and getattr(data_type, "__name__", "") == "ConsumerRegistry":
                return _ConsumerRegistry()
            raise LookupError("unexpected resolve request")

    class _DiscoveryNode:
        def __call__(self, msg: object, _ctx: object | None):
            assert isinstance(msg, ControlPlaneLeafDiscoveryRequestEvent)
            return [
                ControlPlaneLeafDiscoveryAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    request_id=msg.request_id,
                    status="accepted",
                    discovered_nodes=tuple(msg.required_nodes),
                    missing_nodes=(),
                )
            ]

    class _StopNode:
        def __call__(self, msg: object, _ctx: object | None):
            assert isinstance(msg, ControlPlaneLeafStopCommand)
            return [
                ControlPlaneLeafStopAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    command_id=msg.command_id,
                    status="accepted",
                )
            ]

    ipc = _ExecutionIpc(
        incoming=[
            ControlPlaneLeafDiscoveryRequestEvent(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                request_id="req-discovery",
                required_nodes=("node.a",),
                protocol_revision=2,
            ),
            ControlPlaneLeafStopCommand(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                command_id="stop-1",
            ),
        ]
    )
    service = DefaultLeafControlIngressService(command_loop_service=_CommandLoop(), execution_ipc=ipc)
    session = SimpleNamespace(
        group_name="execution.alpha",
        worker_id="execution.alpha#1",
        child=SimpleNamespace(
            scenario_scope=_Scope(),
            scenario_steps={
                "system.cp.leaf_discovery": _DiscoveryNode(),
                "system.cp.leaf_stop": _StopNode(),
            },
        ),
    )

    status = service.run_until_stopped(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(stop_after_checks=1000),
        poll_interval_seconds=0.0001,
    )

    assert status == "stop_requested"
    replies = [payload for _target_id, payload, _no_reply in ipc.sent]
    assert any(isinstance(payload, ControlPlaneLeafDiscoveryAckEvent) for payload in replies)
    assert any(isinstance(payload, ControlPlaneLeafStopAckEvent) for payload in replies)


def test_leaf_control_ingress_service_finalizes_session_after_leaf_stop_ack_dispatch() -> None:
    @dataclass(slots=True)
    class _OrderSensitiveIpc:
        incoming: list[object] = field(default_factory=list)
        sent: list[tuple[str, object, bool]] = field(default_factory=list)
        finalized: bool = False

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
            if self.finalized:
                raise RuntimeError("ack must be sent before finalization")
            self.sent.append((target_id, payload, bool(no_reply)))
            return None

    @dataclass(slots=True)
    class _Finalizer:
        ipc: _OrderSensitiveIpc
        seen: list[object] = field(default_factory=list)

        def finalize(self, *, session: object) -> None:
            self.seen.append(session)
            self.ipc.finalized = True

    class _CommandLoop:
        def handle_control_message(self, **_kwargs):
            raise AssertionError("leaf stop should be handled by system.cp.leaf_stop node")

    class _ConsumerRegistry:
        def get_consumers(self, token: object) -> list[str]:
            if token is ControlPlaneLeafStopCommand:
                return ["system.cp.leaf_stop"]
            return []

    class _Scope:
        def resolve(self, port_type: str, data_type: object, *, qualifier: str | None = None):
            _ = qualifier
            if port_type == "service" and getattr(data_type, "__name__", "") == "ConsumerRegistry":
                return _ConsumerRegistry()
            raise LookupError("unexpected resolve request")

    class _StopNode:
        def __call__(self, msg: object, _ctx: object | None):
            assert isinstance(msg, ControlPlaneLeafStopCommand)
            return [
                ControlPlaneLeafStopAckEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    command_id=msg.command_id,
                    status="accepted",
                )
            ]

    ipc = _OrderSensitiveIpc(
        incoming=[
            ControlPlaneLeafStopCommand(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                command_id="stop-order",
            )
        ]
    )
    finalizer = _Finalizer(ipc=ipc)
    service = DefaultLeafControlIngressService(
        command_loop_service=_CommandLoop(),
        execution_ipc=ipc,
        finalization_service=finalizer,
    )
    session = SimpleNamespace(
        group_name="execution.alpha",
        worker_id="execution.alpha#1",
        child=SimpleNamespace(
            scenario_scope=_Scope(),
            scenario_steps={"system.cp.leaf_stop": _StopNode()},
        ),
    )

    status = service.run_until_stopped(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(stop_after_checks=1000),
        poll_interval_seconds=0.0001,
    )

    assert status == "stop_requested"
    assert len(ipc.sent) == 1
    assert isinstance(ipc.sent[0][1], ControlPlaneLeafStopAckEvent)
    assert finalizer.seen == [session]


def test_leaf_control_ingress_service_dispatches_via_leaf_boundary_node() -> None:
    class _CommandLoop:
        def handle_control_message(self, **_kwargs):
            raise AssertionError("phase C: command-loop path must not handle boundary messages directly")

    class _ConsumerRegistry:
        def get_consumers(self, token: object) -> list[str]:
            if token is ControlPlaneLeafBoundaryExecuteCommand:
                return ["system.cp.leaf_boundary_execute"]
            return []

    class _Scope:
        def resolve(self, port_type: str, data_type: object, *, qualifier: str | None = None):
            _ = qualifier
            if port_type == "service" and getattr(data_type, "__name__", "") == "ConsumerRegistry":
                return _ConsumerRegistry()
            raise LookupError("unexpected resolve request")

    class _BoundaryNode:
        def __call__(self, msg: object, _ctx: object | None):
            assert isinstance(msg, ControlPlaneLeafBoundaryExecuteCommand)
            return [
                ControlPlaneLeafBoundaryResultEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    request_id=msg.request_id,
                    status="completed",
                    outputs=({"ok": True, "count": len(msg.inputs)},),
                )
            ]

    ipc = _ExecutionIpc(
        incoming=[
            ControlPlaneLeafBoundaryExecuteCommand(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                request_id="req-1",
                inputs=({"payload": 1},),
                finalize=True,
            ),
            ControlPlaneLeafStopCommand(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                command_id="stop-1",
            ),
        ]
    )
    service = DefaultLeafControlIngressService(command_loop_service=_CommandLoop(), execution_ipc=ipc)
    session = SimpleNamespace(
        group_name="execution.alpha",
        worker_id="execution.alpha#1",
        child=SimpleNamespace(
            scenario_scope=_Scope(),
            scenario_steps={
                "system.cp.leaf_boundary_execute": _BoundaryNode(),
            },
        ),
    )
    class _StopRegistry:
        def get_consumers(self, token: object) -> list[str]:
            if token is ControlPlaneLeafBoundaryExecuteCommand:
                return ["system.cp.leaf_boundary_execute"]
            if token is ControlPlaneLeafStopCommand:
                return []
            return []

    session.child.scenario_scope.resolve = lambda port_type, data_type, qualifier=None: _StopRegistry()  # type: ignore[method-assign]
    status = service.run_until_stopped(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(stop_after_checks=2),
        poll_interval_seconds=0.0001,
    )

    assert status in {"stop_event", "stop_requested"}
    boundary_replies = [
        payload for _target_id, payload, _no_reply in ipc.sent if isinstance(payload, ControlPlaneLeafBoundaryResultEvent)
    ]
    transport_acks = [
        payload for _target_id, payload, _no_reply in ipc.sent if isinstance(payload, ExecutionIpcControlSignal)
    ]
    assert len(boundary_replies) >= 1
    assert not transport_acks


def test_leaf_control_ingress_service_uses_nonblocking_poll_in_hot_path() -> None:
    class _CommandLoop:
        def handle_control_message(self, *, session, control_pipe, message):
            _ = (session, control_pipe)
            if isinstance(message, ControlPlaneLeafStopCommand):
                return "stop_requested"
            return None

    ipc = _ExecutionIpc(
        incoming=[
            ControlPlaneLeafStopCommand(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                command_id="stop-nonblocking",
            )
        ]
    )
    service = DefaultLeafControlIngressService(command_loop_service=_CommandLoop(), execution_ipc=ipc)
    session = SimpleNamespace(group_name="execution.alpha", worker_id="execution.alpha#1")

    status = service.run_until_stopped(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(stop_after_checks=1000),
        poll_interval_seconds=0.05,
    )

    assert status == "stop_requested"
    assert ipc.recv_timeouts
    assert all(timeout == 0.0 for timeout in ipc.recv_timeouts)
    assert not any(isinstance(payload, ExecutionIpcControlSignal) for _, payload, _ in ipc.sent)


def test_leaf_control_ingress_service_ignores_inbound_transport_ack_signal() -> None:
    seen: list[object] = []

    class _CommandLoop:
        def handle_control_message(self, *, session, control_pipe, message):
            _ = (session, control_pipe)
            seen.append(message)
            if isinstance(message, ControlPlaneLeafStopCommand):
                return "stop_requested"
            return None

    ipc = _ExecutionIpc(
        incoming=[
            ExecutionIpcControlSignal(kind="ack", count=5),
            ControlPlaneLeafStopCommand(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                command_id="stop-after-transport-ack",
            ),
        ]
    )
    service = DefaultLeafControlIngressService(command_loop_service=_CommandLoop(), execution_ipc=ipc)
    session = SimpleNamespace(group_name="execution.alpha", worker_id="execution.alpha#1")

    status = service.run_until_stopped(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(stop_after_checks=1000),
        poll_interval_seconds=0.0001,
    )

    assert status == "stop_requested"
    assert len(seen) == 1
    assert isinstance(seen[0], ControlPlaneLeafStopCommand)
    assert not any(isinstance(payload, ExecutionIpcControlSignal) for _, payload, _ in ipc.sent)
