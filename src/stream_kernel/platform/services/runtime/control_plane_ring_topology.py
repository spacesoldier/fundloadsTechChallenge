from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_LOG,
    EXECUTION_IPC_LANE_METRIC,
    EXECUTION_IPC_LANE_TRACE,
    ExecutionIpcTransportService,
    compose_execution_ipc_worker_target_id,
)
from stream_kernel.integration.kv_store import KVStore
from stream_kernel.platform.services.runtime.control_plane_state import ControlPlaneStateService

_DIRECT_TARGET_PREFIX = "target::"
_PLAN_KEY = "control_plane.ring_topology.plan"
_CHILD_ENDPOINTS_KEY = "control_plane.ring_topology.child_endpoints_by_worker"


class ControlPlaneRingTopologyStore(KVStore):
    # KV marker for ring topology plan + per-worker endpoint staging.
    pass


@dataclass(frozen=True, slots=True)
class ControlPlaneRingLinkSpec:
    from_worker_id: str
    to_worker_id: str
    lane: str
    target_id: str


@dataclass(frozen=True, slots=True)
class ControlPlaneRingTopologyPlan:
    enabled: bool
    worker_ids: tuple[str, ...] = field(default_factory=tuple)
    links: tuple[ControlPlaneRingLinkSpec, ...] = field(default_factory=tuple)


@runtime_checkable
class ControlPlaneRingTopologyService(Protocol):
    def configure(self, *, runtime: dict[str, object], groups: list[dict[str, object]]) -> None:
        raise NotImplementedError

    def plan(self) -> ControlPlaneRingTopologyPlan | None:
        raise NotImplementedError

    def pop_child_endpoints(self, *, worker_id: str) -> dict[str, object]:
        raise NotImplementedError


@service(name="control_plane_ring_topology_service")
@dataclass(slots=True)
class DefaultControlPlaneRingTopologyService(ControlPlaneRingTopologyService):
    execution_ipc: ExecutionIpcTransportService = inject.service(ExecutionIpcTransportService)
    state: object | None = inject.service(ControlPlaneStateService)
    store: object = inject.kv(ControlPlaneRingTopologyStore)

    def configure(self, *, runtime: dict[str, object], groups: list[dict[str, object]]) -> None:
        store = self._store()
        store.set(_PLAN_KEY, None)
        store.set(_CHILD_ENDPOINTS_KEY, {})
        if _resolve_data_plane_topology(runtime) != "ring":
            return
        worker_ids = _ring_worker_ids(groups)
        if len(worker_ids) < 2:
            store.set(
                _PLAN_KEY,
                ControlPlaneRingTopologyPlan(enabled=True, worker_ids=tuple(worker_ids), links=()),
            )
            return
        links: list[ControlPlaneRingLinkSpec] = []
        child_endpoints_by_worker: dict[str, dict[str, object]] = {}
        for index, from_worker_id in enumerate(worker_ids):
            to_worker_id = worker_ids[(index + 1) % len(worker_ids)]
            target_id = f"ring:{from_worker_id}->{to_worker_id}:data"
            from_endpoint, to_endpoint = self._allocate_direct_link_endpoints(target_id=target_id)
            child_endpoints_by_worker.setdefault(from_worker_id, {})[
                f"{_DIRECT_TARGET_PREFIX}{target_id}"
            ] = from_endpoint
            child_endpoints_by_worker.setdefault(to_worker_id, {})[
                f"{_DIRECT_TARGET_PREFIX}{target_id}"
            ] = to_endpoint
            links.append(
                ControlPlaneRingLinkSpec(
                    from_worker_id=from_worker_id,
                    to_worker_id=to_worker_id,
                    lane="data",
                    target_id=target_id,
                )
            )
        observability_worker_id = _observability_worker_id(groups)
        if isinstance(observability_worker_id, str) and observability_worker_id:
            for source_worker_id in worker_ids:
                for lane in (
                    EXECUTION_IPC_LANE_TRACE,
                    EXECUTION_IPC_LANE_LOG,
                    EXECUTION_IPC_LANE_METRIC,
                ):
                    target_id = compose_execution_ipc_worker_target_id(source_worker_id, lane=lane)
                    source_endpoint, observability_endpoint = self._allocate_direct_link_endpoints(
                        target_id=target_id
                    )
                    child_endpoints_by_worker.setdefault(source_worker_id, {})[
                        f"{_DIRECT_TARGET_PREFIX}{target_id}"
                    ] = source_endpoint
                    child_endpoints_by_worker.setdefault(observability_worker_id, {})[
                        f"{_DIRECT_TARGET_PREFIX}{target_id}"
                    ] = observability_endpoint
                    links.append(
                        ControlPlaneRingLinkSpec(
                            from_worker_id=source_worker_id,
                            to_worker_id=observability_worker_id,
                            lane=lane,
                            target_id=target_id,
                        )
                    )
        store.set(_CHILD_ENDPOINTS_KEY, dict(child_endpoints_by_worker))
        store.set(
            _PLAN_KEY,
            ControlPlaneRingTopologyPlan(
            enabled=True,
            worker_ids=tuple(worker_ids),
            links=tuple(links),
            ),
        )
        self._append_state_event(
            {
                "kind": "control_plane.ring_topology.configured",
                "worker_ids": list(worker_ids),
                "link_count": len(links),
            }
        )

    def plan(self) -> ControlPlaneRingTopologyPlan | None:
        candidate = self._store().get(_PLAN_KEY)
        if isinstance(candidate, ControlPlaneRingTopologyPlan):
            return candidate
        return None

    def pop_child_endpoints(self, *, worker_id: str) -> dict[str, object]:
        if not isinstance(worker_id, str) or not worker_id:
            return {}
        store = self._store()
        raw = store.get(_CHILD_ENDPOINTS_KEY)
        endpoints_by_worker = dict(raw) if isinstance(raw, dict) else {}
        payload = endpoints_by_worker.pop(worker_id, None)
        store.set(_CHILD_ENDPOINTS_KEY, endpoints_by_worker)
        if not isinstance(payload, dict):
            return {}
        return dict(payload)

    def _ipc(self) -> ExecutionIpcTransportService:
        candidate = self.execution_ipc
        if isinstance(candidate, ExecutionIpcTransportService):
            return candidate
        if callable(getattr(candidate, "allocate_local_endpoints", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ExecutionIpcTransportService binding is required")

    def _append_state_event(self, event: object) -> None:
        candidate = self.state
        append_event = getattr(candidate, "append_event", None)
        if callable(append_event):
            try:
                append_event(event)
            except Exception:
                return

    def _store(self) -> KVStore:
        candidate = self.store
        if isinstance(candidate, KVStore):
            return candidate
        if callable(getattr(candidate, "set", None)) and callable(getattr(candidate, "get", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ControlPlaneRingTopologyStore binding is required")

    def _allocate_direct_link_endpoints(self, *, target_id: str) -> tuple[object, object]:
        allocator = getattr(self._ipc(), "allocate_local_endpoints", None)
        if not callable(allocator):
            raise ValueError("ExecutionIpcTransportService.allocate_local_endpoints binding is required")
        try:
            return allocator(target_id, register_parent_endpoint=False)
        except TypeError:
            return allocator(target_id)


def _resolve_data_plane_topology(runtime: dict[str, object]) -> str:
    if not isinstance(runtime, dict):
        return "star"
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return "star"
    execution_ipc = platform.get("execution_ipc", {})
    if not isinstance(execution_ipc, dict):
        return "star"
    raw = execution_ipc.get("data_plane_topology", "star")
    if not isinstance(raw, str) or not raw:
        return "star"
    normalized = raw.strip().lower()
    if normalized not in {"star", "ring"}:
        return "star"
    return normalized


def _ring_worker_ids(groups: list[dict[str, object]]) -> list[str]:
    worker_ids: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_name = group.get("name")
        if not isinstance(group_name, str) or not group_name:
            continue
        nodes = group.get("nodes")
        node_list = nodes if isinstance(nodes, list) else []
        is_observability = group_name == "system.observability" or any(
            isinstance(node_name, str) and node_name.startswith("system.obs.")
            for node_name in node_list
        )
        if is_observability:
            continue
        workers = group.get("workers", 1)
        worker_count = int(workers) if isinstance(workers, int) and workers > 0 else 1
        for index in range(worker_count):
            worker_ids.append(f"{group_name}#{index + 1}")
    return worker_ids


def _observability_worker_id(groups: list[dict[str, object]]) -> str | None:
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_name = group.get("name")
        if not isinstance(group_name, str) or not group_name:
            continue
        nodes = group.get("nodes")
        node_list = nodes if isinstance(nodes, list) else []
        is_observability = group_name == "system.observability" or any(
            isinstance(node_name, str) and node_name.startswith("system.obs.")
            for node_name in node_list
        )
        if not is_observability:
            continue
        workers = group.get("workers", 1)
        worker_count = int(workers) if isinstance(workers, int) and workers > 0 else 1
        if worker_count <= 0:
            return None
        return f"{group_name}#1"
    return None


__all__ = [
    "ControlPlaneRingLinkSpec",
    "ControlPlaneRingTopologyPlan",
    "ControlPlaneRingTopologyService",
    "ControlPlaneRingTopologyStore",
    "DefaultControlPlaneRingTopologyService",
]
