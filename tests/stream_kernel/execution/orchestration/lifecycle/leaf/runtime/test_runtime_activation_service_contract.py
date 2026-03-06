from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from stream_kernel.execution.transport.ipc.ipc_transport import ExecutionIpcMessage
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryItemEvent,
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


@dataclass(slots=True)
class _Bootstrapper:
    items: list[ControlPlaneDiscoveryItemEvent] = field(default_factory=list)
    calls: int = 0

    def discover_all(self, runtime: dict[str, object]) -> list[ControlPlaneDiscoveryItemEvent]:
        _ = runtime
        self.calls += 1
        return list(self.items)


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
    bootstrapper = _Bootstrapper(
        items=[ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.a"})]
    )
    service = DefaultLeafRuntimeActivationService(
        discovery=discovery,
        config_store=_ConfigStore(),
        bootstrapper=bootstrapper,
    )

    ack = service.apply_config(session=session, card=card)

    assert ack.status == "rejected"
    assert bootstrapper.calls == 0


def test_leaf_runtime_activation_service_can_use_local_discovery_fallback_when_enabled() -> None:
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
    bootstrapper = _Bootstrapper(
        items=[ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.a"})]
    )
    service = DefaultLeafRuntimeActivationService(
        discovery=discovery,
        config_store=_ConfigStore(),
        bootstrapper=bootstrapper,
        allow_local_discovery_fallback=True,
    )

    ack = service.apply_config(session=session, card=card)

    assert ack.status == "applied"
    assert ack.resolved_nodes == ("node.a",)
    assert bootstrapper.calls == 1


def test_leaf_runtime_activation_service_runtime_flag_overrides_local_discovery_fallback() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
        DefaultLeafRuntimeActivationService,
    )

    session = SimpleNamespace(
        child=SimpleNamespace(
            scenario_steps={},
            runtime={"platform": {"control_plane": {"leaf_local_discovery_fallback": True}}},
        ),
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
    bootstrapper = _Bootstrapper(
        items=[ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.a"})]
    )
    service = DefaultLeafRuntimeActivationService(
        discovery=discovery,
        config_store=_ConfigStore(),
        bootstrapper=bootstrapper,
        allow_local_discovery_fallback=False,
    )

    ack = service.apply_config(session=session, card=card)

    assert ack.status == "applied"
    assert bootstrapper.calls == 1


def test_leaf_worker_command_loop_service_delegates_config_to_activation_service() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    class _Activation:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ControlPlaneLeafConfigCardEvent]] = []

        def apply_config(self, *, session: object, card: ControlPlaneLeafConfigCardEvent):
            self.calls.append((session, card))
            return ControlPlaneLeafConfigAckEvent(
                target_group=session.group_name,
                worker_id=session.worker_id,
                config_id=card.config_id,
                status="applied",
                resolved_nodes=tuple(card.nodes),
            )

    @dataclass(slots=True)
    class _ExecutionIpc:
        incoming: list[object] = field(default_factory=list)
        sent: list[tuple[str, object, bool]] = field(default_factory=list)

        def recv(self, target_id: str, *, timeout: float | None = None):
            _ = timeout
            if not self.incoming:
                return None
            return ExecutionIpcMessage(target_id=target_id, payload=self.incoming.pop(0), ts_epoch_ms=0)

        def send(self, target_id: str, payload: object, *, no_reply: bool = False):
            self.sent.append((target_id, payload, bool(no_reply)))
            return None

    @dataclass(slots=True)
    class _StopEvent:
        is_set_now: bool = False

        def is_set(self) -> bool:
            return self.is_set_now

    activation = _Activation()
    ipc = _ExecutionIpc(incoming=[_card()])
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=activation,  # RED: command loop should delegate config apply
        execution_ipc=ipc,
    )

    result = service.run_control_iteration(
        session=_session(),
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert result == "configured"
    assert len(activation.calls) == 1
    assert len(ipc.sent) == 1
    assert isinstance(ipc.sent[0][1], ControlPlaneLeafConfigAckEvent)
