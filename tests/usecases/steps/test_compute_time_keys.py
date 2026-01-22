from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

# ComputeTimeKeys behavior is specified in docs/implementation/steps/02 ComputeTimeKeys.md.
from fund_load.domain.messages import AttemptWithKeys, LoadAttempt
from fund_load.domain.money import Money
from fund_load.usecases.steps.compute_time_keys import ComputeTimeKeys


def _attempt(ts: datetime) -> LoadAttempt:
    # Helper builds a minimal LoadAttempt per Message Types doc for time-key derivation tests.
    return LoadAttempt(
        line_no=1,
        id="1",
        customer_id="2",
        amount=Money(currency="USD", amount=Decimal("1.00")),
        ts=ts,
        raw={},
    )


def test_time_keys_day_key_basic() -> None:
    # Day key uses UTC date per Time and Money Semantics + Step 02 spec.
    step = ComputeTimeKeys(week_start="MON")
    msg = _attempt(datetime(2000, 1, 1, 23, 59, 59, tzinfo=UTC))
    outputs = list(step(msg, ctx=None))
    assert len(outputs) == 1
    assert isinstance(outputs[0], AttemptWithKeys)
    assert outputs[0].day_key == date(2000, 1, 1)


def test_time_keys_week_key_monday_start() -> None:
    # Calendar week is anchored to configured week_start (default MON).
    step = ComputeTimeKeys(week_start="MON")
    msg = _attempt(datetime(2000, 1, 3, 12, 0, 0, tzinfo=UTC))  # Monday
    result = list(step(msg, ctx=None))[0]
    assert result.week_key.week_start_date == date(2000, 1, 3)
    assert result.week_key.week_start == "MON"


def test_time_keys_week_boundary_sunday() -> None:
    # Sunday belongs to the previous Monday-start week per Step 02 calendar rules.
    step = ComputeTimeKeys(week_start="MON")
    msg = _attempt(datetime(2000, 1, 2, 12, 0, 0, tzinfo=UTC))  # Sunday
    result = list(step(msg, ctx=None))[0]
    assert result.week_key.week_start_date == date(1999, 12, 27)
    assert result.week_key.week_start == "MON"


def test_time_keys_year_boundary() -> None:
    # Week key must remain stable across year boundaries (Step 02 edge case).
    step = ComputeTimeKeys(week_start="MON")
    msg = _attempt(datetime(2001, 1, 1, 0, 0, 0, tzinfo=UTC))  # Monday
    result = list(step(msg, ctx=None))[0]
    assert result.week_key.week_start_date == date(2001, 1, 1)


def test_time_keys_custom_week_start() -> None:
    # Custom week_start is supported by Step 02; verify Sunday-start bucketing.
    step = ComputeTimeKeys(week_start="SUN")
    msg = _attempt(datetime(2000, 1, 4, 12, 0, 0, tzinfo=UTC))  # Tuesday
    result = list(step(msg, ctx=None))[0]
    assert result.week_key.week_start_date == date(2000, 1, 2)
    assert result.week_key.week_start == "SUN"


def test_time_keys_invalid_week_start_raises() -> None:
    # Invalid week_start is rejected by ComputeTimeKeys (Step 02 input validation).
    step = ComputeTimeKeys(week_start="BAD")
    msg = _attempt(datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC))
    with pytest.raises(ValueError):
        list(step(msg, ctx=None))
