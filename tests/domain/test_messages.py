from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import pytest

# Message immutability is required by docs/implementation/domain/Message Types.md.
from fund_load.domain.messages import Decision, LoadAttempt, RawLine
from fund_load.domain.money import Money


def test_rawline_is_immutable() -> None:
    # Frozen dataclasses enforce message immutability across steps.
    msg = RawLine(line_no=1, raw_text="{}")
    with pytest.raises(FrozenInstanceError):
        msg.line_no = 2  # type: ignore[misc]


def test_loadattempt_is_immutable() -> None:
    msg = LoadAttempt(
        line_no=1,
        id="1",
        customer_id="2",
        amount=Money(currency="USD", amount=Decimal("1.00")),
        ts=datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC),
        raw={},
    )
    with pytest.raises(FrozenInstanceError):
        msg.id = "3"  # type: ignore[misc]


def test_decision_defaults_are_stable() -> None:
    # Defaults keep later steps simple and deterministic.
    decision = Decision(line_no=1, id="1", customer_id="2", accepted=False)
    assert decision.reasons == ()
