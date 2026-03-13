from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
)


@dataclass(slots=True)
class _Discovery:
    entities: list[ControlPlaneDiscoveryEntityRecord] = field(default_factory=list)
    discovery_items: list[object] = field(default_factory=list)

    def entity_records(self, *, kind: str | None = None) -> list[ControlPlaneDiscoveryEntityRecord]:
        if kind is None:
            return list(self.entities)
        return [item for item in self.entities if item.entity_kind == kind]

    def items(self) -> list[object]:
        return list(self.discovery_items)

    def append_item(self, item: object) -> None:
        self.discovery_items.append(item)


@dataclass(slots=True)
class _ConfigStore:
    def records(self, *, section: str) -> list[object]:
        _ = section
        return []


def _session() -> object:
    return SimpleNamespace(
        child=SimpleNamespace(
            scenario_steps={
                "node.a": object(),
                "node.b": object(),
            }
        ),
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="auto",
        runner_profile_effective="sync",
    )


def _card() -> ControlPlaneLeafConfigCardEvent:
    return ControlPlaneLeafConfigCardEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        config_id="cfg-1",
        run_id="run",
        scenario_id="scenario",
        group_name="execution.alpha",
        nodes=("node.a", "node.b"),
        runner_profile="auto",
    )


def test_leaf_runtime_activation_service_applies_config_and_returns_applied_ack() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
        DefaultLeafRuntimeActivationService,
    )

    service = DefaultLeafRuntimeActivationService(
        discovery=_Discovery(entities=[]),
        config_store=_ConfigStore(),
    )

    ack = service.apply_config(session=_session(), card=_card())

    assert isinstance(ack, ControlPlaneLeafConfigAckEvent)
    assert ack.status == "applied"
    assert ack.worker_id == "execution.alpha#1"
    assert ack.resolved_nodes == ("node.a", "node.b")


def test_leaf_runtime_activation_service_returns_rejected_ack_on_missing_runtime_node() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
        DefaultLeafRuntimeActivationService,
    )

    service = DefaultLeafRuntimeActivationService(
        discovery=_Discovery(entities=[]),
        config_store=_ConfigStore(),
    )
    session = _session()
    card = ControlPlaneLeafConfigCardEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        config_id="cfg-1",
        run_id="run",
        scenario_id="scenario",
        group_name="execution.alpha",
        nodes=("node.a", "node.missing"),
        runner_profile="auto",
    )

    ack = service.apply_config(session=session, card=card)

    assert isinstance(ack, ControlPlaneLeafConfigAckEvent)
    assert ack.status == "rejected"
    assert ack.error is not None


def test_leaf_runtime_activation_service_accepts_transport_alias_nodes_without_runtime_metadata() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
        DefaultLeafRuntimeActivationService,
    )

    service = DefaultLeafRuntimeActivationService(
        discovery=_Discovery(entities=[]),
        config_store=_ConfigStore(),
    )
    session = _session()
    card = ControlPlaneLeafConfigCardEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        config_id="cfg-bridge",
        run_id="run",
        scenario_id="scenario",
        group_name="execution.alpha",
        nodes=("node.a", "egress_line_bridge", "sink:sink"),
        runner_profile="auto",
    )

    ack = service.apply_config(session=session, card=card)

    assert isinstance(ack, ControlPlaneLeafConfigAckEvent)
    assert ack.status == "applied"
    assert ack.error is None
    assert ack.resolved_nodes == ("node.a", "egress_line_bridge", "sink:sink")


def test_leaf_runtime_activation_service_accepts_system_observability_nodes_without_runtime_metadata() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
        DefaultLeafRuntimeActivationService,
    )

    service = DefaultLeafRuntimeActivationService(
        discovery=_Discovery(entities=[]),
        config_store=_ConfigStore(),
    )
    session = _session()
    card = ControlPlaneLeafConfigCardEvent(
        target_group="system.observability",
        worker_id="system.observability#1",
        config_id="cfg-obs",
        run_id="run",
        scenario_id="scenario",
        group_name="system.observability",
        nodes=("system.obs.trace_dispatch", "system.obs.log_dispatch"),
        runner_profile="auto",
    )

    ack = service.apply_config(session=session, card=card)

    assert isinstance(ack, ControlPlaneLeafConfigAckEvent)
    assert ack.status == "applied"
    assert ack.error is None
    assert ack.resolved_nodes == ("system.obs.trace_dispatch", "system.obs.log_dispatch")


def test_leaf_runtime_activation_service_accepts_transport_handoff_nodes_without_runtime_metadata() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
        DefaultLeafRuntimeActivationService,
    )

    service = DefaultLeafRuntimeActivationService(
        discovery=_Discovery(entities=[]),
        config_store=_ConfigStore(),
    )
    session = _session()
    card = ControlPlaneLeafConfigCardEvent(
        target_group="execution.ingress",
        worker_id="execution.ingress#1",
        config_id="cfg-handoff",
        run_id="run",
        scenario_id="scenario",
        group_name="execution.ingress",
        nodes=("source:source", "system.transport.handoff.observability_dispatch"),
        runner_profile="auto",
    )

    ack = service.apply_config(session=session, card=card)

    assert isinstance(ack, ControlPlaneLeafConfigAckEvent)
    assert ack.status == "applied"
    assert ack.error is None
    assert ack.resolved_nodes == ("source:source", "system.transport.handoff.observability_dispatch")


def test_leaf_runtime_activation_service_accepts_logical_suffix_alias() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
        DefaultLeafRuntimeActivationService,
    )

    session = SimpleNamespace(
        child=SimpleNamespace(scenario_steps={"format_output-logical": object()}),
        worker_id="execution.egress#1",
        group_name="execution.egress",
        runner_profile_requested="auto",
        runner_profile_effective="async",
    )
    card = ControlPlaneLeafConfigCardEvent(
        target_group="execution.egress",
        worker_id="execution.egress#1",
        config_id="cfg-egress",
        run_id="run",
        scenario_id="scenario",
        group_name="execution.egress",
        nodes=("format_output",),
        runner_profile="auto",
    )
    service = DefaultLeafRuntimeActivationService(
        discovery=_Discovery(entities=[]),
        config_store=_ConfigStore(),
    )

    ack = service.apply_config(session=session, card=card)

    assert isinstance(ack, ControlPlaneLeafConfigAckEvent)
    assert ack.status == "applied"
    assert ack.resolved_nodes == ("format_output",)


def test_leaf_runtime_activation_service_does_not_use_local_discovery_fallback_by_default() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
        DefaultLeafRuntimeActivationService,
    )

    session = SimpleNamespace(
        child=SimpleNamespace(scenario_steps={}),
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="auto",
        runner_profile_effective="async",
    )
    card = ControlPlaneLeafConfigCardEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        config_id="cfg-1",
        run_id="run",
        scenario_id="scenario",
        group_name="execution.alpha",
        nodes=("node.a",),
    )
    discovery = _Discovery()
    service = DefaultLeafRuntimeActivationService(
        discovery=discovery,
        config_store=_ConfigStore(),
    )

    ack = service.apply_config(session=session, card=card)

    assert ack.status == "rejected"

