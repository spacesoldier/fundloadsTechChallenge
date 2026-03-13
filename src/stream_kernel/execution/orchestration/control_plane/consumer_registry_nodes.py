from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.application_context.inject import inject
from stream_kernel.kernel.node_annotation import node
from stream_kernel.platform.services.runtime.control_plane_consumer_registry import (
    ControlPlaneDynamicConsumerRoutingService,
)
from stream_kernel.platform.services.runtime.control_plane_startup_bindings import (
    ControlPlaneStartupConsumerBindingsService,
)
from stream_kernel.platform.services.runtime.control_plane_deferred_message import (
    ControlPlaneDeferredMessageService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConsumerRegistryBindingsApplyEvent,
    ControlPlaneInitEvent,
    ControlPlaneDeferredMessageHoldEvent,
    ControlPlaneDeferredMessageReplayRequestEvent,
    ControlPlaneConsumerRegistryRemoveNodesEvent,
    ControlPlaneDiscoveryBatchReadyEvent,
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneLeafDiscoverySnapshotEvent,
)
from stream_kernel.routing.envelope import Envelope


@node(
    name="system.cp.consumer_registry_bindings_apply",
    consumes=[ControlPlaneConsumerRegistryBindingsApplyEvent],
    emits=[ControlPlaneDeferredMessageReplayRequestEvent],
)
@dataclass
class ControlPlaneConsumerRegistryBindingsApplyNode:
    service: ControlPlaneDynamicConsumerRoutingService = inject.service(
        ControlPlaneDynamicConsumerRoutingService
    )

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneConsumerRegistryBindingsApplyEvent):
            return []
        self.service.apply_bindings(list(payload.bindings))
        return [ControlPlaneDeferredMessageReplayRequestEvent(reason="bindings_applied")]


@node(
    name="system.cp.consumer_registry_bindings_bootstrap",
    consumes=[ControlPlaneInitEvent],
    emits=[ControlPlaneDeferredMessageReplayRequestEvent, ControlPlaneInitEvent],
)
@dataclass
class ControlPlaneConsumerRegistryBindingsBootstrapNode:
    startup_bindings: ControlPlaneStartupConsumerBindingsService = inject.service(
        ControlPlaneStartupConsumerBindingsService
    )
    routing: ControlPlaneDynamicConsumerRoutingService = inject.service(
        ControlPlaneDynamicConsumerRoutingService
    )

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneInitEvent):
            return []
        bindings = tuple(self.startup_bindings.collect_once())
        outputs: list[object] = []
        if bindings:
            self.routing.apply_bindings(bindings)
            outputs.append(ControlPlaneDeferredMessageReplayRequestEvent(reason="startup_bindings_applied"))
        runtime = payload.runtime if isinstance(payload.runtime, dict) else {}
        process_role = runtime.get("__process_role")
        target = (
            "system.cp.initialization_dispatch"
            if isinstance(process_role, str) and process_role in {"worker", "observability_worker"}
            else "system.cp.bootstrap_dispatch"
        )
        outputs.append(
            Envelope(
                payload=payload,
                target=target,
            )
        )
        return outputs


@node(
    name="system.cp.consumer_registry_discovery_apply",
    consumes=[ControlPlaneDiscoveryBatchReadyEvent, ControlPlaneLeafDiscoverySnapshotEvent],
    emits=[ControlPlaneDeferredMessageReplayRequestEvent],
)
@dataclass
class ControlPlaneConsumerRegistryDiscoveryApplyNode:
    service: ControlPlaneDynamicConsumerRoutingService = inject.service(
        ControlPlaneDynamicConsumerRoutingService
    )

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if isinstance(payload, ControlPlaneDiscoveryBatchReadyEvent):
            self.service.apply_discovery_records(
                [
                    record
                    for record in payload.entities
                    if isinstance(record, ControlPlaneDiscoveryEntityRecord)
                ]
            )
        elif isinstance(payload, ControlPlaneLeafDiscoverySnapshotEvent):
            self.service.apply_discovery_records(
                [
                    record
                    for record in payload.snapshot_records
                    if isinstance(record, ControlPlaneDiscoveryEntityRecord)
                ]
            )
        return [ControlPlaneDeferredMessageReplayRequestEvent(reason="discovery_bindings_applied")]


@node(
    name="system.cp.consumer_registry_remove",
    consumes=[ControlPlaneConsumerRegistryRemoveNodesEvent],
    emits=[],
)
@dataclass
class ControlPlaneConsumerRegistryRemoveNode:
    service: ControlPlaneDynamicConsumerRoutingService = inject.service(
        ControlPlaneDynamicConsumerRoutingService
    )

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneConsumerRegistryRemoveNodesEvent):
            return []
        self.service.remove_node_bindings(list(payload.node_names))
        return []


@node(
    name="system.cp.deferred_message_hold",
    consumes=[ControlPlaneDeferredMessageHoldEvent],
    emits=[],
)
@dataclass
class ControlPlaneDeferredMessageHoldNode:
    service: ControlPlaneDeferredMessageService = inject.service(ControlPlaneDeferredMessageService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        self.service.hold(payload)
        return []


@node(
    name="system.cp.deferred_message_replay",
    consumes=[ControlPlaneDeferredMessageReplayRequestEvent],
    emits=[object],
)
@dataclass
class ControlPlaneDeferredMessageReplayNode:
    service: ControlPlaneDeferredMessageService = inject.service(ControlPlaneDeferredMessageService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneDeferredMessageReplayRequestEvent):
            return []
        return list(self.service.collect_replayable())


__all__ = [
    "ControlPlaneConsumerRegistryBindingsBootstrapNode",
    "ControlPlaneConsumerRegistryBindingsApplyNode",
    "ControlPlaneConsumerRegistryDiscoveryApplyNode",
    "ControlPlaneConsumerRegistryRemoveNode",
    "ControlPlaneDeferredMessageHoldNode",
    "ControlPlaneDeferredMessageReplayNode",
]
