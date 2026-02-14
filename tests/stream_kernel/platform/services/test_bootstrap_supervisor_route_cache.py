from __future__ import annotations

import pytest

from stream_kernel.platform.services.bootstrap import MultiprocessBootstrapSupervisor


def _resolve(supervisor: MultiprocessBootstrapSupervisor, *, target: str, source_group: str | None) -> str:
    resolver = getattr(supervisor, "_resolve_group_for_target")
    return resolver(target=target, source_group=source_group)


def test_route_cache_positive_hit_and_stats() -> None:
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_process_groups(
        [
            {
                "name": "execution.cpu",
                "workers": 1,
                "nodes": ["compute_features"],
            }
        ]
    )

    assert _resolve(supervisor, target="compute_features", source_group=None) == "execution.cpu"
    first = supervisor.route_cache_snapshot()
    assert first["misses"] == 1
    assert first["hits"] == 0
    assert first["positive_entries"] == 1

    assert _resolve(supervisor, target="compute_features", source_group=None) == "execution.cpu"
    second = supervisor.route_cache_snapshot()
    assert second["misses"] == 1
    assert second["hits"] == 1
    assert second["positive_entries"] == 1


def test_route_cache_negative_hit_for_unknown_target() -> None:
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_process_groups(
        [
            {
                "name": "execution.cpu",
                "workers": 1,
                "nodes": ["compute_features"],
            }
        ]
    )

    with pytest.raises(ConnectionError, match="unknown.node"):
        _resolve(supervisor, target="unknown.node", source_group=None)
    first = supervisor.route_cache_snapshot()
    assert first["misses"] == 1
    assert first["negative_hits"] == 0
    assert first["negative_entries"] == 1

    with pytest.raises(ConnectionError, match="unknown.node"):
        _resolve(supervisor, target="unknown.node", source_group=None)
    second = supervisor.route_cache_snapshot()
    assert second["misses"] == 1
    assert second["negative_hits"] == 1
    assert second["negative_entries"] == 1


def test_route_cache_is_invalidated_when_process_group_placement_changes() -> None:
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_process_groups(
        [
            {
                "name": "execution.cpu",
                "workers": 1,
                "nodes": ["compute_features"],
            }
        ]
    )

    assert _resolve(supervisor, target="compute_features", source_group=None) == "execution.cpu"
    before = supervisor.route_cache_snapshot()
    assert before["positive_entries"] == 1
    generation_before = before["generation"]

    supervisor.configure_process_groups(
        [
            {
                "name": "execution.gpu",
                "workers": 1,
                "nodes": ["compute_features"],
            }
        ]
    )

    after_config = supervisor.route_cache_snapshot()
    assert after_config["generation"] > generation_before
    assert after_config["positive_entries"] == 0

    assert _resolve(supervisor, target="compute_features", source_group=None) == "execution.gpu"


def test_route_cache_can_be_disabled() -> None:
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_process_groups(
        [
            {
                "name": "execution.cpu",
                "workers": 1,
                "nodes": ["compute_features"],
            }
        ]
    )
    supervisor.configure_routing_cache({"enabled": False, "negative_cache": True, "max_entries": 32})

    assert _resolve(supervisor, target="compute_features", source_group=None) == "execution.cpu"
    assert _resolve(supervisor, target="compute_features", source_group=None) == "execution.cpu"
    snapshot = supervisor.route_cache_snapshot()
    assert snapshot["enabled"] is False
    assert snapshot["hits"] == 0
    assert snapshot["misses"] == 2
    assert snapshot["positive_entries"] == 0
