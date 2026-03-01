from __future__ import annotations

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)


def test_control_plane_state_service_appends_events_in_order() -> None:
    store = InMemoryKvStore()
    service = InMemoryControlPlaneStateService(store=store)

    service.append_event({"kind": "control.init"})
    service.append_event({"kind": "control.spawn.requested"})

    events = service.events()
    assert len(events) == 2
    assert events[0].get("kind") == "control.init"
    assert events[1].get("kind") == "control.spawn.requested"


def test_control_plane_state_service_latest_returns_last_match() -> None:
    store = InMemoryKvStore()
    service = InMemoryControlPlaneStateService(store=store)

    service.append_event({"kind": "control.init"})
    service.append_event({"kind": "control.spawn.requested"})
    service.append_event({"kind": "control.init"})

    latest = service.latest(kind="control.init")
    assert latest is not None
    assert latest.get("kind") == "control.init"
