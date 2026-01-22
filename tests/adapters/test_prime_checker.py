from __future__ import annotations

# Prime checker semantics and suggested cases are documented in
# docs/implementation/ports/PrimeChecker.md.
from fund_load.adapters.prime_checker import SievePrimeChecker


def test_prime_checker_basic_values() -> None:
    # Basic primality rules (n <= 1 not prime; 2 and 3 prime; even > 2 not prime).
    checker = SievePrimeChecker.from_max(100)
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
    checker = SievePrimeChecker.from_range(1, 30)
    for n in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29):
        assert checker.is_prime(n) is True
    for n in (4, 6, 8, 9, 10, 12, 14, 15, 16, 18, 20, 21, 22, 24, 25, 26, 27, 28, 30):
        assert checker.is_prime(n) is False


def test_prime_checker_outside_range_fallback() -> None:
    # If queried outside the precomputed range, we fall back to deterministic trial division.
    checker = SievePrimeChecker.from_max(30)
    assert checker.is_prime(97) is True
    assert checker.is_prime(99) is False
