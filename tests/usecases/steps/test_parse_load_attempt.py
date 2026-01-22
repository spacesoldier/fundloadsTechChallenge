from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fund_load.domain.messages import Decision, LoadAttempt, RawLine
from fund_load.domain.reasons import ReasonCode
from fund_load.usecases.steps.parse_load_attempt import ParseLoadAttempt


# Helper keeps tests aligned with Step 01 contract (docs/implementation/steps/01 ParseLoadAttempt.md).
def _parse_one(raw_text: str, line_no: int = 1) -> LoadAttempt | Decision:
    step = ParseLoadAttempt()
    outputs = list(step(RawLine(line_no=line_no, raw_text=raw_text), ctx=None))
    assert len(outputs) == 1
    return outputs[0]


def test_parse_success_minimal() -> None:
    raw = (
        '{"id":"15887","customer_id":"528","load_amount":"$3318.47",'
        '"time":"2000-01-01T00:00:00Z"}'
    )
    result = _parse_one(raw)
    assert isinstance(result, LoadAttempt)
    assert result.id == "15887"
    assert result.customer_id == "528"
    assert result.amount.currency == "USD"
    assert result.amount.amount == Decimal("3318.47")
    assert result.ts == datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC)


def test_parse_amount_formats_equivalent() -> None:
    formats = ["$1234.00", "USD1234.00", "USD$1234.00", " USD $1234.00 "]
    for value in formats:
        raw = (
            '{"id":"1","customer_id":"2","load_amount":"'
            + value
            + '","time":"2000-01-01T00:00:00Z"}'
        )
        result = _parse_one(raw)
        assert isinstance(result, LoadAttempt)
        assert result.amount.amount == Decimal("1234.00")


def test_parse_invalid_amount_declined() -> None:
    raw = (
        '{"id":"1","customer_id":"2","load_amount":"$12.3",'
        '"time":"2000-01-01T00:00:00Z"}'
    )
    result = _parse_one(raw)
    assert isinstance(result, Decision)
    assert result.accepted is False
    assert result.reasons == (ReasonCode.INVALID_AMOUNT_FORMAT.value,)


def test_parse_invalid_json_declined() -> None:
    raw = "{not-json"
    result = _parse_one(raw)
    assert isinstance(result, Decision)
    assert result.accepted is False
    assert result.reasons == (ReasonCode.INPUT_PARSE_ERROR.value,)
    assert result.id == ""
    assert result.customer_id == ""
