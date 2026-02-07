from __future__ import annotations

import json

# FormatOutput behavior is specified in docs/implementation/steps/07 FormatOutput.md.
from fund_load.usecases.messages import WindowedDecision
from fund_load.usecases.steps.format_output import FormatOutput


def test_format_output_schema_and_types() -> None:
    # Output JSON must include id, customer_id (strings), accepted (bool).
    step = FormatOutput()
    decision = WindowedDecision(
        line_no=1,
        id="1",
        customer_id="2",
        accepted=True,
    )
    out = list(step(decision, ctx=None))[0]
    obj = json.loads(out.json_text)
    assert obj == {"id": "1", "customer_id": "2", "accepted": True}


def test_format_output_deterministic_key_order() -> None:
    # Serializer must emit keys in fixed order (id, customer_id, accepted).
    step = FormatOutput()
    decision = WindowedDecision(
        line_no=1,
        id="1",
        customer_id="2",
        accepted=False,
    )
    out = list(step(decision, ctx=None))[0]
    assert out.json_text == '{"id":"1","customer_id":"2","accepted":false}'


def test_format_output_no_extra_fields() -> None:
    # Output must not include internal fields like reasons.
    step = FormatOutput()
    decision = WindowedDecision(
        line_no=1,
        id="1",
        customer_id="2",
        accepted=False,
    )
    out = list(step(decision, ctx=None))[0]
    obj = json.loads(out.json_text)
    assert list(obj.keys()) == ["id", "customer_id", "accepted"]
