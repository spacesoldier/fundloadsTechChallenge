from __future__ import annotations

import math
from dataclasses import dataclass

from fund_load.ports.prime_checker import PrimeChecker


@dataclass(frozen=True, slots=True)
class SievePrimeChecker(PrimeChecker):
    # Sieve-based prime checker follows the recommended approach in PrimeChecker.md.
    _max_n: int
    _is_prime: tuple[bool, ...]

    @classmethod
    def from_range(cls, min_n: int, max_n: int) -> SievePrimeChecker:
        # Range constructor keeps the public API aligned with the doc terminology.
        if max_n < 0:
            max_n = 0
        sieve = _sieve(max_n)
        return cls(_max_n=max_n, _is_prime=sieve)

    @classmethod
    def from_max(cls, max_n: int) -> SievePrimeChecker:
        # Convenience constructor for known upper bounds.
        return cls.from_range(0, max_n)

    def is_prime(self, id_num: int) -> bool:
        # Deterministic handling for n <= 1 per PrimeChecker doc.
        if id_num <= 1:
            return False

        if id_num <= self._max_n:
            return self._is_prime[id_num]

        # Outside precomputed range we fall back to trial division for determinism.
        return _is_prime_trial_division(id_num)


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

