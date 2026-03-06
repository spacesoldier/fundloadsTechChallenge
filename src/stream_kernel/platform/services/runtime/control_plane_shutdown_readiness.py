from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import KVStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafDrainReadyEvent,
)

_EXPECTED_GROUPS_KEY = "control_plane.shutdown.expected_groups"
_TOMBSTONE_GROUPS_KEY = "control_plane.shutdown.tombstone_groups"
_READY_GROUPS_KEY = "control_plane.shutdown.ready_groups"
_SEEN_REQUEST_IDS_KEY = "control_plane.shutdown.seen_request_ids"
_EMITTED_READY_KEY = "control_plane.shutdown.emitted_ready"

_LEAF_SEEN_REQUESTS_KEY = "control_plane.leaf_shutdown.seen_requests"


class ControlPlaneShutdownReadinessStore(KVStore):
    # KV marker contract for root-side shutdown readiness tracking.
    pass


class ControlPlaneLeafShutdownReadinessStore(KVStore):
    # KV marker contract for leaf-side tombstone/drain tracking.
    pass


@dataclass(frozen=True, slots=True)
class ControlPlaneShutdownReadinessSnapshot:
    expected_groups: tuple[str, ...] = ()
    tombstone_groups: tuple[str, ...] = ()
    ready_groups: tuple[str, ...] = ()
    emitted_ready: bool = False

    @property
    def missing_groups(self) -> tuple[str, ...]:
        expected = set(self.expected_groups)
        ready = set(self.ready_groups)
        return tuple(sorted(expected - ready))

    @property
    def shutdown_ready(self) -> bool:
        expected = set(self.expected_groups)
        ready = set(self.ready_groups)
        tombstone = set(self.tombstone_groups)
        return bool(tombstone) and bool(expected) and expected.issubset(ready)


@runtime_checkable
class ControlPlaneShutdownReadinessService(Protocol):
    def configure_expected_groups(self, groups: tuple[str, ...]) -> None:
        raise NotImplementedError

    def observe_tombstone(self, result: ControlPlaneLeafBoundaryResultEvent) -> ControlPlaneShutdownReadinessSnapshot:
        raise NotImplementedError

    def mark_leaf_ready(self, event: ControlPlaneLeafDrainReadyEvent) -> tuple[bool, ControlPlaneShutdownReadinessSnapshot]:
        raise NotImplementedError

    def snapshot(self) -> ControlPlaneShutdownReadinessSnapshot:
        raise NotImplementedError


@runtime_checkable
class ControlPlaneLeafShutdownReadinessService(Protocol):
    def observe_boundary_result(self, result: ControlPlaneLeafBoundaryResultEvent) -> ControlPlaneLeafDrainReadyEvent | None:
        raise NotImplementedError


@service(name="control_plane_shutdown_readiness_service")
@dataclass(slots=True)
class InMemoryControlPlaneShutdownReadinessService(ControlPlaneShutdownReadinessService):
    store: KVStore = inject.kv(ControlPlaneShutdownReadinessStore)

    def configure_expected_groups(self, groups: tuple[str, ...]) -> None:
        normalized = tuple(
            sorted(
                {
                    name
                    for name in groups
                    if isinstance(name, str) and name and not name.startswith("system.")
                }
            )
        )
        self.store.set(_EXPECTED_GROUPS_KEY, list(normalized))

    def observe_tombstone(self, result: ControlPlaneLeafBoundaryResultEvent) -> ControlPlaneShutdownReadinessSnapshot:
        if result.status.strip().lower() == "completed" and result.tombstone_input:
            groups = self._load_set(_TOMBSTONE_GROUPS_KEY)
            groups.add(result.target_group)
            self._save_set(_TOMBSTONE_GROUPS_KEY, groups)
        return self.snapshot()

    def mark_leaf_ready(self, event: ControlPlaneLeafDrainReadyEvent) -> tuple[bool, ControlPlaneShutdownReadinessSnapshot]:
        seen = self._load_set(_SEEN_REQUEST_IDS_KEY)
        if event.request_id not in seen:
            seen.add(event.request_id)
            self._save_set(_SEEN_REQUEST_IDS_KEY, seen)
            ready = self._load_set(_READY_GROUPS_KEY)
            ready.add(event.target_group)
            self._save_set(_READY_GROUPS_KEY, ready)
        snapshot = self.snapshot()
        emitted_ready = bool(self.store.get(_EMITTED_READY_KEY) is True)
        should_emit = snapshot.shutdown_ready and not emitted_ready
        if should_emit:
            self.store.set(_EMITTED_READY_KEY, True)
            snapshot = self.snapshot()
        return should_emit, snapshot

    def snapshot(self) -> ControlPlaneShutdownReadinessSnapshot:
        expected_groups = tuple(sorted(self._load_set(_EXPECTED_GROUPS_KEY)))
        tombstone_groups = tuple(sorted(self._load_set(_TOMBSTONE_GROUPS_KEY)))
        ready_groups = tuple(sorted(self._load_set(_READY_GROUPS_KEY)))
        emitted_ready = bool(self.store.get(_EMITTED_READY_KEY) is True)
        return ControlPlaneShutdownReadinessSnapshot(
            expected_groups=expected_groups,
            tombstone_groups=tombstone_groups,
            ready_groups=ready_groups,
            emitted_ready=emitted_ready,
        )

    def _load_set(self, key: str) -> set[str]:
        raw = self.store.get(key)
        if isinstance(raw, list):
            return {item for item in raw if isinstance(item, str) and item}
        if isinstance(raw, tuple):
            return {item for item in raw if isinstance(item, str) and item}
        return set()

    def _save_set(self, key: str, items: set[str]) -> None:
        self.store.set(key, sorted(items))


@service(name="control_plane_leaf_shutdown_readiness_service")
@dataclass(slots=True)
class InMemoryControlPlaneLeafShutdownReadinessService(ControlPlaneLeafShutdownReadinessService):
    store: KVStore = inject.kv(ControlPlaneLeafShutdownReadinessStore)

    def observe_boundary_result(self, result: ControlPlaneLeafBoundaryResultEvent) -> ControlPlaneLeafDrainReadyEvent | None:
        if result.status.strip().lower() != "completed":
            return None
        if not result.tombstone_input:
            return None
        seen = self._seen_request_ids()
        if result.request_id in seen:
            return None
        seen.add(result.request_id)
        self.store.set(_LEAF_SEEN_REQUESTS_KEY, sorted(seen))
        return ControlPlaneLeafDrainReadyEvent(
            target_group=result.target_group,
            worker_id=result.worker_id,
            request_id=result.request_id,
            tombstone_output=result.tombstone_output,
        )

    def _seen_request_ids(self) -> set[str]:
        raw = self.store.get(_LEAF_SEEN_REQUESTS_KEY)
        if isinstance(raw, list):
            return {item for item in raw if isinstance(item, str) and item}
        if isinstance(raw, tuple):
            return {item for item in raw if isinstance(item, str) and item}
        return set()


__all__ = [
    "ControlPlaneShutdownReadinessStore",
    "ControlPlaneLeafShutdownReadinessStore",
    "ControlPlaneShutdownReadinessSnapshot",
    "ControlPlaneShutdownReadinessService",
    "ControlPlaneLeafShutdownReadinessService",
    "InMemoryControlPlaneShutdownReadinessService",
    "InMemoryControlPlaneLeafShutdownReadinessService",
]
