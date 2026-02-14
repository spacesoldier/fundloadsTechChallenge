# Phase C: httpx backend (sync + async) (TDD)

## Objective

Add `httpx` exporter backend supporting both sync and async clients, including optional HTTP/2.

## Deliverables

- `httpx.Client` path for sync runtime;
- `httpx.AsyncClient` path for async runtime;
- HTTP/2 toggle and connection pool settings.

## RED tests

- `OBS-HTTPX-01` sync mode exports OTLP payload through `httpx.Client`.
- `OBS-HTTPX-02` async mode exports OTLP payload through `httpx.AsyncClient`.
- `OBS-HTTPX-03` HTTP/2 flag propagates to client settings.
- `OBS-HTTPX-04` retry/backoff policy is applied deterministically.
- `OBS-HTTPX-05` cancellation/shutdown drains queue and closes client cleanly.

## GREEN target

- backend selectable via `backend=httpx`;
- async mode integrates with AsyncRunner lifecycle hooks;
- shutdown path does not leak pending tasks/connections.

## Refactor

- unify sync/async transport wrappers under one backend facade.

## Exit criteria

- sync and async test paths green;
- no regression in trace continuity across process-group hops.
