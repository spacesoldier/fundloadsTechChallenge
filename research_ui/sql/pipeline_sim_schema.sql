-- Pipeline topology simulation schema.
-- Separate from sim_scenarios (multi_leaf_sim) — different topology and metrics.
-- Populated by research_ui/simulation/pipeline_sim.py.

CREATE TABLE IF NOT EXISTS pipeline_sim_runs (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              TEXT        NOT NULL,
    run_ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    label               TEXT        NOT NULL,

    -- Topology config
    n_stages            INT         NOT NULL,
    source_rate_per_s   FLOAT       NOT NULL,
    node_latency_ms     FLOAT       NOT NULL,
    sync_block_ms       FLOAT       NOT NULL DEFAULT 0,
    tick_interval_ms    FLOAT       NOT NULL,
    ipc_poll_ms         FLOAT       NOT NULL,
    obs_every_n         INT         NOT NULL DEFAULT 0,
    duration_s          FLOAT       NOT NULL,

    -- Throughput
    total_sent          INT         NOT NULL,
    total_completed     INT         NOT NULL,
    lost                INT         GENERATED ALWAYS AS (total_sent - total_completed) STORED,
    throughput_per_s    FLOAT       NOT NULL,
    efficiency_pct      FLOAT       NOT NULL,

    -- E2E latency (source stamp → sink receipt)
    e2e_p50_ms          FLOAT       NULL,
    e2e_p90_ms          FLOAT       NULL,
    e2e_p99_ms          FLOAT       NULL,
    e2e_max_ms          FLOAT       NULL,

    -- Root router tick accuracy
    root_tick_p50_ms    FLOAT       NULL,
    root_tick_p99_ms    FLOAT       NULL,

    -- Root routing counters
    root_routed_data    INT         NOT NULL DEFAULT 0,
    root_routed_obs     INT         NOT NULL DEFAULT 0,

    -- OBS leaf
    obs_received        INT         NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pipeline_sim_runs_run_ts
    ON pipeline_sim_runs(run_ts DESC);

CREATE INDEX IF NOT EXISTS idx_pipeline_sim_runs_n_stages
    ON pipeline_sim_runs(n_stages, run_ts DESC);

-- Per-stage breakdown
CREATE TABLE IF NOT EXISTS pipeline_sim_stages (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT      NOT NULL REFERENCES pipeline_sim_runs(id) ON DELETE CASCADE,
    stage_idx       INT         NOT NULL,
    pid             INT         NULL,
    received        INT         NOT NULL,
    processed       INT         NOT NULL,
    tick_p50_ms     FLOAT       NULL,
    runner_q_max    INT         NULL,
    obs_sent        INT         NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pipeline_sim_stages_run
    ON pipeline_sim_stages(run_id, stage_idx);

-- Per-hop latency percentiles (hop = source→stage-i→root, indexed by hop)
CREATE TABLE IF NOT EXISTS pipeline_sim_hops (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT      NOT NULL REFERENCES pipeline_sim_runs(id) ON DELETE CASCADE,
    hop_idx         INT         NOT NULL,   -- 0 = source→stage-0, 1 = stage-0→stage-1, ...
    hop_p50_ms      FLOAT       NULL,
    hop_p90_ms      FLOAT       NULL,
    hop_p99_ms      FLOAT       NULL,
    hop_max_ms      FLOAT       NULL,
    sample_count    INT         NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pipeline_sim_hops_run
    ON pipeline_sim_hops(run_id, hop_idx);
