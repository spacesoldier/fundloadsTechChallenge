from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.application_context.inject import inject
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.kernel.node_annotation import node
from stream_kernel.platform.services.runtime.platform_scheduler import (
    PlatformSchedulerCancelCommand,
    PlatformSchedulerService,
    PlatformSchedulerTimerService,
    PlatformSchedulerTickEvent,
    PlatformSchedulerUpsertCommand,
)
from stream_kernel.routing.envelope import Envelope

SCHEDULER_COMMAND_NODE_NAME = "system.scheduler.command"
SCHEDULER_TIMER_NODE_NAME = "system.scheduler.timer"
SCHEDULER_TICK_NODE_NAME = "system.scheduler.tick"


@node(
    name=SCHEDULER_COMMAND_NODE_NAME,
    consumes=[PlatformSchedulerUpsertCommand, PlatformSchedulerCancelCommand],
    emits=[],
)
@dataclass
class PlatformSchedulerCommandNode:
    scheduler: PlatformSchedulerService = inject.service(PlatformSchedulerService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, (PlatformSchedulerUpsertCommand, PlatformSchedulerCancelCommand)):
            return []
        self.scheduler.apply_command(payload)
        return []


@node(
    name=SCHEDULER_TIMER_NODE_NAME,
    consumes=[PlatformSchedulerUpsertCommand, PlatformSchedulerCancelCommand],
    emits=[],
)
@dataclass
class PlatformSchedulerTimerNode:
    timer: PlatformSchedulerTimerService = inject.service(PlatformSchedulerTimerService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, (PlatformSchedulerUpsertCommand, PlatformSchedulerCancelCommand)):
            return []
        self.timer.apply_command(payload)
        return []


@node(
    name=SCHEDULER_TICK_NODE_NAME,
    consumes=[PlatformSchedulerTickEvent],
    emits=[Envelope],
)
@dataclass
class PlatformSchedulerTickNode:
    scheduler: PlatformSchedulerService = inject.service(PlatformSchedulerService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, PlatformSchedulerTickEvent):
            return []
        due = self.scheduler.dispatch_due(payload)
        return [
            Envelope(
                payload=item.payload
                if item.payload is not None
                else BootstrapControl(target=item.target),
                target=item.target,
            )
            for item in due
            if isinstance(item.target, str) and item.target
        ]


__all__ = [
    "SCHEDULER_COMMAND_NODE_NAME",
    "SCHEDULER_TIMER_NODE_NAME",
    "SCHEDULER_TICK_NODE_NAME",
    "PlatformSchedulerCommandNode",
    "PlatformSchedulerTimerNode",
    "PlatformSchedulerTickNode",
]
