from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

# EvaluatePolicies behavior is specified in docs/implementation/steps/05 EvaluatePolicies.md.
from fund_load.services.window_store import InMemoryWindowStore
from fund_load.domain.messages import IdemStatus, LoadAttempt
from fund_load.domain.money import Money
from fund_load.domain.reasons import ReasonCode
from fund_load.usecases.messages import (
    AttemptWithKeys,
    Decision,
    EnrichedAttempt,
    Features,
    IdempotencyClassifiedAttempt,
    WeekKey,
)
from fund_load.usecases.steps.evaluate_policies import EvaluatePolicies
from stream_kernel.integration.kv_store import InMemoryKvStore


def _enriched_attempt(
    *,
    id_value: str = "10",
    customer_id: str = "20",
    effective_amount: Decimal = Decimal("10.00"),
    ts: datetime | None = None,
    idem_status: IdemStatus = IdemStatus.CANONICAL,
    is_prime: bool = False,
) -> EnrichedAttempt:
    # Helper builds EnrichedAttempt without invoking other steps.
    if ts is None:
        ts = datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC)
    attempt = LoadAttempt(
        line_no=1,
        id=id_value,
        customer_id=customer_id,
        amount=Money(currency="USD", amount=Decimal("10.00")),
        ts=ts,
        raw={},
    )
    day_key = ts.date()
    week_key = WeekKey(week_start_date=day_key, week_start="MON")
    with_keys = AttemptWithKeys(attempt=attempt, day_key=day_key, week_key=week_key)
    classified = IdempotencyClassifiedAttempt(
        base=with_keys,
        idem_status=idem_status,
        fingerprint="fp",
        canonical_line_no=1,
    )
    features = Features(
        risk_factor=Decimal("1"),
        effective_amount=Money(currency="USD", amount=effective_amount),
        is_prime_id=is_prime,
    )
    return EnrichedAttempt(base=classified, features=features)


def _policy_step(store: InMemoryWindowStore, *, prime_enabled: bool = False) -> EvaluatePolicies:
    # Helper builds EvaluatePolicies with baseline limits and optional prime gate.
    # Prime config comes from nodes.evaluate_policies.prime_gate in newgen config.
    return EvaluatePolicies(
        window_store=store,
        daily_attempt_limit=3,
        daily_amount_limit=Decimal("5000.00"),
        weekly_amount_limit=Decimal("20000.00"),
        prime_enabled=prime_enabled,
        prime_amount_cap=Decimal("9999.00"),
        prime_global_per_day=1,
    )


def test_policy_baseline_approved_happy_path() -> None:
    # Canonical attempt within limits should be approved.
    store = InMemoryWindowStore(store=InMemoryKvStore())
    step = _policy_step(store)
    msg = _enriched_attempt()
    decision = list(step(msg, ctx=None))[0]
    assert isinstance(decision, Decision)
    assert decision.accepted is True
    assert decision.reasons == ()
    assert decision.is_canonical is True


def test_policy_decline_daily_attempt_limit() -> None:
    # Attempt limit is enforced first (attempt_no = before + 1).
    store = InMemoryWindowStore(store=InMemoryKvStore())
    for _ in range(3):
        store.inc_daily_attempts(customer_id="20", day_key=date(2000, 1, 1))
    step = _policy_step(store)
    msg = _enriched_attempt()
    decision = list(step(msg, ctx=None))[0]
    assert isinstance(decision, Decision)
    assert decision.accepted is False
    assert decision.reasons == (ReasonCode.DAILY_ATTEMPT_LIMIT.value,)


def test_policy_decline_daily_amount_limit() -> None:
    # Daily accepted amount limit uses effective_amount (Step 05 spec).
    store = InMemoryWindowStore(store=InMemoryKvStore())
    store.add_daily_accepted_amount(
        customer_id="20",
        day_key=date(2000, 1, 1),
        amount=Money("USD", Decimal("5000.00")),
    )
    step = _policy_step(store)
    msg = _enriched_attempt(effective_amount=Decimal("0.01"))
    decision = list(step(msg, ctx=None))[0]
    assert isinstance(decision, Decision)
    assert decision.accepted is False
    assert decision.reasons == (ReasonCode.DAILY_AMOUNT_LIMIT.value,)


def test_policy_decline_weekly_amount_limit() -> None:
    # Weekly accepted amount limit uses effective_amount (Step 05 spec).
    store = InMemoryWindowStore(store=InMemoryKvStore())
    store.add_weekly_accepted_amount(
        customer_id="20",
        week_key=date(2000, 1, 1),
        amount=Money("USD", Decimal("20000.00")),
    )
    step = _policy_step(store)
    msg = _enriched_attempt(effective_amount=Decimal("0.01"))
    decision = list(step(msg, ctx=None))[0]
    assert isinstance(decision, Decision)
    assert decision.accepted is False
    assert decision.reasons == (ReasonCode.WEEKLY_AMOUNT_LIMIT.value,)


def test_policy_decline_duplicate_replay() -> None:
    # Conflict note: Input data analysis suggests replays reuse canonical decision,
    # but Reference output generation seeds replays as declined; we follow the reference.
    store = InMemoryWindowStore(store=InMemoryKvStore())
    step = _policy_step(store)
    msg = _enriched_attempt(idem_status=IdemStatus.DUP_REPLAY)
    decision = list(step(msg, ctx=None))[0]
    assert isinstance(decision, Decision)
    assert decision.accepted is False
    assert decision.reasons == (ReasonCode.ID_DUPLICATE_REPLAY.value,)
    assert decision.is_canonical is False


def test_policy_decline_duplicate_conflict() -> None:
    # Conflicts are always declined and do not affect windows (Step 05 spec).
    store = InMemoryWindowStore(store=InMemoryKvStore())
    step = _policy_step(store)
    msg = _enriched_attempt(idem_status=IdemStatus.DUP_CONFLICT)
    decision = list(step(msg, ctx=None))[0]
    assert isinstance(decision, Decision)
    assert decision.accepted is False
    assert decision.reasons == (ReasonCode.ID_DUPLICATE_CONFLICT.value,)
    assert decision.is_canonical is False


def test_policy_prime_gate_amount_cap() -> None:
    # Prime amount cap is enforced before global quota (Step 05 order).
    store = InMemoryWindowStore(store=InMemoryKvStore())
    step = _policy_step(store, prime_enabled=True)
    msg = _enriched_attempt(effective_amount=Decimal("10000.00"), is_prime=True)
    decision = list(step(msg, ctx=None))[0]
    assert isinstance(decision, Decision)
    assert decision.accepted is False
    assert decision.reasons == (ReasonCode.PRIME_AMOUNT_CAP.value,)


def test_policy_prime_gate_global_quota() -> None:
    # Prime global quota declines when already used.
    store = InMemoryWindowStore(store=InMemoryKvStore())
    store.inc_daily_prime_gate(day_key=date(2000, 1, 1))
    step = _policy_step(store, prime_enabled=True)
    msg = _enriched_attempt(is_prime=True)
    decision = list(step(msg, ctx=None))[0]
    assert isinstance(decision, Decision)
    assert decision.accepted is False
    assert decision.reasons == (ReasonCode.PRIME_DAILY_GLOBAL_LIMIT.value,)


def test_policy_order_attempt_limit_before_prime_gate() -> None:
    # Attempt limit should take precedence even if prime rules would also fail.
    store = InMemoryWindowStore(store=InMemoryKvStore())
    for _ in range(3):
        store.inc_daily_attempts(customer_id="20", day_key=date(2000, 1, 1))
    step = _policy_step(store, prime_enabled=True)
    msg = _enriched_attempt(is_prime=True, effective_amount=Decimal("10000.00"))
    decision = list(step(msg, ctx=None))[0]
    assert isinstance(decision, Decision)
    assert decision.accepted is False
    assert decision.reasons == (ReasonCode.DAILY_ATTEMPT_LIMIT.value,)
