from __future__ import annotations

import pytest

from stream_kernel.platform.services.runtime.process_group_router import (
    InMemoryProcessGroupRouterService,
)


def test_process_group_router_positive_cache_hit_and_stats() -> None:
    router = InMemoryProcessGroupRouterService()
    router.configure_process_groups(
        [
            {
                "name": "execution.cpu",
                "workers": 1,
                "nodes": ["compute_features"],
            }
        ]
    )

    assert router.resolve_group_for_target(target="compute_features", source_group=None) == "execution.cpu"
    first = router.snapshot()
    assert first["misses"] == 1
    assert first["hits"] == 0
    assert first["positive_entries"] == 1

    assert router.resolve_group_for_target(target="compute_features", source_group=None) == "execution.cpu"
    second = router.snapshot()
    assert second["misses"] == 1
    assert second["hits"] == 1
    assert second["positive_entries"] == 1


def test_process_group_router_negative_hit_for_unknown_target() -> None:
    router = InMemoryProcessGroupRouterService()
    router.configure_process_groups(
        [
            {
                "name": "execution.cpu",
                "workers": 1,
                "nodes": ["compute_features"],
            }
        ]
    )

    with pytest.raises(ConnectionError, match="unknown.node"):
        router.resolve_group_for_target(target="unknown.node", source_group=None)
    first = router.snapshot()
    assert first["misses"] == 1
    assert first["negative_hits"] == 0
    assert first["negative_entries"] == 1

    with pytest.raises(ConnectionError, match="unknown.node"):
        router.resolve_group_for_target(target="unknown.node", source_group=None)
    second = router.snapshot()
    assert second["misses"] == 1
    assert second["negative_hits"] == 1
    assert second["negative_entries"] == 1


def test_process_group_router_invalidates_cache_on_placement_update() -> None:
    router = InMemoryProcessGroupRouterService()
    router.configure_process_groups(
        [
            {
                "name": "execution.cpu",
                "workers": 1,
                "nodes": ["compute_features"],
            }
        ]
    )
    assert router.resolve_group_for_target(target="compute_features", source_group=None) == "execution.cpu"
    before = router.snapshot()
    assert before["positive_entries"] == 1
    generation_before = before["generation"]

    router.configure_process_groups(
        [
            {
                "name": "execution.gpu",
                "workers": 1,
                "nodes": ["compute_features"],
            }
        ]
    )

    after = router.snapshot()
    assert after["generation"] > generation_before
    assert after["positive_entries"] == 0
    assert router.resolve_group_for_target(target="compute_features", source_group=None) == "execution.gpu"

