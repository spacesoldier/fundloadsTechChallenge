from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore

_EVENTS_KEY = "control_plane.events"


class ControlPlaneEventStore(KVStore):
    # KV marker contract for control-plane event persistence.
    pass


@runtime_checkable
class ControlPlaneStateService(Protocol):
    def append_event(self, event: object) -> None:
        raise NotImplementedError("ControlPlaneStateService.append_event must be implemented")

    def events(self) -> list[object]:
        raise NotImplementedError("ControlPlaneStateService.events must be implemented")

    def latest(self, *, kind: str) -> object | None:
        raise NotImplementedError("ControlPlaneStateService.latest must be implemented")


@service(name="control_plane_state_service")
@dataclass(slots=True)
class InMemoryControlPlaneStateService(ControlPlaneStateService):
    store: KVStore = inject.kv(ControlPlaneEventStore)

    def append_event(self, event: object) -> None:
        events = self._load_events()
        events.append(event)
        self.store.set(_EVENTS_KEY, events)

    def events(self) -> list[object]:
        return list(self._load_events())

    def latest(self, *, kind: str) -> object | None:
        if not isinstance(kind, str) or not kind:
            return None
        for event in reversed(self._load_events()):
            if _event_kind(event) == kind:
                return event
        return None

    def _load_events(self) -> list[object]:
        existing = self.store.get(_EVENTS_KEY)
        if isinstance(existing, list):
            return list(existing)
        return []


def _event_kind(event: object) -> str | None:
    if isinstance(event, dict):
        raw = event.get("kind")
        return raw if isinstance(raw, str) else None
    return getattr(event, "kind", None)


__all__ = [
    "ControlPlaneEventStore",
    "ControlPlaneStateService",
    "InMemoryControlPlaneStateService",
]
