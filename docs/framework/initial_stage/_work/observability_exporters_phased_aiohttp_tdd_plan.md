# Phase D: aiohttp backend (TDD)

## Status

- [x] `aiohttp` backend implemented with async transport path.
- [x] `OBS-AIO-01..05` tests added and green.
- [x] Queue drop-policy and `settings.aiohttp` config contract are validated and wired.

## Objective

Add high-throughput async OTLP HTTP backend using `aiohttp`.

## Deliverables

- `aiohttp.ClientSession` exporter transport;
- async batch worker + bounded queue + drop policy handling;
- graceful shutdown drain semantics.
- config-level `settings.aiohttp` contract normalization.

## RED tests

- `OBS-AIO-01` exporter posts OTLP payload with `aiohttp` session.
- `OBS-AIO-02` bounded queue applies configured `drop_policy` deterministically.
- `OBS-AIO-03` flush interval triggers batch send under low throughput.
- `OBS-AIO-04` shutdown path flushes pending spans within timeout budget.
- `OBS-AIO-05` exporter exceptions remain isolated from business execution.

## GREEN target

- backend selectable via `backend=aiohttp`;
- compatible with current sync rails via bridge-style execution and ready for AsyncRunner rollout;
- batching/backpressure behavior observable through diagnostics counters.

## Refactor

- shared queue/batch worker for async HTTP backends (`httpx.AsyncClient`, `aiohttp`).

## Exit criteria

- async backend tests green;
- no unbounded memory growth under synthetic burst test.

## Implementation notes

- keep shared OTLP payload model unchanged;
- support queue controls from common contract:
  - `queue.max_items`
  - `queue.drop_policy` (`drop_newest | drop_oldest | block_with_timeout`)
- add optional `settings.aiohttp` section:
  - `shutdown_timeout_seconds`
  - `connector_limit`
  - `connector_limit_per_host`

## Validation commands

- `.venv/bin/pytest -q tests/adapters/test_trace_sinks.py -k 'obs_aio_'`
- `.venv/bin/pytest -q tests/adapters/test_trace_sinks.py -k 'otel_otlp_trace_sink'`
- `.venv/bin/pytest -q tests/stream_kernel/config/test_newgen_validator.py -k 'obs_aio_cfg_'`
