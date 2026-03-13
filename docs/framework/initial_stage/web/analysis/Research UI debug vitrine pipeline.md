# Research UI debug vitrine pipeline

This document describes the runtime-debug vitrine added to `research_ui`.

## Goal

Provide a fast inspection surface for multiprocess execution:

- load runtime debug events from Redis Streams;
- continuously ingest snapshots to Postgres and/or MongoDB;
- render time-aligned events by process columns in UI.

## Components

- `research_ui.debug_vitrine_backend`
  - Redis source: reads `stream_kernel:debug:*` run/process/event keys.
  - Ingestion service: reloads runs and stores snapshots.
  - Stores:
    - in-memory (always on),
    - Postgres (optional),
    - MongoDB (optional).
- `research_ui.debug_ingest_worker`
  - background poller that calls Redis reload and persists changed runs only.
- `research_ui.webapp`
  - API endpoints for reload/runs/events.
  - `/debug-vitrine` page with client-side timeline rendering.

## Redis source contract

Expected key layout:

- `<prefix>:runs:index:by_time` (zset of run ids)
- `<prefix>:runs:meta:<run_id>` (hash run metadata)
- `<prefix>:runs:<run_id>:processes:by_time` or `<prefix>:runs:<run_id>:processes`
- `<prefix>:runs:<run_id>:debug:<process_id>` (Redis Stream of JSON debug records)
- `<prefix>:runs:<run_id>:logs:<process_id>` (Redis Stream of JSON log records)

The parser is RESP-based and uses direct socket I/O (`TYPE`, `XRANGE`, fallback `LRANGE` for legacy keys).

## API contract

- `POST /api/debug/reload`
  - body:
    - `run_id?: string`
    - `limit_runs: int` (default 20)
    - `max_events_per_process: int` (default 100000)
  - action: force reload from Redis Streams and persist changed runs.

- `GET /api/debug/runs?limit=N`
  - returns run list with process summaries.

- `GET /api/debug/runs/{run_id}/events?config_path=...`
  - returns:
    - process column order,
    - chronological event stream enriched with timeline ratio,
    - communication marker (`is_communication`) for channel-related events.

## UI behavior (`/debug-vitrine`)

- left panel:
  - reload controls;
  - list of runs with summary.
- right panel:
  - columns by process order:
    - observability first,
    - root second,
    - execution groups next (config order),
    - unknown groups last.
  - events positioned by timestamp ratio (`0..1`) on vertical axis.
  - communication events highlighted separately from generic runtime events.

## Docker compose additions

`research_ui/docker-compose.yml` includes:

- `postgres` (`research_ui` DB),
- `mongodb`,
- `research_ui` service wired to both and to Redis source.

Environment knobs:

- `RESEARCH_UI_REDIS_*`
- `RESEARCH_UI_REDIS_AUTO_INGEST_ENABLED`
- `RESEARCH_UI_REDIS_AUTO_INGEST_INTERVAL_SECONDS`
- `RESEARCH_UI_REDIS_AUTO_INGEST_LIMIT_RUNS`
- `RESEARCH_UI_REDIS_AUTO_INGEST_MAX_EVENTS_PER_PROCESS`
- `RESEARCH_UI_POSTGRES_ENABLED`, `RESEARCH_UI_POSTGRES_DSN`
- `RESEARCH_UI_MONGO_ENABLED`, `RESEARCH_UI_MONGO_URI`, `RESEARCH_UI_MONGO_DB`
