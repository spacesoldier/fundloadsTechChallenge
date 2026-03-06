from __future__ import annotations

from pathlib import Path

from research_ui.topology_snapshot import build_topology_snapshot

_CONFIG = Path("src/fund_load/experiment_config_newgen_multiprocess_jaeger.yml")


def test_build_topology_snapshot_has_root_and_ipc_links() -> None:
    snapshot = build_topology_snapshot(_CONFIG)

    assert snapshot["meta"]["transport"] == "ipc_local"
    assert snapshot["meta"]["process_count"] >= 2
    assert snapshot["meta"]["pipe_count"] >= 1

    processes = snapshot["processes"]
    assert any(proc["process_id"] == "root" for proc in processes)

    pipes = snapshot["pipes"]
    assert any(pipe["from_process"] == "root" for pipe in pipes)
    assert all(pipe["lanes"] == ["control", "data", "trace", "log", "metric"] for pipe in pipes)


def test_build_topology_snapshot_includes_observability_worker_group() -> None:
    snapshot = build_topology_snapshot(_CONFIG)

    processes = snapshot["processes"]
    assert any(proc["group_name"] == "system.observability" for proc in processes)


def test_build_topology_snapshot_populates_catalogs() -> None:
    snapshot = build_topology_snapshot(_CONFIG)

    catalog = snapshot["catalog"]
    assert catalog["nodes"]
    assert catalog["services"]
    assert catalog["adapters"]
    assert "stores" in catalog


def test_build_topology_snapshot_process_components_and_links_present() -> None:
    snapshot = build_topology_snapshot(_CONFIG)

    processes = snapshot["processes"]
    assert any(proc["services"] for proc in processes)
    assert any(proc["stores"] for proc in processes)
    assert any(proc["adapters"] for proc in processes)
    assert any(proc["links"] for proc in processes)
