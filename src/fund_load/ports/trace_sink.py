from __future__ import annotations

from typing import Protocol, runtime_checkable

from fund_load.kernel.trace import TraceRecord


# TraceSink is a port-like interface for trace adapters (Trace and Context Change Log spec §6).
@runtime_checkable
class TraceSink(Protocol):
    def emit(self, record: TraceRecord) -> None:
        """Consume one TraceRecord."""
        raise NotImplementedError("TraceSink is a port; use a concrete adapter.")

    def flush(self) -> None:
        """Flush buffered trace output if supported."""
        raise NotImplementedError("TraceSink is a port; use a concrete adapter.")

    def close(self) -> None:
        """Close the sink and release resources."""
        raise NotImplementedError("TraceSink is a port; use a concrete adapter.")
