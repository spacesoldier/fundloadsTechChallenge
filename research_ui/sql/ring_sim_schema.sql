-- Ring topology simulation schema (Experiment B)
-- Topology: source → leaf:0 → leaf:1 → … → leaf:N-1 → sink
--           each leaf sends OBS events directly to obs leaf (3 dedicated pipes)
--           root only holds control pipes (command plane, near-idle)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ring_sim_runs (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              TEXT        NOT NULL,
    run_ts              TIMESTAMPTZ DEFAULT NOW(),
    label               TEXT        NOT NULL,
    scenario_id         TEXT        NOT NULL,   -- 'B-1' .. 'B-4'
    n_stages            INT         NOT NULL,
    ring_return         BOOLEAN     NOT NULL DEFAULT FALSE,
    source_rate_per_s   FLOAT       NOT NULL,
    node_latency_ms     FLOAT       NOT NULL,
    tick_interval_ms    FLOAT       NOT NULL,
    obs_steps_per_msg   INT         NOT NULL DEFAULT 0,
    duration_s          FLOAT       NOT NULL,
    total_sent          INT         NOT NULL DEFAULT 0,
    total_completed     INT         NOT NULL DEFAULT 0,
    lost        INT GENERATED ALWAYS AS (total_sent - total_completed) STORED,
    throughput_per_s    FLOAT,
    efficiency_pct      FLOAT,
    e2e_p50_ms          FLOAT,
    e2e_p90_ms          FLOAT,
    e2e_p99_ms          FLOAT,
    e2e_max_ms          FLOAT,
    rtt_p50_ms          FLOAT,      -- B-4 round-trip only
    rtt_p90_ms          FLOAT,
    rtt_p99_ms          FLOAT,
    -- OBS integration summary
    obs_total_received  INT         NOT NULL DEFAULT 0,
    jaeger_sent         INT         NOT NULL DEFAULT 0,
    jaeger_dropped      INT         NOT NULL DEFAULT 0,
    redis_sent          INT         NOT NULL DEFAULT 0,
    redis_dropped       INT         NOT NULL DEFAULT 0,
    kafka_sent          INT         NOT NULL DEFAULT 0,
    kafka_dropped       INT         NOT NULL DEFAULT 0,
    file_written        INT         NOT NULL DEFAULT 0,
    file_dropped        INT         NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ring_sim_leaves (
    id              BIGSERIAL   PRIMARY KEY,
    run_id          BIGINT      REFERENCES ring_sim_runs(id) ON DELETE CASCADE,
    leaf_idx        INT         NOT NULL,
    pid             INT         NOT NULL,
    received        INT         NOT NULL DEFAULT 0,
    processed       INT         NOT NULL DEFAULT 0,
    tick_p50_ms     FLOAT,
    tick_p99_ms     FLOAT,
    runner_q_max    INT,
    obs_logs_sent   INT         NOT NULL DEFAULT 0,
    obs_mon_sent    INT         NOT NULL DEFAULT 0,
    obs_traces_sent INT         NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ring_sim_hops (
    id          BIGSERIAL   PRIMARY KEY,
    run_id      BIGINT      REFERENCES ring_sim_runs(id) ON DELETE CASCADE,
    hop_idx     INT         NOT NULL,
    hop_label   TEXT        NOT NULL,   -- e.g. 'src→leaf:0', 'leaf:0→leaf:1'
    hop_p50_ms  FLOAT,
    hop_p90_ms  FLOAT,
    hop_p99_ms  FLOAT,
    hop_max_ms  FLOAT,
    sample_count INT        NOT NULL DEFAULT 0
);
