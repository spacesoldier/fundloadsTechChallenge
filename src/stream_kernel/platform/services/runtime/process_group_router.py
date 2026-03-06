from __future__ import annotations

from threading import Lock

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore

_TARGET_GROUP_MAP_KEY = "process_group_router.target_group_map"


class ProcessGroupRouterStore(KVStore):
    # KV marker contract for process-group routing placement map.
    pass


class ProcessGroupRouterService:
    # Runtime routing contract for resolving node target -> process-group with route-cache.
    def configure_process_groups(self, groups: list[dict[str, object]]) -> None:
        raise NotImplementedError("ProcessGroupRouterService.configure_process_groups must be implemented")

    def configure_routing_cache(self, settings: dict[str, object]) -> None:
        raise NotImplementedError("ProcessGroupRouterService.configure_routing_cache must be implemented")

    def resolve_group_for_target(self, *, target: str, source_group: str | None) -> str:
        raise NotImplementedError("ProcessGroupRouterService.resolve_group_for_target must be implemented")

    def snapshot(self) -> dict[str, object]:
        raise NotImplementedError("ProcessGroupRouterService.snapshot must be implemented")


@service(name="process_group_router_service")
class InMemoryProcessGroupRouterService(ProcessGroupRouterService):
    # In-memory route-cache and placement map for supervisor boundary dispatch.
    store: object = inject.kv(ProcessGroupRouterStore)

    def __init__(self, *, store: KVStore | None = None) -> None:
        self._explicit_store = store if isinstance(store, KVStore) else None
        self._fallback_store = InMemoryKvStore()
        self._target_group_map: dict[str, str] = {}
        self._route_cache_enabled = True
        self._route_cache_negative = True
        self._route_cache_max_entries = 100000
        self._route_cache: dict[tuple[str, str | None], str] = {}
        self._route_negative_cache: set[tuple[str, str | None]] = set()
        self._route_cache_hits = 0
        self._route_cache_misses = 0
        self._route_cache_negative_hits = 0
        self._route_cache_generation = 0
        self._lock = Lock()
        self._target_group_map = self._load_target_group_map()

    def configure_process_groups(self, groups: list[dict[str, object]]) -> None:
        target_map: dict[str, str] = {}
        for index, group in enumerate(groups):
            if not isinstance(group, dict):
                raise ValueError(f"runtime.platform.process_groups[{index}] must be a mapping")
            name = group.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError(f"runtime.platform.process_groups[{index}].name must be a non-empty string")
            nodes = group.get("nodes", [])
            if nodes is None:
                nodes = []
            if not isinstance(nodes, list):
                raise ValueError(f"runtime.platform.process_groups[{index}].nodes must be a list")
            for node_name in nodes:
                if not isinstance(node_name, str) or not node_name:
                    raise ValueError(
                        f"runtime.platform.process_groups[{index}].nodes entries must be non-empty strings"
                    )
                existing = target_map.get(node_name)
                if isinstance(existing, str) and existing != name:
                    raise ValueError(
                        f"runtime.platform.process_groups has duplicate placement for node '{node_name}'"
                    )
                target_map[node_name] = name
        with self._lock:
            self._target_group_map = target_map
            self._persist_target_group_map_locked(target_map)
            self._clear_route_cache_locked()

    def configure_routing_cache(self, settings: dict[str, object]) -> None:
        enabled = settings.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("runtime.platform.routing_cache.enabled must be a boolean when provided")
        negative_cache = settings.get("negative_cache", True)
        if not isinstance(negative_cache, bool):
            raise ValueError("runtime.platform.routing_cache.negative_cache must be a boolean when provided")
        max_entries = settings.get("max_entries", 100000)
        if not isinstance(max_entries, int):
            raise ValueError("runtime.platform.routing_cache.max_entries must be an integer when provided")
        if max_entries <= 0:
            raise ValueError("runtime.platform.routing_cache.max_entries must be > 0")
        with self._lock:
            self._route_cache_enabled = enabled
            self._route_cache_negative = negative_cache
            self._route_cache_max_entries = max_entries
            self._clear_route_cache_locked()

    def resolve_group_for_target(self, *, target: str, source_group: str | None) -> str:
        cache_key = (target, source_group if isinstance(source_group, str) and source_group else None)
        with self._lock:
            if self._route_cache_enabled:
                cached_group = self._route_cache.get(cache_key)
                if isinstance(cached_group, str) and cached_group:
                    self._route_cache_hits += 1
                    return cached_group
                if cache_key in self._route_negative_cache:
                    self._route_cache_negative_hits += 1
                    raise ConnectionError(f"remote handoff transport failed for group '{target}'")

            self._route_cache_misses += 1
            if not self._target_group_map:
                self._target_group_map = self._load_target_group_map()
            mapped_group = self._target_group_map.get(target)
            if isinstance(mapped_group, str) and mapped_group:
                if self._route_cache_enabled:
                    self._route_cache[cache_key] = mapped_group
                    self._route_negative_cache.discard(cache_key)
                    self._evict_route_cache_locked()
                return mapped_group

            if self._target_group_map:
                if self._route_cache_enabled and self._route_cache_negative:
                    self._route_negative_cache.add(cache_key)
                    self._evict_route_cache_locked()
                raise ConnectionError(f"remote handoff transport failed for group '{target}'")

            if isinstance(source_group, str) and source_group:
                if self._route_cache_enabled:
                    self._route_cache[cache_key] = source_group
                    self._route_negative_cache.discard(cache_key)
                    self._evict_route_cache_locked()
                return source_group

            if self._route_cache_enabled and self._route_cache_negative:
                self._route_negative_cache.add(cache_key)
                self._evict_route_cache_locked()
            raise ConnectionError(f"remote handoff transport failed for group '{target}'")

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": self._route_cache_enabled,
                "negative_cache": self._route_cache_negative,
                "max_entries": self._route_cache_max_entries,
                "generation": self._route_cache_generation,
                "hits": self._route_cache_hits,
                "misses": self._route_cache_misses,
                "negative_hits": self._route_cache_negative_hits,
                "positive_entries": len(self._route_cache),
                "negative_entries": len(self._route_negative_cache),
            }

    def _clear_route_cache_locked(self) -> None:
        self._route_cache.clear()
        self._route_negative_cache.clear()
        self._route_cache_generation += 1

    def _evict_route_cache_locked(self) -> None:
        while len(self._route_cache) > self._route_cache_max_entries:
            oldest_key = next(iter(self._route_cache))
            self._route_cache.pop(oldest_key, None)
        while len(self._route_negative_cache) > self._route_cache_max_entries:
            self._route_negative_cache.pop()

    def _store(self) -> KVStore:
        if isinstance(self._explicit_store, KVStore):
            return self._explicit_store
        candidate = self.store
        if isinstance(candidate, KVStore):
            return candidate
        return self._fallback_store

    def _persist_target_group_map_locked(self, target_map: dict[str, str]) -> None:
        self._store().set(_TARGET_GROUP_MAP_KEY, dict(target_map))

    def _load_target_group_map(self) -> dict[str, str]:
        raw = self._store().get(_TARGET_GROUP_MAP_KEY)
        if not isinstance(raw, dict):
            return {}
        return {
            target: group
            for target, group in raw.items()
            if isinstance(target, str)
            and target
            and isinstance(group, str)
            and group
        }
