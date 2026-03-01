from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.execution.transport.ipc.ipc_transport import ExecutionIpcMessage
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneGroupSpec,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafDiscoverySnapshotEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafHelloEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)


@dataclass(slots=True)
class _IpcPort:
    incoming_by_target: dict[str, list[ExecutionIpcMessage]] = field(default_factory=dict)
    sends: list[dict[str, object]] = field(default_factory=list)

    def recv(self, target_id: str, *, timeout: float | None = None):
        _ = timeout
        queue = self.incoming_by_target.setdefault(target_id, [])
        if not queue:
            return None
        return queue.pop(0)

    def send(self, target_id: str, payload: object, *, no_reply: bool = False):
        self.sends.append({"target_id": target_id, "payload": payload, "no_reply": no_reply})
        return None


def test_root_reply_ingress_service_appends_boundary_result_into_state() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
        DefaultControlPlaneRootReplyIngressService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    ipc = _IpcPort(
        incoming_by_target={
            "execution.alpha#1": [
                ExecutionIpcMessage(
                    target_id="execution.alpha#1",
                    payload=ControlPlaneLeafBoundaryResultEvent(
                        target_group="execution.alpha",
                        worker_id="execution.alpha#1",
                        request_id="req-1",
                        status="completed",
                        outputs=("out-1",),
                    ),
                    ts_epoch_ms=1,
                )
            ]
        }
    )
    service = DefaultControlPlaneRootReplyIngressService(state=state, execution_ipc=ipc)

    drained = service.drain_worker_replies(
        worker_id="execution.alpha#1",
        timeout_seconds=0.0,
    )

    assert drained == 1
    events = state.events()
    assert any(
        isinstance(item, ControlPlaneLeafBoundaryResultEvent) and item.request_id == "req-1"
        for item in events
    )


def test_root_reply_ingress_service_processes_leaf_hello_and_sends_config_card() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
        DefaultControlPlaneRootReplyIngressService,
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
    service = DefaultControlPlaneRootReplyIngressService(state=state, execution_ipc=ipc)

    drained = service.drain_worker_replies(worker_id="execution.alpha#1", timeout_seconds=0.0)

    assert drained == 1
    assert len(ipc.sends) == 1
    sent = ipc.sends[0]
    assert sent["target_id"] == "execution.alpha#1"
    assert sent["no_reply"] is True
    assert isinstance(sent["payload"], ControlPlaneLeafConfigCardEvent)
    events = state.events()
    assert any(isinstance(item, ControlPlaneLeafHelloEvent) for item in events)
    assert any(isinstance(item, ControlPlaneLeafConfigCardEvent) for item in events)


def test_root_reply_ingress_service_v2_processes_leaf_hello_and_sends_discovery_request() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
        DefaultControlPlaneRootReplyIngressService,
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
    service = DefaultControlPlaneRootReplyIngressService(
        state=state,
        execution_ipc=ipc,
        startup_protocol_revision=2,
    )

    drained = service.drain_worker_replies(worker_id="execution.alpha#1", timeout_seconds=0.0)

    assert drained == 1
    assert len(ipc.sends) == 1
    sent = ipc.sends[0]
    assert sent["target_id"] == "execution.alpha#1"
    assert sent["no_reply"] is True
    assert isinstance(sent["payload"], ControlPlaneLeafDiscoveryRequestEvent)
    payload = sent["payload"]
    assert payload.required_nodes == ("node.a", "node.b")
    assert payload.protocol_revision == 2


def test_root_reply_ingress_service_v2_discovery_ack_accepted_sends_config_card() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
        DefaultControlPlaneRootReplyIngressService,
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
    service = DefaultControlPlaneRootReplyIngressService(
        state=state,
        execution_ipc=ipc,
        startup_protocol_revision=2,
    )

    drained = service.drain_worker_replies(worker_id="execution.alpha#1", timeout_seconds=0.0)

    assert drained == 1
    assert len(ipc.sends) == 1
    sent = ipc.sends[0]
    assert isinstance(sent["payload"], ControlPlaneLeafConfigCardEvent)
    card = sent["payload"]
    assert card.worker_id == "execution.alpha#1"
    assert card.nodes == ("node.a", "node.b")
    assert card.runner_profile == "async"


def test_root_reply_ingress_service_v2_discovery_ack_rejected_does_not_send_config() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
        DefaultControlPlaneRootReplyIngressService,
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
    service = DefaultControlPlaneRootReplyIngressService(
        state=state,
        execution_ipc=ipc,
        startup_protocol_revision=2,
    )

    drained = service.drain_worker_replies(worker_id="execution.alpha#1", timeout_seconds=0.0)

    assert drained == 1
    assert ipc.sends == []


def test_root_reply_ingress_service_v3_sends_discovery_snapshot_card() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
        DefaultControlPlaneRootReplyIngressService,
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
    service = DefaultControlPlaneRootReplyIngressService(
        state=state,
        execution_ipc=ipc,
        startup_protocol_revision=3,
        snapshot_builder=_SnapshotBuilder(),
    )

    drained = service.drain_worker_replies(worker_id="execution.alpha#1", timeout_seconds=0.0)

    assert drained == 1
    assert len(ipc.sends) == 1
    sent = ipc.sends[0]
    assert isinstance(sent["payload"], ControlPlaneLeafDiscoverySnapshotEvent)
    payload = sent["payload"]
    assert payload.required_nodes == ("node.a", "node.b")
    assert payload.protocol_revision == 3


def test_root_reply_ingress_service_v3_without_snapshot_does_not_fallback_by_default() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
        DefaultControlPlaneRootReplyIngressService,
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
    service = DefaultControlPlaneRootReplyIngressService(
        state=state,
        execution_ipc=ipc,
        startup_protocol_revision=3,
        snapshot_builder=_SnapshotBuilder(),
    )

    drained = service.drain_worker_replies(worker_id="execution.alpha#1", timeout_seconds=0.0)

    assert drained == 1
    assert ipc.sends == []


def test_root_reply_ingress_service_v3_can_fallback_to_discovery_request_when_enabled() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
        DefaultControlPlaneRootReplyIngressService,
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
    service = DefaultControlPlaneRootReplyIngressService(
        state=state,
        execution_ipc=ipc,
        startup_protocol_revision=3,
        snapshot_builder=_SnapshotBuilder(),
        discovery_request_fallback_enabled=True,
    )

    drained = service.drain_worker_replies(worker_id="execution.alpha#1", timeout_seconds=0.0)

    assert drained == 1
    assert len(ipc.sends) == 1
    assert isinstance(ipc.sends[0]["payload"], ControlPlaneLeafDiscoveryRequestEvent)
