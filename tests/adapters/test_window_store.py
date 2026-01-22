from __future__ import annotations

from datetime import date
from decimal import Decimal

# WindowStore behavior is specified in docs/implementation/ports/WindowStore.md.
from fund_load.adapters.window_store import InMemoryWindowStore
from fund_load.domain.money import Money


def test_read_snapshot_defaults_to_zero() -> None:
    # Missing entries must default to zero per WindowStore spec.
    store = InMemoryWindowStore()
    snapshot = store.read_snapshot(customer_id="1", day_key=date(2000, 1, 1), week_key=date(1999, 12, 27))
    assert snapshot.day_attempts_before == 0
    assert snapshot.day_accepted_amount_before.amount == Decimal("0.00")
    assert snapshot.week_accepted_amount_before.amount == Decimal("0.00")
    assert snapshot.prime_approved_count_before == 0


def test_inc_daily_attempts_accumulates() -> None:
    # Daily attempts increment accumulates per (customer_id, day_key).
    store = InMemoryWindowStore()
    key_day = date(2000, 1, 1)
    store.inc_daily_attempts(customer_id="1", day_key=key_day)
    store.inc_daily_attempts(customer_id="1", day_key=key_day)
    snapshot = store.read_snapshot(customer_id="1", day_key=key_day, week_key=key_day)
    assert snapshot.day_attempts_before == 2


def test_add_daily_amount_accumulates_cents() -> None:
    # Daily accepted amount is accumulated in cents and exposed as Money.
    store = InMemoryWindowStore()
    key_day = date(2000, 1, 1)
    store.add_daily_accepted_amount(customer_id="1", day_key=key_day, amount=Money("USD", Decimal("1.50")))
    store.add_daily_accepted_amount(customer_id="1", day_key=key_day, amount=Money("USD", Decimal("2.25")))
    snapshot = store.read_snapshot(customer_id="1", day_key=key_day, week_key=key_day)
    assert snapshot.day_accepted_amount_before.amount == Decimal("3.75")


def test_weekly_and_daily_are_independent() -> None:
    # Weekly and daily windows are keyed separately and do not interfere.
    store = InMemoryWindowStore()
    day_key = date(2000, 1, 1)
    week_key = date(1999, 12, 27)
    store.add_daily_accepted_amount(customer_id="1", day_key=day_key, amount=Money("USD", Decimal("1.00")))
    store.add_weekly_accepted_amount(customer_id="1", week_key=week_key, amount=Money("USD", Decimal("2.00")))
    snapshot = store.read_snapshot(customer_id="1", day_key=day_key, week_key=week_key)
    assert snapshot.day_accepted_amount_before.amount == Decimal("1.00")
    assert snapshot.week_accepted_amount_before.amount == Decimal("2.00")


def test_prime_gate_counter_defaults_and_increments() -> None:
    # Prime gate counter is global per day (not per customer).
    store = InMemoryWindowStore()
    key_day = date(2000, 1, 1)
    store.inc_daily_prime_gate(day_key=key_day)
    store.inc_daily_prime_gate(day_key=key_day)
    snapshot = store.read_snapshot(customer_id="any", day_key=key_day, week_key=key_day)
    assert snapshot.prime_approved_count_before == 2
