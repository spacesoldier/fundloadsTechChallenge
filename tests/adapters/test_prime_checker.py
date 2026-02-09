from __future__ import annotations

# Prime checker semantics and suggested cases are documented in
# docs/implementation/ports/PrimeChecker.md.
from fund_load.services.prime_checker import SievePrimeChecker, _is_prime_trial_division
from stream_kernel.integration.kv_store import InMemoryKvStore


class _SpyStore:
    def __init__(self) -> None:
        self._values: dict[str, object] = {}
        self.get_calls = 0
        self.set_calls = 0

    def get(self, key: str) -> object | None:
        self.get_calls += 1
        return self._values.get(key)

    def set(self, key: str, value: object) -> None:
        self.set_calls += 1
        self._values[key] = value

    def delete(self, key: str) -> None:
        self._values.pop(key, None)


def test_prime_checker_basic_values() -> None:
    # Basic primality rules (n <= 1 not prime; 2 and 3 prime; even > 2 not prime).
    checker = SievePrimeChecker.from_max(100, store=InMemoryKvStore())
    assert checker.is_prime(-3) is False
    assert checker.is_prime(0) is False
    assert checker.is_prime(1) is False
    assert checker.is_prime(2) is True
    assert checker.is_prime(3) is True
    assert checker.is_prime(4) is False
    assert checker.is_prime(17) is True
    assert checker.is_prime(18) is False


def test_prime_checker_range_membership() -> None:
    # Range-based sieve should include known primes within the range.
    checker = SievePrimeChecker.from_range(1, 30, store=InMemoryKvStore())
    for n in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29):
        assert checker.is_prime(n) is True
    for n in (4, 6, 8, 9, 10, 12, 14, 15, 16, 18, 20, 21, 22, 24, 25, 26, 27, 28, 30):
        assert checker.is_prime(n) is False


def test_prime_checker_outside_range_fallback() -> None:
    # If queried outside the precomputed range, we fall back to deterministic trial division.
    checker = SievePrimeChecker.from_max(30, store=InMemoryKvStore())
    assert checker.is_prime(97) is True
    assert checker.is_prime(99) is False


def test_prime_checker_negative_range_is_clamped() -> None:
    # Negative max range is clamped to 0 per PrimeChecker spec.
    checker = SievePrimeChecker.from_range(0, -5, store=InMemoryKvStore())
    assert checker.is_prime(2) is True


def test_prime_checker_trial_division_edge_cases() -> None:
    # Trial division handles n<=1, n==2, and even numbers (PrimeChecker spec).
    assert _is_prime_trial_division(1) is False
    assert _is_prime_trial_division(2) is True
    assert _is_prime_trial_division(4) is False


def test_prime_checker_uses_kv_cache_for_repeated_checks() -> None:
    # Platform-aligned service: repeated checks should be served from KV-backed cache.
    store = _SpyStore()
    checker = SievePrimeChecker.from_max(10, store=store)

    assert checker.is_prime(11) is True
    writes_after_first_call = store.set_calls
    assert writes_after_first_call == 1

    assert checker.is_prime(11) is True
    assert store.set_calls == writes_after_first_call
    assert store.get_calls >= 2
