from __future__ import annotations

from stream_kernel.execution.transport.handoff.ipc_route_table_service import (
    InMemoryExecutionIpcRouteTableService,
)
from stream_kernel.integration.kv_store import InMemoryKvStore


def test_ipc_route_table_service_preloads_snapshot_routes() -> None:
    service = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())

    loaded = service.preload_snapshot(
        routes={
            "node.a": "execution.alpha#1",
            "node.b": "execution.beta#1",
        }
    )

    assert loaded == 2
    assert service.resolve_route(target="node.a") == "execution.alpha#1"
    assert service.resolve_route(target="node.b") == "execution.beta#1"


def test_ipc_route_table_service_preload_snapshot_replace_mode_drops_previous_entries() -> None:
    service = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())
    service.upsert_route(target="old.node", target_id="execution.old#1")

    loaded = service.preload_snapshot(
        routes={"new.node": "execution.new#1"},
        replace=True,
    )

    assert loaded == 1
    assert service.resolve_route(target="old.node") is None
    assert service.resolve_route(target="new.node") == "execution.new#1"


def test_ipc_route_table_service_preload_snapshot_ignores_invalid_entries() -> None:
    service = InMemoryExecutionIpcRouteTableService(store=InMemoryKvStore())

    loaded = service.preload_snapshot(
        routes={
            "valid.node": "execution.valid#1",
            "": "execution.empty#1",
            "bad.target_id": "",
        }
    )

    assert loaded == 1
    assert service.routes() == {"valid.node": "execution.valid#1"}
