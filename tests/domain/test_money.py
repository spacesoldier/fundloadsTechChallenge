from __future__ import annotations

from decimal import Decimal

import pytest

# Money parsing rules follow docs/implementation/domain/Time and Money Semantics.md and Step 01 spec.
from fund_load.domain.money import MoneyParseError, parse_money
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


# NOTE: Coverage for src/fund_load/domain/money.py is intentionally <100% right now.
# - Money.__post_init__ has a negative-amount guard that is *not* reachable via parse_money,
#   because parse_money rejects negatives before constructing Money.
# - The Decimal(…) InvalidOperation branch is also effectively unreachable because the
#   regex enforces a strict numeric format (^\d+\.\d{2}$) before Decimal parsing.
# This is by design per Step 01 + Time/Money semantics; we keep the guards to prevent
# misuse when Money is constructed directly, even though those lines are not executed here.
