# Phase F: OTLP gRPC backend (grpcio) (TDD)

## Status

- [x] `grpcio` backend implemented (channel/stub export path).
- [x] `OBS-GRPC-01..05` tests added and green.
- [x] `settings.grpc` config contract normalized and wired.

## Objective

Add binary OTLP gRPC exporter backend for lower overhead and collector-native pipeline.

## Deliverables

- gRPC channel/stub exporter path for OTLP traces;
- secure/insecure channel config model;
- retry/deadline mapping for gRPC status codes.
- config-level `settings.grpc` normalization.

## RED tests

- `OBS-GRPC-01` valid OTLP span batch is sent via gRPC export call.
- `OBS-GRPC-02` channel creation respects endpoint and tls/insecure mode.
- `OBS-GRPC-03` deadline/timeout settings map to call options.
- `OBS-GRPC-04` retriable status codes follow configured retry policy.
- `OBS-GRPC-05` non-retriable failure increments dropped/error counters and is isolated.

## GREEN target

- backend selectable via `backend=grpcio`;
- payload path remains trace-compatible with existing Jaeger/collector flow;
- exporter can run in both sync and async execution profiles through queue worker abstraction.

## Refactor

- isolate protobuf mapping layer behind transport-neutral span batch interface.

## Exit criteria

- gRPC backend tests green;
- collector integration smoke test passes with local OTLP gRPC endpoint.

## Implementation notes

- keep runner/business path transport-agnostic; grpc details stay inside observability adapter rails;
- support optional `settings.grpc`:
  - `insecure` (bool)
  - `timeout_seconds` (numeric > 0)
  - `retryable_status_codes` (list[str], e.g. `UNAVAILABLE`, `DEADLINE_EXCEEDED`)
- keep deterministic exporter-failure isolation.

## Validation commands

- `.venv/bin/pytest -q tests/adapters/test_trace_sinks.py -k 'obs_grpc_'`
- `.venv/bin/pytest -q tests/adapters/test_trace_sinks.py -k 'otel_otlp_trace_sink'`
- `.venv/bin/pytest -q tests/stream_kernel/config/test_newgen_validator.py -k 'obs_grpc_cfg_'`
