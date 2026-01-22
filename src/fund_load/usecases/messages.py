from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fund_load.domain.messages import LoadAttempt


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
