from __future__ import annotations

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConsumerBindingRecord,
)
from stream_kernel.platform.services.runtime.control_plane_startup_bindings import (
    InMemoryControlPlaneStartupConsumerBindingsService,
)


class _TokenA:
    pass


def test_startup_bindings_service_collect_once_returns_seeded_bindings_once() -> None:
    service = InMemoryControlPlaneStartupConsumerBindingsService(store=InMemoryKvStore())
    bindings = (
        ControlPlaneConsumerBindingRecord(
            token=_TokenA,
            node_names=("system.node.a",),
        ),
    )

    service.seed_bindings(bindings)

    first = service.collect_once()
    second = service.collect_once()

    assert first == bindings
    assert second == ()

