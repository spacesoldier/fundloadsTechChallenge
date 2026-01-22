from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

from fund_load.domain.messages import RawLine


# InputSource port defines how raw input lines enter the system (docs/implementation/ports/InputSource.md).
@runtime_checkable
class InputSource(Protocol):
    def read(self) -> Iterable[RawLine]:
        """Yield RawLine records in deterministic input order."""
        # Port contract has no implementation; calling it directly is a wiring error.
        raise NotImplementedError("InputSource is a port; use a concrete adapter.")
