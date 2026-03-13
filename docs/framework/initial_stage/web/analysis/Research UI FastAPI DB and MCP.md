# Research UI FastAPI DB and MCP

## Scope

This note documents the runtime-debug vitrine delivery path:

1. `FastAPI` endpoints in `research_ui/webapp.py`
2. Redis debug ingestion via `research_ui/debug_vitrine_backend.py`
3. Persistent storage in PostgreSQL and MongoDB
4. MCP access to PostgreSQL slices for investigation workflows

## Thin client layout

UI pages are static assets, not inline HTML in Python source:

- `research_ui/static/topology.html`
- `research_ui/static/debug-vitrine.html`

FastAPI only serves files and JSON APIs.

## API surface

- `GET /api/debug/runs`
- `GET /api/debug/runs/{run_id}/events`
- `POST /api/debug/runs/{run_id}/events/query`
- `POST /api/debug/reload`

`/api/debug/reload` pulls run payloads from Redis and persists them into configured stores.

## PostgreSQL schema

Schema file: `research_ui/sql/postgres_schema.sql`

Tables:

- `debug_runs`
- `debug_run_processes`
- `debug_run_events`

`debug_run_events` includes payload-focused fields for SQL slicing:

- `payload_model`
- `payload_data_json`
- `fields_json`
- `payload_json` (full original event)

## MCP server

Module: `research_ui/mcp/postgres_server.py`

Tools:

- `list_runs(limit)`
- `run_summary(run_id)`
- `query_events(run_id, limit, process_id, event_name)`
- `sql_select(query, limit)` (`SELECT`/`WITH` only)

Run script:

- `scripts/run_research_ui_mcp.sh`

## Debug payload enrichment

Runtime debug instrumentation adds payload metadata into debug events:

- `payload_model`
- `payload`
- `args_values`
- `kwargs_values`

This is produced for injected port calls and instrumented service method calls.
