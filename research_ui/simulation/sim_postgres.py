"""
Postgres writer for pipeline topology simulation results.
Tables: pipeline_sim_runs, pipeline_sim_stages, pipeline_sim_hops
Schema: research_ui/sql/pipeline_sim_schema.sql
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

POSTGRES_DSN = os.getenv(
    "RESEARCH_UI_POSTGRES_DSN",
    "postgresql://postgres:postgres@127.0.0.1:5432/research_ui",
)

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "sql" / "pipeline_sim_schema.sql"
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


def write_pipeline_run(
    *,
    run_id: str,
    label: str,
    n_stages: int,
    source_rate_per_s: float,
    node_latency_ms: float,
    sync_block_ms: float,
    tick_interval_ms: float,
    ipc_poll_ms: float,
    obs_every_n: int,
    duration_s: float,
    total_sent: int,
    total_completed: int,
    throughput_per_s: float,
    efficiency_pct: float,
    e2e_p50_ms: float | None,
    e2e_p90_ms: float | None,
    e2e_p99_ms: float | None,
    e2e_max_ms: float | None,
    root_tick_p50_ms: float | None,
    root_tick_p99_ms: float | None,
    root_routed_data: int,
    root_routed_obs: int,
    obs_received: int,
    stages: list[dict],   # [{stage_idx, pid, received, processed, tick_p50_ms, runner_q_max, obs_sent}]
    hops: list[dict],     # [{hop_idx, p50_ms, p90_ms, p99_ms, max_ms, count}]
) -> bool:
    if not ensure_schema():
        return False
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pipeline_sim_runs (
                        run_id, label,
                        n_stages, source_rate_per_s, node_latency_ms, sync_block_ms,
                        tick_interval_ms, ipc_poll_ms, obs_every_n, duration_s,
                        total_sent, total_completed, throughput_per_s, efficiency_pct,
                        e2e_p50_ms, e2e_p90_ms, e2e_p99_ms, e2e_max_ms,
                        root_tick_p50_ms, root_tick_p99_ms,
                        root_routed_data, root_routed_obs, obs_received
                    ) VALUES (
                        %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s
                    ) RETURNING id
                    """,
                    (
                        run_id, label,
                        n_stages, source_rate_per_s, node_latency_ms, sync_block_ms,
                        tick_interval_ms, ipc_poll_ms, obs_every_n, duration_s,
                        total_sent, total_completed, throughput_per_s, efficiency_pct,
                        e2e_p50_ms, e2e_p90_ms, e2e_p99_ms, e2e_max_ms,
                        root_tick_p50_ms, root_tick_p99_ms,
                        root_routed_data, root_routed_obs, obs_received,
                    ),
                )
                row = cur.fetchone()
                run_db_id = row[0] if row else None

                if run_db_id is not None:
                    if stages:
                        cur.executemany(
                            """
                            INSERT INTO pipeline_sim_stages
                                (run_id, stage_idx, pid, received, processed,
                                 tick_p50_ms, runner_q_max, obs_sent)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            [
                                (
                                    run_db_id,
                                    s.get("stage_idx", 0),
                                    s.get("pid"),
                                    s.get("received", 0),
                                    s.get("processed", 0),
                                    s.get("tick_p50_ms"),
                                    s.get("runner_q_max"),
                                    s.get("obs_sent", 0),
                                )
                                for s in stages
                            ],
                        )
                    if hops:
                        cur.executemany(
                            """
                            INSERT INTO pipeline_sim_hops
                                (run_id, hop_idx,
                                 hop_p50_ms, hop_p90_ms, hop_p99_ms, hop_max_ms, sample_count)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            [
                                (
                                    run_db_id,
                                    h.get("hop_idx", 0),
                                    h.get("p50_ms"),
                                    h.get("p90_ms"),
                                    h.get("p99_ms"),
                                    h.get("max_ms"),
                                    h.get("count", 0),
                                )
                                for h in hops
                            ],
                        )
            conn.commit()
        return True
    except Exception:
        return False
