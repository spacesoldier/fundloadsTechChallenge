-- Simulation experiment results schema.
-- Stores per-scenario aggregate metrics from multi_leaf_sim.py and real_process_sim.py.
-- Complement to debug_run_events (which stores raw per-tick Redis events).

CREATE TABLE IF NOT EXISTS sim_scenarios (
    id              BIGSERIAL PRIMARY KEY,
    run_id          TEXT        NOT NULL,       -- matches Redis run_id
    run_ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    label           TEXT        NOT NULL,       -- human-readable scenario name
    sim_type        TEXT        NOT NULL,       -- 'multi_leaf' | 'real_process' | 'asyncio'
    n_leaves        INT         NOT NULL,
    total_rate_per_s FLOAT      NOT NULL,
    rate_per_leaf_s  FLOAT      NOT NULL,
    node_latency_ms  FLOAT      NOT NULL,
    tick_interval_ms FLOAT      NOT NULL,
    ipc_poll_ms      FLOAT      NOT NULL,
    sync_block_ms    FLOAT      NOT NULL DEFAULT 0,
    duration_s       FLOAT      NOT NULL,

    -- Throughput
    sent             INT         NOT NULL,
    received         INT         NOT NULL,
    processed        INT         NOT NULL,
    lost             INT         GENERATED ALWAYS AS (sent - received) STORED,
    throughput_per_s FLOAT       NOT NULL,
    efficiency_pct   FLOAT       NOT NULL,

    -- E2E latency (aggregate, all leaves)
    e2e_p50_ms       FLOAT       NULL,
    e2e_p90_ms       FLOAT       NULL,
    e2e_p99_ms       FLOAT       NULL,
    e2e_max_ms       FLOAT       NULL,

    -- Scheduler tick accuracy (aggregate)
    tick_p50_ms      FLOAT       NULL,
    tick_p99_ms      FLOAT       NULL,
    ticks_fired      INT         NULL,

    -- Runner queue depth
    runner_q_max     INT         NULL,
    runner_q_mean    FLOAT       NULL
);

CREATE INDEX IF NOT EXISTS idx_sim_scenarios_run_ts
    ON sim_scenarios(run_ts DESC);

CREATE INDEX IF NOT EXISTS idx_sim_scenarios_n_leaves
    ON sim_scenarios(n_leaves, run_ts DESC);

-- Per-leaf breakdown for each scenario
CREATE TABLE IF NOT EXISTS sim_scenario_leaves (
    id              BIGSERIAL PRIMARY KEY,
    scenario_id     BIGINT      NOT NULL REFERENCES sim_scenarios(id) ON DELETE CASCADE,
    leaf_id         INT         NOT NULL,
    pid             INT         NULL,
    received        INT         NOT NULL,
    processed       INT         NOT NULL,
    e2e_p50_ms      FLOAT       NULL,
    e2e_p99_ms      FLOAT       NULL,
    tick_p50_ms     FLOAT       NULL,
    runner_q_max    INT         NULL
);

CREATE INDEX IF NOT EXISTS idx_sim_scenario_leaves_scenario
    ON sim_scenario_leaves(scenario_id, leaf_id);
