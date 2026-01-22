from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .reasons import ReasonCode


class MoneyParseError(ValueError):
    def __init__(self, reason: ReasonCode) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Money:
    currency: str
    amount: Decimal

    def __post_init__(self) -> None:
        # Money is non-negative by domain rule (docs/implementation/domain/Time and Money Semantics.md).
        if self.amount < 0:
            raise ValueError("Money amount must be non-negative")


# Parser implements accepted formats from Step 01 spec (USD prefix and/or $), whitespace allowed.
_AMOUNT_PATTERN = re.compile(r"^\d+\.\d{2}$")
_PREFIX_PATTERN = re.compile(r"^(?:USD)?\$?(?:USD)?")


def parse_money(raw: str, *, currency: str = "USD") -> Money:
    if not isinstance(raw, str):
        raise MoneyParseError(ReasonCode.INVALID_AMOUNT_FORMAT)

    # Normalize whitespace and currency tokens exactly once; strict 2-decimal format is enforced.
    text = re.sub(r"\s+", "", raw)
    text = _PREFIX_PATTERN.sub("", text)

    if not _AMOUNT_PATTERN.match(text):
        raise MoneyParseError(ReasonCode.INVALID_AMOUNT_FORMAT)

    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise MoneyParseError(ReasonCode.INVALID_AMOUNT_FORMAT) from exc

    # Quantize to cents per money semantics; input with other scales is rejected above.
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Money(currency=currency, amount=amount)
