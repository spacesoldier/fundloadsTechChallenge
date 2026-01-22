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
    # Invalid JSON is declined per Step 01 failure policy.
    raw = "{not-json"
    result = _parse_one(raw)
    assert isinstance(result, Decision)
    assert result.accepted is False
    assert result.reasons == (ReasonCode.INPUT_PARSE_ERROR.value,)
    assert result.id == ""
    assert result.customer_id == ""


def test_parse_non_object_json_declined() -> None:
    # Non-dict JSON is rejected (Step 01 requires object with fields).
    raw = '["not", "an", "object"]'
    result = _parse_one(raw)
    assert isinstance(result, Decision)
    assert result.accepted is False
    assert result.reasons == (ReasonCode.INPUT_PARSE_ERROR.value,)


def test_parse_missing_field_declined() -> None:
    # Missing required fields triggers schema error (Step 01 required inputs).
    raw = '{"id":"1","customer_id":"2","time":"2000-01-01T00:00:00Z"}'
    result = _parse_one(raw)
    assert isinstance(result, Decision)
    assert result.accepted is False
    assert result.reasons == (ReasonCode.INPUT_PARSE_ERROR.value,)
    assert result.id == "1"
    assert result.customer_id == "2"


def test_parse_invalid_id_declined() -> None:
    # IDs must be digits-only after trimming (Step 01 normalization rule).
    raw = (
        '{"id":"A12","customer_id":"2","load_amount":"$1.00",'
        '"time":"2000-01-01T00:00:00Z"}'
    )
    result = _parse_one(raw)
    assert isinstance(result, Decision)
    assert result.accepted is False
    assert result.reasons == (ReasonCode.INVALID_ID_FORMAT.value,)
    assert result.id == "A12"
    assert result.customer_id == "2"


def test_parse_invalid_customer_id_declined() -> None:
    # Customer IDs follow the same digits-only rule as IDs (Step 01).
    raw = (
        '{"id":"1","customer_id":"B2","load_amount":"$1.00",'
        '"time":"2000-01-01T00:00:00Z"}'
    )
    result = _parse_one(raw)
    assert isinstance(result, Decision)
    assert result.accepted is False
    assert result.reasons == (ReasonCode.INVALID_ID_FORMAT.value,)
    assert result.id == "1"
    assert result.customer_id == "B2"


def test_parse_invalid_timestamp_declined() -> None:
    # Timestamp must be ISO8601 with timezone; missing TZ is invalid.
    raw = (
        '{"id":"1","customer_id":"2","load_amount":"$1.00",'
        '"time":"2000-01-01T00:00:00"}'
    )
    result = _parse_one(raw)
    assert isinstance(result, Decision)
    assert result.accepted is False
    assert result.reasons == (ReasonCode.INVALID_TIMESTAMP.value,)


def test_parse_invalid_timestamp_format_declined() -> None:
    # Malformed timestamp is declined deterministically.
    raw = (
        '{"id":"1","customer_id":"2","load_amount":"$1.00",'
        '"time":"not-a-time"}'
    )
    result = _parse_one(raw)
    assert isinstance(result, Decision)
    assert result.accepted is False
    assert result.reasons == (ReasonCode.INVALID_TIMESTAMP.value,)
