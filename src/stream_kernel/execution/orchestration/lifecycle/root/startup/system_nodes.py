from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.application_context.inject import inject
from stream_kernel.kernel.node_annotation import node
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.observability.events import LogDispatchEvent
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneSpawnRequestedEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneStateService,
)
from stream_kernel.routing.envelope import Envelope

from .lifecycle_service import (
    ControlPlaneLifecycleOrchestrationService,
)
from .console_log_dispatch_service import RootConsoleLogDispatchService
from .log_factory_service import RootLifecycleLogFactory


@dataclass(frozen=True, slots=True)
class _GroupStartupReadyMarker:
    group_name: str


@dataclass(frozen=True, slots=True)
class _GroupStartupFailedMarker:
    group_name: str


@node(
    name="system.lifecycle.spawn_dispatch",
    consumes=[ControlPlaneSpawnRequestedEvent],
    emits=[LogMessage],
)
@dataclass
class ControlPlaneSpawnDispatchNode:
    lifecycle: ControlPlaneLifecycleOrchestrationService = inject.service(
        ControlPlaneLifecycleOrchestrationService
    )
    log_factory: RootLifecycleLogFactory = inject.service(RootLifecycleLogFactory)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneSpawnRequestedEvent):
            return []
        self.lifecycle.on_spawn_requested(payload)
        factory = self.log_factory
        if hasattr(factory, "spawn_requested"):
            return [factory.spawn_requested(payload)]
        return []


@node(
    name="system.lifecycle.group_startup_wait",
    consumes=[ControlPlaneSpawnRequestedEvent, ControlPlaneLeafConfigAckEvent],
    emits=[LogMessage],
)
@dataclass
class ControlPlaneGroupStartupWaitNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)
    log_factory: RootLifecycleLogFactory = inject.service(RootLifecycleLogFactory)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if isinstance(payload, ControlPlaneSpawnRequestedEvent):
            self.state.append_event(payload)
            return []
        if not isinstance(payload, ControlPlaneLeafConfigAckEvent):
            return []
        self.state.append_event(payload)
        latest_spawn = _latest_group_spawn(self.state.events(), payload.target_group)
        if latest_spawn is None:
            return []
        worker_ids = tuple(
            f"{latest_spawn.group_name}#{index + 1}" for index in range(latest_spawn.workers)
        )
        if payload.worker_id not in worker_ids:
            return []

        latest_acks = _latest_group_acks(self.state.events(), latest_spawn.group_name, worker_ids)
        factory = self.log_factory
        if any(ack.status == "rejected" for ack in latest_acks.values()):
            if _group_failed_already_reported(self.state.events(), latest_spawn.group_name):
                return []
            self.state.append_event(_GroupStartupFailedMarker(group_name=latest_spawn.group_name))
            if hasattr(factory, "group_startup_failed"):
                failing = next(
                    (ack for ack in latest_acks.values() if ack.status == "rejected"),
                    payload,
                )
                error_message = (
                    failing.error
                    if isinstance(failing.error, str) and failing.error
                    else "leaf startup rejected"
                )
                return [
                    factory.group_startup_failed(
                        event=latest_spawn,
                        worker_ids=worker_ids,
                        error=RuntimeError(error_message),
                    )
                ]
            return []
        if not all(
            isinstance(latest_acks.get(worker_id), ControlPlaneLeafConfigAckEvent)
            and latest_acks[worker_id].status == "applied"
            for worker_id in worker_ids
        ):
            return []
        if _group_ready_already_reported(self.state.events(), latest_spawn.group_name):
            return []
        self.state.append_event(_GroupStartupReadyMarker(group_name=latest_spawn.group_name))
        if hasattr(factory, "group_startup_ready"):
            return [factory.group_startup_ready(event=latest_spawn, worker_ids=worker_ids)]
        return []


@node(
    name="system.lifecycle.log_dispatch",
    consumes=[LogMessage],
    emits=[LogDispatchEvent],
)
@dataclass
class ControlPlaneLifecycleLogDispatchNode:
    console_dispatch: RootConsoleLogDispatchService = inject.service(RootConsoleLogDispatchService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, LogMessage):
            return []
        return [
            LogDispatchEvent(
                payload=payload,
                attributes={"origin_node": "system.lifecycle.log_dispatch"},
            )
        ]


def _latest_group_spawn(events: list[object], group_name: str) -> ControlPlaneSpawnRequestedEvent | None:
    for event in reversed(events):
        if isinstance(event, ControlPlaneSpawnRequestedEvent) and event.group_name == group_name:
            return event
    return None


def _latest_group_acks(
    events: list[object],
    group_name: str,
    worker_ids: tuple[str, ...],
) -> dict[str, ControlPlaneLeafConfigAckEvent]:
    remaining = set(worker_ids)
    latest: dict[str, ControlPlaneLeafConfigAckEvent] = {}
    for event in reversed(events):
        if not isinstance(event, ControlPlaneLeafConfigAckEvent):
            continue
        if event.target_group != group_name:
            continue
        if event.worker_id not in remaining:
            continue
        latest[event.worker_id] = event
        remaining.remove(event.worker_id)
        if not remaining:
            break
    return latest


def _group_ready_already_reported(events: list[object], group_name: str) -> bool:
    return any(
        isinstance(event, _GroupStartupReadyMarker) and event.group_name == group_name
        for event in events
    )


def _group_failed_already_reported(events: list[object], group_name: str) -> bool:
    return any(
        isinstance(event, _GroupStartupFailedMarker) and event.group_name == group_name
        for event in events
    )
__all__ = [
    "ControlPlaneGroupStartupWaitNode",
    "ControlPlaneLifecycleLogDispatchNode",
    "ControlPlaneSpawnDispatchNode",
]
