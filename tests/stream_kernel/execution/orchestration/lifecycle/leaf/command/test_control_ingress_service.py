from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from stream_kernel.execution.orchestration.lifecycle.leaf.command.control_ingress_service import (
    DefaultLeafControlIngressService,
)
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.execution.transport.ipc.ipc_transport import (
    ExecutionIpcControlSignal,
    ExecutionIpcMessage,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafStartWorkEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
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
    stop_after_checks: int = 1_000_000
    checks: int = 0

    def is_set(self) -> bool:
        self.checks += 1
        return self.checks >= self.stop_after_checks


@dataclass(slots=True)
class _ConsumerRegistry:
    routes: dict[object, list[str]] = field(default_factory=dict)

    def get_consumers(self, token: object) -> list[str]:
        return list(self.routes.get(token, ()))


@dataclass(slots=True)
class _Scope:
    registry: _ConsumerRegistry

    def resolve(self, port_type: str, data_type: object, *, qualifier: str | None = None):
        _ = qualifier
        if port_type == "service" and getattr(data_type, "__name__", "") == "ConsumerRegistry":
            return self.registry
        raise LookupError("unexpected resolve request")


def test_leaf_control_ingress_service_does_not_dispatch_when_leaf_nodes_are_missing() -> None:
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
    service = DefaultLeafControlIngressService(execution_ipc=ipc)
    session = SimpleNamespace(group_name="execution.alpha", worker_id="execution.alpha#1")

    status = service.run_until_stopped(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(stop_after_checks=16),
        poll_interval_seconds=0.0001,
    )

    assert status == "stop_event"
    assert ipc.sent == []


def test_leaf_control_ingress_service_honors_stop_event_with_bounded_polling() -> None:
    @dataclass(slots=True)
    class _Finalizer:
        seen: list[object] = field(default_factory=list)

        def finalize(self, *, session: object) -> None:
            self.seen.append(session)

    finalizer = _Finalizer()
    service = DefaultLeafControlIngressService(
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
    service = DefaultLeafControlIngressService(execution_ipc=ipc)
    registry = _ConsumerRegistry(
        routes={
            ControlPlaneLeafConfigCardEvent: ["system.cp.leaf_apply_config"],
            ControlPlaneLeafStopCommand: ["system.cp.leaf_stop"],
        }
    )
    session = SimpleNamespace(
        group_name="execution.alpha",
        worker_id="execution.alpha#1",
        child=SimpleNamespace(
            scenario_scope=_Scope(registry=registry),
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
    replies = [payload for _target, payload, _no_reply in ipc.sent]
    assert any(isinstance(payload, ControlPlaneLeafConfigAckEvent) for payload in replies)
    assert any(isinstance(payload, ControlPlaneLeafStopAckEvent) for payload in replies)
    stop_statuses = {
        payload.status
        for payload in replies
        if isinstance(payload, ControlPlaneLeafStopAckEvent)
    }
    assert stop_statuses >= {"accepted", "completed"}
    assert not any(isinstance(payload, ExecutionIpcControlSignal) for payload in replies)


def test_leaf_control_ingress_service_dispatches_leaf_discovery_ack_to_root() -> None:
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
    service = DefaultLeafControlIngressService(execution_ipc=ipc)
    registry = _ConsumerRegistry(
        routes={
            ControlPlaneLeafDiscoveryRequestEvent: ["system.cp.leaf_discovery"],
            ControlPlaneLeafStopCommand: ["system.cp.leaf_stop"],
        }
    )
    session = SimpleNamespace(
        group_name="execution.alpha",
        worker_id="execution.alpha#1",
        child=SimpleNamespace(
            scenario_scope=_Scope(registry=registry),
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
    replies = [payload for _target, payload, _no_reply in ipc.sent]
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
        execution_ipc=ipc,
        finalization_service=finalizer,
    )
    registry = _ConsumerRegistry(routes={ControlPlaneLeafStopCommand: ["system.cp.leaf_stop"]})
    session = SimpleNamespace(
        group_name="execution.alpha",
        worker_id="execution.alpha#1",
        child=SimpleNamespace(
            scenario_scope=_Scope(registry=registry),
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
    assert len(ipc.sent) == 2
    assert all(isinstance(item[1], ControlPlaneLeafStopAckEvent) for item in ipc.sent)
    statuses = [item[1].status for item in ipc.sent]
    assert statuses == ["accepted", "completed"]
    assert finalizer.seen == [session]


def test_leaf_control_ingress_service_dispatches_via_leaf_boundary_node() -> None:
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
    service = DefaultLeafControlIngressService(execution_ipc=ipc)

    registry = _ConsumerRegistry(
        routes={
            ControlPlaneLeafBoundaryExecuteCommand: ["system.cp.leaf_boundary_execute"],
        }
    )
    session = SimpleNamespace(
        group_name="execution.alpha",
        worker_id="execution.alpha#1",
        child=SimpleNamespace(
            scenario_scope=_Scope(registry=registry),
            scenario_steps={"system.cp.leaf_boundary_execute": _BoundaryNode()},
        ),
    )

    status = service.run_until_stopped(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(stop_after_checks=2),
        poll_interval_seconds=0.0001,
    )

    assert status in {"stop_event", "stop_requested"}
    replies = [payload for _target, payload, _no_reply in ipc.sent]
    assert any(isinstance(payload, ControlPlaneLeafBoundaryResultEvent) for payload in replies)
    assert not any(isinstance(payload, ExecutionIpcControlSignal) for payload in replies)


def test_leaf_control_ingress_service_dispatches_leaf_start_work_inline_via_boundary() -> None:
    seen_boundary: list[ControlPlaneLeafBoundaryExecuteCommand] = []

    class _StartWorkNode:
        def __call__(self, msg: object, _ctx: object | None):
            assert isinstance(msg, ControlPlaneLeafStartWorkEvent)
            return [Envelope(payload=BootstrapControl(target="source:source"), target="source:source")]

    class _BoundaryNode:
        def __call__(self, msg: object, _ctx: object | None):
            assert isinstance(msg, ControlPlaneLeafBoundaryExecuteCommand)
            seen_boundary.append(msg)
            assert msg.finalize is True
            return [
                ControlPlaneLeafBoundaryResultEvent(
                    target_group=msg.target_group,
                    worker_id=msg.worker_id,
                    request_id=msg.request_id,
                    status="completed",
                    outputs=({"target": msg.inputs[0].get("target")},),
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
            ControlPlaneLeafStartWorkEvent(source_targets=("source:source",)),
            ControlPlaneLeafStopCommand(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                command_id="stop-1",
            ),
        ]
    )
    service = DefaultLeafControlIngressService(execution_ipc=ipc)
    registry = _ConsumerRegistry(
        routes={
            ControlPlaneLeafStartWorkEvent: ["system.cp.leaf_start_work"],
            ControlPlaneLeafBoundaryExecuteCommand: ["system.cp.leaf_boundary_execute"],
            ControlPlaneLeafStopCommand: ["system.cp.leaf_stop"],
        }
    )
    session = SimpleNamespace(
        group_name="execution.alpha",
        worker_id="execution.alpha#1",
        child=SimpleNamespace(
            scenario_scope=_Scope(registry=registry),
            scenario_steps={
                "system.cp.leaf_start_work": _StartWorkNode(),
                "system.cp.leaf_boundary_execute": _BoundaryNode(),
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
    assert len(seen_boundary) == 1
    replies = [payload for _target, payload, _no_reply in ipc.sent]
    assert any(isinstance(payload, ControlPlaneLeafBoundaryResultEvent) for payload in replies)
    assert any(isinstance(payload, ControlPlaneLeafStopAckEvent) for payload in replies)
    stop_statuses = {
        payload.status
        for payload in replies
        if isinstance(payload, ControlPlaneLeafStopAckEvent)
    }
    assert stop_statuses >= {"accepted", "completed"}


def test_leaf_control_ingress_service_uses_nonblocking_poll_in_hot_path() -> None:
    ipc = _ExecutionIpc(
        incoming=[
            ControlPlaneLeafStopCommand(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                command_id="stop-nonblocking",
            )
        ]
    )
    service = DefaultLeafControlIngressService(execution_ipc=ipc)
    session = SimpleNamespace(group_name="execution.alpha", worker_id="execution.alpha#1")

    status = service.run_until_stopped(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(stop_after_checks=3),
        poll_interval_seconds=0.05,
    )

    assert status == "stop_event"
    assert ipc.recv_timeouts
    assert all(timeout == 0.0 for timeout in ipc.recv_timeouts)
    assert not any(isinstance(payload, ExecutionIpcControlSignal) for _, payload, _ in ipc.sent)


def test_leaf_control_ingress_service_ignores_inbound_transport_ack_signal() -> None:
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
    service = DefaultLeafControlIngressService(execution_ipc=ipc)
    session = SimpleNamespace(group_name="execution.alpha", worker_id="execution.alpha#1")

    status = service.run_until_stopped(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(stop_after_checks=16),
        poll_interval_seconds=0.0001,
    )

    assert status == "stop_event"
    assert not any(isinstance(payload, ExecutionIpcControlSignal) for _, payload, _ in ipc.sent)
