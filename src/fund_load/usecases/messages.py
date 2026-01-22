from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fund_load.domain.messages import IdemStatus, LoadAttempt
from fund_load.domain.money import Money


# NOTE: docs/implementation/domain/Message Types.md places these flow messages in domain,
# but per project direction we keep step-to-step messages in usecases to keep domain minimal.
# We follow this choice consistently across steps and tests.
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
class IdempotencyClassifiedAttempt:
    # IdempotencyClassifiedAttempt is produced by Step 03 (docs/implementation/steps/03 IdempotencyGate.md).
    base: AttemptWithKeys
    idem_status: IdemStatus
    fingerprint: str
    canonical_line_no: int


@dataclass(frozen=True, slots=True)
class Features:
    # Features are derived in Step 04 (docs/implementation/steps/04 ComputeFeatures.md).
    risk_factor: Decimal
    effective_amount: Money
    is_prime_id: bool


@dataclass(frozen=True, slots=True)
class EnrichedAttempt:
    # EnrichedAttempt bundles idempotency output with derived features (Step 04).
    base: IdempotencyClassifiedAttempt
    features: Features


@dataclass(frozen=True, slots=True)
class Decision:
    # Decision mirrors Step 05 output but lives in usecases to keep domain minimal.
    line_no: int
    id: str
    customer_id: str
    accepted: bool
    reasons: tuple[str, ...]
    day_key: date
    week_key: date
    effective_amount: Money
    idem_status: IdemStatus
    is_prime_id: bool
    is_canonical: bool


@dataclass(frozen=True, slots=True)
class OutputLine:
    # OutputLine is produced by FormatOutput (docs/implementation/steps/07 FormatOutput.md).
    line_no: int
    json_text: str
