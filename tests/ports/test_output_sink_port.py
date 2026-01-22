from __future__ import annotations

import pytest

# OutputSink port contract is documented in docs/implementation/ports/OutputSink.md.
from fund_load.ports.output_sink import OutputSink


def test_output_sink_port_default_raises() -> None:
    # Direct port calls without an adapter are wiring errors (port methods raise by default).
    class _PortOnly(OutputSink):
        pass

    port = _PortOnly()  # type: ignore[misc,abstract]
    with pytest.raises(NotImplementedError):
        port.write_line('{"id":"1"}')
    with pytest.raises(NotImplementedError):
        port.close()
