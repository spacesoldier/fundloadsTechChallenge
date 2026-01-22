from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

# FormatOutput behavior is specified in docs/implementation/steps/07 FormatOutput.md.
from fund_load.domain.money import Money
from fund_load.usecases.messages import Decision
from fund_load.usecases.steps.format_output import FormatOutput


def test_format_output_schema_and_types() -> None:
    # Output JSON must include id, customer_id (strings), accepted (bool).
    step = FormatOutput()
    decision = Decision(
        line_no=1,
        id="1",
        customer_id="2",
        accepted=True,
        reasons=(),
        day_key=date(2000, 1, 1),
        week_key=date(1999, 12, 27),
        effective_amount=Money("USD", Decimal("1.00")),
        idem_status=None,  # not used by formatter
        is_prime_id=False,
        is_canonical=True,
    )
    out = list(step(decision, ctx=None))[0]
    obj = json.loads(out.json_text)
    assert obj == {"id": "1", "customer_id": "2", "accepted": True}


def test_format_output_deterministic_key_order() -> None:
    # Serializer must emit keys in fixed order (id, customer_id, accepted).
    step = FormatOutput()
    decision = Decision(
        line_no=1,
        id="1",
        customer_id="2",
        accepted=False,
        reasons=(),
        day_key=date(2000, 1, 1),
        week_key=date(1999, 12, 27),
        effective_amount=Money("USD", Decimal("1.00")),
        idem_status=None,
        is_prime_id=False,
        is_canonical=True,
    )
    out = list(step(decision, ctx=None))[0]
    assert out.json_text == '{"id":"1","customer_id":"2","accepted":false}'


def test_format_output_no_extra_fields() -> None:
    # Output must not include internal fields like reasons.
    step = FormatOutput()
    decision = Decision(
        line_no=1,
        id="1",
        customer_id="2",
        accepted=False,
        reasons=("X",),
        day_key=date(2000, 1, 1),
        week_key=date(1999, 12, 27),
        effective_amount=Money("USD", Decimal("1.00")),
        idem_status=None,
        is_prime_id=False,
        is_canonical=True,
    )
    out = list(step(decision, ctx=None))[0]
    obj = json.loads(out.json_text)
    assert list(obj.keys()) == ["id", "customer_id", "accepted"]
