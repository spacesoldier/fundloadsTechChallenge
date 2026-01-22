from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

# Idempotency gate behavior is specified in docs/implementation/steps/03 IdempotencyGate.md.
from fund_load.domain.messages import IdemStatus, LoadAttempt
from fund_load.domain.money import Money
from fund_load.usecases.messages import AttemptWithKeys, WeekKey
from fund_load.usecases.steps.idempotency_gate import IdempotencyGate


def _attempt_with_keys(
    *,
    line_no: int,
    id_value: str,
    customer_id: str,
    amount: str,
    ts: datetime,
    day_key: date | None = None,
    week_start: str = "MON",
    week_start_date: date | None = None,
) -> AttemptWithKeys:
    # Helper builds AttemptWithKeys without relying on Step 02 (unit isolation).
    attempt = LoadAttempt(
        line_no=line_no,
        id=id_value,
        customer_id=customer_id,
        amount=Money(currency="USD", amount=Decimal(amount)),
        ts=ts,
        raw={},
    )
    if day_key is None:
        day_key = ts.date()
    if week_start_date is None:
        week_start_date = day_key
    week_key = WeekKey(week_start_date=week_start_date, week_start=week_start)
    return AttemptWithKeys(attempt=attempt, day_key=day_key, week_key=week_key)


def test_idempotency_first_is_canonical() -> None:
    # First seen id in stream order is canonical (IdempotencyGate spec).
    step = IdempotencyGate()
    msg = _attempt_with_keys(
        line_no=1,
        id_value="10",
        customer_id="20",
        amount="1.00",
        ts=datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    result = list(step(msg, ctx=None))[0]
    assert result.idem_status == IdemStatus.CANONICAL
    assert result.canonical_line_no == 1
    assert result.fingerprint


def test_idempotency_replay_detected() -> None:
    # Same id + same payload fingerprint => DUP_REPLAY (analysis doc).
    step = IdempotencyGate()
    first = _attempt_with_keys(
        line_no=1,
        id_value="10",
        customer_id="20",
        amount="1.00",
        ts=datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    second = _attempt_with_keys(
        line_no=2,
        id_value="10",
        customer_id="20",
        amount="1.00",
        ts=datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    list(step(first, ctx=None))
    result = list(step(second, ctx=None))[0]
    assert result.idem_status == IdemStatus.DUP_REPLAY
    assert result.canonical_line_no == 1


def test_idempotency_conflict_detected_amount_diff() -> None:
    # Same id + different amount => DUP_CONFLICT.
    step = IdempotencyGate()
    first = _attempt_with_keys(
        line_no=1,
        id_value="10",
        customer_id="20",
        amount="1.00",
        ts=datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    second = _attempt_with_keys(
        line_no=2,
        id_value="10",
        customer_id="20",
        amount="2.00",
        ts=datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    list(step(first, ctx=None))
    result = list(step(second, ctx=None))[0]
    assert result.idem_status == IdemStatus.DUP_CONFLICT
    assert result.canonical_line_no == 1


def test_idempotency_conflict_detected_customer_diff() -> None:
    # Same id + different customer_id => DUP_CONFLICT.
    step = IdempotencyGate()
    first = _attempt_with_keys(
        line_no=1,
        id_value="10",
        customer_id="20",
        amount="1.00",
        ts=datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    second = _attempt_with_keys(
        line_no=2,
        id_value="10",
        customer_id="21",
        amount="1.00",
        ts=datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    list(step(first, ctx=None))
    result = list(step(second, ctx=None))[0]
    assert result.idem_status == IdemStatus.DUP_CONFLICT
    assert result.canonical_line_no == 1


def test_idempotency_multiple_duplicates_mixed() -> None:
    # Replays always compare to canonical fingerprint (conflicts do not replace it).
    step = IdempotencyGate()
    canonical = _attempt_with_keys(
        line_no=1,
        id_value="10",
        customer_id="20",
        amount="1.00",
        ts=datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    replay = _attempt_with_keys(
        line_no=2,
        id_value="10",
        customer_id="20",
        amount="1.00",
        ts=datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    conflict = _attempt_with_keys(
        line_no=3,
        id_value="10",
        customer_id="20",
        amount="2.00",
        ts=datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    replay_after_conflict = _attempt_with_keys(
        line_no=4,
        id_value="10",
        customer_id="20",
        amount="1.00",
        ts=datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    list(step(canonical, ctx=None))
    assert list(step(replay, ctx=None))[0].idem_status == IdemStatus.DUP_REPLAY
    assert list(step(conflict, ctx=None))[0].idem_status == IdemStatus.DUP_CONFLICT
    assert list(step(replay_after_conflict, ctx=None))[0].idem_status == IdemStatus.DUP_REPLAY


def test_idempotency_fingerprint_independent_of_time_keys() -> None:
    # Fingerprint excludes derived day/week keys per analysis doc and updated Step 03 spec.
    step = IdempotencyGate()
    ts = datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC)
    first = _attempt_with_keys(
        line_no=1,
        id_value="10",
        customer_id="20",
        amount="1.00",
        ts=ts,
        day_key=date(2000, 1, 1),
        week_start="MON",
        week_start_date=date(1999, 12, 27),
    )
    second = _attempt_with_keys(
        line_no=2,
        id_value="10",
        customer_id="20",
        amount="1.00",
        ts=ts,
        day_key=date(2000, 1, 2),
        week_start="SUN",
        week_start_date=date(2000, 1, 2),
    )
    list(step(first, ctx=None))
    result = list(step(second, ctx=None))[0]
    assert result.idem_status == IdemStatus.DUP_REPLAY
