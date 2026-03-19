from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.execution.transport.ipc.ipc_transport import ExecutionIpcMessage
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneGroupSpec,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafDiscoverySnapshotEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafHelloEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)
from stream_kernel.routing.envelope import Envelope
from tests.stream_kernel.execution.orchestration.control_plane.leaf_ingress_helpers import (
    drain_worker_replies,
)


@dataclass(slots=True)
class _IpcPort:
    incoming_by_target: dict[str, list[ExecutionIpcMessage]] = field(default_factory=dict)
    sends: list[dict[str, object]] = field(default_factory=list)
    recv_calls: list[tuple[str, float | None]] = field(default_factory=list)
    recv_buffered_calls: list[tuple[str, float | None]] = field(default_factory=list)
    fail_recv: bool = False

    def recv(self, target_id: str, *, timeout: float | None = None):
        self.recv_calls.append((target_id, timeout))
        if self.fail_recv:
            raise AssertionError("recv() should not be used when recv_buffered() is available")
        _ = timeout
        queue = self.incoming_by_target.setdefault(target_id, [])
        if not queue:
            return None
        return queue.pop(0)

    def recv_buffered(self, target_id: str, *, timeout: float | None = None):
        self.recv_buffered_calls.append((target_id, timeout))
        _ = timeout
        queue = self.incoming_by_target.setdefault(target_id, [])
        if not queue:
            return None
        return queue.pop(0)

    def send(self, target_id: str, payload: object, *, no_reply: bool = False):
        self.sends.append({"target_id": target_id, "payload": payload, "no_reply": no_reply})
        return None


@dataclass(slots=True)
class _FailingSendIpcPort(_IpcPort):
    fail_on_payload_type: type[object] | None = None

    def send(self, target_id: str, payload: object, *, no_reply: bool = False):
        if self.fail_on_payload_type is not None and isinstance(payload, self.fail_on_payload_type):
            raise ConnectionError("simulated send failure")
        return super().send(target_id, payload, no_reply=no_reply)


@dataclass(slots=True)
class _NoBufferedRecvIpcPort:
    recv_calls: list[tuple[str, float | None]] = field(default_factory=list)

    def recv(self, target_id: str, *, timeout: float | None = None):
        self.recv_calls.append((target_id, timeout))
        raise AssertionError("recv() fallback path must not be used")

    def send(self, target_id: str, payload: object, *, no_reply: bool = False):
        _ = (target_id, payload, no_reply)
        return None


@dataclass(slots=True)
class _CallbackIpcPort:
    callbacks: list[tuple[str, object]] = field(default_factory=list)

    def register_data_available_callback(
        self,
        target_id: str,
        callback: object,
        *,
        loop: object | None = None,
    ) -> bool:
        _ = loop
        self.callbacks.append((target_id, callback))
        return True


def test_root_leaf_ingress_service_polls_envelope_payload() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
        DefaultControlPlaneRootLeafIngressService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    ipc = _IpcPort(
        incoming_by_target={
            "execution.alpha#1": [
                ExecutionIpcMessage(
                    target_id="execution.alpha#1",
                    payload=Envelope(
                        target="compute_time_keys",
                        payload={"out": 1},
                    ),
                    ts_epoch_ms=1,
                )
            ]
        }
    )
    service = DefaultControlPlaneRootLeafIngressService(state=state, execution_ipc=ipc)

    payload = service.poll_next_leaf_ingress_for_worker_lane(
        worker_id="execution.alpha#1",
        lane="control",
        timeout_seconds=0.0,
    )

    assert isinstance(payload, Envelope)
    assert payload.target == "compute_time_keys"


def test_root_leaf_ingress_service_dispatch_does_not_handle_envelope_payload() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
        DefaultControlPlaneRootLeafIngressService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    service = DefaultControlPlaneRootLeafIngressService(state=state, execution_ipc=_IpcPort())
    payload = Envelope(target="compute_time_keys", payload={"req": "dispatch"})

    handled = service.dispatch_polled_leaf_ingress(
        worker_id="execution.alpha#1",
        payload=payload,
        lane="control",
    )

    assert handled is False
    assert not any(isinstance(item, Envelope) for item in state.events())


def test_root_leaf_ingress_service_prefers_buffered_recv_when_available() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
        DefaultControlPlaneRootLeafIngressService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    ipc = _IpcPort(
        incoming_by_target={
            "execution.alpha#1": [
                ExecutionIpcMessage(
                    target_id="execution.alpha#1",
                    payload=Envelope(target="compute_time_keys", payload={"out": 1}),
                    ts_epoch_ms=1,
                )
            ]
        }
    )
    service = DefaultControlPlaneRootLeafIngressService(state=state, execution_ipc=ipc)

    payload = service.poll_next_leaf_ingress_for_worker_lane(
        worker_id="execution.alpha#1",
        lane="control",
        timeout_seconds=0.0,
    )

    assert isinstance(payload, Envelope)
    assert payload.target == "compute_time_keys"
    assert len(ipc.recv_buffered_calls) == 1


def test_root_leaf_ingress_service_returns_none_when_buffered_recv_is_unavailable() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
        DefaultControlPlaneRootLeafIngressService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    ipc = _NoBufferedRecvIpcPort()
    service = DefaultControlPlaneRootLeafIngressService(state=state, execution_ipc=ipc)

    payload = service.poll_next_leaf_ingress_for_worker_lane(
        worker_id="execution.alpha#1",
        lane="control",
        timeout_seconds=0.0,
    )

    assert payload is None
    assert ipc.recv_calls == []


def test_root_leaf_ingress_service_registers_data_available_callback_for_lane_target() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
        DefaultControlPlaneRootLeafIngressService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    control = _CallbackIpcPort()
    data = _CallbackIpcPort()
    trace = _CallbackIpcPort()
    log = _CallbackIpcPort()
    metric = _CallbackIpcPort()
    service = DefaultControlPlaneRootLeafIngressService(
        state=state,
        control_lane_ipc=control,  # type: ignore[arg-type]
        data_lane_ipc=data,  # type: ignore[arg-type]
        trace_lane_ipc=trace,  # type: ignore[arg-type]
        log_lane_ipc=log,  # type: ignore[arg-type]
        metric_lane_ipc=metric,  # type: ignore[arg-type]
    )
    callback = lambda: None

    accepted = service.register_data_available_callback(
        worker_id="execution.alpha#1",
        lane="data",
        callback=callback,
    )

    assert accepted is True
    assert data.callbacks == [("execution.alpha#1::data", callback)]


def test_root_leaf_ingress_service_processes_leaf_hello_and_sends_config_card() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
        DefaultControlPlaneRootLeafIngressService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.alpha",
                        workers=1,
                        nodes=("node.a", "node.b"),
                    ),
                )
            )
        )
    )
    ipc = _IpcPort(
        incoming_by_target={
            "execution.alpha#1": [
                ExecutionIpcMessage(
                    target_id="execution.alpha#1",
                    payload=ControlPlaneLeafHelloEvent(
                        target_group="execution.alpha",
                        worker_id="execution.alpha#1",
                        pid=123,
                    ),
                    ts_epoch_ms=1,
                )
            ]
        }
    )
    service = DefaultControlPlaneRootLeafIngressService(state=state, execution_ipc=ipc)

    drained = drain_worker_replies(service, worker_id="execution.alpha#1", timeout_seconds=0.0)

    assert drained == 1
    assert len(ipc.sends) == 1
    sent = ipc.sends[0]
    assert sent["target_id"] == "execution.alpha#1"
    assert sent["no_reply"] is True
    assert isinstance(sent["payload"], ControlPlaneLeafConfigCardEvent)
    events = state.events()
    assert any(isinstance(item, ControlPlaneLeafHelloEvent) for item in events)
    assert any(isinstance(item, ControlPlaneLeafConfigCardEvent) for item in events)


def test_root_leaf_ingress_service_v2_processes_leaf_hello_and_sends_discovery_request() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
        DefaultControlPlaneRootLeafIngressService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.alpha",
                        workers=1,
                        nodes=("node.a", "node.b"),
                    ),
                )
            )
        )
    )
    ipc = _IpcPort(
        incoming_by_target={
            "execution.alpha#1": [
                ExecutionIpcMessage(
                    target_id="execution.alpha#1",
                    payload=ControlPlaneLeafHelloEvent(
                        target_group="execution.alpha",
                        worker_id="execution.alpha#1",
                        pid=123,
                    ),
                    ts_epoch_ms=1,
                )
            ]
        }
    )
    service = DefaultControlPlaneRootLeafIngressService(
        state=state,
        execution_ipc=ipc,
        startup_protocol_revision=2,
    )

    drained = drain_worker_replies(service, worker_id="execution.alpha#1", timeout_seconds=0.0)

    assert drained == 1
    assert len(ipc.sends) == 1
    sent = ipc.sends[0]
    assert sent["target_id"] == "execution.alpha#1"
    assert sent["no_reply"] is True
    assert isinstance(sent["payload"], ControlPlaneLeafDiscoveryRequestEvent)
    payload = sent["payload"]
    assert payload.required_nodes == ("node.a", "node.b")
    assert payload.protocol_revision == 2


def test_root_leaf_ingress_service_v2_discovery_ack_accepted_sends_config_card() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
        DefaultControlPlaneRootLeafIngressService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.alpha",
                        workers=1,
                        nodes=("node.a", "node.b"),
                    ),
                )
            )
        )
    )
    state.append_event(
        ControlPlaneLeafHelloEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            pid=123,
            runner_profile="async",
        )
    )
    ipc = _IpcPort(
        incoming_by_target={
            "execution.alpha#1": [
                ExecutionIpcMessage(
                    target_id="execution.alpha#1",
                    payload=ControlPlaneLeafDiscoveryAckEvent(
                        target_group="execution.alpha",
                        worker_id="execution.alpha#1",
                        request_id="req-1",
                        status="accepted",
                        discovered_nodes=("node.a", "node.b"),
                    ),
                    ts_epoch_ms=1,
                )
            ]
        }
    )
    service = DefaultControlPlaneRootLeafIngressService(
        state=state,
        execution_ipc=ipc,
        startup_protocol_revision=2,
    )

    drained = drain_worker_replies(service, worker_id="execution.alpha#1", timeout_seconds=0.0)

    assert drained == 1
    assert len(ipc.sends) == 1
    sent = ipc.sends[0]
    assert isinstance(sent["payload"], ControlPlaneLeafConfigCardEvent)
    card = sent["payload"]
    assert card.worker_id == "execution.alpha#1"
    assert card.nodes == ("node.a", "node.b")
    assert card.runner_profile == "async"


def test_root_leaf_ingress_service_v2_discovery_ack_rejected_does_not_send_config() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
        DefaultControlPlaneRootLeafIngressService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.alpha",
                        workers=1,
                        nodes=("node.a", "node.b"),
                    ),
                )
            )
        )
    )
    ipc = _IpcPort(
        incoming_by_target={
            "execution.alpha#1": [
                ExecutionIpcMessage(
                    target_id="execution.alpha#1",
                    payload=ControlPlaneLeafDiscoveryAckEvent(
                        target_group="execution.alpha",
                        worker_id="execution.alpha#1",
                        request_id="req-1",
                        status="rejected",
                        missing_nodes=("node.b",),
                        error="missing runtime metadata",
                    ),
                    ts_epoch_ms=1,
                )
            ]
        }
    )
    service = DefaultControlPlaneRootLeafIngressService(
        state=state,
        execution_ipc=ipc,
        startup_protocol_revision=2,
    )

    drained = drain_worker_replies(service, worker_id="execution.alpha#1", timeout_seconds=0.0)

    assert drained == 1
    assert ipc.sends == []


def test_root_leaf_ingress_service_v3_sends_discovery_snapshot_card() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
        DefaultControlPlaneRootLeafIngressService,
    )

    @dataclass(slots=True)
    class _SnapshotBuilder:
        def build_snapshot(
            self,
            *,
            hello: ControlPlaneLeafHelloEvent,
            protocol_revision: int,
        ) -> ControlPlaneLeafDiscoverySnapshotEvent | None:
            return ControlPlaneLeafDiscoverySnapshotEvent(
                target_group=hello.target_group,
                worker_id=hello.worker_id,
                request_id="req-snapshot-1",
                required_nodes=("node.a", "node.b"),
                snapshot_records=(
                    ControlPlaneDiscoveryEntityRecord(
                        entity_kind="node",
                        entity_id="node:node.a",
                        source_scope="project",
                        module="pkg.module",
                        qualname="node_a",
                        meta={"name": "node.a"},
                    ),
                ),
                protocol_revision=protocol_revision,
            )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    ipc = _IpcPort(
        incoming_by_target={
            "execution.alpha#1": [
                ExecutionIpcMessage(
                    target_id="execution.alpha#1",
                    payload=ControlPlaneLeafHelloEvent(
                        target_group="execution.alpha",
                        worker_id="execution.alpha#1",
                        pid=123,
                    ),
                    ts_epoch_ms=1,
                )
            ]
        }
    )
    service = DefaultControlPlaneRootLeafIngressService(
        state=state,
        execution_ipc=ipc,
        startup_protocol_revision=3,
        snapshot_builder=_SnapshotBuilder(),
    )

    drained = drain_worker_replies(service, worker_id="execution.alpha#1", timeout_seconds=0.0)

    assert drained == 1
    assert len(ipc.sends) == 1
    sent = ipc.sends[0]
    assert isinstance(sent["payload"], ControlPlaneLeafDiscoverySnapshotEvent)
    payload = sent["payload"]
    assert payload.required_nodes == ("node.a", "node.b")
    assert payload.protocol_revision == 3


def test_root_leaf_ingress_service_v3_without_snapshot_marks_leaf_rejected() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
        DefaultControlPlaneRootLeafIngressService,
    )

    @dataclass(slots=True)
    class _SnapshotBuilder:
        def build_snapshot(
            self,
            *,
            hello: ControlPlaneLeafHelloEvent,
            protocol_revision: int,
        ) -> ControlPlaneLeafDiscoverySnapshotEvent | None:
            _ = hello
            _ = protocol_revision
            return None

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.alpha",
                        workers=1,
                        nodes=("node.a", "node.b"),
                    ),
                )
            )
        )
    )
    ipc = _IpcPort(
        incoming_by_target={
            "execution.alpha#1": [
                ExecutionIpcMessage(
                    target_id="execution.alpha#1",
                    payload=ControlPlaneLeafHelloEvent(
                        target_group="execution.alpha",
                        worker_id="execution.alpha#1",
                        pid=123,
                    ),
                    ts_epoch_ms=1,
                )
            ]
        }
    )
    service = DefaultControlPlaneRootLeafIngressService(
        state=state,
        execution_ipc=ipc,
        startup_protocol_revision=3,
        snapshot_builder=_SnapshotBuilder(),
    )

    drained = drain_worker_replies(service, worker_id="execution.alpha#1", timeout_seconds=0.0)

    assert drained == 1
    assert ipc.sends == []
    config_acks = [
        event
        for event in state.events()
        if isinstance(event, ControlPlaneLeafConfigAckEvent)
    ]
    assert len(config_acks) == 1
    assert config_acks[0].worker_id == "execution.alpha#1"
    assert config_acks[0].status == "rejected"
    assert config_acks[0].error == "discovery snapshot unavailable"


def test_root_leaf_ingress_service_marks_leaf_rejected_when_dispatch_raises_on_hello() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
        DefaultControlPlaneRootLeafIngressService,
    )

    @dataclass(slots=True)
    class _SnapshotBuilder:
        def build_snapshot(
            self,
            *,
            hello: ControlPlaneLeafHelloEvent,
            protocol_revision: int,
        ) -> ControlPlaneLeafDiscoverySnapshotEvent | None:
            return ControlPlaneLeafDiscoverySnapshotEvent(
                target_group=hello.target_group,
                worker_id=hello.worker_id,
                request_id="req-snapshot-err",
                required_nodes=("node.a",),
                snapshot_records=(),
                protocol_revision=protocol_revision,
            )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    ipc = _FailingSendIpcPort(
        incoming_by_target={
            "execution.alpha#1": [
                ExecutionIpcMessage(
                    target_id="execution.alpha#1",
                    payload=ControlPlaneLeafHelloEvent(
                        target_group="execution.alpha",
                        worker_id="execution.alpha#1",
                        pid=777,
                    ),
                    ts_epoch_ms=1,
                )
            ]
        },
        fail_on_payload_type=ControlPlaneLeafDiscoverySnapshotEvent,
    )
    service = DefaultControlPlaneRootLeafIngressService(
        state=state,
        execution_ipc=ipc,
        startup_protocol_revision=3,
        snapshot_builder=_SnapshotBuilder(),
    )

    drained = drain_worker_replies(service, worker_id="execution.alpha#1", timeout_seconds=0.0)

    assert drained == 1
    config_acks = [
        event
        for event in state.events()
        if isinstance(event, ControlPlaneLeafConfigAckEvent)
    ]
    assert len(config_acks) == 1
    assert config_acks[0].worker_id == "execution.alpha#1"
    assert config_acks[0].status == "rejected"
    assert "dispatch failed" in (config_acks[0].error or "")
