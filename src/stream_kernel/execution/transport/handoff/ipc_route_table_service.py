from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import KVStore
from stream_kernel.platform.services.runtime.debug_buffer import (
    debug_instrument_service_methods,
)

_ROUTE_TABLE_KEY = "execution.transport.ipc.route_table"


class ExecutionIpcRouteTableStore(KVStore):
    # KV marker contract for IPC route table persistence.
    pass


@runtime_checkable
class ExecutionIpcRouteTableService(Protocol):
    def upsert_route(self, *, target: str, target_id: str) -> None:
        raise NotImplementedError

    def preload_snapshot(
        self,
        *,
        routes: dict[str, str],
        replace: bool = False,
    ) -> int:
        raise NotImplementedError

    def resolve_route(self, *, target: str) -> str | None:
        raise NotImplementedError

    def routes(self) -> dict[str, str]:
        raise NotImplementedError


@service(name="execution_ipc_route_table_service")
@debug_instrument_service_methods
@dataclass(slots=True)
class InMemoryExecutionIpcRouteTableService(ExecutionIpcRouteTableService):
    store: KVStore = inject.kv(ExecutionIpcRouteTableStore)
    runtime_debug_buffer: object | None = None

    def upsert_route(self, *, target: str, target_id: str) -> None:
        if not isinstance(target, str) or not target:
            return
        if not isinstance(target_id, str) or not target_id:
            return
        routes = self.routes()
        routes[target] = target_id
        self.store.set(_ROUTE_TABLE_KEY, routes)

    def preload_snapshot(
        self,
        *,
        routes: dict[str, str],
        replace: bool = False,
    ) -> int:
        if not isinstance(routes, dict):
            return 0
        loaded = 0
        current = {} if replace else self.routes()
        merged = dict(current)
        for target, target_id in routes.items():
            if not isinstance(target, str) or not target:
                continue
            if not isinstance(target_id, str) or not target_id:
                continue
            merged[target] = target_id
            loaded += 1
        self.store.set(_ROUTE_TABLE_KEY, merged)
        return loaded

    def resolve_route(self, *, target: str) -> str | None:
        if not isinstance(target, str) or not target:
            return None
        target_id = self.routes().get(target)
        if isinstance(target_id, str) and target_id:
            return target_id
        return None

    def routes(self) -> dict[str, str]:
        existing = self.store.get(_ROUTE_TABLE_KEY)
        if not isinstance(existing, dict):
            return {}
        return {
            key: value
            for key, value in existing.items()
            if isinstance(key, str)
            and key
            and isinstance(value, str)
            and value
        }


__all__ = [
    "ExecutionIpcRouteTableStore",
    "ExecutionIpcRouteTableService",
    "InMemoryExecutionIpcRouteTableService",
]
