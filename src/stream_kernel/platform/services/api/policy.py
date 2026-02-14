from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore


@runtime_checkable
class ApiPolicyService(Protocol):
    # Platform-owned API policy descriptor service.
    def defaults(self) -> dict[str, object]:
        raise NotImplementedError("ApiPolicyService.defaults must be implemented")

    def profile(self, name: str | None) -> dict[str, object]:
        raise NotImplementedError("ApiPolicyService.profile must be implemented")

    def list_profiles(self) -> list[str]:
        raise NotImplementedError("ApiPolicyService.list_profiles must be implemented")


@runtime_checkable
class RateLimiterService(Protocol):
    # Platform-owned rate-limiter contract (algorithm implementation is backend-specific).
    def profile_name(self) -> str | None:
        raise NotImplementedError("RateLimiterService.profile_name must be implemented")

    def config(self) -> dict[str, object]:
        raise NotImplementedError("RateLimiterService.config must be implemented")

    def allow(self, *, key: str | None = None, cost: int = 1) -> bool:
        raise NotImplementedError("RateLimiterService.allow must be implemented")

    def release(self, *, key: str | None = None, cost: int = 1) -> None:
        raise NotImplementedError("RateLimiterService.release must be implemented")


@service(name="api_policy_service")
@dataclass(slots=True)
class InMemoryApiPolicyService(ApiPolicyService):
    # In-memory API policy service baseline used for runtime profile resolution.
    defaults_policy: dict[str, object] = field(default_factory=dict)
    profiles: dict[str, dict[str, object]] = field(default_factory=dict)

    def defaults(self) -> dict[str, object]:
        return dict(self.defaults_policy)

    def profile(self, name: str | None) -> dict[str, object]:
        if isinstance(name, str) and name:
            profile = self.profiles.get(name, {})
            return _merge_policy_dicts(self.defaults_policy, profile)
        return dict(self.defaults_policy)

    def list_profiles(self) -> list[str]:
        return sorted(self.profiles.keys())


@service(name="rate_limiter_service")
@dataclass(slots=True)
class InMemoryRateLimiterService(RateLimiterService):
    # Deterministic limiter implementations persisted through platform KV port.
    limiter_profile_name: str | None = None
    limiter_config: dict[str, object] = field(default_factory=dict)
    store: object = inject.kv(KVStore)
    state_prefix: str = "rate_limiter"
    now_fn: Callable[[], float] = time.monotonic
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _fallback_store: KVStore = field(default_factory=InMemoryKvStore, init=False, repr=False)

    def profile_name(self) -> str | None:
        return self.limiter_profile_name

    def config(self) -> dict[str, object]:
        return dict(self.limiter_config)

    def allow(self, *, key: str | None = None, cost: int = 1) -> bool:
        if not isinstance(cost, int) or cost <= 0:
            return False
        kind = self._kind()
        if kind is None:
            return True
        resolved_key = self._resolved_key(key)
        now = float(self.now_fn())
        with self._lock:
            if kind == "fixed_window":
                return self._allow_fixed_window(now=now, key=resolved_key, cost=cost)
            if kind == "sliding_window_counter":
                return self._allow_sliding_window_counter(now=now, key=resolved_key, cost=cost)
            if kind == "sliding_window_log":
                return self._allow_sliding_window_log(now=now, key=resolved_key, cost=cost)
            if kind == "token_bucket":
                return self._allow_token_bucket(now=now, key=resolved_key, cost=cost)
            if kind == "leaky_bucket":
                return self._allow_leaky_bucket(now=now, key=resolved_key, cost=cost)
            if kind == "concurrency":
                return self._allow_concurrency(key=resolved_key, cost=cost)
            return True

    def release(self, *, key: str | None = None, cost: int = 1) -> None:
        if self._kind() != "concurrency":
            return
        if not isinstance(cost, int) or cost <= 0:
            return
        resolved_key = self._resolved_key(key)
        with self._lock:
            store = self._kv_store()
            state_key = self._state_key(kind="concurrency", key=resolved_key)
            current_raw = store.get(state_key)
            current = int(current_raw) if isinstance(current_raw, int) else 0
            remaining = max(0, current - cost)
            if remaining == 0:
                store.delete(state_key)
            else:
                store.set(state_key, remaining)

    def _kind(self) -> str | None:
        kind = self.limiter_config.get("kind")
        if isinstance(kind, str) and kind:
            return kind
        return None

    def _resolved_key(self, key: str | None) -> str:
        if isinstance(key, str) and key:
            return key
        scope = self.limiter_config.get("scope")
        if isinstance(scope, str) and scope:
            return f"scope:{scope}"
        return "__global__"

    def _namespace(self) -> str:
        profile = self.limiter_profile_name or "default"
        return f"{self.state_prefix}:{profile}"

    def _state_key(self, *, kind: str, key: str) -> str:
        return f"{self._namespace()}:{kind}:{key}"

    def _kv_store(self) -> KVStore:
        candidate = self.store
        if isinstance(candidate, KVStore):
            return candidate
        if (
            callable(getattr(candidate, "get", None))
            and callable(getattr(candidate, "set", None))
            and callable(getattr(candidate, "delete", None))
        ):
            return candidate  # type: ignore[return-value]
        return self._fallback_store

    def _allow_fixed_window(self, *, now: float, key: str, cost: int) -> bool:
        limit = int(self.limiter_config.get("limit", 0))
        window_ms = int(self.limiter_config.get("window_ms", 0))
        if limit <= 0 or window_ms <= 0:
            return True
        store = self._kv_store()
        state_key = self._state_key(kind="fixed_window", key=key)
        window_id = int((now * 1000.0) // window_ms)
        existing_raw = store.get(state_key)
        existing = existing_raw if isinstance(existing_raw, dict) else {}
        prev_window = existing.get("window_id")
        prev_count = existing.get("count")
        count = int(prev_count) if prev_window == window_id and isinstance(prev_count, int) else 0
        if count + cost > limit:
            return False
        store.set(state_key, {"window_id": window_id, "count": count + cost})
        return True

    def _allow_sliding_window_counter(self, *, now: float, key: str, cost: int) -> bool:
        limit = int(self.limiter_config.get("limit", 0))
        window_ms = int(self.limiter_config.get("window_ms", 0))
        if limit <= 0 or window_ms <= 0:
            return True
        store = self._kv_store()
        state_key = self._state_key(kind="sliding_window_counter", key=key)
        now_ms = now * 1000.0
        window_id = int(now_ms // window_ms)
        elapsed_ms = now_ms - (window_id * window_ms)
        existing_raw = store.get(state_key)
        existing = existing_raw if isinstance(existing_raw, dict) else {}
        current_window = existing.get("window_id")
        current_count = existing.get("current")
        previous_count = existing.get("previous")
        if not isinstance(current_window, int):
            current_window = window_id
        if not isinstance(current_count, int):
            current_count = 0
        if not isinstance(previous_count, int):
            previous_count = 0
        if window_id != current_window:
            if window_id == current_window + 1:
                previous_count = current_count
            else:
                previous_count = 0
            current_count = 0
            current_window = window_id
        previous_weight = max(0.0, (window_ms - elapsed_ms) / float(window_ms))
        effective_count = current_count + (previous_count * previous_weight)
        if effective_count + cost > (limit + 1e-9):
            return False
        current_count += cost
        store.set(
            state_key,
            {
                "window_id": current_window,
                "current": current_count,
                "previous": previous_count,
            },
        )
        return True

    def _allow_sliding_window_log(self, *, now: float, key: str, cost: int) -> bool:
        limit = int(self.limiter_config.get("limit", 0))
        window_ms = int(self.limiter_config.get("window_ms", 0))
        if limit <= 0 or window_ms <= 0:
            return True
        store = self._kv_store()
        state_key = self._state_key(kind="sliding_window_log", key=key)
        window_seconds = window_ms / 1000.0
        existing_raw = store.get(state_key)
        history = existing_raw if isinstance(existing_raw, list) else []
        history = [item for item in history if isinstance(item, (int, float))]
        cutoff = now - window_seconds
        history = [stamp for stamp in history if float(stamp) > cutoff]
        if len(history) + cost > limit:
            return False
        history.extend([now] * cost)
        store.set(state_key, history)
        return True

    def _allow_token_bucket(self, *, now: float, key: str, cost: int) -> bool:
        refill_rate = float(self.limiter_config.get("refill_rate_per_sec", 0.0))
        capacity = float(self.limiter_config.get("bucket_capacity", 0))
        if refill_rate <= 0.0 or capacity <= 0.0:
            return True
        store = self._kv_store()
        state_key = self._state_key(kind="token_bucket", key=key)
        existing_raw = store.get(state_key)
        existing = existing_raw if isinstance(existing_raw, dict) else {}
        tokens = existing.get("tokens")
        last_refill = existing.get("last")
        if not isinstance(tokens, (int, float)):
            tokens = capacity
        if not isinstance(last_refill, (int, float)):
            last_refill = now
        tokens = float(tokens)
        last_refill = float(last_refill)
        elapsed = max(0.0, now - last_refill)
        tokens = min(capacity, tokens + (elapsed * refill_rate))
        if tokens + 1e-9 < cost:
            store.set(state_key, {"tokens": tokens, "last": now})
            return False
        store.set(state_key, {"tokens": tokens - cost, "last": now})
        return True

    def _allow_leaky_bucket(self, *, now: float, key: str, cost: int) -> bool:
        leak_rate = float(self.limiter_config.get("refill_rate_per_sec", 0.0))
        capacity = float(self.limiter_config.get("bucket_capacity", 0))
        if leak_rate <= 0.0 or capacity <= 0.0:
            return True
        store = self._kv_store()
        state_key = self._state_key(kind="leaky_bucket", key=key)
        existing_raw = store.get(state_key)
        existing = existing_raw if isinstance(existing_raw, dict) else {}
        level = existing.get("level")
        last_update = existing.get("last")
        if not isinstance(level, (int, float)):
            level = 0.0
        if not isinstance(last_update, (int, float)):
            last_update = now
        level = float(level)
        last_update = float(last_update)
        elapsed = max(0.0, now - last_update)
        level = max(0.0, level - (elapsed * leak_rate))
        if level + cost > capacity + 1e-9:
            store.set(state_key, {"level": level, "last": now})
            return False
        store.set(state_key, {"level": level + cost, "last": now})
        return True

    def _allow_concurrency(self, *, key: str, cost: int) -> bool:
        max_in_flight = int(self.limiter_config.get("max_in_flight", 0))
        if max_in_flight <= 0:
            return True
        store = self._kv_store()
        state_key = self._state_key(kind="concurrency", key=key)
        current_raw = store.get(state_key)
        current = int(current_raw) if isinstance(current_raw, int) else 0
        if current + cost > max_in_flight:
            return False
        store.set(state_key, current + cost)
        return True


def _merge_policy_dicts(
    base: dict[str, object],
    overlay: dict[str, object],
) -> dict[str, object]:
    merged = dict(base)
    for key, value in overlay.items():
        if (
            isinstance(value, dict)
            and isinstance(merged.get(key), dict)
        ):
            merged[key] = _merge_policy_dicts(
                merged.get(key, {}),  # type: ignore[arg-type]
                value,
            )
        else:
            merged[key] = value
    return merged
