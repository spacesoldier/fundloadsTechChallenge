from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import KVStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafShutdownPrepareCommand,
)

_EXPECTED_GROUPS_KEY = "control_plane.shutdown.expected_groups"
_TOMBSTONE_GROUPS_KEY = "control_plane.shutdown.tombstone_groups"
_READY_GROUPS_KEY = "control_plane.shutdown.ready_groups"
_SEEN_REQUEST_IDS_KEY = "control_plane.shutdown.seen_request_ids"
_PREPARE_EMITTED_KEY = "control_plane.shutdown.prepare_emitted"
_EMITTED_READY_KEY = "control_plane.shutdown.emitted_ready"

_LEAF_SEEN_REQUESTS_KEY = "control_plane.leaf_shutdown.seen_requests"
_LEAF_PREPARE_SEEN_KEY = "control_plane.leaf_shutdown.prepare_seen"
_LEAF_PENDING_TOMBSTONE_KEY = "control_plane.leaf_shutdown.pending_tombstone"


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
    prepare_emitted: bool = False
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
        return self.prepare_emitted and bool(expected) and expected.issubset(ready) and expected.issubset(tombstone)


@runtime_checkable
class ControlPlaneShutdownReadinessService(Protocol):
    def configure_expected_groups(self, groups: tuple[str, ...]) -> None:
        raise NotImplementedError

    def observe_tombstone(
        self, result: ControlPlaneLeafBoundaryResultEvent
    ) -> tuple[bool, ControlPlaneShutdownReadinessSnapshot]:
        raise NotImplementedError

    def mark_leaf_ready(self, event: ControlPlaneLeafDrainReadyEvent) -> tuple[bool, ControlPlaneShutdownReadinessSnapshot]:
        raise NotImplementedError

    def snapshot(self) -> ControlPlaneShutdownReadinessSnapshot:
        raise NotImplementedError


@runtime_checkable
class ControlPlaneLeafShutdownReadinessService(Protocol):
    def observe_boundary_result(self, result: ControlPlaneLeafBoundaryResultEvent) -> ControlPlaneLeafDrainReadyEvent | None:
        raise NotImplementedError

    def observe_prepare_command(
        self, command: ControlPlaneLeafShutdownPrepareCommand
    ) -> ControlPlaneLeafDrainReadyEvent | None:
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

    def observe_tombstone(
        self, result: ControlPlaneLeafBoundaryResultEvent
    ) -> tuple[bool, ControlPlaneShutdownReadinessSnapshot]:
        if result.status.strip().lower() == "completed" and (result.tombstone_input or result.tombstone_output):
            groups = self._load_set(_TOMBSTONE_GROUPS_KEY)
            groups.add(result.target_group)
            self._save_set(_TOMBSTONE_GROUPS_KEY, groups)
        snapshot = self.snapshot()
        expected = set(snapshot.expected_groups)
        observed = set(snapshot.tombstone_groups)
        prepare_emitted = bool(self.store.get(_PREPARE_EMITTED_KEY) is True)
        should_emit_prepare = bool(expected) and expected.issubset(observed) and not prepare_emitted
        if should_emit_prepare:
            self.store.set(_PREPARE_EMITTED_KEY, True)
            snapshot = self.snapshot()
        return should_emit_prepare, snapshot

    def mark_leaf_ready(self, event: ControlPlaneLeafDrainReadyEvent) -> tuple[bool, ControlPlaneShutdownReadinessSnapshot]:
        seen = self._load_set(_SEEN_REQUEST_IDS_KEY)
        dedupe_key = _ready_dedupe_key(event.target_group, event.request_id)
        if dedupe_key not in seen:
            seen.add(dedupe_key)
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
        prepare_emitted = bool(self.store.get(_PREPARE_EMITTED_KEY) is True)
        emitted_ready = bool(self.store.get(_EMITTED_READY_KEY) is True)
        return ControlPlaneShutdownReadinessSnapshot(
            expected_groups=expected_groups,
            tombstone_groups=tombstone_groups,
            ready_groups=ready_groups,
            prepare_emitted=prepare_emitted,
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
        if not (result.tombstone_input or result.tombstone_output):
            return None
        if self._already_emitted(result.target_group, result.request_id):
            return None
        if not self._prepare_seen():
            self.store.set(
                _LEAF_PENDING_TOMBSTONE_KEY,
                {
                    "target_group": result.target_group,
                    "worker_id": result.worker_id,
                    "request_id": result.request_id,
                    "tombstone_output": result.tombstone_output,
                },
            )
            return None
        return self._emit_ready(
            target_group=result.target_group,
            worker_id=result.worker_id,
            request_id=result.request_id,
            tombstone_output=result.tombstone_output,
        )

    def observe_prepare_command(
        self, command: ControlPlaneLeafShutdownPrepareCommand
    ) -> ControlPlaneLeafDrainReadyEvent | None:
        self.store.set(_LEAF_PREPARE_SEEN_KEY, True)
        pending = self._pending_tombstone()
        if pending is None:
            return None
        if self._already_emitted(pending.target_group, pending.request_id):
            return None
        return self._emit_ready(
            target_group=pending.target_group,
            worker_id=pending.worker_id,
            request_id=pending.request_id,
            tombstone_output=pending.tombstone_output,
        )

    def _seen_request_ids(self) -> set[str]:
        raw = self.store.get(_LEAF_SEEN_REQUESTS_KEY)
        if isinstance(raw, list):
            return {item for item in raw if isinstance(item, str) and item}
        if isinstance(raw, tuple):
            return {item for item in raw if isinstance(item, str) and item}
        return set()

    def _prepare_seen(self) -> bool:
        return bool(self.store.get(_LEAF_PREPARE_SEEN_KEY) is True)

    def _already_emitted(self, target_group: str, request_id: str) -> bool:
        return _ready_dedupe_key(target_group, request_id) in self._seen_request_ids()

    def _emit_ready(
        self,
        *,
        target_group: str,
        worker_id: str,
        request_id: str,
        tombstone_output: bool,
    ) -> ControlPlaneLeafDrainReadyEvent:
        seen = self._seen_request_ids()
        seen.add(_ready_dedupe_key(target_group, request_id))
        self.store.set(_LEAF_SEEN_REQUESTS_KEY, sorted(seen))
        return ControlPlaneLeafDrainReadyEvent(
            target_group=target_group,
            worker_id=worker_id,
            request_id=request_id,
            tombstone_output=tombstone_output,
        )

    def _pending_tombstone(self) -> _PendingTombstone | None:
        raw = self.store.get(_LEAF_PENDING_TOMBSTONE_KEY)
        if not isinstance(raw, dict):
            return None
        target_group = raw.get("target_group")
        worker_id = raw.get("worker_id")
        request_id = raw.get("request_id")
        tombstone_output = raw.get("tombstone_output")
        if (
            not isinstance(target_group, str)
            or not target_group
            or not isinstance(worker_id, str)
            or not worker_id
            or not isinstance(request_id, str)
            or not request_id
            or not isinstance(tombstone_output, bool)
        ):
            return None
        return _PendingTombstone(
            target_group=target_group,
            worker_id=worker_id,
            request_id=request_id,
            tombstone_output=tombstone_output,
        )


@dataclass(frozen=True, slots=True)
class _PendingTombstone:
    target_group: str
    worker_id: str
    request_id: str
    tombstone_output: bool


def _ready_dedupe_key(target_group: str, request_id: str) -> str:
    normalized_group = target_group.strip() if isinstance(target_group, str) else ""
    normalized_request = request_id.strip() if isinstance(request_id, str) else ""
    if not normalized_group:
        return normalized_request
    if not normalized_request:
        return normalized_group
    return f"{normalized_group}::{normalized_request}"


__all__ = [
    "ControlPlaneShutdownReadinessStore",
    "ControlPlaneLeafShutdownReadinessStore",
    "ControlPlaneShutdownReadinessSnapshot",
    "ControlPlaneShutdownReadinessService",
    "ControlPlaneLeafShutdownReadinessService",
    "InMemoryControlPlaneShutdownReadinessService",
    "InMemoryControlPlaneLeafShutdownReadinessService",
]
