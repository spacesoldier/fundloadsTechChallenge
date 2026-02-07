from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

# WindowStore port contract is documented in docs/implementation/ports/WindowStore.md.
from fund_load.adapters.state.window_store import InMemoryWindowStore
from fund_load.domain.money import Money
from fund_load.ports.window_store import WindowReadPort, WindowWritePort


def test_window_store_port_conformance() -> None:
    # Adapter should conform to both read and write port protocols for wiring safety.
    store = InMemoryWindowStore()
    assert isinstance(store, WindowReadPort)
    assert isinstance(store, WindowWritePort)


def test_window_store_port_default_raises() -> None:
    # Direct port calls without adapter wiring should raise (same pattern as PrimeChecker port).
    class _PortOnly(WindowReadPort, WindowWritePort):
        pass

    port = _PortOnly()  # type: ignore[misc]
    with pytest.raises(NotImplementedError):
        port.read_snapshot(customer_id="1", day_key=date(2000, 1, 1), week_key=date(1999, 12, 27))
    with pytest.raises(NotImplementedError):
        port.inc_daily_attempts(customer_id="1", day_key=date(2000, 1, 1))
    with pytest.raises(NotImplementedError):
        port.add_daily_accepted_amount(
            customer_id="1",
            day_key=date(2000, 1, 1),
            amount=Money("USD", Decimal("1.00")),
        )
    with pytest.raises(NotImplementedError):
        port.add_weekly_accepted_amount(
            customer_id="1",
            week_key=date(1999, 12, 27),
            amount=Money("USD", Decimal("1.00")),
        )
    with pytest.raises(NotImplementedError):
        port.inc_daily_prime_gate(day_key=date(2000, 1, 1))
