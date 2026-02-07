from __future__ import annotations

from typing import Any

from fund_load.adapters.services.prime_checker import SievePrimeChecker
from fund_load.ports.prime_checker import PrimeChecker
from stream_kernel.adapters.contracts import adapter


@adapter(
    name="prime_checker",
    kind="stub.prime_checker",
    consumes=[],
    emits=[],
    binds=[("kv", PrimeChecker)],
)
def prime_checker_stub(settings: dict[str, Any]) -> SievePrimeChecker:
    # Factory for stubbed prime checker (sieve).
    return SievePrimeChecker.from_max(int(settings.get("max_id", 0)))
