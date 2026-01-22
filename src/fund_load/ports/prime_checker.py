from __future__ import annotations

from typing import Protocol, runtime_checkable


# PrimeChecker port defines the boundary for primality checks (docs/implementation/ports/PrimeChecker.md).
@runtime_checkable
class PrimeChecker(Protocol):
    def is_prime(self, id_num: int) -> bool:
        """Return True if id_num is prime under the configured strategy."""
        # Port contract has no implementation; calling it directly is a wiring error.
        raise NotImplementedError("PrimeChecker is a port; use a concrete adapter.")
