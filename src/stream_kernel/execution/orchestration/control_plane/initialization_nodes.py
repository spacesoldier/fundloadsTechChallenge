from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.application_context.inject import inject
from stream_kernel.kernel.node_annotation import node
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneInitEvent,
    ControlPlaneInitializationCompletedEvent,
    ControlPlaneInitializationRequestedEvent,
    ControlPlaneNodeInitializeCommand,
    ControlPlaneNodeInitializedEvent,
    ControlPlaneReadyForWorkEvent,
)
from stream_kernel.platform.services.runtime.control_plane_node_initialization import (
    ControlPlaneNodeInitializationService,
)
from stream_kernel.routing.envelope import Envelope


@node(
    name="system.cp.initialization_dispatch",
    consumes=[ControlPlaneInitEvent],
    emits=[ControlPlaneInitializationRequestedEvent],
)
@dataclass
class ControlPlaneInitializationDispatchNode:
    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneInitEvent):
            return []
        runtime = payload.runtime if isinstance(payload.runtime, dict) else {}
        return [ControlPlaneInitializationRequestedEvent(runtime=runtime)]


@node(
    name="system.cp.initialization_plan",
    consumes=[ControlPlaneInitializationRequestedEvent],
    emits=[ControlPlaneNodeInitializeCommand, ControlPlaneInitializationCompletedEvent],
)
@dataclass
class ControlPlaneInitializationPlanNode:
    initialization: ControlPlaneNodeInitializationService = inject.service(
        ControlPlaneNodeInitializationService
    )
    candidate_node_names: tuple[str, ...] = ()

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneInitializationRequestedEvent):
            return []
        candidates = self.initialization.begin_phase(
            runtime=payload.runtime,
            node_names=self.candidate_node_names,
        )
        if not candidates:
            return [
                ControlPlaneInitializationCompletedEvent(
                    runtime=payload.runtime,
                    initialized_nodes=(),
                    expected_nodes=(),
                )
            ]
        return [ControlPlaneNodeInitializeCommand(node_name=name) for name in candidates]


@node(
    name="system.cp.node_initialize",
    consumes=[ControlPlaneNodeInitializeCommand],
    emits=[ControlPlaneNodeInitializedEvent, ControlPlaneInitializationCompletedEvent],
)
@dataclass
class ControlPlaneNodeInitializeNode:
    initialization: ControlPlaneNodeInitializationService = inject.service(
        ControlPlaneNodeInitializationService
    )
    node_lookup: dict[str, object] = field(default_factory=dict)

    async def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneNodeInitializeCommand):
            return []
        node = self.node_lookup.get(payload.node_name)
        if node is not None:
            initialize = getattr(node, "initialize", None)
            if callable(initialize):
                result = initialize()
                if hasattr(result, "__await__"):
                    await result
        progress = self.initialization.mark_initialized(node_name=payload.node_name)
        produced: list[object] = [
            ControlPlaneNodeInitializedEvent(
                node_name=payload.node_name,
                initialized_count=len(progress.initialized_nodes),
                total_count=len(progress.expected_nodes),
            )
        ]
        if progress.completed:
            produced.append(
                ControlPlaneInitializationCompletedEvent(
                    runtime=progress.runtime,
                    initialized_nodes=progress.initialized_nodes,
                    expected_nodes=progress.expected_nodes,
                )
            )
        return produced


@node(
    name="system.cp.ready_for_work",
    consumes=[ControlPlaneInitializationCompletedEvent],
    emits=[ControlPlaneReadyForWorkEvent],
)
@dataclass
class ControlPlaneReadyForWorkNode:
    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneInitializationCompletedEvent):
            return []
        runtime = payload.runtime if isinstance(payload.runtime, dict) else {}
        return [ControlPlaneReadyForWorkEvent(runtime=runtime)]


__all__ = [
    "ControlPlaneInitializationDispatchNode",
    "ControlPlaneInitializationPlanNode",
    "ControlPlaneNodeInitializeNode",
    "ControlPlaneReadyForWorkNode",
]
