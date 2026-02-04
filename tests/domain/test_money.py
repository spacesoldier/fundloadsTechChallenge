from __future__ import annotations

from decimal import Decimal

import pytest

# Money parsing rules follow docs/implementation/domain/Time and Money Semantics.md and Step 01 spec.
from fund_load.domain.money import Money, MoneyParseError, parse_money
from decimal import InvalidOperation
from fund_load.domain.reasons import ReasonCode


def test_parse_money_accepts_expected_formats() -> None:
    # Accepted formats normalize to the same Decimal amount.
    for raw in ("$1234.00", "USD1234.00", "USD$1234.00", " USD $1234.00 "):
        money = parse_money(raw)
        assert money.currency == "USD"
        assert money.amount == Decimal("1234.00")


def test_parse_money_rejects_non_string() -> None:
    # Parse is strict on input type to keep normalization deterministic.
    with pytest.raises(MoneyParseError) as exc:
        parse_money(1234)  # type: ignore[arg-type]
    assert exc.value.reason == ReasonCode.INVALID_AMOUNT_FORMAT


def test_parse_money_rejects_wrong_decimal_scale() -> None:
    # The challenge dataset uses 2 decimals; other scales are rejected.
    with pytest.raises(MoneyParseError) as exc:
        parse_money("$12.3")
    assert exc.value.reason == ReasonCode.INVALID_AMOUNT_FORMAT


def test_parse_money_rejects_negative_amount() -> None:
    # Negative amounts are out of domain for this challenge.
    with pytest.raises(ValueError):
        parse_money("$-1.00")

def test_money_rejects_negative_amount_on_init() -> None:
    # Money.__post_init__ guards invariants beyond parse_money (Time/Money semantics doc).
    with pytest.raises(ValueError):
        Money(currency="USD", amount=Decimal("-1.00"))


def test_parse_money_reports_invalid_decimal(monkeypatch: pytest.MonkeyPatch) -> None:
    # Defensive path: Decimal conversion failures surface as MoneyParseError.
    def _boom(_: str) -> Decimal:
        raise InvalidOperation("boom")

    monkeypatch.setattr("fund_load.domain.money.Decimal", _boom)

    with pytest.raises(MoneyParseError):
        parse_money("1.00")
