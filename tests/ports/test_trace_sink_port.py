from __future__ import annotations

import pytest

# TraceSink is a port-only interface (docs/implementation/kernel/Trace and Context Change Log Spec.md).
from fund_load.ports.trace_sink import TraceSink


class _PortOnly(TraceSink):
    # Port-only stub with no implementation.
    def emit(self, record: object) -> None:
        raise NotImplementedError("port-only stub")

    def flush(self) -> None:
        raise NotImplementedError("port-only stub")

    def close(self) -> None:
        raise NotImplementedError("port-only stub")


def test_trace_sink_port_raises_on_direct_use() -> None:
    # Port methods must not be used directly; adapters implement the interface.
    port = _PortOnly()  # type: ignore[misc,abstract]
    with pytest.raises(NotImplementedError):
        port.emit(object())
    with pytest.raises(NotImplementedError):
        port.flush()
    with pytest.raises(NotImplementedError):
        port.close()
