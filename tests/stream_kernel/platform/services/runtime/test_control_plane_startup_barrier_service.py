from __future__ import annotations

from stream_kernel.platform.services.runtime.control_plane_startup_barrier import (
    InMemoryControlPlaneStartupBarrierService,
)


def test_startup_barrier_opens_once_after_discovery_and_config_completion() -> None:
    service = InMemoryControlPlaneStartupBarrierService()
    runtime = {"platform": {"process_groups": []}}

    assert service.is_open() is False
    assert service.mark_discovery_completed(runtime=runtime) is False
    assert service.is_open() is False
    assert service.mark_config_completed(runtime=runtime) is True
    assert service.is_open() is True

    # Idempotent open: duplicates must not re-open.
    assert service.mark_discovery_completed(runtime=runtime) is False
    assert service.mark_config_completed(runtime=runtime) is False
    assert service.is_open() is True
