from __future__ import annotations

# PrimeChecker port contract is documented in docs/implementation/ports/PrimeChecker.md.
import pytest

from fund_load.adapters.prime_checker import SievePrimeChecker
from fund_load.ports.prime_checker import PrimeChecker


def test_prime_checker_port_conformance() -> None:
    # Adapter should conform to the PrimeChecker port at runtime for wiring safety.
    checker = SievePrimeChecker.from_max(10)
    assert isinstance(checker, PrimeChecker)


def test_prime_checker_port_returns_bool() -> None:
    # Port contract expects a boolean result for any integer input.
    checker = SievePrimeChecker.from_max(10)
    for value in (-5, 0, 1, 2, 9, 11):
        assert isinstance(checker.is_prime(value), bool)


def test_prime_checker_port_default_raises() -> None:
    # Direct port calls are a wiring error; the default implementation raises.
    class _PortOnly(PrimeChecker):
        pass

    with pytest.raises(NotImplementedError):
        _PortOnly().is_prime(2)
