# Phase B: requests Session backend (TDD)

## Status

- [x] `requests` backend implemented in OTLP trace sink.
- [x] `OBS-REQ-01..05` tests added and green.
- [x] Exporter `backend` is forwarded from runtime exporter entry into tracing adapter settings.

## Objective

Add `requests`-based OTLP HTTP exporter backend with pooled connections and deterministic failure isolation.

## Deliverables

- adapter implementation using `requests.Session`;
- configurable session pool and retry policy;
- batching worker integration from common contract.

## RED tests

- `OBS-REQ-01` backend posts OTLP payload via Session.
- `OBS-REQ-02` keep-alive session is reused across multiple spans.
- `OBS-REQ-03` request headers and timeout are respected.
- `OBS-REQ-04` exporter transport exception increments dropped counter and does not raise into runner.
- `OBS-REQ-05` batch flush by item-count and by timer works deterministically.

## GREEN target

- backend selectable via `backend=requests`;
- sync runner remains stable;
- no direct per-span blocking in runner thread when async exporter queue is enabled.

## Refactor

- shared HTTP payload builder reused with urllib/httpx/urllib3 backends.

## Exit criteria

- focused backend tests green;
- parity with existing OTLP payload fields preserved.

## Validation commands

- `.venv/bin/pytest -q tests/adapters/test_trace_sinks.py -k 'obs_req_'`
- `.venv/bin/pytest -q tests/stream_kernel/observability/test_tracing_observer_factory.py -k 'forwards_otel_backend'`
- `.venv/bin/pytest -q tests/adapters/test_trace_sinks.py -k 'otel_otlp_trace_sink'`
