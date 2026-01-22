from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .reasons import ReasonCode


class MoneyParseError(ValueError):
    # Parse errors carry a stable reason code for deterministic declines.
    def __init__(self, reason: ReasonCode) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Money:
    # Money is immutable and non-negative per docs/implementation/domain/Time and Money Semantics.md.
    currency: str
    amount: Decimal

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValueError("Money amount must be non-negative")


# Accepted format is strict 2-decimal numeric after stripping USD/$ prefixes and whitespace.
_AMOUNT_PATTERN = re.compile(r"^\d+\.\d{2}$")
_PREFIX_PATTERN = re.compile(r"^(?:USD)?\$?(?:USD)?")


def parse_money(raw: str, *, currency: str = "USD") -> Money:
    # Step 01 parsing rules accept USD/$ prefixes and require two decimals.
    if not isinstance(raw, str):
        raise MoneyParseError(ReasonCode.INVALID_AMOUNT_FORMAT)

    text = re.sub(r"\s+", "", raw)
    text = _PREFIX_PATTERN.sub("", text)

    # Negative values are out of scope for this challenge (explicit domain rule).
    if text.startswith("-"):
        raise ValueError("Negative amounts are not allowed")

    if not _AMOUNT_PATTERN.match(text):
        raise MoneyParseError(ReasonCode.INVALID_AMOUNT_FORMAT)

    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise MoneyParseError(ReasonCode.INVALID_AMOUNT_FORMAT) from exc

    # Quantize to cents for deterministic arithmetic (no binary floats).
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Money(currency=currency, amount=amount)
