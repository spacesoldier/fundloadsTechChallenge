from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import KVStore


class PrimeCacheKVStore(KVStore):
    # Marker KV contract for primality cache storage.
    pass


@runtime_checkable
class PrimeCheckerService(Protocol):
    # Service API for primality checks used by feature computation.
    def is_prime(self, id_num: int) -> bool:
        ...


@service(name="prime_checker_service")
@dataclass(frozen=True, slots=True)
class SievePrimeChecker(PrimeCheckerService):
    # Sieve-based prime checker. Cache is persisted through framework KV contract.
    _max_n: int = 0
    _is_prime: tuple[bool, ...] = field(default_factory=lambda: (False,))
    store: PrimeCacheKVStore = inject.kv(PrimeCacheKVStore, qualifier="prime_cache")

    @classmethod
    def from_range(
        cls,
        min_n: int,
        max_n: int,
        *,
        store: KVStore,
    ) -> SievePrimeChecker:
        # Range constructor keeps public API aligned with domain wording.
        _ = min_n
        if max_n < 0:
            max_n = 0
        sieve = _sieve(max_n)
        return cls(_max_n=max_n, _is_prime=sieve, store=store)

    @classmethod
    def from_max(cls, max_n: int, *, store: KVStore) -> SievePrimeChecker:
        # Convenience constructor for known upper bounds.
        return cls.from_range(0, max_n, store=store)

    def is_prime(self, id_num: int) -> bool:
        # Deterministic handling for n <= 1 per PrimeChecker spec.
        if id_num <= 1:
            return False

        cache_key = self._cache_key(id_num)
        cached = self._get_cached(cache_key)
        if isinstance(cached, bool):
            return cached

        if id_num <= self._max_n:
            result = self._is_prime[id_num]
            self._set_cached(cache_key, result)
            return result

        # Outside precomputed range we fall back to deterministic trial division.
        result = _is_prime_trial_division(id_num)
        self._set_cached(cache_key, result)
        return result

    def _get_cached(self, key: str) -> object | None:
        return self.store.get(key)

    def _set_cached(self, key: str, value: bool) -> None:
        self.store.set(key, value)

    @staticmethod
    def _cache_key(id_num: int) -> str:
        return f"prime:{id_num}"


def _sieve(max_n: int) -> tuple[bool, ...]:
    # Sieve of Eratosthenes for fast membership checks.
    if max_n < 1:
        return tuple([False] * (max_n + 1))

    is_prime = [True] * (max_n + 1)
    is_prime[0] = False
    is_prime[1] = False

    for p in range(2, int(math.isqrt(max_n)) + 1):
        if is_prime[p]:
            for multiple in range(p * p, max_n + 1, p):
                is_prime[multiple] = False

    return tuple(is_prime)


def _is_prime_trial_division(n: int) -> bool:
    # Trial division fallback is deterministic and fast enough for occasional out-of-range checks.
    if n <= 1:
        return False
    if n == 2:
        return True
    if n % 2 == 0:
        return False
    limit = int(math.isqrt(n))
    for d in range(3, limit + 1, 2):
        if n % d == 0:
            return False
    return True
