from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.consumer_registry import ConsumerRegistry
from stream_kernel.integration.kv_store import KVStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDeferredMessageHoldEvent,
)

_PENDING_KEY = "control_plane.deferred_messages.pending"


class ControlPlaneDeferredMessageStore(KVStore):
    pass


@runtime_checkable
class ControlPlaneDeferredMessageService(Protocol):
    def hold(self, event: object) -> None:
        raise NotImplementedError

    def collect_replayable(self) -> list[object]:
        raise NotImplementedError

    def pending_count(self) -> int:
        raise NotImplementedError


@service(name="control_plane_deferred_message_service")
@dataclass(slots=True)
class InMemoryControlPlaneDeferredMessageService(ControlPlaneDeferredMessageService):
    registry: ConsumerRegistry = inject.service(ConsumerRegistry)
    store: KVStore = inject.kv(ControlPlaneDeferredMessageStore)

    def hold(self, event: object) -> None:
        if not isinstance(event, ControlPlaneDeferredMessageHoldEvent):
            return
        pending = self._load_pending()
        pending.append(event)
        self.store.set(_PENDING_KEY, pending)

    def collect_replayable(self) -> list[object]:
        pending = self._load_pending()
        if not pending:
            return []
        replayable: list[object] = []
        remaining: list[ControlPlaneDeferredMessageHoldEvent] = []
        for event in pending:
            payload = event.payload
            token = type(payload)
            consumers = self.registry.get_consumers(token)
            if isinstance(consumers, list) and any(
                isinstance(name, str) and bool(name) for name in consumers
            ):
                replayable.append(payload)
            else:
                remaining.append(event)
        self.store.set(_PENDING_KEY, remaining)
        return replayable

    def pending_count(self) -> int:
        return len(self._load_pending())

    def _load_pending(self) -> list[ControlPlaneDeferredMessageHoldEvent]:
        raw = self.store.get(_PENDING_KEY)
        if not isinstance(raw, list):
            return []
        return [event for event in raw if isinstance(event, ControlPlaneDeferredMessageHoldEvent)]


__all__ = [
    "ControlPlaneDeferredMessageStore",
    "ControlPlaneDeferredMessageService",
    "InMemoryControlPlaneDeferredMessageService",
]
