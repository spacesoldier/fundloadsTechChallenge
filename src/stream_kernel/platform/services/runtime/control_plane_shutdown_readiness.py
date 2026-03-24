from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import KVStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafRunnerTombstoneEvent,
)

_EXPECTED_GROUPS_KEY = "control_plane.shutdown.expected_groups"
_READY_GROUPS_KEY = "control_plane.shutdown.ready_groups"
_SEEN_REQUEST_IDS_KEY = "control_plane.shutdown.seen_request_ids"
_EMITTED_READY_KEY = "control_plane.shutdown.emitted_ready"

_LEAF_RUNNER_TOMBSTONE_EXPECTED_NODES_KEY = "control_plane.leaf_shutdown.runner.expected_nodes"
_LEAF_RUNNER_TOMBSTONE_SEEN_NODES_KEY = "control_plane.leaf_shutdown.runner.seen_nodes"
_LEAF_RUNNER_TOMBSTONE_EMITTED_READY_KEY = "control_plane.leaf_shutdown.runner.emitted_ready"

_LOGGER = logging.getLogger(__name__)


class ControlPlaneShutdownReadinessStore(KVStore):
    # KV marker contract for root-side shutdown readiness tracking.
    pass


class ControlPlaneLeafShutdownReadinessStore(KVStore):
    # KV marker contract for leaf-side tombstone/drain tracking.
    pass


@dataclass(frozen=True, slots=True)
class ControlPlaneShutdownReadinessSnapshot:
    expected_groups: tuple[str, ...] = ()
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
        return bool(expected) and expected.issubset(ready)


@runtime_checkable
class ControlPlaneShutdownReadinessService(Protocol):
    def configure_expected_groups(self, groups: tuple[str, ...]) -> None:
        raise NotImplementedError

    def mark_leaf_ready(self, event: ControlPlaneLeafDrainReadyEvent) -> tuple[bool, ControlPlaneShutdownReadinessSnapshot]:
        raise NotImplementedError

    def snapshot(self) -> ControlPlaneShutdownReadinessSnapshot:
        raise NotImplementedError


@runtime_checkable
class ControlPlaneLeafShutdownReadinessService(Protocol):
    def observe_runner_tombstone(
        self, event: ControlPlaneLeafRunnerTombstoneEvent
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
                    if isinstance(name, str) and name
                }
            )
        )
        self.store.set(_EXPECTED_GROUPS_KEY, list(normalized))

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
        ready_groups = tuple(sorted(self._load_set(_READY_GROUPS_KEY)))
        emitted_ready = bool(self.store.get(_EMITTED_READY_KEY) is True)
        return ControlPlaneShutdownReadinessSnapshot(
            expected_groups=expected_groups,
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

    def observe_runner_tombstone(
        self, event: ControlPlaneLeafRunnerTombstoneEvent
    ) -> ControlPlaneLeafDrainReadyEvent | None:
        if not event.tombstone_output:
            _LOGGER.info(
                "leaf_shutdown_runner_tombstone_ignored",
                extra={
                    "target_group": event.target_group,
                    "worker_id": event.worker_id,
                    "request_id": event.request_id,
                    "observed_node": event.observed_node,
                    "reason": "tombstone_output_false",
                },
            )
            return None
        if self.store.get(_LEAF_RUNNER_TOMBSTONE_EMITTED_READY_KEY) is True:
            _LOGGER.info(
                "leaf_shutdown_runner_tombstone_ignored",
                extra={
                    "target_group": event.target_group,
                    "worker_id": event.worker_id,
                    "request_id": event.request_id,
                    "observed_node": event.observed_node,
                    "reason": "already_emitted_ready",
                },
            )
            return None
        expected_nodes = self._runner_expected_nodes()
        expected_nodes.update(
            name
            for name in event.expected_nodes
            if isinstance(name, str) and name
        )
        self.store.set(_LEAF_RUNNER_TOMBSTONE_EXPECTED_NODES_KEY, sorted(expected_nodes))
        if not expected_nodes:
            _LOGGER.warning(
                "leaf_shutdown_runner_tombstone_expected_nodes_empty",
                extra={
                    "target_group": event.target_group,
                    "worker_id": event.worker_id,
                    "request_id": event.request_id,
                    "observed_node": event.observed_node,
                },
            )
            return None
        seen_nodes = self._runner_seen_nodes()
        if event.observed_node in expected_nodes:
            seen_nodes.add(event.observed_node)
            self.store.set(_LEAF_RUNNER_TOMBSTONE_SEEN_NODES_KEY, sorted(seen_nodes))
        if not expected_nodes.issubset(seen_nodes):
            _LOGGER.info(
                "leaf_shutdown_runner_tombstone_pending",
                extra={
                    "target_group": event.target_group,
                    "worker_id": event.worker_id,
                    "request_id": event.request_id,
                    "observed_node": event.observed_node,
                    "expected_nodes": sorted(expected_nodes),
                    "seen_nodes": sorted(seen_nodes),
                },
            )
            return None
        self.store.set(_LEAF_RUNNER_TOMBSTONE_EMITTED_READY_KEY, True)
        _LOGGER.info(
            "leaf_shutdown_runner_tombstone_quorum_reached",
            extra={
                "target_group": event.target_group,
                "worker_id": event.worker_id,
                "request_id": event.request_id,
                "observed_node": event.observed_node,
                "expected_nodes": sorted(expected_nodes),
                "seen_nodes": sorted(seen_nodes),
            },
        )
        return self._emit_ready(
            target_group=event.target_group,
            worker_id=event.worker_id,
            request_id=event.request_id,
            tombstone_output=True,
        )

    def _runner_expected_nodes(self) -> set[str]:
        raw = self.store.get(_LEAF_RUNNER_TOMBSTONE_EXPECTED_NODES_KEY)
        if isinstance(raw, list):
            return {item for item in raw if isinstance(item, str) and item}
        if isinstance(raw, tuple):
            return {item for item in raw if isinstance(item, str) and item}
        return set()

    def _runner_seen_nodes(self) -> set[str]:
        raw = self.store.get(_LEAF_RUNNER_TOMBSTONE_SEEN_NODES_KEY)
        if isinstance(raw, list):
            return {item for item in raw if isinstance(item, str) and item}
        if isinstance(raw, tuple):
            return {item for item in raw if isinstance(item, str) and item}
        return set()

    def _emit_ready(
        self,
        *,
        target_group: str,
        worker_id: str,
        request_id: str,
        tombstone_output: bool,
    ) -> ControlPlaneLeafDrainReadyEvent:
        _LOGGER.info(
            "leaf_shutdown_emit_ready",
            extra={
                "target_group": target_group,
                "worker_id": worker_id,
                "request_id": request_id,
                "tombstone_output": tombstone_output,
            },
        )
        return ControlPlaneLeafDrainReadyEvent(
            target_group=target_group,
            worker_id=worker_id,
            request_id=request_id,
            tombstone_output=tombstone_output,
        )


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
