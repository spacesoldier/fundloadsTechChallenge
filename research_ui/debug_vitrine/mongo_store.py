from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .event_ops import filter_events
from .helpers import as_int, as_optional_str, as_str


@dataclass(slots=True)
class MongoRunStore:
    uri: str
    db_name: str
    _client: Any | None = None

    def _db(self) -> Any:
        if self._client is None:
            from pymongo import MongoClient  # type: ignore[import-not-found]

            self._client = MongoClient(self.uri, serverSelectionTimeoutMS=1000)
        return self._client[self.db_name]

    def save(self, snapshot) -> None:
        db = self._db()
        runs = db.debug_runs
        processes = db.debug_run_processes
        events = db.debug_run_events
        runs.update_one(
            {"run_id": snapshot.run_id},
            {
                "$set": {
                    "run_id": snapshot.run_id,
                    "logical_run_id": snapshot.logical_run_id,
                    "first_ts": snapshot.first_ts,
                    "last_ts": snapshot.last_ts,
                    "total_records": snapshot.total_records,
                    "ingested_at": datetime.now(tz=UTC).isoformat(),
                }
            },
            upsert=True,
        )
        processes.delete_many({"run_id": snapshot.run_id})
        events.delete_many({"run_id": snapshot.run_id})
        if snapshot.processes:
            processes.insert_many(
                [
                    {
                        "run_id": snapshot.run_id,
                        "process_id": item.process_id,
                        "process_role": item.process_role,
                        "execution_group": item.execution_group,
                        "worker_index": item.worker_index,
                        "event_count": item.event_count,
                        "first_ts": item.first_ts,
                        "last_ts": item.last_ts,
                    }
                    for item in snapshot.processes
                ],
                ordered=False,
            )
        if snapshot.events:
            events.insert_many(
                [
                    {
                        "run_id": snapshot.run_id,
                        "seq": int(event.get("seq", 0)),
                        **event,
                    }
                    for event in snapshot.events
                ],
                ordered=False,
            )

    def list_runs(self, *, limit: int = 50) -> list[dict[str, object]]:
        db = self._db()
        rows = list(
            db.debug_runs.find({}, {"_id": 0})
            .sort([("first_ts", -1), ("run_id", -1)])
            .limit(max(1, int(limit)))
        )
        result: list[dict[str, object]] = []
        for row in rows:
            run_id = as_str(row.get("run_id"))
            proc_rows = list(
                db.debug_run_processes.find({"run_id": run_id}, {"_id": 0}).sort(
                    [("process_role", 1), ("execution_group", 1), ("worker_index", 1)]
                )
            )
            result.append(
                {
                    "run_id": run_id,
                    "logical_run_id": as_str(row.get("logical_run_id"), default=run_id),
                    "first_ts": as_optional_str(row.get("first_ts")),
                    "last_ts": as_optional_str(row.get("last_ts")),
                    "total_records": as_int(row.get("total_records")),
                    "processes": proc_rows,
                }
            )
        return result

    def run_events(
        self, run_id: str
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        db = self._db()
        processes = list(
            db.debug_run_processes.find({"run_id": run_id}, {"_id": 0}).sort(
                [("process_role", 1), ("execution_group", 1), ("worker_index", 1)]
            )
        )
        events = list(
            db.debug_run_events.find({"run_id": run_id}, {"_id": 0}).sort([("seq", 1)])
        )
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
