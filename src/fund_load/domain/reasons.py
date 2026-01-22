from __future__ import annotations

from enum import Enum


# Stable internal reason codes are defined in docs/implementation/domain/Reason Codes.md.
class ReasonCode(str, Enum):
    INPUT_PARSE_ERROR = "INPUT_PARSE_ERROR"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    INVALID_AMOUNT_FORMAT = "INVALID_AMOUNT_FORMAT"
    INVALID_ID_FORMAT = "INVALID_ID_FORMAT"
