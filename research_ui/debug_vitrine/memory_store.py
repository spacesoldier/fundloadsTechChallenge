from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

from .event_ops import filter_events, process_summary_dict, run_summary_dict
from .types import RunSnapshot


@dataclass(slots=True)
class InMemoryRunStore:
    _runs: dict[str, RunSnapshot] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def save(self, snapshot: RunSnapshot) -> None:
        with self._lock:
            self._runs[snapshot.run_id] = snapshot

    def list_runs(self, *, limit: int = 50) -> list[dict[str, object]]:
        with self._lock:
            runs = list(self._runs.values())
        runs.sort(
            key=lambda item: (
                item.first_ts or "",
                item.run_id,
            ),
            reverse=True,
        )
        return [run_summary_dict(item) for item in runs[: max(1, int(limit))]]

    def run_events(
        self, run_id: str
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        with self._lock:
            snapshot = self._runs.get(run_id)
        if snapshot is None:
            return ([], [])
        processes = [process_summary_dict(item) for item in snapshot.processes]
        events = [dict(item) for item in snapshot.events]
        return (processes, events)

    def query_events(
        self,
        run_id: str,
        *,
        process_id: str | None = None,
        event_name: str | None = None,
        payload_model: str | None = None,
        timestamp_from: str | None = None,
        timestamp_to: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]], int]:
        processes, events = self.run_events(run_id)
        filtered = filter_events(
            events,
            process_id=process_id,
            event_name=event_name,
            payload_model=payload_model,
            timestamp_from=timestamp_from,
            timestamp_to=timestamp_to,
        )
        total = len(filtered)
        sliced = filtered[
            max(0, int(offset)) : max(0, int(offset)) + max(1, int(limit))
        ]
        return (processes, sliced, total)
