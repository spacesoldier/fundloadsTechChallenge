from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProcessSummary:
    process_id: str
    process_role: str
    execution_group: str
    worker_index: int
    event_count: int
    first_ts: str | None
    last_ts: str | None


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    run_id: str
    logical_run_id: str
    first_ts: str | None
    last_ts: str | None
    total_records: int
    processes: tuple[ProcessSummary, ...]
    events: tuple[dict[str, object], ...]
