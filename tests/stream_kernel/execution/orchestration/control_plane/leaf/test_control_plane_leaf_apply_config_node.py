from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from stream_kernel.execution.orchestration.control_plane import (
    ControlPlaneLeafApplyConfigNode,
    ControlPlaneLeafDiscoveryRequestNode,
    ControlPlaneLeafSnapshotApplyNode,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_bootstrapper import (
    ControlPlaneBootstrapperService,
)
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    InMemoryControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryItemEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafDiscoverySnapshotEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
)


@dataclass(slots=True)
class _Bootstrapper(ControlPlaneBootstrapperService):
    items: list[ControlPlaneDiscoveryItemEvent] = field(default_factory=list)
    discover_all_calls: list[dict[str, object]] = field(default_factory=list)
    discover_subset_calls: list[tuple[dict[str, object], list[str]]] = field(default_factory=list)
    fail_with: Exception | None = None

    def discover_all(self, runtime: dict[str, object]) -> list[ControlPlaneDiscoveryItemEvent]:
        self.discover_all_calls.append(dict(runtime))
        if self.fail_with is not None:
            raise self.fail_with
        return list(self.items)

    def discover_subset(
        self,
        *,
        runtime: dict[str, object],
        node_names: list[str],
    ) -> list[ControlPlaneDiscoveryItemEvent]:
        self.discover_subset_calls.append((dict(runtime), list(node_names)))
        if self.fail_with is not None:
            raise self.fail_with
        return []


def test_leaf_apply_config_discovers_full_registry_and_emits_ack() -> None:
    bootstrapper = _Bootstrapper(
        items=[
            ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.a"}),
            ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.b"}),
        ]
    )
    discovery = InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())
    node = ControlPlaneLeafApplyConfigNode(bootstrapper=bootstrapper, discovery=discovery)
    card = ControlPlaneLeafConfigCardEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        config_id="cfg-1",
        run_id="run",
        scenario_id="scenario",
        group_name="execution.alpha",
        nodes=("node.a", "node.b"),
        runner_profile="auto",
    )

    produced = node(card, None)

    assert len(produced) == 1
    ack = produced[0]
    assert isinstance(ack, ControlPlaneLeafConfigAckEvent)
    assert ack.status == "applied"
    assert ack.target_group == "execution.alpha"
    assert ack.worker_id == "execution.alpha#1"
    assert ack.config_id == "cfg-1"
    assert ack.resolved_nodes == ("node.a", "node.b")
    assert ack.error is None
    assert bootstrapper.discover_all_calls == [{}]
    assert bootstrapper.discover_subset_calls == []
    assert discovery.items() == bootstrapper.items


def test_leaf_apply_config_emits_rejected_ack_when_discovery_fails() -> None:
    bootstrapper = _Bootstrapper(fail_with=RuntimeError("subset discovery failed"))
    discovery = InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())
    node = ControlPlaneLeafApplyConfigNode(bootstrapper=bootstrapper, discovery=discovery)
    card = ControlPlaneLeafConfigCardEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        config_id="cfg-1",
        run_id="run",
        scenario_id="scenario",
        group_name="execution.alpha",
        nodes=("node.a",),
    )

    produced = node(card, None)

    assert len(produced) == 1
    ack = produced[0]
    assert isinstance(ack, ControlPlaneLeafConfigAckEvent)
    assert ack.status == "rejected"
    assert ack.error is not None
    assert "subset discovery failed" in ack.error
    assert ack.resolved_nodes == ()
    assert discovery.items() == []


def test_leaf_apply_config_emits_rejected_ack_when_requested_node_absent_in_discovery_registry() -> None:
    bootstrapper = _Bootstrapper(
        items=[ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.a"})]
    )
    discovery = InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())
    node = ControlPlaneLeafApplyConfigNode(bootstrapper=bootstrapper, discovery=discovery)
    card = ControlPlaneLeafConfigCardEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        config_id="cfg-2",
        run_id="run",
        scenario_id="scenario",
        group_name="execution.alpha",
        nodes=("node.a", "node.missing"),
    )

    produced = node(card, None)

    assert len(produced) == 1
    ack = produced[0]
    assert isinstance(ack, ControlPlaneLeafConfigAckEvent)
    assert ack.status == "rejected"
    assert ack.error is not None
    assert "node.missing" in ack.error
    assert ack.resolved_nodes == ()


def test_leaf_apply_config_accepts_runtime_step_aliases_from_context() -> None:
    bootstrapper = _Bootstrapper(items=[])
    discovery = InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())
    node = ControlPlaneLeafApplyConfigNode(bootstrapper=bootstrapper, discovery=discovery)
    card = ControlPlaneLeafConfigCardEvent(
        target_group="execution.egress",
        worker_id="execution.egress#1",
        config_id="cfg-ctx",
        run_id="run",
        scenario_id="scenario",
        group_name="execution.egress",
        nodes=("format_output", "egress_line_bridge", "sink:sink"),
    )
    leaf_session = SimpleNamespace(
        child=SimpleNamespace(
            runtime={},
            scenario_steps={"format_output-logical": object()},
        )
    )

    produced = node(card, {"__leaf_session": leaf_session})

    assert len(produced) == 1
    ack = produced[0]
    assert isinstance(ack, ControlPlaneLeafConfigAckEvent)
    assert ack.status == "applied"
    assert ack.resolved_nodes == ("format_output", "egress_line_bridge", "sink:sink")


def test_leaf_discovery_request_node_emits_accepted_ack_for_resolved_subset() -> None:
    bootstrapper = _Bootstrapper(
        items=[
            ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.a"}),
            ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.b"}),
            ControlPlaneDiscoveryItemEvent(item_kind="service", payload={"name": "svc.x"}),
        ]
    )
    discovery = InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())
    node = ControlPlaneLeafDiscoveryRequestNode(bootstrapper=bootstrapper, discovery=discovery)
    request = ControlPlaneLeafDiscoveryRequestEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-1",
        required_nodes=("node.a", "node.b"),
        protocol_revision=2,
    )

    produced = node(request, None)

    assert len(produced) == 1
    ack = produced[0]
    assert isinstance(ack, ControlPlaneLeafDiscoveryAckEvent)
    assert ack.status == "accepted"
    assert ack.missing_nodes == ()
    assert ack.discovered_nodes == ("node.a", "node.b")


def test_leaf_discovery_request_node_emits_rejected_ack_for_missing_nodes() -> None:
    bootstrapper = _Bootstrapper(
        items=[ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.a"})]
    )
    discovery = InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())
    node = ControlPlaneLeafDiscoveryRequestNode(bootstrapper=bootstrapper, discovery=discovery)
    request = ControlPlaneLeafDiscoveryRequestEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-2",
        required_nodes=("node.a", "node.missing"),
        protocol_revision=2,
    )

    produced = node(request, None)

    assert len(produced) == 1
    ack = produced[0]
    assert isinstance(ack, ControlPlaneLeafDiscoveryAckEvent)
    assert ack.status == "rejected"
    assert ack.error is not None
    assert "node.missing" in ack.error
    assert ack.missing_nodes == ("node.missing",)


def test_leaf_discovery_request_node_uses_leaf_runtime_from_context() -> None:
    bootstrapper = _Bootstrapper(
        items=[ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.a"})]
    )
    discovery = InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())
    node = ControlPlaneLeafDiscoveryRequestNode(bootstrapper=bootstrapper, discovery=discovery)
    request = ControlPlaneLeafDiscoveryRequestEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-runtime-ctx",
        required_nodes=("node.a",),
        protocol_revision=2,
    )
    leaf_session = SimpleNamespace(
        child=SimpleNamespace(runtime={"discovery_modules": ["pkg.platform", "pkg.project"]})
    )

    produced = node(request, {"__leaf_session": leaf_session})

    assert len(produced) == 1
    ack = produced[0]
    assert isinstance(ack, ControlPlaneLeafDiscoveryAckEvent)
    assert ack.status == "accepted"
    assert bootstrapper.discover_all_calls == [{"discovery_modules": ["pkg.platform", "pkg.project"]}]


def test_leaf_snapshot_apply_node_emits_discovery_ack_via_snapshot_service() -> None:
    @dataclass(slots=True)
    class _SnapshotApplyService:
        seen: list[tuple[object, ControlPlaneLeafDiscoverySnapshotEvent]] = field(default_factory=list)

        def apply_snapshot(
            self,
            *,
            session: object,
            snapshot: ControlPlaneLeafDiscoverySnapshotEvent,
        ) -> ControlPlaneLeafDiscoveryAckEvent:
            self.seen.append((session, snapshot))
            return ControlPlaneLeafDiscoveryAckEvent(
                target_group=snapshot.target_group,
                worker_id=snapshot.worker_id,
                request_id=snapshot.request_id,
                status="accepted",
                discovered_nodes=tuple(snapshot.required_nodes),
                missing_nodes=(),
            )

    service = _SnapshotApplyService()
    node = ControlPlaneLeafSnapshotApplyNode(snapshot_apply=service)
    event = ControlPlaneLeafDiscoverySnapshotEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        request_id="req-snapshot-1",
        required_nodes=("node.a",),
        snapshot_records=(),
        protocol_revision=3,
    )
    session = SimpleNamespace(
        child=SimpleNamespace(runtime={}),
        group_name="execution.alpha",
        worker_id="execution.alpha#1",
    )

    produced = node(event, {"__leaf_session": session})

    assert len(produced) == 1
    ack = produced[0]
    assert isinstance(ack, ControlPlaneLeafDiscoveryAckEvent)
    assert ack.status == "accepted"
    assert len(service.seen) == 1
