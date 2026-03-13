from __future__ import annotations

import json
import os
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "mcp package is required. Install with: poetry install --with research-ui"
    ) from exc

import psycopg

_DSN = os.getenv(
    "RESEARCH_UI_POSTGRES_DSN",
    "postgresql://postgres:postgres@127.0.0.1:5432/research_ui",
)

mcp = FastMCP("research-ui-postgres")


def _query(sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
    with psycopg.connect(_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            columns = [item.name for item in cur.description] if cur.description else []
            rows = cur.fetchall()
    result: list[dict[str, object]] = []
    for row in rows:
        record: dict[str, object] = {}
        for idx, value in enumerate(row):
            key = columns[idx] if idx < len(columns) else f"c{idx}"
            record[key] = value
        result.append(record)
    return result


@mcp.tool()
def list_runs(limit: int = 20) -> str:
    rows = _query(
        """
        SELECT run_id, logical_run_id, first_ts, last_ts, total_records, ingested_at
        FROM debug_runs
        ORDER BY first_ts DESC NULLS LAST, run_id DESC
        LIMIT %s
        """,
        (max(1, min(int(limit), 500)),),
    )
    return json.dumps(rows, default=str)


@mcp.tool()
def run_summary(run_id: str) -> str:
    run_rows = _query(
        """
        SELECT run_id, logical_run_id, first_ts, last_ts, total_records, ingested_at
        FROM debug_runs
        WHERE run_id = %s
        """,
        (run_id,),
    )
    process_rows = _query(
        """
        SELECT process_id, process_role, execution_group, worker_index, event_count, first_ts, last_ts
        FROM debug_run_processes
        WHERE run_id = %s
        ORDER BY process_role ASC, execution_group ASC, worker_index ASC
        """,
        (run_id,),
    )
    return json.dumps({"run": run_rows, "processes": process_rows}, default=str)


@mcp.tool()
def query_events(
    run_id: str,
    limit: int = 200,
    process_id: str | None = None,
    event_name: str | None = None,
) -> str:
    where = ["run_id = %s"]
    params: list[object] = [run_id]
    if isinstance(process_id, str) and process_id:
        where.append("process_id = %s")
        params.append(process_id)
    if isinstance(event_name, str) and event_name:
        where.append("event_name = %s")
        params.append(event_name)
    params.append(max(1, min(int(limit), 5000)))
    sql = (
        "SELECT run_id, seq, process_id, ts, event_name, source, payload_model, payload_data_json, fields_json "
        "FROM debug_run_events "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY seq ASC LIMIT %s"
    )
    rows = _query(sql, tuple(params))
    return json.dumps(rows, default=str)


@mcp.tool()
def sql_select(query: str, limit: int = 500) -> str:
    raw = query.strip()
    normalized = raw.lower()
    if not (normalized.startswith("select") or normalized.startswith("with")):
        raise ValueError("Only SELECT/CTE statements are allowed")
    wrapped = f"SELECT * FROM ({raw}) AS q LIMIT %s"
    rows = _query(wrapped, (max(1, min(int(limit), 5000)),))
    return json.dumps(rows, default=str)


if __name__ == "__main__":  # pragma: no cover
    mcp.run()
