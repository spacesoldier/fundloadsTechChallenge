from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .event_ops import build_debug_run_event_insert
from .helpers import as_int, parse_iso, to_iso
from .types import RunSnapshot


@dataclass(slots=True)
class PostgresRunStore:
    dsn: str
    _initialized: bool = False

    def _connect(self) -> Any:
        import psycopg  # type: ignore[import-not-found]

        return psycopg.connect(self.dsn)

    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        schema_path = Path(__file__).resolve().parents[1] / "sql" / "postgres_schema.sql"
        schema_sql = schema_path.read_text(encoding="utf-8")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(schema_sql)
            conn.commit()
        self._initialized = True

    def save(self, snapshot: RunSnapshot) -> None:
        self._ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO debug_runs(run_id, logical_run_id, first_ts, last_ts, total_records, ingested_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (run_id) DO UPDATE
                    SET logical_run_id = EXCLUDED.logical_run_id,
                        first_ts = EXCLUDED.first_ts,
                        last_ts = EXCLUDED.last_ts,
                        total_records = EXCLUDED.total_records,
                        ingested_at = NOW()
                    """,
                    (
                        snapshot.run_id,
                        snapshot.logical_run_id,
                        parse_iso(snapshot.first_ts),
                        parse_iso(snapshot.last_ts),
                        snapshot.total_records,
                    ),
                )
                cur.execute(
                    "DELETE FROM debug_run_processes WHERE run_id = %s",
                    (snapshot.run_id,),
                )
                cur.execute(
                    "DELETE FROM debug_run_events WHERE run_id = %s", (snapshot.run_id,)
                )
                cur.executemany(
                    """
                    INSERT INTO debug_run_processes(
                        run_id, process_id, process_role, execution_group, worker_index,
                        event_count, first_ts, last_ts
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            snapshot.run_id,
                            item.process_id,
                            item.process_role,
                            item.execution_group,
                            item.worker_index,
                            item.event_count,
                            parse_iso(item.first_ts),
                            parse_iso(item.last_ts),
                        )
                        for item in snapshot.processes
                    ],
                )
                cur.executemany(
                    """
                    INSERT INTO debug_run_events(
                        run_id, seq, process_id, ts, event_name, source,
                        payload_model, payload_data_json,
                        payload_json, fields_json, timeline_ms
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        build_debug_run_event_insert(snapshot.run_id, event)
                        for event in snapshot.events
                    ],
                )
            conn.commit()

    def list_runs(self, *, limit: int = 50) -> list[dict[str, object]]:
        self._ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT run_id, logical_run_id, first_ts, last_ts, total_records
                    FROM debug_runs
                    ORDER BY first_ts DESC NULLS LAST, run_id DESC
                    LIMIT %s
                    """,
                    (max(1, int(limit)),),
                )
                rows = cur.fetchall()
                result: list[dict[str, object]] = []
                for run_id, logical_run_id, first_ts, last_ts, total_records in rows:
                    cur.execute(
                        """
                        SELECT process_id, process_role, execution_group, worker_index, event_count, first_ts, last_ts
                        FROM debug_run_processes
                        WHERE run_id = %s
                        ORDER BY process_role ASC, execution_group ASC, worker_index ASC
                        """,
                        (run_id,),
                    )
                    processes = [
                        {
                            "process_id": process_id,
                            "process_role": process_role,
                            "execution_group": execution_group,
                            "worker_index": int(worker_index),
                            "event_count": int(event_count),
                            "first_ts": to_iso(first_ts),
                            "last_ts": to_iso(last_ts),
                        }
                        for process_id, process_role, execution_group, worker_index, event_count, first_ts, last_ts in cur.fetchall()
                    ]
                    result.append(
                        {
                            "run_id": run_id,
                            "logical_run_id": logical_run_id,
                            "first_ts": to_iso(first_ts),
                            "last_ts": to_iso(last_ts),
                            "total_records": int(total_records),
                            "processes": processes,
                        }
                    )
                return result

    def run_events(
        self, run_id: str
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        self._ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT process_id, process_role, execution_group, worker_index, event_count, first_ts, last_ts
                    FROM debug_run_processes
                    WHERE run_id = %s
                    ORDER BY process_role ASC, execution_group ASC, worker_index ASC
                    """,
                    (run_id,),
                )
                processes = [
                    {
                        "process_id": process_id,
                        "process_role": process_role,
                        "execution_group": execution_group,
                        "worker_index": int(worker_index),
                        "event_count": int(event_count),
                        "first_ts": to_iso(first_ts),
                        "last_ts": to_iso(last_ts),
                    }
                    for process_id, process_role, execution_group, worker_index, event_count, first_ts, last_ts in cur.fetchall()
                ]
                cur.execute(
                    """
                    SELECT payload_json
                    FROM debug_run_events
                    WHERE run_id = %s
                    ORDER BY seq ASC
                    """,
                    (run_id,),
                )
                events = []
                for (payload_json,) in cur.fetchall():
                    try:
                        parsed = json.loads(str(payload_json))
                    except Exception:
                        continue
                    if isinstance(parsed, dict):
                        events.append(parsed)
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
        self._ensure_schema()
        where: list[str] = ["run_id = %s"]
        params: list[object] = [run_id]
        if isinstance(process_id, str) and process_id:
            where.append("process_id = %s")
            params.append(process_id)
        if isinstance(event_name, str) and event_name:
            where.append("event_name = %s")
            params.append(event_name)
        if isinstance(payload_model, str) and payload_model:
            where.append("payload_model = %s")
            params.append(payload_model)
        ts_from = parse_iso(timestamp_from)
        ts_to = parse_iso(timestamp_to)
        if ts_from is not None:
            where.append("ts >= %s")
            params.append(ts_from)
        if ts_to is not None:
            where.append("ts <= %s")
            params.append(ts_to)
        where_sql = " AND ".join(where)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT process_id, process_role, execution_group, worker_index, event_count, first_ts, last_ts
                    FROM debug_run_processes
                    WHERE run_id = %s
                    ORDER BY process_role ASC, execution_group ASC, worker_index ASC
                    """,
                    (run_id,),
                )
                processes = [
                    {
                        "process_id": process_id_row,
                        "process_role": process_role,
                        "execution_group": execution_group,
                        "worker_index": int(worker_index),
                        "event_count": int(event_count),
                        "first_ts": to_iso(first_ts),
                        "last_ts": to_iso(last_ts),
                    }
                    for process_id_row, process_role, execution_group, worker_index, event_count, first_ts, last_ts in cur.fetchall()
                ]

                cur.execute(
                    f"SELECT COUNT(*) FROM debug_run_events WHERE {where_sql}",
                    tuple(params),
                )
                count_row = cur.fetchone()
                total = int(count_row[0]) if isinstance(count_row, tuple) and count_row else 0

                cur.execute(
                    f"""
                    SELECT payload_json
                    FROM debug_run_events
                    WHERE {where_sql}
                    ORDER BY seq ASC
                    LIMIT %s OFFSET %s
                    """,
                    tuple([*params, max(1, int(limit)), max(0, int(offset))]),
                )
                events: list[dict[str, object]] = []
                for (payload_json,) in cur.fetchall():
                    try:
                        parsed = json.loads(str(payload_json))
                    except Exception:
                        continue
                    if isinstance(parsed, dict):
                        events.append(parsed)
                return (processes, events, total)
