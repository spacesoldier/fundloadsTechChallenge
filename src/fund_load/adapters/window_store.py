from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from fund_load.domain.money import Money
from fund_load.ports.window_store import WindowReadPort, WindowSnapshot, WindowWritePort


@dataclass
class InMemoryWindowStore(WindowReadPort, WindowWritePort):
    # In-memory adapter is the reference implementation for the challenge.
    # This is a natural boundary for external state (e.g., Redis) via the WindowStore ports.
    _daily_attempts: dict[tuple[str, date], int] = field(default_factory=dict)
    _daily_accepted_amount: dict[tuple[str, date], int] = field(default_factory=dict)
    _weekly_accepted_amount: dict[tuple[str, date], int] = field(default_factory=dict)
    _prime_daily_gate: dict[date, int] = field(default_factory=dict)

    def read_snapshot(self, *, customer_id: str, day_key: date, week_key: date) -> WindowSnapshot:
        # Read path would be a port call to external storage in a Redis-backed adapter.
        day_attempts = self._daily_attempts.get((customer_id, day_key), 0)
        day_amount_cents = self._daily_accepted_amount.get((customer_id, day_key), 0)
        week_amount_cents = self._weekly_accepted_amount.get((customer_id, week_key), 0)
        prime_used = self._prime_daily_gate.get(day_key, 0)
        return WindowSnapshot(
            day_attempts_before=day_attempts,
            day_accepted_amount_before=_money_from_cents(day_amount_cents),
            week_accepted_amount_before=_money_from_cents(week_amount_cents),
            prime_approved_count_before=prime_used,
        )

    def inc_daily_attempts(self, *, customer_id: str, day_key: date, delta: int = 1) -> None:
        # Write path would be a port call to external storage in a Redis-backed adapter.
        key = (customer_id, day_key)
        self._daily_attempts[key] = self._daily_attempts.get(key, 0) + delta

    def add_daily_accepted_amount(self, *, customer_id: str, day_key: date, amount: Money) -> None:
        # Write path would be a port call to external storage in a Redis-backed adapter.
        key = (customer_id, day_key)
        self._daily_accepted_amount[key] = self._daily_accepted_amount.get(key, 0) + _to_cents(amount)

    def add_weekly_accepted_amount(self, *, customer_id: str, week_key: date, amount: Money) -> None:
        # Write path would be a port call to external storage in a Redis-backed adapter.
        key = (customer_id, week_key)
        self._weekly_accepted_amount[key] = self._weekly_accepted_amount.get(key, 0) + _to_cents(amount)

    def inc_daily_prime_gate(self, *, day_key: date, delta: int = 1) -> None:
        # Write path would be a port call to external storage in a Redis-backed adapter.
        self._prime_daily_gate[day_key] = self._prime_daily_gate.get(day_key, 0) + delta


def _to_cents(amount: Money) -> int:
    # Internal storage uses integer cents per WindowStore.md recommendations.
    cents = (amount.amount.quantize(Decimal("0.01")) * 100).to_integral_value()
    return int(cents)


def _money_from_cents(cents: int) -> Money:
    # Convert back to Money for snapshot consumers.
    value = (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"))
    return Money(currency="USD", amount=value)
