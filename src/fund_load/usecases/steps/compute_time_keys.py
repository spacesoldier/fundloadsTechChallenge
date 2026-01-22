from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fund_load.domain.messages import LoadAttempt
from fund_load.usecases.messages import AttemptWithKeys, WeekKey


@dataclass(frozen=True, slots=True)
class ComputeTimeKeys:
    # Step 02 derives UTC day/week keys per docs/implementation/steps/02 ComputeTimeKeys.md.
    week_start: str = "MON"

    def __call__(self, msg: LoadAttempt, ctx: object | None) -> list[AttemptWithKeys]:
        # Day key is the UTC date derived from the already-normalized timestamp.
        day_key = msg.ts.date()
        week_key = _compute_week_key(day_key, self.week_start)
        return [AttemptWithKeys(attempt=msg, day_key=day_key, week_key=week_key)]


_WEEKDAY_ORDER = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def _compute_week_key(day_key: date, week_start: str) -> WeekKey:
    # Calendar week is anchored to week_start; rolling windows are out of scope here.
    if week_start not in _WEEKDAY_ORDER:
        raise ValueError("week_start must be one of MON..SUN")

    dow = day_key.weekday()  # 0=Mon..6=Sun
    start_dow = _WEEKDAY_ORDER.index(week_start)
    delta = (dow - start_dow) % 7
    week_start_date = day_key.fromordinal(day_key.toordinal() - delta)
    return WeekKey(week_start_date=week_start_date, week_start=week_start)
