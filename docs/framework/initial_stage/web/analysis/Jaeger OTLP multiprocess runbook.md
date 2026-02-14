# Jaeger OTLP multiprocess runbook

## Goal

Run the multiprocess topology and inspect cross-process hop metadata in Jaeger.

## Prerequisites

- Docker available locally.
- Python env with project deps (`.venv`).

## 1) Start Jaeger (persistent) with docker-compose

```bash
docker compose -f docker-compose.jaeger.yml up -d
```

Jaeger UI: `http://127.0.0.1:16686`

## 2) Run multiprocess pipeline with OTLP exporter

Use dedicated config with tracing exporter:

- `src/fund_load/baseline_config_newgen_multiprocess_jaeger.yml`

Run:

```bash
.venv/bin/python -m fund_load \
  --config src/fund_load/baseline_config_newgen_multiprocess_jaeger.yml \
  --input docs/analysis/data/assets/input.txt
```

## 3) Inspect traces in Jaeger

1. Open Jaeger UI.
2. Select service: `fund-load-multiprocess`.
3. Search recent traces.
4. Open a span and inspect attributes:
   - `process_group`
   - `handoff_from`
   - `route_hop`
   - `stream_kernel.trace_id`

These attributes show message movement across process groups.

## 4) Stop Jaeger

```bash
docker compose -f docker-compose.jaeger.yml down
```

Keep data volume (default behavior) so traces survive restart.  
To stop and remove volume too:

```bash
docker compose -f docker-compose.jaeger.yml down -v
```

## Notes

- OTLP exporter is HTTP-based and isolated from execution flow: exporter failures increment dropped diagnostics and do not break business processing.
- This compose profile runs Jaeger as `root` (`user: "0:0"`) to avoid badger volume permission issues in local dev.
- If no traces appear, verify endpoint in config:
  - `runtime.observability.tracing.exporters[0].settings.endpoint`
  - expected: `http://127.0.0.1:4318/v1/traces`
