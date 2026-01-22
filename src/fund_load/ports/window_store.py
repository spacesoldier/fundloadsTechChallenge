from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from fund_load.domain.money import Money


@dataclass(frozen=True, slots=True)
class WindowSnapshot:
    # Snapshot fields are defined in docs/implementation/ports/WindowStore.md.
    day_attempts_before: int
    day_accepted_amount_before: Money
    week_accepted_amount_before: Money
    prime_approved_count_before: int


# WindowStore ports isolate state access (docs/implementation/ports/WindowStore.md).
@runtime_checkable
class WindowReadPort(Protocol):
    def read_snapshot(self, *, customer_id: str, day_key: date, week_key: date) -> WindowSnapshot:
        """Return window snapshot for the given customer/day/week keys."""
        raise NotImplementedError("WindowReadPort is a port; use a concrete adapter.")


@runtime_checkable
class WindowWritePort(Protocol):
    def inc_daily_attempts(self, *, customer_id: str, day_key: date, delta: int = 1) -> None:
        """Increment daily attempts for customer/day."""
        raise NotImplementedError("WindowWritePort is a port; use a concrete adapter.")

    def add_daily_accepted_amount(self, *, customer_id: str, day_key: date, amount: Money) -> None:
        """Add accepted amount to daily bucket."""
        raise NotImplementedError("WindowWritePort is a port; use a concrete adapter.")

    def add_weekly_accepted_amount(self, *, customer_id: str, week_key: date, amount: Money) -> None:
        """Add accepted amount to weekly bucket."""
        raise NotImplementedError("WindowWritePort is a port; use a concrete adapter.")

    def inc_daily_prime_gate(self, *, day_key: date, delta: int = 1) -> None:
        """Increment global prime-approved counter for a day."""
        raise NotImplementedError("WindowWritePort is a port; use a concrete adapter.")
