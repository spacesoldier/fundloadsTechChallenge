CREATE TABLE IF NOT EXISTS debug_runs (
    run_id TEXT PRIMARY KEY,
    logical_run_id TEXT NOT NULL,
    first_ts TIMESTAMPTZ NULL,
    last_ts TIMESTAMPTZ NULL,
    total_records BIGINT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS debug_run_processes (
    run_id TEXT NOT NULL,
    process_id TEXT NOT NULL,
    process_role TEXT NOT NULL,
    execution_group TEXT NOT NULL,
    worker_index INT NOT NULL,
    event_count BIGINT NOT NULL,
    first_ts TIMESTAMPTZ NULL,
    last_ts TIMESTAMPTZ NULL,
    PRIMARY KEY (run_id, process_id)
);

CREATE TABLE IF NOT EXISTS debug_run_events (
    run_id TEXT NOT NULL,
    seq BIGINT NOT NULL,
    process_id TEXT NOT NULL,
    ts TIMESTAMPTZ NULL,
    event_name TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_model TEXT NULL,
    payload_data_json TEXT NULL,
    payload_json TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    timeline_ms BIGINT NULL,
    PRIMARY KEY (run_id, seq)
);

ALTER TABLE debug_run_events
    ADD COLUMN IF NOT EXISTS payload_model TEXT NULL;
ALTER TABLE debug_run_events
    ADD COLUMN IF NOT EXISTS payload_data_json TEXT NULL;

CREATE INDEX IF NOT EXISTS idx_debug_run_events_run_ts
    ON debug_run_events(run_id, ts, seq);

CREATE INDEX IF NOT EXISTS idx_debug_run_events_process
    ON debug_run_events(run_id, process_id, seq);

CREATE INDEX IF NOT EXISTS idx_debug_run_events_name
    ON debug_run_events(run_id, event_name, seq);

CREATE INDEX IF NOT EXISTS idx_debug_run_events_payload_model
    ON debug_run_events(run_id, payload_model);
