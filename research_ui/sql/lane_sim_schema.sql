-- Schema for multi-lane topology simulation experiments (A: star-5lane, B: ring, C: hybrid).
-- Created by: multi_lane_star_sim.py / ring_sim.py
-- Extends pipeline_sim_schema.sql concepts with topology_type and lane-level metrics.

CREATE TABLE IF NOT EXISTS lane_sim_runs (
    id              BIGSERIAL    PRIMARY KEY,
    run_id          TEXT         NOT NULL,
    run_ts          TIMESTAMPTZ  DEFAULT NOW(),
    label           TEXT         NOT NULL,
    topology        TEXT         NOT NULL,   -- 'star_5lane', 'ring', 'hybrid'
    n_stages        INT          NOT NULL,
    n_endpoints     INT          NOT NULL,   -- root's total adapter endpoint count
    source_rate_per_s   FLOAT   NOT NULL,
    node_latency_ms     FLOAT   NOT NULL,
    sync_block_ms       FLOAT   NOT NULL DEFAULT 0,
    tick_interval_ms    FLOAT   NOT NULL,
    ipc_poll_ms         FLOAT   NOT NULL,
    obs_every_n         INT     NOT NULL DEFAULT 0,
    obs_steps_per_msg   INT     NOT NULL DEFAULT 0,
    ack_enabled         BOOLEAN NOT NULL DEFAULT FALSE,
    duration_s          FLOAT   NOT NULL,
    total_sent          INT     NOT NULL,
    total_completed     INT     NOT NULL,
    lost            INT GENERATED ALWAYS AS (total_sent - total_completed) STORED,
    throughput_per_s    FLOAT   NULL,
    efficiency_pct      FLOAT   NULL,
    e2e_p50_ms      FLOAT   NULL,
    e2e_p90_ms      FLOAT   NULL,
    e2e_p99_ms      FLOAT   NULL,
    e2e_max_ms      FLOAT   NULL,
    root_tick_p50_ms    FLOAT   NULL,
    root_tick_p99_ms    FLOAT   NULL,
    root_routed_data    INT     NOT NULL DEFAULT 0,
    root_routed_logs    INT     NOT NULL DEFAULT 0,
    root_routed_mon     INT     NOT NULL DEFAULT 0,
    root_routed_traces  INT     NOT NULL DEFAULT 0,
    root_acks_to_stages INT     NOT NULL DEFAULT 0,
    root_acks_from_obs  INT     NOT NULL DEFAULT 0,
    obs_received        INT     NOT NULL DEFAULT 0,
    obs_acks_sent       INT     NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS lane_sim_stages (
    id          BIGSERIAL   PRIMARY KEY,
    run_id      BIGINT      REFERENCES lane_sim_runs(id) ON DELETE CASCADE,
    stage_idx   INT         NOT NULL,
    pid         INT,
    received    INT         NOT NULL DEFAULT 0,
    processed   INT         NOT NULL DEFAULT 0,
    tick_p50_ms FLOAT       NULL,
    runner_q_max INT        NULL,
    obs_logs_sent   INT     NOT NULL DEFAULT 0,
    obs_mon_sent    INT     NOT NULL DEFAULT 0,
    obs_traces_sent INT     NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS lane_sim_hops (
    id          BIGSERIAL   PRIMARY KEY,
    run_id      BIGINT      REFERENCES lane_sim_runs(id) ON DELETE CASCADE,
    hop_idx     INT         NOT NULL,
    hop_p50_ms  FLOAT       NULL,
    hop_p90_ms  FLOAT       NULL,
    hop_p99_ms  FLOAT       NULL,
    hop_max_ms  FLOAT       NULL,
    sample_count INT        NOT NULL DEFAULT 0
);
