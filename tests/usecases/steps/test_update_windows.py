from __future__ import annotations

from datetime import date
from decimal import Decimal

# UpdateWindows behavior is specified in docs/implementation/steps/06 UpdateWindows.md.
from fund_load.adapters.state.window_store import InMemoryWindowStore
from fund_load.domain.messages import IdemStatus
from fund_load.domain.money import Money
from fund_load.usecases.messages import Decision, WindowedDecision
from fund_load.usecases.steps.update_windows import UpdateWindows


def _decision(
    *,
    accepted: bool,
    is_canonical: bool,
    is_prime: bool = False,
    day_key: date = date(2000, 1, 1),
    week_key: date = date(1999, 12, 27),
    amount: Decimal = Decimal("10.00"),
    idem_status: IdemStatus = IdemStatus.CANONICAL,
) -> Decision:
    # Helper builds a Decision without invoking EvaluatePolicies (unit isolation).
    return Decision(
        line_no=1,
        id="1",
        customer_id="2",
        accepted=accepted,
        reasons=(),
        day_key=day_key,
        week_key=week_key,
        effective_amount=Money("USD", amount),
        idem_status=idem_status,
        is_prime_id=is_prime,
        is_canonical=is_canonical,
    )


def test_windows_update_canonical_approved_updates_all() -> None:
    # Canonical + approved updates attempts and accepted sums (Step 06).
    store = InMemoryWindowStore()
    step = UpdateWindows(window_store=store, prime_gate_enabled=True)
    decision = _decision(accepted=True, is_canonical=True)
    out = list(step(decision, ctx=None))[0]
    snapshot = store.read_snapshot(customer_id="2", day_key=decision.day_key, week_key=decision.week_key)
    assert isinstance(out, WindowedDecision)
    assert out.id == decision.id
    assert out.customer_id == decision.customer_id
    assert out.accepted is decision.accepted
    assert snapshot.day_attempts_before == 1
    assert snapshot.day_accepted_amount_before.amount == Decimal("10.00")
    assert snapshot.week_accepted_amount_before.amount == Decimal("10.00")


def test_windows_update_canonical_declined_updates_attempts_only() -> None:
    # Canonical + declined still increments attempts, but not accepted sums.
    store = InMemoryWindowStore()
    step = UpdateWindows(window_store=store, prime_gate_enabled=True)
    decision = _decision(accepted=False, is_canonical=True)
    list(step(decision, ctx=None))
    snapshot = store.read_snapshot(customer_id="2", day_key=decision.day_key, week_key=decision.week_key)
    assert snapshot.day_attempts_before == 1
    assert snapshot.day_accepted_amount_before.amount == Decimal("0.00")
    assert snapshot.week_accepted_amount_before.amount == Decimal("0.00")


def test_windows_update_noncanonical_no_updates() -> None:
    # Non-canonical decisions must not mutate state (Step 06 invariant).
    store = InMemoryWindowStore()
    step = UpdateWindows(window_store=store, prime_gate_enabled=True)
    decision = _decision(accepted=False, is_canonical=False, idem_status=IdemStatus.DUP_REPLAY)
    list(step(decision, ctx=None))
    snapshot = store.read_snapshot(customer_id="2", day_key=decision.day_key, week_key=decision.week_key)
    assert snapshot.day_attempts_before == 0
    assert snapshot.day_accepted_amount_before.amount == Decimal("0.00")
    assert snapshot.week_accepted_amount_before.amount == Decimal("0.00")


def test_windows_update_prime_gate_only_for_prime_approved() -> None:
    # Prime gate counter increments only for approved prime canonicals.
    store = InMemoryWindowStore()
    step = UpdateWindows(window_store=store, prime_gate_enabled=True)
    decision = _decision(accepted=True, is_canonical=True, is_prime=True)
    list(step(decision, ctx=None))
    snapshot = store.read_snapshot(customer_id="2", day_key=decision.day_key, week_key=decision.week_key)
    assert snapshot.prime_approved_count_before == 1


def test_windows_update_prime_gate_disabled_no_update() -> None:
    # If prime gate is disabled in config, counter should remain unchanged.
    store = InMemoryWindowStore()
    step = UpdateWindows(window_store=store, prime_gate_enabled=False)
    decision = _decision(accepted=True, is_canonical=True, is_prime=True)
    list(step(decision, ctx=None))
    snapshot = store.read_snapshot(customer_id="2", day_key=decision.day_key, week_key=decision.week_key)
    assert snapshot.prime_approved_count_before == 0
