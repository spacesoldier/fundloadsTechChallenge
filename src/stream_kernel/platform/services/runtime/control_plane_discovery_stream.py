from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import KVStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryBatchReadyEvent,
    ControlPlaneDiscoveryBatchRequestedEvent,
    ControlPlaneDiscoveryCompletedEvent,
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneDiscoverySourceCompletedEvent,
    ControlPlaneDiscoveryStartRequestedEvent,
    discovery_entity_sort_key,
)

_SESSIONS_SEQ_KEY = "control_plane.discovery_stream.sessions.seq"
_SESSION_KEY_PREFIX = "control_plane.discovery_stream.sessions."


class ControlPlaneDiscoverySessionRegistry(KVStore):
    # KV marker contract for discovery stream sessions.
    pass


@dataclass(slots=True)
class _DiscoverySourceState:
    next_cursor: int = 0
    emitted: int = 0
    completed: bool = False
    processed_cursors: set[int] = field(default_factory=set)


@dataclass(slots=True)
class _DiscoverySession:
    session_id: str
    runtime: dict[str, object]
    batch_limit: int
    active_scope: str = "platform"
    completed: bool = False
    platform: _DiscoverySourceState = field(default_factory=_DiscoverySourceState)
    project: _DiscoverySourceState = field(default_factory=_DiscoverySourceState)


@runtime_checkable
class ControlPlaneDiscoverySessionStore(Protocol):
    def create(self, *, runtime: dict[str, object], batch_limit: int) -> _DiscoverySession:
        raise NotImplementedError("ControlPlaneDiscoverySessionStore.create must be implemented")

    def get(self, session_id: str) -> _DiscoverySession | None:
        raise NotImplementedError("ControlPlaneDiscoverySessionStore.get must be implemented")

    def save(self, session: _DiscoverySession) -> None:
        raise NotImplementedError("ControlPlaneDiscoverySessionStore.save must be implemented")


@service(name="control_plane_discovery_session_store")
@dataclass(slots=True)
class InMemoryControlPlaneDiscoverySessionStore(ControlPlaneDiscoverySessionStore):
    store: KVStore = inject.kv(ControlPlaneDiscoverySessionRegistry)

    def create(self, *, runtime: dict[str, object], batch_limit: int) -> _DiscoverySession:
        seq = self.store.get(_SESSIONS_SEQ_KEY)
        current = seq if isinstance(seq, int) and seq >= 0 else 0
        next_seq = current + 1
        self.store.set(_SESSIONS_SEQ_KEY, next_seq)
        session = _DiscoverySession(
            session_id=f"discovery-session-{next_seq}",
            runtime=dict(runtime),
            batch_limit=batch_limit,
        )
        self.save(session)
        return session

    def get(self, session_id: str) -> _DiscoverySession | None:
        key = self._session_key(session_id)
        loaded = self.store.get(key)
        if isinstance(loaded, _DiscoverySession):
            return loaded
        return None

    def save(self, session: _DiscoverySession) -> None:
        self.store.set(self._session_key(session.session_id), session)

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"{_SESSION_KEY_PREFIX}{session_id}"


@runtime_checkable
class ControlPlaneDiscoverySourceAdapter(Protocol):
    def next_batch(
        self,
        *,
        runtime: dict[str, object],
        cursor: int,
        limit: int,
    ) -> tuple[tuple[ControlPlaneDiscoveryEntityRecord, ...], int | None]:
        raise NotImplementedError("ControlPlaneDiscoverySourceAdapter.next_batch must be implemented")


@runtime_checkable
class ControlPlaneDiscoveryStreamService(Protocol):
    def start(self, event: ControlPlaneDiscoveryStartRequestedEvent) -> list[object]:
        raise NotImplementedError("ControlPlaneDiscoveryStreamService.start must be implemented")

    def request_batch(self, event: ControlPlaneDiscoveryBatchRequestedEvent) -> list[object]:
        raise NotImplementedError("ControlPlaneDiscoveryStreamService.request_batch must be implemented")


@service(name="control_plane_discovery_stream_service")
@dataclass(slots=True)
class DefaultControlPlaneDiscoveryStreamService(ControlPlaneDiscoveryStreamService):
    platform_adapter: ControlPlaneDiscoverySourceAdapter = inject.service(
        ControlPlaneDiscoverySourceAdapter,
        qualifier="platform_discovery_source_adapter",
    )
    project_adapter: ControlPlaneDiscoverySourceAdapter = inject.service(
        ControlPlaneDiscoverySourceAdapter,
        qualifier="project_discovery_source_adapter",
    )
    session_store: ControlPlaneDiscoverySessionStore = inject.service(ControlPlaneDiscoverySessionStore)

    def start(self, event: ControlPlaneDiscoveryStartRequestedEvent) -> list[object]:
        batch_limit = _resolve_batch_limit(event.runtime)
        session = self._session_store().create(runtime=event.runtime, batch_limit=batch_limit)
        return [
            ControlPlaneDiscoveryBatchRequestedEvent(
                session_id=session.session_id,
                source_scope="platform",
                cursor=0,
                limit=session.batch_limit,
            )
        ]

    def request_batch(self, event: ControlPlaneDiscoveryBatchRequestedEvent) -> list[object]:
        store = self._session_store()
        session = store.get(event.session_id)
        if session is None or session.completed:
            return []
        if event.source_scope != session.active_scope:
            return []

        source_state = _scope_state(session, event.source_scope)
        if source_state.completed:
            return []
        if event.cursor in source_state.processed_cursors:
            return []
        if event.cursor != source_state.next_cursor:
            return []

        adapter = self._adapter_for_scope(event.source_scope)
        entities, next_cursor = adapter.next_batch(
            runtime=session.runtime,
            cursor=event.cursor,
            limit=event.limit,
        )
        ordered_entities = tuple(sorted(entities, key=discovery_entity_sort_key))
        has_more = isinstance(next_cursor, int) and next_cursor > event.cursor
        source_state.processed_cursors.add(event.cursor)
        source_state.emitted += len(ordered_entities)

        events: list[object] = [
            ControlPlaneDiscoveryBatchReadyEvent(
                session_id=event.session_id,
                source_scope=event.source_scope,
                cursor=event.cursor,
                entities=ordered_entities,
                has_more=has_more,
                next_cursor=next_cursor if has_more else None,
            )
        ]

        if has_more:
            source_state.next_cursor = next_cursor
            events.append(
                ControlPlaneDiscoveryBatchRequestedEvent(
                    session_id=event.session_id,
                    source_scope=event.source_scope,
                    cursor=next_cursor,
                    limit=session.batch_limit,
                )
            )
            store.save(session)
            return events

        source_state.completed = True
        events.append(
            ControlPlaneDiscoverySourceCompletedEvent(
                session_id=event.session_id,
                source_scope=event.source_scope,
                total_emitted=source_state.emitted,
            )
        )

        if event.source_scope == "platform":
            session.active_scope = "project"
            events.append(
                ControlPlaneDiscoveryBatchRequestedEvent(
                    session_id=event.session_id,
                    source_scope="project",
                    cursor=session.project.next_cursor,
                    limit=session.batch_limit,
                )
            )
        else:
            session.completed = True
            events.append(ControlPlaneDiscoveryCompletedEvent(runtime=dict(session.runtime)))

        store.save(session)
        return events

    def _adapter_for_scope(self, scope: str) -> ControlPlaneDiscoverySourceAdapter:
        if scope == "platform":
            return self._platform_adapter()
        return self._project_adapter()

    def _platform_adapter(self) -> ControlPlaneDiscoverySourceAdapter:
        adapter = self.platform_adapter
        if not isinstance(adapter, ControlPlaneDiscoverySourceAdapter):
            raise ValueError("ControlPlaneDiscoverySourceAdapter platform binding is required")
        return adapter

    def _project_adapter(self) -> ControlPlaneDiscoverySourceAdapter:
        adapter = self.project_adapter
        if not isinstance(adapter, ControlPlaneDiscoverySourceAdapter):
            raise ValueError("ControlPlaneDiscoverySourceAdapter project binding is required")
        return adapter

    def _session_store(self) -> ControlPlaneDiscoverySessionStore:
        store = self.session_store
        if not isinstance(store, ControlPlaneDiscoverySessionStore):
            raise ValueError("ControlPlaneDiscoverySessionStore binding is required")
        return store


def _scope_state(session: _DiscoverySession, scope: str) -> _DiscoverySourceState:
    if scope == "platform":
        return session.platform
    return session.project


def _resolve_batch_limit(runtime: dict[str, object]) -> int:
    discovery = runtime.get("platform")
    if not isinstance(discovery, dict):
        return 128
    platform_discovery = discovery.get("discovery")
    if not isinstance(platform_discovery, dict):
        return 128
    batch_size = platform_discovery.get("batch_size")
    if not isinstance(batch_size, int) or batch_size <= 0:
        return 128
    return batch_size


__all__ = [
    "ControlPlaneDiscoverySessionRegistry",
    "ControlPlaneDiscoverySessionStore",
    "InMemoryControlPlaneDiscoverySessionStore",
    "ControlPlaneDiscoverySourceAdapter",
    "ControlPlaneDiscoveryStreamService",
    "DefaultControlPlaneDiscoveryStreamService",
]
