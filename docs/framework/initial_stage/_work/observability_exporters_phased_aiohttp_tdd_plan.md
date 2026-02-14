# Phase D: aiohttp backend (TDD)

## Objective

Add high-throughput async OTLP HTTP backend using `aiohttp`.

## Deliverables

- `aiohttp.ClientSession` exporter transport;
- async batch worker + bounded queue + drop policy handling;
- graceful shutdown drain semantics.

## RED tests

- `OBS-AIO-01` exporter posts OTLP payload with `aiohttp` session.
- `OBS-AIO-02` bounded queue applies configured `drop_policy` deterministically.
- `OBS-AIO-03` flush interval triggers batch send under low throughput.
- `OBS-AIO-04` shutdown path flushes pending spans within timeout budget.
- `OBS-AIO-05` exporter exceptions remain isolated from business execution.

## GREEN target

- backend selectable via `backend=aiohttp`;
- requires async runtime rails;
- batching/backpressure behavior observable through diagnostics counters.

## Refactor

- shared queue/batch worker for async HTTP backends (`httpx.AsyncClient`, `aiohttp`).

## Exit criteria

- async backend tests green;
- no unbounded memory growth under synthetic burst test.
