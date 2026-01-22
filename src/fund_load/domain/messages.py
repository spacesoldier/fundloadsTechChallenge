from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping

from .money import Money


class IdemStatus(str, Enum):
    # Idempotency status values match docs/implementation/domain/Message Types.md.
    CANONICAL = "CANONICAL"
    DUP_REPLAY = "DUP_REPLAY"
    DUP_CONFLICT = "DUP_CONFLICT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RawLine:
    # RawLine preserves input order via line_no (docs/implementation/architecture/Flow spec.md).
    line_no: int
    raw_text: str


@dataclass(frozen=True, slots=True)
class LoadAttempt:
    # LoadAttempt is immutable per docs/implementation/domain/Message Types.md.
    line_no: int
    id: str
    customer_id: str
    amount: Money
    ts: datetime
    raw: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class WeekKey:
    # WeekKey follows calendar week semantics per docs/implementation/steps/02 ComputeTimeKeys.md.
    week_start_date: date
    week_start: str


@dataclass(frozen=True, slots=True)
class AttemptWithKeys:
    # AttemptWithKeys bundles derived day/week keys with the original attempt (Step 02).
    attempt: LoadAttempt
    day_key: date
    week_key: WeekKey


@dataclass(frozen=True, slots=True)
class Decision:
    # Decision carries fields needed by later steps (UpdateWindows/FormatOutput).
    line_no: int
    id: str
    customer_id: str
    accepted: bool
    reasons: tuple[str, ...] = ()
    idem_status: IdemStatus = IdemStatus.UNKNOWN
    day_key: date | None = None
    week_key: date | None = None
    effective_amount: Money | None = None
    is_prime_id: bool | None = None
    is_canonical: bool | None = None
