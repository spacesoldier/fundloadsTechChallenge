from __future__ import annotations

from enum import StrEnum


class RoutingErrorCode(StrEnum):
    UNKNOWN_TARGET = "unknown_target"
    TARGET_DOES_NOT_CONSUME = "target_does_not_consume"
    NO_CONSUMERS = "no_consumers"
    SELF_LOOP_REQUIRES_EXPLICIT_TARGET = "self_loop_requires_explicit_target"


class RoutingError(ValueError):
    def __init__(self, *, code: RoutingErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return self.message


__all__ = [
    "RoutingErrorCode",
    "RoutingError",
]
