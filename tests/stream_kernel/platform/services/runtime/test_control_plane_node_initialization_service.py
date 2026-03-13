from __future__ import annotations

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_node_initialization import (
    InMemoryControlPlaneNodeInitializationService,
)


def test_node_initialization_service_tracks_progress_until_completion() -> None:
    service = InMemoryControlPlaneNodeInitializationService(store=InMemoryKvStore())

    phase = service.begin_phase(node_names=("a", "b", "a", ""))
    assert phase == ("a", "b")

    first = service.mark_initialized(node_name="a")
    assert first.expected_nodes == ("a", "b")
    assert first.initialized_nodes == ("a",)
    assert first.pending_nodes == ("b",)
    assert not first.completed

    second = service.mark_initialized(node_name="b")
    assert second.initialized_nodes == ("a", "b")
    assert second.pending_nodes == ()
    assert second.completed


def test_node_initialization_service_ignores_unknown_markers() -> None:
    service = InMemoryControlPlaneNodeInitializationService(store=InMemoryKvStore())
    service.begin_phase(node_names=("n1",))

    progress = service.mark_initialized(node_name="unknown")

    assert progress.expected_nodes == ("n1",)
    assert progress.initialized_nodes == ()
    assert progress.pending_nodes == ("n1",)
    assert not progress.completed
