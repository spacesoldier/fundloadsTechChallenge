from __future__ import annotations

from typing import Protocol, runtime_checkable


# OutputSink port defines how formatted output leaves the system (docs/implementation/ports/OutputSink.md).
@runtime_checkable
class OutputSink(Protocol):
    def write_line(self, line: str) -> None:
        """Write a single output line (already formatted JSON)."""
        # Port contract has no implementation; calling it directly is a wiring error.
        raise NotImplementedError("OutputSink is a port; use a concrete adapter.")

    def close(self) -> None:
        """Finalize and release resources held by the sink."""
        # Port contract has no implementation; calling it directly is a wiring error.
        raise NotImplementedError("OutputSink is a port; use a concrete adapter.")
