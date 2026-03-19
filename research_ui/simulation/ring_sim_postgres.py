"""Postgres writer for ring topology simulation (Experiment B)."""
from __future__ import annotations

import os
from pathlib import Path

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "sql" / "ring_sim_schema.sql"

_DSN = os.getenv(
    "RESEARCH_UI_DB_URL",
    "postgresql://postgres:postgres@127.0.0.1:5432/research_ui",
)


def _connect():
    import psycopg
    return psycopg.connect(_DSN, autocommit=False)


def ensure_schema() -> bool:
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA_PATH.read_text())
            conn.commit()
        return True
    except Exception as exc:
        print(f"[ring_sim_postgres] ensure_schema failed: {exc}")
        return False


def write_ring_run(
    *,
    run_id: str,
    label: str,
    scenario_id: str,
    n_stages: int,
    ring_return: bool,
    source_rate_per_s: float,
    node_latency_ms: float,
    tick_interval_ms: float,
    obs_steps_per_msg: int,
    duration_s: float,
    total_sent: int,
    total_completed: int,
    throughput_per_s: float,
    efficiency_pct: float,
    e2e_p50_ms: float | None,
    e2e_p90_ms: float | None,
    e2e_p99_ms: float | None,
    e2e_max_ms: float | None,
    rtt_p50_ms: float | None,
    rtt_p90_ms: float | None,
    rtt_p99_ms: float | None,
    obs_total_received: int,
    jaeger_sent: int,
    jaeger_dropped: int,
    redis_sent: int,
    redis_dropped: int,
    kafka_sent: int,
    kafka_dropped: int,
    file_written: int,
    file_dropped: int,
    leaves: list[dict],   # [{leaf_idx, pid, received, processed, tick_p50_ms,
                          #   tick_p99_ms, runner_q_max, obs_logs_sent, obs_mon_sent,
                          #   obs_traces_sent}]
    hops: list[dict],     # [{hop_idx, hop_label, p50_ms, p90_ms, p99_ms, max_ms, count}]
) -> bool:
    if not ensure_schema():
        return False
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ring_sim_runs (
                        run_id, label, scenario_id,
                        n_stages, ring_return,
                        source_rate_per_s, node_latency_ms, tick_interval_ms,
                        obs_steps_per_msg, duration_s,
                        total_sent, total_completed, throughput_per_s, efficiency_pct,
                        e2e_p50_ms, e2e_p90_ms, e2e_p99_ms, e2e_max_ms,
                        rtt_p50_ms, rtt_p90_ms, rtt_p99_ms,
                        obs_total_received,
                        jaeger_sent, jaeger_dropped,
                        redis_sent,  redis_dropped,
                        kafka_sent,  kafka_dropped,
                        file_written, file_dropped
                    ) VALUES (
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s,
                        %s, %s,
                        %s, %s,
                        %s, %s,
                        %s, %s
                    ) RETURNING id
                    """,
                    (
                        run_id, label, scenario_id,
                        n_stages, ring_return,
                        source_rate_per_s, node_latency_ms, tick_interval_ms,
                        obs_steps_per_msg, duration_s,
                        total_sent, total_completed, throughput_per_s, efficiency_pct,
                        e2e_p50_ms, e2e_p90_ms, e2e_p99_ms, e2e_max_ms,
                        rtt_p50_ms, rtt_p90_ms, rtt_p99_ms,
                        obs_total_received,
                        jaeger_sent, jaeger_dropped,
                        redis_sent, redis_dropped,
                        kafka_sent, kafka_dropped,
                        file_written, file_dropped,
                    ),
                )
                row = cur.fetchone()
                run_db_id = row[0] if row else None

                if run_db_id is not None:
                    if leaves:
                        cur.executemany(
                            """
                            INSERT INTO ring_sim_leaves (
                                run_id, leaf_idx, pid, received, processed,
                                tick_p50_ms, tick_p99_ms, runner_q_max,
                                obs_logs_sent, obs_mon_sent, obs_traces_sent
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            """,
                            [
                                (
                                    run_db_id,
                                    lf["leaf_idx"], lf["pid"],
                                    lf["received"], lf["processed"],
                                    lf.get("tick_p50_ms"), lf.get("tick_p99_ms"),
                                    lf.get("runner_q_max"),
                                    lf["obs_logs_sent"], lf["obs_mon_sent"],
                                    lf["obs_traces_sent"],
                                )
                                for lf in leaves
                            ],
                        )
                    if hops:
                        cur.executemany(
                            """
                            INSERT INTO ring_sim_hops (
                                run_id, hop_idx, hop_label,
                                hop_p50_ms, hop_p90_ms, hop_p99_ms, hop_max_ms,
                                sample_count
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                            """,
                            [
                                (
                                    run_db_id,
                                    h["hop_idx"], h["hop_label"],
                                    h.get("p50_ms"), h.get("p90_ms"),
                                    h.get("p99_ms"), h.get("max_ms"),
                                    h.get("count", 0),
                                )
                                for h in hops
                            ],
                        )
            conn.commit()
        return True
    except Exception as exc:
        print(f"[ring_sim_postgres] write_ring_run failed: {exc}")
        return False
