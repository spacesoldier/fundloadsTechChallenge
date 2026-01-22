from __future__ import annotations

# Domain reason codes are a stable internal contract per docs/implementation/domain/Reason Codes.md.
from fund_load.domain.reasons import ReasonCode


def test_reason_codes_include_parse_and_amount_errors() -> None:
    # Core parse/normalization errors must remain stable for deterministic testing.
    assert ReasonCode.INPUT_PARSE_ERROR.value == "INPUT_PARSE_ERROR"
    assert ReasonCode.INVALID_TIMESTAMP.value == "INVALID_TIMESTAMP"
    assert ReasonCode.INVALID_AMOUNT_FORMAT.value == "INVALID_AMOUNT_FORMAT"
