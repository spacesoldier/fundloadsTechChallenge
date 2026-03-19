"""
Postgres writer for simulation scenario results.
Writes directly to sim_scenarios + sim_scenario_leaves tables.
Schema: research_ui/sql/sim_schema.sql
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


POSTGRES_DSN = os.getenv(
    "RESEARCH_UI_POSTGRES_DSN",
    "postgresql://postgres:postgres@127.0.0.1:5432/research_ui",
)

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "sql" / "sim_schema.sql"
)
_initialized = False


def _connect() -> Any:
    import psycopg
    return psycopg.connect(POSTGRES_DSN)


def ensure_schema() -> bool:
    global _initialized
    if _initialized:
        return True
    try:
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
        _initialized = True
        return True
    except Exception:
        return False


def write_scenario(
    *,
    run_id: str,
    label: str,
    sim_type: str,
    n_leaves: int,
    total_rate_per_s: float,
    rate_per_leaf_s: float,
    node_latency_ms: float,
    tick_interval_ms: float,
    ipc_poll_ms: float,
    sync_block_ms: float,
    duration_s: float,
    sent: int,
    received: int,
    processed: int,
    throughput_per_s: float,
    efficiency_pct: float,
    e2e_p50_ms: float | None,
    e2e_p90_ms: float | None,
    e2e_p99_ms: float | None,
    e2e_max_ms: float | None,
    tick_p50_ms: float | None,
    tick_p99_ms: float | None,
    ticks_fired: int | None,
    runner_q_max: int | None,
    runner_q_mean: float | None,
    leaves: list[dict],   # [{leaf_id, pid, received, processed, e2e_p50_ms, e2e_p99_ms, tick_p50_ms, runner_q_max}]
) -> bool:
    if not ensure_schema():
        return False
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sim_scenarios (
                        run_id, label, sim_type,
                        n_leaves, total_rate_per_s, rate_per_leaf_s,
                        node_latency_ms, tick_interval_ms, ipc_poll_ms, sync_block_ms, duration_s,
                        sent, received, processed, throughput_per_s, efficiency_pct,
                        e2e_p50_ms, e2e_p90_ms, e2e_p99_ms, e2e_max_ms,
                        tick_p50_ms, tick_p99_ms, ticks_fired,
                        runner_q_max, runner_q_mean
                    ) VALUES (
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s
                    ) RETURNING id
                    """,
                    (
                        run_id, label, sim_type,
                        n_leaves, total_rate_per_s, rate_per_leaf_s,
                        node_latency_ms, tick_interval_ms, ipc_poll_ms, sync_block_ms, duration_s,
                        sent, received, processed, throughput_per_s, efficiency_pct,
                        e2e_p50_ms, e2e_p90_ms, e2e_p99_ms, e2e_max_ms,
                        tick_p50_ms, tick_p99_ms, ticks_fired,
                        runner_q_max, runner_q_mean,
                    ),
                )
                row = cur.fetchone()
                scenario_id = row[0] if row else None

                if scenario_id is not None and leaves:
                    cur.executemany(
                        """
                        INSERT INTO sim_scenario_leaves (
                            scenario_id, leaf_id, pid,
                            received, processed,
                            e2e_p50_ms, e2e_p99_ms, tick_p50_ms, runner_q_max
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        [
                            (
                                scenario_id,
                                lf.get("leaf_id", 0),
                                lf.get("pid"),
                                lf.get("received", 0),
                                lf.get("processed", 0),
                                lf.get("e2e_p50_ms"),
                                lf.get("e2e_p99_ms"),
                                lf.get("tick_p50_ms"),
                                lf.get("runner_q_max"),
                            )
                            for lf in leaves
                        ],
                    )
            conn.commit()
        return True
    except Exception:
        return False
