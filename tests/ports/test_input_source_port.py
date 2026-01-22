from __future__ import annotations

import pytest

# InputSource port contract is documented in docs/implementation/ports/InputSource.md.
from fund_load.ports.input_source import InputSource


def test_input_source_port_default_raises() -> None:
    # Direct port calls without an adapter are wiring errors (port methods raise by default).
    class _PortOnly(InputSource):
        pass

    port = _PortOnly()  # type: ignore[misc]
    with pytest.raises(NotImplementedError):
        list(port.read())
