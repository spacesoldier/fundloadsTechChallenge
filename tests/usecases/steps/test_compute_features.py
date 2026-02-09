from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

# ComputeFeatures behavior is specified in docs/implementation/steps/04 ComputeFeatures.md.
import pytest

from fund_load.domain.messages import IdemStatus, LoadAttempt
from fund_load.domain.money import Money
from fund_load.usecases.messages import AttemptWithKeys, IdempotencyClassifiedAttempt, WeekKey
from fund_load.usecases.steps.compute_features import ComputeFeatures


class _FakePrimeChecker:
    # Minimal fake for the PrimeChecker service contract.
    def __init__(self, primes: set[int]) -> None:
        self._primes = primes

    def is_prime(self, id_num: int) -> bool:
        return id_num in self._primes


def _classified_attempt(ts: datetime, *, id_value: str = "11") -> IdempotencyClassifiedAttempt:
    # Helper builds an IdempotencyClassifiedAttempt without invoking other steps.
    attempt = LoadAttempt(
        line_no=1,
        id=id_value,
        customer_id="20",
        amount=Money(currency="USD", amount=Decimal("10.00")),
        ts=ts,
        raw={},
    )
    day_key = ts.date()
    week_key = WeekKey(week_start_date=day_key, week_start="MON")
    with_keys = AttemptWithKeys(attempt=attempt, day_key=day_key, week_key=week_key)
    return IdempotencyClassifiedAttempt(
        base=with_keys,
        idem_status=IdemStatus.CANONICAL,
        fingerprint="fp",
        canonical_line_no=1,
    )


def test_features_baseline_no_multiplier_no_prime() -> None:
    # Baseline: risk_factor=1, effective_amount=amount, prime flag disabled.
    step = ComputeFeatures(
        monday_multiplier_enabled=False,
        monday_multiplier=Decimal("2.0"),
        apply_to="amount",
        prime_checker=_FakePrimeChecker({11}),
        prime_enabled=False,
    )
    msg = _classified_attempt(datetime(2000, 1, 4, 12, 0, 0, tzinfo=UTC))  # Tuesday
    result = list(step(msg, ctx=None))[0]
    assert result.features.risk_factor == Decimal("1")
    assert result.features.effective_amount.amount == Decimal("10.00")
    assert result.features.is_prime_id is False


def test_features_monday_multiplier_applied() -> None:
    # Monday multiplier applies to amount when enabled (Step 04 default).
    step = ComputeFeatures(
        monday_multiplier_enabled=True,
        monday_multiplier=Decimal("2.0"),
        apply_to="amount",
        prime_checker=_FakePrimeChecker(set()),
        prime_enabled=False,
    )
    msg = _classified_attempt(datetime(2000, 1, 3, 12, 0, 0, tzinfo=UTC))  # Monday
    result = list(step(msg, ctx=None))[0]
    assert result.features.risk_factor == Decimal("2.0")
    assert result.features.effective_amount.amount == Decimal("20.00")


def test_features_non_monday_multiplier_not_applied() -> None:
    # Non-Monday retains risk_factor=1 even when multiplier is enabled.
    step = ComputeFeatures(
        monday_multiplier_enabled=True,
        monday_multiplier=Decimal("2.0"),
        apply_to="amount",
        prime_checker=_FakePrimeChecker(set()),
        prime_enabled=False,
    )
    msg = _classified_attempt(datetime(2000, 1, 4, 12, 0, 0, tzinfo=UTC))  # Tuesday
    result = list(step(msg, ctx=None))[0]
    assert result.features.risk_factor == Decimal("1")
    assert result.features.effective_amount.amount == Decimal("10.00")


def test_features_prime_detection_enabled() -> None:
    # Prime flag follows PrimeChecker result when enabled.
    step = ComputeFeatures(
        monday_multiplier_enabled=False,
        monday_multiplier=Decimal("2.0"),
        apply_to="amount",
        prime_checker=_FakePrimeChecker({11}),
        prime_enabled=True,
    )
    msg = _classified_attempt(datetime(2000, 1, 4, 12, 0, 0, tzinfo=UTC), id_value="11")
    result = list(step(msg, ctx=None))[0]
    assert result.features.is_prime_id is True


def test_features_prime_detection_disabled() -> None:
    # Prime flag is forced false when feature is disabled, regardless of id.
    step = ComputeFeatures(
        monday_multiplier_enabled=False,
        monday_multiplier=Decimal("2.0"),
        apply_to="amount",
        prime_checker=_FakePrimeChecker({11}),
        prime_enabled=False,
    )
    msg = _classified_attempt(datetime(2000, 1, 4, 12, 0, 0, tzinfo=UTC), id_value="11")
    result = list(step(msg, ctx=None))[0]
    assert result.features.is_prime_id is False


def test_features_apply_to_limits_keeps_amount() -> None:
    # When apply_to="limits", effective_amount stays equal to raw amount (Step 04 option).
    step = ComputeFeatures(
        monday_multiplier_enabled=True,
        monday_multiplier=Decimal("2.0"),
        apply_to="limits",
        prime_checker=_FakePrimeChecker(set()),
        prime_enabled=False,
    )
    msg = _classified_attempt(datetime(2000, 1, 3, 12, 0, 0, tzinfo=UTC))  # Monday
    result = list(step(msg, ctx=None))[0]
    assert result.features.risk_factor == Decimal("2.0")
    assert result.features.effective_amount.amount == Decimal("10.00")


def test_features_invalid_apply_to_raises() -> None:
    # Invalid apply_to is rejected to avoid silent misconfiguration.
    step = ComputeFeatures(
        monday_multiplier_enabled=False,
        monday_multiplier=Decimal("2.0"),
        apply_to="bad",
        prime_checker=_FakePrimeChecker(set()),
        prime_enabled=False,
    )
    msg = _classified_attempt(datetime(2000, 1, 4, 12, 0, 0, tzinfo=UTC))
    with pytest.raises(ValueError):
        list(step(msg, ctx=None))
