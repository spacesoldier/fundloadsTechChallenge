from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import KVStore

_STATE_KEY = "control_plane.node_initialization.state"


class ControlPlaneNodeInitializationStore(KVStore):
    # KV marker for control-plane node initialization phase state.
    pass


@dataclass(frozen=True, slots=True)
class ControlPlaneNodeInitializationProgress:
    runtime: dict[str, object] = field(default_factory=dict)
    expected_nodes: tuple[str, ...] = ()
    initialized_nodes: tuple[str, ...] = ()
    pending_nodes: tuple[str, ...] = ()

    @property
    def completed(self) -> bool:
        return bool(self.expected_nodes) and not self.pending_nodes


@runtime_checkable
class ControlPlaneNodeInitializationService(Protocol):
    def begin_phase(
        self,
        *,
        runtime: dict[str, object] | None = None,
        node_names: tuple[str, ...] | list[str],
    ) -> tuple[str, ...]:
        raise NotImplementedError

    def mark_initialized(self, *, node_name: str) -> ControlPlaneNodeInitializationProgress:
        raise NotImplementedError

    def progress(self) -> ControlPlaneNodeInitializationProgress:
        raise NotImplementedError


@service(name="control_plane_node_initialization_service")
@dataclass(slots=True)
class InMemoryControlPlaneNodeInitializationService(ControlPlaneNodeInitializationService):
    store: KVStore = inject.kv(ControlPlaneNodeInitializationStore)

    def begin_phase(
        self,
        *,
        runtime: dict[str, object] | None = None,
        node_names: tuple[str, ...] | list[str],
    ) -> tuple[str, ...]:
        expected = _normalize_node_names(node_names)
        state = {
            "runtime": dict(runtime) if isinstance(runtime, dict) else {},
            "expected_nodes": list(expected),
            "initialized_nodes": [],
            "pending_nodes": list(expected),
        }
        self.store.set(_STATE_KEY, state)
        return expected

    def mark_initialized(self, *, node_name: str) -> ControlPlaneNodeInitializationProgress:
        state = self._load_state()
        if node_name in state["pending_nodes"]:
            state["pending_nodes"] = [item for item in state["pending_nodes"] if item != node_name]
        if node_name in state["expected_nodes"] and node_name not in state["initialized_nodes"]:
            state["initialized_nodes"].append(node_name)
        self.store.set(_STATE_KEY, state)
        return _state_to_progress(state)

    def progress(self) -> ControlPlaneNodeInitializationProgress:
        return _state_to_progress(self._load_state())

    def _load_state(self) -> dict[str, object]:
        raw = self.store.get(_STATE_KEY)
        if not isinstance(raw, dict):
            return {
                "runtime": {},
                "expected_nodes": [],
                "initialized_nodes": [],
                "pending_nodes": [],
            }
        runtime = raw.get("runtime")
        normalized_runtime = dict(runtime) if isinstance(runtime, dict) else {}
        expected = _normalize_node_names(raw.get("expected_nodes", []))
        initialized = [name for name in raw.get("initialized_nodes", []) if isinstance(name, str) and name in expected]
        pending = [name for name in raw.get("pending_nodes", []) if isinstance(name, str) and name in expected]
        # Keep deterministic ordering by expected declaration order.
        initialized_set = set(initialized)
        pending_set = set(pending)
        return {
            "runtime": normalized_runtime,
            "expected_nodes": list(expected),
            "initialized_nodes": [name for name in expected if name in initialized_set],
            "pending_nodes": [name for name in expected if name in pending_set],
        }



def _normalize_node_names(node_names: tuple[str, ...] | list[str] | object) -> tuple[str, ...]:
    if not isinstance(node_names, (tuple, list)):
        return ()
    seen: set[str] = set()
    normalized: list[str] = []
    for item in node_names:
        if not isinstance(item, str) or not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return tuple(normalized)



def _state_to_progress(state: dict[str, object]) -> ControlPlaneNodeInitializationProgress:
    runtime = state.get("runtime", {})
    expected = _normalize_node_names(state.get("expected_nodes", []))
    initialized = tuple(
        name for name in expected if name in set(_normalize_node_names(state.get("initialized_nodes", [])))
    )
    pending = tuple(
        name for name in expected if name in set(_normalize_node_names(state.get("pending_nodes", [])))
    )
    return ControlPlaneNodeInitializationProgress(
        runtime=dict(runtime) if isinstance(runtime, dict) else {},
        expected_nodes=expected,
        initialized_nodes=initialized,
        pending_nodes=pending,
    )


__all__ = [
    "ControlPlaneNodeInitializationStore",
    "ControlPlaneNodeInitializationProgress",
    "ControlPlaneNodeInitializationService",
    "InMemoryControlPlaneNodeInitializationService",
]
