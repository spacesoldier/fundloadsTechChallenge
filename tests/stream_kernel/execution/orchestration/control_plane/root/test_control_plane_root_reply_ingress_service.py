from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.execution.transport.handoff.system_nodes import (
    OBSERVABILITY_HANDOFF_NODE_NAME,
)
from stream_kernel.execution.transport.ipc.ipc_transport import ExecutionIpcMessage
from stream_kernel.observability.events import TraceDispatchEvent
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneGroupSpec,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafDiscoverySnapshotEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafShutdownPrepareCommand,
)
from stream_kernel.platform.services.runtime.control_plane_shutdown_readiness import (
    InMemoryControlPlaneShutdownReadinessService,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)
from stream_kernel.routing.envelope import Envelope


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


@dataclass(slots=True)
class _BoundaryHandoff:
    calls: list[dict[str, object]] = field(default_factory=list)

    def drain_external_deliveries(self, *, envelopes: list[Envelope], source_group: str | None = None):
        self.calls.append(
            {
                "envelopes": list(envelopes),
                "source_group": source_group,
            }
        )
        return []


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


def test_root_reply_ingress_service_routes_boundary_result_outputs_via_handoff() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
        DefaultControlPlaneRootReplyIngressService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    handoff = _BoundaryHandoff()
    ipc = _IpcPort(
        incoming_by_target={
            "execution.alpha#1": [
                ExecutionIpcMessage(
                    target_id="execution.alpha#1",
                    payload=ControlPlaneLeafBoundaryResultEvent(
                        target_group="execution.alpha",
                        worker_id="execution.alpha#1",
                        request_id="req-2",
                        status="completed",
                        outputs=(
                            Envelope(payload={"k": 1}, target="compute_time_keys"),
                            {"ignored": True},
                        ),
                    ),
                    ts_epoch_ms=2,
                )
            ]
        }
    )
    service = DefaultControlPlaneRootReplyIngressService(
        state=state,
        execution_ipc=ipc,
        root_boundary_handoff=handoff,
    )

    drained = service.drain_worker_replies(
        worker_id="execution.alpha#1",
        timeout_seconds=0.0,
    )

    assert drained == 1
    assert len(handoff.calls) == 1
    call = handoff.calls[0]
    assert call["source_group"] == "execution.alpha"
    envelopes = call["envelopes"]
    assert isinstance(envelopes, list)
    assert len(envelopes) == 1
    assert isinstance(envelopes[0], Envelope)
    assert envelopes[0].target == "compute_time_keys"


def test_root_reply_ingress_service_tracks_leaf_tombstone_completion() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
        DefaultControlPlaneRootReplyIngressService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    handoff = _BoundaryHandoff()
    ipc = _IpcPort(
        incoming_by_target={
            "execution.ingress#1": [
                ExecutionIpcMessage(
                    target_id="execution.ingress#1",
                    payload=ControlPlaneLeafBoundaryResultEvent(
                        target_group="execution.ingress",
                        worker_id="execution.ingress#1",
                        request_id="req-tomb",
                        status="completed",
                        tombstone_input=True,
                        tombstone_output=True,
                        outputs=(
                            Envelope(payload={"k": 1}, target="compute_time_keys", tombstone=True),
                        ),
                    ),
                    ts_epoch_ms=2,
                )
            ]
        }
    )
    service = DefaultControlPlaneRootReplyIngressService(
        state=state,
        execution_ipc=ipc,
        root_boundary_handoff=handoff,
    )

    drained = service.drain_worker_replies(worker_id="execution.ingress#1", timeout_seconds=0.0)

    assert drained == 1
    assert len(handoff.calls) == 1
    envelopes = handoff.calls[0]["envelopes"]
    assert isinstance(envelopes, list)
    assert len(envelopes) == 1
    assert isinstance(envelopes[0], Envelope)
    assert envelopes[0].target == "compute_time_keys"
    assert envelopes[0].tombstone is True
    events = state.events()
    completions = [
        item for item in events if isinstance(item, dict) and item.get("kind") == "leaf_tombstone_completed"
    ]
    assert len(completions) == 1
    assert completions[0]["target_group"] == "execution.ingress"
    assert completions[0]["worker_id"] == "execution.ingress#1"
    assert completions[0]["tombstone_output"] is True


def test_root_reply_ingress_service_tracks_leaf_tombstone_completion_with_empty_outputs() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
        DefaultControlPlaneRootReplyIngressService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    handoff = _BoundaryHandoff()
    ipc = _IpcPort(
        incoming_by_target={
            "execution.egress#1": [
                ExecutionIpcMessage(
                    target_id="execution.egress#1",
                    payload=ControlPlaneLeafBoundaryResultEvent(
                        target_group="execution.egress",
                        worker_id="execution.egress#1",
                        request_id="req-egress-tomb",
                        status="completed",
                        tombstone_input=True,
                        tombstone_output=False,
                        outputs=(),
                    ),
                    ts_epoch_ms=2,
                )
            ]
        }
    )
    service = DefaultControlPlaneRootReplyIngressService(
        state=state,
        execution_ipc=ipc,
        root_boundary_handoff=handoff,
    )

    drained = service.drain_worker_replies(worker_id="execution.egress#1", timeout_seconds=0.0)

    assert drained == 1
    assert handoff.calls == []
    events = state.events()
    completions = [
        item for item in events if isinstance(item, dict) and item.get("kind") == "leaf_tombstone_completed"
    ]
    assert len(completions) == 1
    assert completions[0]["target_group"] == "execution.egress"
    assert completions[0]["worker_id"] == "execution.egress#1"
    assert completions[0]["tombstone_output"] is False


def test_root_reply_ingress_service_dispatches_shutdown_prepare_when_all_tombstones_observed() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
        DefaultControlPlaneRootReplyIngressService,
    )

    store = InMemoryKvStore()
    state = InMemoryControlPlaneStateService(store=store)
    # Simulate live workers for expected groups.
    state.append_event(
        {
            "kind": "control_plane.lifecycle.worker_spawned",
            "group_name": "execution.features",
            "worker_id": "execution.features#1",
        }
    )
    state.append_event(
        {
            "kind": "control_plane.lifecycle.worker_spawned",
            "group_name": "execution.policy",
            "worker_id": "execution.policy#1",
        }
    )
    shutdown_readiness = InMemoryControlPlaneShutdownReadinessService(store=store)
    shutdown_readiness.configure_expected_groups(("execution.features", "execution.policy"))
    ipc = _IpcPort(
        incoming_by_target={
            "execution.features#1": [
                ExecutionIpcMessage(
                    target_id="execution.features#1",
                    payload=ControlPlaneLeafBoundaryResultEvent(
                        target_group="execution.features",
                        worker_id="execution.features#1",
                        request_id="req-tomb-1",
                        status="completed",
                        tombstone_input=True,
                        outputs=(),
                    ),
                    ts_epoch_ms=1,
                )
            ],
            "execution.policy#1": [
                ExecutionIpcMessage(
                    target_id="execution.policy#1",
                    payload=ControlPlaneLeafBoundaryResultEvent(
                        target_group="execution.policy",
                        worker_id="execution.policy#1",
                        request_id="req-tomb-2",
                        status="completed",
                        tombstone_input=True,
                        outputs=(),
                    ),
                    ts_epoch_ms=2,
                )
            ],
        }
    )
    service = DefaultControlPlaneRootReplyIngressService(
        state=state,
        execution_ipc=ipc,
        shutdown_readiness=shutdown_readiness,
    )

    drained_1 = service.drain_worker_replies(worker_id="execution.features#1", timeout_seconds=0.0)
    drained_2 = service.drain_worker_replies(worker_id="execution.policy#1", timeout_seconds=0.0)

    assert drained_1 == 1
    assert drained_2 == 1
    prepare_messages = [
        item for item in ipc.sends if isinstance(item["payload"], ControlPlaneLeafShutdownPrepareCommand)
    ]
    assert len(prepare_messages) == 2
    targets = {item["target_id"] for item in prepare_messages}
    assert targets == {"execution.features#1", "execution.policy#1"}


def test_root_reply_ingress_service_remaps_observability_relay_target_before_handoff() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
        DefaultControlPlaneRootReplyIngressService,
    )

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    handoff = _BoundaryHandoff()
    ipc = _IpcPort(
        incoming_by_target={
            "execution.alpha#1": [
                ExecutionIpcMessage(
                    target_id="execution.alpha#1",
                    payload=ControlPlaneLeafBoundaryResultEvent(
                        target_group="execution.alpha",
                        worker_id="execution.alpha#1",
                        request_id="req-obs-relay",
                        status="completed",
                        outputs=(
                            Envelope(
                                payload=TraceDispatchEvent(payload={"span": "s1"}),
                                target=OBSERVABILITY_HANDOFF_NODE_NAME,
                            ),
                        ),
                    ),
                    ts_epoch_ms=2,
                )
            ]
        }
    )
    service = DefaultControlPlaneRootReplyIngressService(
        state=state,
        execution_ipc=ipc,
        root_boundary_handoff=handoff,
    )

    drained = service.drain_worker_replies(
        worker_id="execution.alpha#1",
        timeout_seconds=0.0,
    )

    assert drained == 1
    assert len(handoff.calls) == 1
    call = handoff.calls[0]
    envelopes = call["envelopes"]
    assert isinstance(envelopes, list)
    assert len(envelopes) == 1
    assert isinstance(envelopes[0], Envelope)
    assert envelopes[0].target == "system.obs.trace_dispatch"


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


def test_root_reply_ingress_service_v3_without_snapshot_marks_leaf_rejected() -> None:
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
    config_acks = [
        event
        for event in state.events()
        if isinstance(event, ControlPlaneLeafConfigAckEvent)
    ]
    assert len(config_acks) == 1
    assert config_acks[0].worker_id == "execution.alpha#1"
    assert config_acks[0].status == "rejected"
    assert config_acks[0].error == "discovery snapshot unavailable"
