from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.control_plane.consumer_registry_nodes import (
    ControlPlaneConsumerRegistryBindingsBootstrapNode,
    ControlPlaneDeferredMessageReplayNode,
    ControlPlaneConsumerRegistryBindingsApplyNode,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConsumerBindingRecord,
    ControlPlaneInitEvent,
    ControlPlaneConsumerRegistryBindingsApplyEvent,
    ControlPlaneDeferredMessageReplayRequestEvent,
)
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class _RoutingService:
    applied: list[ControlPlaneConsumerBindingRecord] = field(default_factory=list)

    def apply_bindings(self, bindings):
        self.applied.extend(list(bindings))


def test_consumer_registry_bindings_apply_node_forwards_bindings_to_service() -> None:
    service = _RoutingService()
    node = ControlPlaneConsumerRegistryBindingsApplyNode(service=service)  # type: ignore[arg-type]
    payload = ControlPlaneConsumerRegistryBindingsApplyEvent(
        bindings=(
            ControlPlaneConsumerBindingRecord(
                token=str,
                node_names=("sink:writer",),
            ),
        )
    )

    produced = node(Envelope(payload=payload, target="system.cp.consumer_registry_bindings_apply"), None)

    assert len(produced) == 1
    assert isinstance(produced[0], ControlPlaneDeferredMessageReplayRequestEvent)
    assert len(service.applied) == 1
    assert service.applied[0].token is str
    assert service.applied[0].node_names == ("sink:writer",)


@dataclass(slots=True)
class _StartupBindingsService:
    bindings: tuple[ControlPlaneConsumerBindingRecord, ...] = ()

    def collect_once(self) -> tuple[ControlPlaneConsumerBindingRecord, ...]:
        current = self.bindings
        self.bindings = ()
        return current


def test_consumer_registry_bindings_bootstrap_node_applies_bindings_once() -> None:
    routing = _RoutingService()
    startup = _StartupBindingsService(
        bindings=(
            ControlPlaneConsumerBindingRecord(
                token=str,
                node_names=("sink:writer",),
            ),
        )
    )
    node = ControlPlaneConsumerRegistryBindingsBootstrapNode(  # type: ignore[arg-type]
        startup_bindings=startup,
        routing=routing,
    )
    payload = ControlPlaneInitEvent(runtime={"platform": {"bootstrap": {"mode": "process_supervisor"}}})
    produced = node(Envelope(payload=payload, target="system.cp.consumer_registry_bindings_bootstrap"), None)
    assert len(produced) == 2
    assert isinstance(produced[0], ControlPlaneDeferredMessageReplayRequestEvent)
    assert isinstance(produced[1], Envelope)
    assert produced[1].target == "system.cp.bootstrap_dispatch"
    assert len(routing.applied) == 1
    assert routing.applied[0].token is str
    assert routing.applied[0].node_names == ("sink:writer",)

    produced_second = node(Envelope(payload=payload, target="system.cp.consumer_registry_bindings_bootstrap"), None)
    assert len(produced_second) == 1
    assert isinstance(produced_second[0], Envelope)
    assert produced_second[0].target == "system.cp.bootstrap_dispatch"


def test_consumer_registry_bindings_bootstrap_targets_initialization_dispatch_for_worker() -> None:
    routing = _RoutingService()
    startup = _StartupBindingsService()
    node = ControlPlaneConsumerRegistryBindingsBootstrapNode(  # type: ignore[arg-type]
        startup_bindings=startup,
        routing=routing,
    )
    payload = ControlPlaneInitEvent(
        runtime={
            "platform": {"bootstrap": {"mode": "process_supervisor"}},
            "__process_role": "worker",
        }
    )

    produced = node(Envelope(payload=payload, target="system.cp.consumer_registry_bindings_bootstrap"), None)

    assert len(produced) == 1
    assert isinstance(produced[0], Envelope)
    assert produced[0].target == "system.cp.initialization_dispatch"


@dataclass(slots=True)
class _DeferredService:
    replayable: list[object] = field(default_factory=list)

    def collect_replayable(self) -> list[object]:
        return list(self.replayable)


def test_deferred_message_replay_node_emits_replayable_messages() -> None:
    service = _DeferredService(replayable=["a", "b"])
    node = ControlPlaneDeferredMessageReplayNode(service=service)  # type: ignore[arg-type]
    payload = ControlPlaneDeferredMessageReplayRequestEvent(reason="bindings_applied")

    produced = node(Envelope(payload=payload, target="system.cp.deferred_message_replay"), None)
    assert produced == ["a", "b"]
