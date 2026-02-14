# Phase A: exporter contract and config freeze (TDD)

## Objective

Freeze one unified exporter contract and runtime config schema before backend-specific implementations.

## Deliverables

- common transport-agnostic sink interface for trace export;
- backend selector in config;
- shared settings blocks (timeouts, retries, batching, queue bounds, drop policy);
- compatibility table for sync/async runtimes.

## Contract

`runtime.observability.tracing.exporters[]` entry:

- `kind: otel_otlp`
- `backend: urllib | requests | httpx | aiohttp | urllib3 | grpcio | otel_sdk`
- `settings: mapping`

Shared settings:

- `endpoint`
- `headers`
- `timeout_seconds`
- `batch.max_items`
- `batch.flush_interval_ms`
- `queue.max_items`
- `queue.drop_policy` (`drop_newest | drop_oldest | block_with_timeout`)
- `retry.max_attempts`
- `retry.backoff_ms`
- `service_name` + resource fields

## RED tests

- `OBS-CFG-A-01` reject unknown `backend`.
- `OBS-CFG-A-02` reject async-only backend with `runner_profile=sync` when async bridging disabled.
- `OBS-CFG-A-03` reject invalid batching/retry bounds.
- `OBS-CFG-A-04` default profile is backward-compatible with current config.
- `OBS-CFG-A-05` reject process-group with `runner_profile=sync` when any bound dependency contract is async-only.
- `OBS-CFG-A-06` reject process-group with `runner_profile=async` when a required dependency is sync-only and no bridge/wrapper is declared.

## GREEN target

- validator accepts normalized config;
- observer factory receives normalized backend descriptor;
- old `otel_otlp` config without `backend` maps to `urllib` (compat).
- preflight establishes deterministic runner compatibility checks for DI contracts before execution start.

## Refactor

- centralize settings normalization helpers;
- add docs section with backend capability matrix.

## Exit criteria

- contract tests green;
- no behavior change for existing current configs by default.
